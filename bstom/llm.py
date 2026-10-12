"""Provider boundary. API limits apply globally, not once per agent/session."""
import asyncio
import hashlib
import json
import re
import time
from collections import deque
from contextlib import asynccontextmanager
from urllib.parse import urlparse

import httpx
from pydantic import Field

from .models import Decision
from .prompts import decision_messages, local_decision_messages, speech_messages


class BudgetExceeded(Exception):
    pass


class ProviderError(Exception):
    def __init__(self, code="network_error", retry_after=8):
        self.code = code
        self.retry_after = retry_after
        super().__init__(code)


class CallGate:
    def __init__(self, settings):
        self.settings = settings
        self.semaphore = asyncio.Semaphore(settings.api_max_concurrency)
        self.lock = asyncio.Lock()
        self.starts = deque()
        self.tokens = deque()
        self.blocked_until = 0.0

    @asynccontextmanager
    async def call(self, session, estimated_tokens=0):
        async with self.semaphore:
            while True:
                async with self.lock:
                    now = time.monotonic()
                    while self.starts and self.starts[0] <= now - 60:
                        self.starts.popleft()
                    while self.tokens and self.tokens[0][0] <= now - 60:
                        self.tokens.popleft()
                    delay = max(0, self.blocked_until - now)
                    if self.settings.api_requests_per_minute and len(self.starts) >= self.settings.api_requests_per_minute:
                        delay = max(delay, self.starts[0] + 60 - now)
                    if self.settings.api_tokens_per_minute and self.tokens and sum(tokens for _, tokens in self.tokens) + estimated_tokens > self.settings.api_tokens_per_minute:
                        delay = max(delay, self.tokens[0][0] + 60 - now)
                    if delay <= 0:
                        if self.settings.session_max_calls and session.calls >= self.settings.session_max_calls:
                            raise BudgetExceeded()
                        session.calls += 1
                        self.starts.append(now)
                        reservation = [now, estimated_tokens]
                        self.tokens.append(reservation)
                        break
                if delay >= 0.5:
                    session.emit("api_wait", seconds=round(delay, 1),
                                 reason="provider_retry" if self.blocked_until > time.monotonic() else "configured_pacing")
                await asyncio.sleep(max(0.01, delay))
            yield reservation


