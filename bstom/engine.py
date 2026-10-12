import asyncio
import json
import time
from collections import deque
from contextlib import aclosing
from pathlib import Path
from uuid import uuid4

from .llm import BudgetExceeded, ProviderError
from .models import Agent, Message, Request, State


class Session:
    def __init__(self, topic, provider, settings, mode="mock"):
        self.id = str(uuid4())
        self.topic, self.provider, self.settings, self.mode = topic, provider, settings, mode
        self.agents = [Agent("naru", "나루", "일상 속 이상한 장면"),
                       Agent("sol", "솔", "직접 만지고 해보는 작은 실험"),
                       Agent("mio", "미오", "사람들이 느끼는 감정과 엉뚱한 비유")]
        self.messages = []
        self.events = deque(maxlen=2000)
        self.subscribers = set()
        self.seq = self.revision = self.epoch = self.calls = 0
        self.status = "created"
        self.requests = {}
        self.tasks = []
        self.speech_task = None
        self.active_message = None
        self.active_agent = None
        self.interrupt_reason = ""
        self.interruption_chain = 0
        self.control = asyncio.Lock()
        self.started = time.monotonic()
        directory = Path(settings.data_dir)
        directory.mkdir(parents=True, exist_ok=True)
        self.log_path = directory / f"{self.id}.jsonl"
        self.history_path = directory / f"{self.id}.json"

    def emit(self, event_type, agent_id=None, **data):
        self.seq += 1
        event = {"type": event_type, "session_id": self.id, "agent_id": agent_id,
                 "timestamp": time.time(), "seq": self.seq, "calls": self.calls, "data": data}
        self.events.append(event)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        for queue in list(self.subscribers):
            if queue.full():
                self.subscribers.remove(queue)
                # Receiver closes and reconnects from a complete snapshot.
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(None)
            else:
                queue.put_nowait(event)
        return event

    def snapshot(self):
        return {"id": self.id, "topic": self.topic, "mode": self.mode, "status": self.status,
                "seq": self.seq, "calls": self.calls,
                "call_limit": self.settings.session_max_calls,
                "agents": [agent.public() for agent in self.agents],
                "messages": [message.public() for message in self.messages],
                "events": list(self.events)[-100:]}

    def save(self):
        # Persist on each completed/interrupted/user message and every control action.
        self.history_path.write_text(json.dumps(self.snapshot(), ensure_ascii=False, indent=2), encoding="utf-8")

    def view(self, reflection=0):
        return {"topic": self.topic, "recent": [m.public() for m in self.messages[-7:]],
                "early": [m.public() for m in self.messages[:2]],
                "message_count": len(self.messages),
                "active_speaker": self.active_agent.id if self.active_agent else None,
                "reflection": reflection}

    def state(self, agent, state, event=None, **data):
        agent.state = state
        self.emit(event or "agent_state", agent.id, state=state, **data)

    def changed(self):
        self.revision += 1

    def start_loops(self):
        self.tasks = [asyncio.create_task(self.agent_loop(agent), name=f"{self.id}:{agent.id}") for agent in self.agents]
        self.tasks.append(asyncio.create_task(self.arbitrate(), name=f"{self.id}:orchestrator"))

    async def start(self):
        self.status = "running"
        self.emit("session_started", topic=self.topic, mode=self.mode)
        self.start_loops()
        self.save()

    async def agent_loop(self, agent):
        reflection = 0
        last_revision = -1
        while self.status == "running":
            if self.active_agent is agent or agent.id in self.requests:
                await asyncio.sleep(0.05)
                continue
            if time.monotonic() - agent.last_spoke < self.settings.speech_cooldown:
                await asyncio.sleep(0.05)
                continue
            if self.revision == last_revision:
                deadline = time.monotonic() + self.settings.reevaluate_seconds
                while self.revision == last_revision and time.monotonic() < deadline and self.status == "running":
                    await asyncio.sleep(min(0.05, self.settings.reevaluate_seconds))
                if self.revision == last_revision:
                    reflection += 1
                else:
                    reflection = 0
            else:
                reflection = 0
            if self.status != "running":
                return
            last_revision = self.revision
            epoch = self.epoch
            view = self.view(reflection)
            self.state(agent, State.THINKING, "agent_thinking", reflection=reflection)
            try:
                decision = await self.provider.decide(agent, view, self)
            except BudgetExceeded:
                self.state(agent, State.IDLE)
                self.emit("budget_exhausted", agent.id)
                return
            except ProviderError as error:
                self.state(agent, State.LISTENING)
                self.emit("provider_error", agent.id, code=error.code, retry_after=error.retry_after)
                await asyncio.sleep(error.retry_after)
                continue
            except asyncio.CancelledError:
                raise
            except Exception:
                self.state(agent, State.LISTENING)
                self.emit("provider_error", agent.id, code="decision_failed")
                await asyncio.sleep(self.settings.reevaluate_seconds)
                continue
            if self.status != "running" or epoch != self.epoch:
                self.state(agent, State.LISTENING)
                continue
            # A decision about much older context is discarded and re-observed.
            if self.revision - last_revision > 2:
                self.state(agent, State.LISTENING)
                continue
            agent.reviews += 1
            agent.observed_revision = last_revision
            agent.interest, agent.desire = decision.interest, decision.desire
            if decision.memory and decision.memory not in agent.memory:
                agent.memory.append(decision.memory)
                agent.memory = agent.memory[-6:]
            if decision.desire >= 0.5 and decision.relevance >= 0.3 and decision.thought:
                agent.pending = decision
                self.requests[agent.id] = Request(agent, decision, last_revision, epoch)
                self.state(agent, State.WANTS_TO_SPEAK, "agent_wants_to_speak", decision=decision.model_dump())
            else:
                agent.pending = None
                self.state(agent, State.LISTENING, desire=decision.desire, interest=decision.interest)

    def score(self, request):
        recent = [m.speaker for m in self.messages[-6:] if m.speaker != "user"]
        decision = request.decision
        repeat = 0.24 if recent and recent[-1] == request.agent.id else 0
        frequency = recent.count(request.agent.id) * 0.10
        freshness = min(0.25, max(0, self.revision - request.revision) * 0.08)
        return decision.desire * 0.5 + decision.relevance * 0.3 + decision.novelty * 0.2 - repeat - frequency - freshness

    def discard_stale(self):
        for key, request in list(self.requests.items()):
            if request.epoch != self.epoch or self.revision - request.revision > 2:
                self.requests.pop(key)
                request.agent.pending = None
                self.state(request.agent, State.LISTENING)

    async def arbitrate(self):
        while self.status == "running":
            await asyncio.sleep(0.06)  # arbitration window for near-simultaneous requests
            if self.calls >= self.settings.session_max_calls and not self.active_agent:
                asyncio.create_task(self.end("call_budget"))
                return
            if len(self.messages) >= self.settings.session_max_messages and not self.active_agent:
                asyncio.create_task(self.end("message_budget"))
                return
            if time.monotonic() - self.started >= self.settings.session_max_seconds:
                asyncio.create_task(self.end("time_budget"))
                return
            self.discard_stale()
            candidates = sorted(self.requests.values(), key=lambda request: (-self.score(request), request.created))
            if self.active_agent:
                interrupters = [r for r in candidates if r.decision.interrupt and r.decision.desire >= 0.88
                                and r.decision.relevance >= 0.75 and self.score(r) >= 0.55]
                if interrupters and self.active_message and len(self.active_message.text) >= 25 and self.interruption_chain < 2:
                    async with self.control:
                        if self.status == "running" and self.active_agent:
                            self.interruption_chain += 1
                            await self.cancel_speech("agent:" + interrupters[0].agent.id)
                            # Interrupted partial content changed the context; regenerate speech from it.
                            self.begin_speech(interrupters[0])
                continue
            if candidates:
                async with self.control:
                    if self.status != "running":
                        continue
                    chosen = candidates[0]
                    if self.requests.get(chosen.agent.id) is not chosen or chosen.epoch != self.epoch:
                        continue
                    self.emit("arbitration", chosen.agent.id,
                              candidates=[{"agent_id": r.agent.id, "score": round(self.score(r), 3)} for r in candidates])
                    self.begin_speech(chosen)

    def begin_speech(self, request):
        if request.epoch != self.epoch or self.status != "running":
            return
        self.requests.pop(request.agent.id, None)
        self.active_agent = request.agent
        self.active_message = Message(request.agent.id, request.agent.name, kind=request.decision.kind)
        self.messages.append(self.active_message)
        self.changed()
        self.state(request.agent, State.SPEAKING, "agent_speaking", message=self.active_message.public())
        self.speech_task = asyncio.create_task(self.speak(request, self.active_message))

    async def speak(self, request, message):
        interrupted = False
        failed = False
        try:
            # A free-tier pacing window can itself take 60 seconds.
            async with asyncio.timeout(120):
                async with aclosing(self.provider.stream(request.agent, self.view(), request.decision, self)) as stream:
                    async for chunk in stream:
                        if self.status != "running":
                            interrupted = True
                            break
                        chunk = chunk[:max(0, self.settings.max_speech_chars - len(message.text))]
                        if not chunk:
                            break
                        message.text += chunk
                        self.emit("speech_chunk", request.agent.id, message_id=message.id, text=chunk)
                        # Once there is substance, independent listeners may request interruption.
                        if len(message.text) >= 40 and len(message.text) - len(chunk) < 40:
                            self.changed()
                        if len(message.text) >= self.settings.max_speech_chars:
                            break
        except asyncio.CancelledError:
            interrupted = True
        except BudgetExceeded:
            failed = True
            self.emit("budget_exhausted", request.agent.id)
        except ProviderError as error:
            failed = True
            self.emit("provider_error", request.agent.id, code=error.code, retry_after=error.retry_after)
        except Exception:
            failed = True
            self.emit("provider_error", request.agent.id, code="speech_failed")
        finally:
            message.interrupted = interrupted or failed
            message.finished = True
            request.agent.last_spoke = time.monotonic()
            request.agent.pending = None
            request.agent.desire = 0
            self.state(request.agent, State.INTERRUPTED if message.interrupted else State.LISTENING,
                       "agent_interrupted" if message.interrupted else "agent_finished",
                       message=message.public(), reason=self.interrupt_reason if interrupted else ("provider_error" if failed else "complete"))
            if not message.interrupted:
                self.interruption_chain = 0
            self.active_agent = self.active_message = None
            self.changed()
            self.save()

    async def cancel_speech(self, reason):
        if self.speech_task and not self.speech_task.done():
            self.interrupt_reason = reason
            self.speech_task.cancel()
            await asyncio.gather(self.speech_task, return_exceptions=True)
        # A task cancelled before its first instruction cannot run its finally block.
        if self.active_message is not None:
            message, agent = self.active_message, self.active_agent
            message.interrupted = message.finished = True
            agent.pending = None
            agent.last_spoke = time.monotonic()
            self.state(agent, State.INTERRUPTED, "agent_interrupted", message=message.public(), reason=reason)
            self.active_agent = self.active_message = None
            self.changed()
            self.save()
        self.speech_task = None

    async def cancel_loops(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks = []
        self.requests.clear()
        for agent in self.agents:
            agent.pending = None

    async def user_message(self, text):
        async with self.control:
            if self.status not in ("running", "paused"):
                raise ValueError("session_ended")
            self.epoch += 1
            # No stale pre-user plan may speak after user participation.
            for request in self.requests.values():
                request.agent.pending = None
                self.state(request.agent, State.LISTENING)
            self.requests.clear()
            await self.cancel_speech("user")
            message = Message("user", "나", text, finished=True)
            self.messages.append(message)
            self.emit("user_spoke", "user", message=message.public())
            self.changed()
            self.save()

    async def pause(self):
        async with self.control:
            if self.status != "running":
                return
            self.interrupt_reason = "pause"
            self.status = "paused"
            self.epoch += 1
            await self.cancel_loops()
            await self.cancel_speech("pause")
            for agent in self.agents:
                self.state(agent, State.IDLE)
            self.emit("session_paused")
            self.save()

    async def resume(self):
        async with self.control:
            if self.status != "paused":
                return
            self.status = "running"
            for agent in self.agents:
                self.state(agent, State.LISTENING)
            self.changed()
            self.emit("session_resumed")
            self.start_loops()
            self.save()

    async def end(self, reason="user"):
        async with self.control:
            if self.status == "ended":
                return
            self.interrupt_reason = reason
            self.status = "ended"
            self.epoch += 1
            await self.cancel_loops()
            await self.cancel_speech("end")
            for agent in self.agents:
                self.state(agent, State.IDLE)
            self.emit("session_ended", reason=reason, calls=self.calls)
            self.save()
