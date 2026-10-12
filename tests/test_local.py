import asyncio
import json
import time

import httpx
import pytest

from bstom.config import Settings
from bstom.engine import Session
from bstom.llm import CallGate, OllamaLLM, MockLLM, ProviderError, make_provider
from bstom.models import Decision, Message


def local_settings(tmp_path, **overrides):
    return Settings(_env_file=None, llm_provider="ollama", llm_base_url="http://127.0.0.1:11434",
                    llm_model="local-test", data_dir=str(tmp_path), **overrides)


def reply():
    return {"desire": .85, "relevance": .9, "novelty": .7, "interrupt": False,
            "thought": "바삭한 소리", "interest": "감자튀김", "memory": "", "kind": "association",
            "utterance": "갓 튀긴 감자튀김은 냄새부터 반칙인데ㅋㅋ"}


@pytest.mark.asyncio
async def test_local_fast_reply_uses_one_call_and_never_sends_keys(tmp_path):
    settings = local_settings(tmp_path, gemini_api_key="private-gemini", llm_api_key="private-other")
    assert settings.live_available and not settings.active_api_key
    provider = make_provider(settings, CallGate(settings))
    assert isinstance(provider, OllamaLLM)
    assert "num_gpu" not in provider.payload([], decision=True)["options"]
    settings.ollama_gpu_layers = 99
    assert provider.payload([], decision=True)["options"]["num_gpu"] == 99
    await provider.client.aclose()
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.host == "127.0.0.1" and request.url.path == "/api/chat"
        assert "authorization" not in request.headers and "x-goog-api-key" not in request.headers
        body = json.loads(request.content)
        assert body["think"] is False and body["options"]["num_ctx"] == 2048
        assert "utterance" in body["format"]["properties"]
        assert not body["stream"]
        return httpx.Response(200, json={"message": {"content": json.dumps(reply(), ensure_ascii=False)}, "done": True})

    provider.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    session = Session("음식", provider, settings)
    plan = await provider.decide(session.agents[0], session.view(), session)
    chunks = [text async for text in provider.stream(session.agents[0], session.view(), plan, session)]
    assert chunks == [reply()["utterance"]] and session.calls == 1 and len(requests) == 1
    assert plan.thought != chunks[0] and not provider.prepared
    await provider.close()


@pytest.mark.asyncio
async def test_local_cache_isolates_sessions_and_discards_pre_user_reply(tmp_path):
    settings = local_settings(tmp_path)
    provider = make_provider(settings, CallGate(settings))
    await provider.client.aclose()
    provider.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"message": {"content": json.dumps(reply())}})))
    a, b = Session("a", provider, settings), Session("b", provider, settings)
    plan_a = await provider.decide(a.agents[0], a.view(), a)
    plan_b = await provider.decide(b.agents[0], b.view(), b)
    a.status = "running"
    await a.user_message("지금은 다른 생각이야")
    with pytest.raises(ProviderError, match="stale_local_reply"):
        async for _ in provider.stream(a.agents[0], a.view(), plan_a, a):
            pass
    assert [text async for text in provider.stream(b.agents[0], b.view(), plan_b, b)] == [reply()["utterance"]]
    await a.end()
    await provider.close()


@pytest.mark.asyncio
async def test_local_ndjson_ignores_thinking_and_rejects_incomplete_stream(tmp_path):
    settings = local_settings(tmp_path, ollama_fast_mode=False)
    provider = make_provider(settings, CallGate(settings))
    await provider.client.aclose()
    complete = [True]

    def handler(request):
        body = json.loads(request.content)
        if not body["stream"]:
            plan = Decision.model_validate(reply())
            return httpx.Response(200, json={"message": {"content": plan.model_dump_json()}})
        items = [{"message": {"thinking": "private", "content": "한마디"}, "done": False}]
        if complete[0]:
            items.append({"message": {"content": " 더"}, "done": True})
        return httpx.Response(200, text="\n".join(json.dumps(item) for item in items))

    provider.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    session = Session("a", provider, settings)
    plan = await provider.decide(session.agents[0], session.view(), session)
    assert [text async for text in provider.stream(session.agents[0], session.view(), plan, session)] == ["한마디", " 더"]
    complete[0] = False
    with pytest.raises(ProviderError, match="incomplete_local_response"):
        async for _ in provider.stream(session.agents[0], session.view(), plan, session):
            pass
    await provider.close()


def test_local_mode_rejects_remote_endpoints_and_cloud_models(tmp_path):
    for address, model in [("https://ollama.com", "test"), ("http://192.168.1.2:11434", "test"), ("http://127.0.0.1:11434", "qwen:cloud")]:
        settings = Settings(_env_file=None, llm_provider="ollama", llm_base_url=address, llm_model=model, data_dir=str(tmp_path))
        with pytest.raises(ValueError):
            make_provider(settings, CallGate(settings))


@pytest.mark.asyncio
async def test_zero_message_and_time_limits_keep_running(tmp_path):
    settings = local_settings(tmp_path, session_max_calls=0, session_max_messages=0, session_max_seconds=0,
                              mock_chunk_delay=.001, turn_pause_seconds=0)
    session = Session("무제한", MockLLM(settings), settings)
    session.messages = [Message("user", "나", "이전 얘기", finished=True) for _ in range(40)]
    session.started = time.monotonic() - 4000
    await session.start()
    try:
        async with asyncio.timeout(3):
            while len(session.messages) == 40:
                await asyncio.sleep(.01)
        assert session.status == "running" and session.calls > 0
    finally:
        await session.end()


def test_desktop_bridge_delivers_controls_and_shuts_down_cleanly(tmp_path, monkeypatch):
    from bstom import desktop
    settings = local_settings(tmp_path, mock_chunk_delay=.001, turn_pause_seconds=0)
    original_client = httpx.AsyncClient
    monkeypatch.setattr(desktop.httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"models": [{"name": "local-test"}]}))))
    monkeypatch.setattr(desktop, "make_provider", lambda cfg, gate: MockLLM(cfg))
    bridge = desktop.DesktopEngine(settings)
    try:
        bridge.submit(bridge.start("친구", "local-test")).result(timeout=3)
        bridge.submit(bridge.command("message", "내 생각도 있어")).result(timeout=3)
        bridge.submit(bridge.command("pause")).result(timeout=3)
        assert bridge.session.status == "paused"
        assert any(m.speaker == "user" for m in bridge.session.messages)
        assert not any(a.typing for a in bridge.session.agents)
        bridge.submit(bridge.command("pause")).result(timeout=3)
        assert bridge.session.status == "running"
        bridge.submit(bridge.command("pause")).result(timeout=3)
        assert bridge.session.status == "paused"
    finally:
        bridge.shutdown().result(timeout=3)
        bridge.thread.join(timeout=3)
    assert not bridge.thread.is_alive()
    assert bridge.session.status == "ended" and bridge.session.history_path.exists()