class CompatibleLLM:
    def __init__(self, settings, gate):
        self.settings, self.gate = settings, gate
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(25), follow_redirects=False)

    def payload(self, messages, stream=False, decision=False):
        payload = {"model": self.settings.llm_model, "messages": messages,
                   "max_completion_tokens": self.settings.max_output_tokens,
                   "stream": stream, "temperature": 0.85}
        if decision:
            payload["response_format"] = {"type": "json_object"}
        if "gpt-oss" in self.settings.llm_model:
            payload["reasoning_effort"] = "low"
        elif "qwen3" in self.settings.llm_model:
            payload["reasoning_effort"] = "none"
        if stream:
            payload["stream_options"] = {"include_usage": True}
        return payload

    def headers(self):
        return {"Authorization": f"Bearer {self.settings.llm_api_key}"}

    def estimate(self, messages):
        # Conservative UTF-8 estimate + reserved output, not a billing measurement.
        return sum(len(m["content"].encode("utf-8")) // 2 + 12 for m in messages) + self.settings.max_output_tokens

    def check(self, response):
        if response.status_code == 429:
            try:
                delay = min(120, max(5, float(response.headers.get("retry-after", "15"))))
            except ValueError:
                delay = 15
            self.gate.blocked_until = time.monotonic() + delay
            raise ProviderError("rate_limited", delay)
        if response.is_error:
            # Provider response bodies may echo credentials or private prompts.
            raise ProviderError(f"http_{response.status_code}", 15)

    async def decide(self, agent, view, session):
        try:
            messages = decision_messages(agent, view)
            async with self.gate.call(session, self.estimate(messages)) as reservation:
                response = await self.client.post(self.settings.llm_base_url.rstrip("/") + "/chat/completions",
                                                  headers=self.headers(),
                                                  json=self.payload(messages, decision=True))
                self.check(response)
                result = response.json()
                usage = result.get("usage", {}).get("total_tokens")
                if isinstance(usage, int) and usage >= 0:
                    reservation[1] = usage
                content = result["choices"][0]["message"]["content"]
                return Decision.model_validate_json(content)
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError("invalid_response" if not isinstance(exc, httpx.HTTPError) else "network_error") from None

    async def stream(self, agent, view, decision, session):
        try:
            messages = speech_messages(agent, view, decision)
            async with self.gate.call(session, self.estimate(messages)) as reservation:
                async with self.client.stream("POST", self.settings.llm_base_url.rstrip("/") + "/chat/completions",
                                              headers=self.headers(),
                                              json=self.payload(messages, stream=True)) as response:
                    self.check(response)
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        item = json.loads(data)
                        usage = (item.get("usage") or item.get("x_groq", {}).get("usage") or {}).get("total_tokens")
                        if isinstance(usage, int) and usage >= 0:
                            reservation[1] = usage
                        choices = item.get("choices", [])
                        if choices:
                            chunk = choices[0].get("delta", {}).get("content")
                            if chunk:
                                yield chunk
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
            raise ProviderError("stream_error") from None

    async def close(self):
        await self.client.aclose()


class GeminiLLM(CompatibleLLM):
    """Native Gemini REST adapter; secrets stay in headers, never URL parameters."""

    def headers(self):
        return {"x-goog-api-key": self.settings.gemini_api_key}

    def endpoint(self, method):
        model = self.settings.llm_model.removeprefix("models/")
        return f'{self.settings.llm_base_url.rstrip("/")}/models/{model}:{method}'

    def payload(self, messages, stream=False, decision=False):
        config = {"maxOutputTokens": self.settings.max_output_tokens, "temperature": 0.85}
        if self.settings.llm_model.removeprefix("models/").startswith("gemini-3"):
            config["thinkingConfig"] = {"thinkingLevel": "LOW"}
        if decision:
            config.update(responseMimeType="application/json", responseJsonSchema=Decision.model_json_schema())
        return {"systemInstruction": {"parts": [{"text": "\n".join(m["content"] for m in messages if m["role"] == "system")}]},
                "contents": [{"role": "model" if m["role"] == "assistant" else "user",
                              "parts": [{"text": m["content"]}]} for m in messages if m["role"] != "system"],
                "generationConfig": config}

    @staticmethod
    def content(result, reservation):
        usage = result.get("usageMetadata", {}).get("totalTokenCount")
        if isinstance(usage, int) and usage >= 0:
            reservation[1] = usage
        if result.get("error"):
            raise ProviderError("provider_stream_error")
        if result.get("promptFeedback", {}).get("blockReason"):
            raise ProviderError("content_blocked", 15)
        candidates = result.get("candidates", [])
        if not candidates:
            return ""
        candidate = candidates[0]
        reason = candidate.get("finishReason")
        if reason and reason not in ("STOP", "MAX_TOKENS"):
            raise ProviderError("content_blocked", 15)
        return "".join(part.get("text", "") for part in candidate.get("content", {}).get("parts", [])
                       if not part.get("thought"))

    async def decide(self, agent, view, session):
        try:
            messages = decision_messages(agent, view)
            async with self.gate.call(session, self.estimate(messages)) as reservation:
                response = await self.client.post(self.endpoint("generateContent"), headers=self.headers(),
                                                  json=self.payload(messages, decision=True))
                self.check(response)
                return Decision.model_validate_json(self.content(response.json(), reservation))
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError("network_error" if isinstance(exc, httpx.HTTPError) else "invalid_response") from None

    async def stream(self, agent, view, decision, session):
        try:
            messages = speech_messages(agent, view, decision)
            received = False
            async with self.gate.call(session, self.estimate(messages)) as reservation:
                async with self.client.stream("POST", self.endpoint("streamGenerateContent"), params={"alt": "sse"},
                                              headers=self.headers(), json=self.payload(messages)) as response:
                    self.check(response)
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if not data:
                            continue
                        chunk = self.content(json.loads(data), reservation)
                        if chunk:
                            received = True
                            yield chunk
                    if not received:
                        raise ProviderError("empty_response")
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
            raise ProviderError("stream_error") from None


class PreparedReply(Decision):
    thought: str = Field(max_length=40)
    interest: str = Field(max_length=30)
    memory: str = Field(default="", max_length=40)
    utterance: str = Field(max_length=140)


class OllamaLLM(CompatibleLLM):
    """Loopback inference only. No cloud fallback, API keys or remote quotas."""

    def __init__(self, settings, gate):
        host = urlparse(settings.llm_base_url)
        if host.scheme != "http" or host.hostname not in ("127.0.0.1", "localhost", "::1") or host.username or host.password:
            raise ValueError("Ollama must use a loopback HTTP address")
        if "cloud" in settings.llm_model.lower() or "/" in settings.llm_model:
            raise ValueError("Use a downloaded local model, not a cloud model")
        self.settings, self.gate = settings, gate
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(120), trust_env=False, follow_redirects=False)
        self.prepared = {}

    def headers(self):
        return {}

    def payload(self, messages, stream=False, decision=False):
        body = {"model": self.settings.llm_model, "messages": messages, "stream": stream,
                "think": False, "keep_alive": "30m",
                "options": {"num_ctx": self.settings.ollama_context_length,
                            "num_predict": self.settings.max_output_tokens, "temperature": 0.65}}
        if decision:
            body["format"] = (PreparedReply if self.settings.ollama_fast_mode else Decision).model_json_schema()
        if self.settings.ollama_gpu_layers >= 0:
            body["options"]["num_gpu"] = self.settings.ollama_gpu_layers
        return body

    def endpoint(self):
        return self.settings.llm_base_url.rstrip("/") + "/api/chat"

    async def decide(self, agent, view, session):
        epoch = session.epoch
        try:
            async with self.gate.call(session):
                # A GPU queue must not make every participant judge old context.
                view.update(session.view(view.get("reflection", 0)))
                view["_observed_revision"] = session.revision
                if self.settings.ollama_fast_mode:
                    messages = local_decision_messages(agent, view)
                else:
                    messages = decision_messages(agent, view)
                response = await self.client.post(self.endpoint(), json=self.payload(messages, decision=True))
                self.check(response)
                result = response.json()
                if result.get("error"):
                    raise ProviderError("local_model_error", 2)
                content = result["message"]["content"]
                if self.settings.ollama_fast_mode:
                    reply = PreparedReply.model_validate_json(content)
                    if re.search(r"[\u3040-\u30ff]", reply.utterance):
                        self.prepared.pop((session.id, agent.id), None)
                        raise ProviderError("unexpected_language", 1)
                    plan = Decision.model_validate(reply.model_dump(exclude={"utterance"}))
                    self.prepared[(session.id, agent.id)] = (epoch, plan, reply.utterance)
                else:
                    plan = Decision.model_validate_json(content)
                session.emit("local_response", agent.id, tokens=result.get("eval_count", 0),
                             seconds=round(result.get("total_duration", 0) / 1e9, 2))
                return plan
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError("local_unavailable" if isinstance(exc, httpx.HTTPError) else "invalid_response", 2) from None

    async def stream(self, agent, view, decision, session):
        if self.settings.ollama_fast_mode:
            cached = self.prepared.pop((session.id, agent.id), None)
            if not cached or cached[0] != session.epoch or cached[1] is not decision:
                raise ProviderError("stale_local_reply", 0.1)
            if not cached[2].strip():
                raise ProviderError("empty_response", 1)
            yield cached[2]
            return
        try:
            async with self.gate.call(session):
                async with self.client.stream("POST", self.endpoint(),
                                              json=self.payload(speech_messages(agent, view, decision), stream=True)) as response:
                    self.check(response)
                    received = False
                    finished = False
                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        item = json.loads(line)
                        if item.get("error"):
                            raise ProviderError("local_model_error", 2)
                        chunk = item.get("message", {}).get("content", "")
                        if chunk:
                            received = True
                            yield chunk
                        if item.get("done"):
                            finished = True
                            break
                    if not received or not finished:
                        raise ProviderError("incomplete_local_response", 2)
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
            raise ProviderError("local_stream_error", 2) from None

    async def close(self):
        self.prepared.clear()
        await super().close()


def make_provider(settings, gate):
    return {"gemini": GeminiLLM, "compatible": CompatibleLLM, "ollama": OllamaLLM}[settings.llm_provider](settings, gate)


class MockLLM:
    """Deterministic content/state approximation, never a claim of real AI."""
    def __init__(self, settings):
        self.settings = settings

    async def decide(self, agent, view, session):
        if self.settings.session_max_calls and session.calls >= self.settings.session_max_calls:
            raise BudgetExceeded()
        session.calls += 1
        await asyncio.sleep(0)
        messages = view["recent"]
        count = view["message_count"]
        latest = messages[-1] if messages else None
        token = f'{view["topic"]}:{latest["text"] if latest else ""}:{agent.id}:{view["reflection"]}'
        value = int(hashlib.sha256(token.encode()).hexdigest()[:6], 16) / 0xFFFFFF
        user = latest and latest["speaker"] == "user"
        desire = 0.53 + 0.44 * value
        if user:
            desire = min(1, desire + 0.17)
        if latest and latest["speaker"] == agent.id:
            desire *= 0.55
        kind = "recall" if count > 7 and value > 0.55 else "association"
        if user:
            thought = f'방금 네가 말한 “{latest["text"][:70]}”, {agent.seed_interest}이랑도 연결될까?'
            kind = "reaction"
        elif kind == "recall":
            thought = f'아까 {view["topic"]} 얘기로 돌아가면, {agent.memory[-1] if agent.memory else "처음 떠올린 장면"}이 다시 생각나.'
        elif latest:
            thought = f'“{latest["text"][:30]}”에서 갑자기 {agent.seed_interest} 쪽으로 생각이 샜어. 작은 실험 하나로 볼 수도 있나?'
        else:
            thought = f'{view["topic"]}라니, 나는 {agent.seed_interest}부터 떠올랐어. 막상 어디서 시작하면 재밌을까?'
        return Decision(desire=desire, relevance=0.8 if user else 0.5 + value / 2,
                        novelty=0.6 + value / 3, interrupt=bool(user and view["active_speaker"]),
                        thought=thought[:180], interest=agent.seed_interest,
                        memory=(latest["text"][:90] if latest else view["topic"][:90]), kind=kind)

    async def stream(self, agent, view, decision, session):
        if self.settings.session_max_calls and session.calls >= self.settings.session_max_calls:
            raise BudgetExceeded()
        session.calls += 1
        text = decision.thought
        for index in range(0, len(text), 4):
            await asyncio.sleep(self.settings.mock_chunk_delay)
            yield text[index:index + 4]

    async def close(self):
        pass
