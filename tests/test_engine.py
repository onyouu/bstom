import asyncio
import time

import pytest

from bstom.config import Settings
from bstom.engine import Session
from bstom.llm import CallGate, MockLLM, ProviderError
from bstom.models import Decision, Request, State


def config(tmp_path, **kwargs):
    return Settings(_env_file=None, data_dir=str(tmp_path), bstom_mode="mock", llm_api_key="",
                    **{"reevaluate_seconds": 0.12, "speech_cooldown": 0.05,
                       "mock_chunk_delay": 0.001, "session_max_calls": 180,
                       "speech_chars_per_second": 0, "turn_pause_seconds": 0, **kwargs})


async def until(predicate, timeout=4):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


def decision(desire=0.8, interrupt=False, kind="association"):
    return Decision(desire=desire, relevance=0.9, novelty=0.8, interrupt=interrupt,
                    thought="이 생각은 아까 했던 작은 실험에서 이어진 거야.", interest="실험", kind=kind)


class Controlled:
    def __init__(self, silent=False, interrupt=False, fail=False):
        self.silent, self.interrupt, self.fail = silent, interrupt, fail
        self.views = []
        self.open_streams = 0

    async def decide(self, agent, view, session):
        self.views.append((agent.id, view))
        if self.fail and agent.id == "naru":
            raise ProviderError("test_network", 0.05)
        desire = 0.1 if self.silent else (0.97 if self.interrupt and agent.id == "mio" else 0.8)
        return decision(desire, self.interrupt and agent.id == "mio" and bool(view["active_speaker"]))

    async def stream(self, agent, view, plan, session):
        self.open_streams += 1
        try:
            for _ in range(60):
                await asyncio.sleep(0.01)
                yield "작은 생각 "
        finally:
            self.open_streams -= 1


@pytest.mark.asyncio
async def test_autonomy_order_drift_and_recall(tmp_path):
    cfg = config(tmp_path)
    session = Session("생명과학", MockLLM(cfg), cfg)
    await session.start()
    try:
        await until(lambda: len([m for m in session.messages if m.finished]) >= 13)
        messages = [m for m in session.messages if m.finished]
        order = [m.speaker for m in messages]
        assert len(set(order)) == 3
        assert any(order[i] != order[i - 3] for i in range(3, len(order)))
        assert any(m.kind == "association" for m in messages)
        assert any(m.kind == "recall" and "생명과학" in m.text for m in messages)
        assert all(agent.memory for agent in session.agents)
    finally:
        await session.end()


@pytest.mark.asyncio
async def test_collision_selects_one_speaker(tmp_path):
    provider = Controlled()
    session = Session("충돌", provider, config(tmp_path))
    await session.start()
    try:
        await until(lambda: any(e["type"] == "arbitration" for e in session.events))
        arbitration = next(e for e in session.events if e["type"] == "arbitration")
        assert len(arbitration["data"]["candidates"]) == 3
        await until(lambda: sum(a.state == State.SPEAKING for a in session.agents) == 1)
        await until(lambda: provider.open_streams == 1)
        assert provider.open_streams == 1
    finally:
        await session.end()
    assert provider.open_streams == 0


@pytest.mark.asyncio
async def test_user_interrupt_preserves_partial_and_priority(tmp_path):
    provider = Controlled()
    session = Session("테이블", provider, config(tmp_path))
    await session.start()
    try:
        await until(lambda: session.active_message and len(session.active_message.text) > 10)
        partial = session.active_message
        await session.user_message("농담인데 식물이 출석 체크하면 어떨까")
        assert partial.interrupted and partial.finished and partial.text
        assert session.messages[-1].speaker == "user"
        events = list(session.events)
        cut = next(i for i, e in enumerate(events) if e["type"] == "agent_interrupted")
        user = next(i for i, e in enumerate(events) if e["type"] == "user_spoke")
        assert cut < user
        assert not any(e["type"] == "speech_chunk" and e["data"]["message_id"] == partial.id for e in events[user:])
        await until(lambda: any(v["recent"] and v["recent"][-1]["speaker"] == "user" for _, v in provider.views))
    finally:
        await session.end()


@pytest.mark.asyncio
async def test_low_desire_keeps_reevaluating_without_silence_mode(tmp_path):
    provider = Controlled(silent=True)
    session = Session("생각", provider, config(tmp_path))
    await session.start()
    try:
        await until(lambda: any(v["reflection"] > 0 for _, v in provider.views))
        assert not session.messages
        assert session.status == "running"
        assert "silence" not in session.view() and "silent" not in session.snapshot()
        provider.silent = False
        await until(lambda: len(session.messages) > 0)
        assert not any(e["type"].startswith("silence_") for e in session.events)
    finally:
        await session.end()


@pytest.mark.asyncio
async def test_mock_has_no_periodic_forced_low_desire(tmp_path):
    cfg = config(tmp_path)
    provider = MockLLM(cfg)
    session = Session("아이디어", provider, cfg)
    for count in (6, 12, 18):
        view = {**session.view(), "message_count": count}
        plans = [await provider.decide(agent, view, session) for agent in session.agents]
        assert all(plan.desire >= 0.5 for plan in plans)


@pytest.mark.asyncio
async def test_agent_interrupt(tmp_path):
    provider = Controlled(interrupt=True)
    session = Session("끼어들기", provider, config(tmp_path, speech_cooldown=0.15))
    await session.start()
    try:
        await until(lambda: any(e["type"] == "agent_interrupted" and e["data"]["reason"].startswith("agent:") for e in session.events))
        assert any(m.interrupted and m.text for m in session.messages)
    finally:
        await session.end()


@pytest.mark.asyncio
async def test_pause_resume_and_end_cancel_all_work(tmp_path):
    provider = Controlled()
    session = Session("제어", provider, config(tmp_path))
    await session.start()
    await until(lambda: session.active_message and session.active_message.text)
    await session.pause()
    seq = session.seq
    assert not session.tasks and provider.open_streams == 0
    await asyncio.sleep(0.2)
    assert session.seq == seq
    await session.user_message("쉬면서 생각해봤는데")
    await session.resume()
    await until(lambda: session.active_agent is not None)
    await session.end()
    assert session.status == "ended" and not session.tasks
    assert all(a.state == State.IDLE for a in session.agents)
    assert session.history_path.exists()


@pytest.mark.asyncio
async def test_failure_isolated_to_one_agent(tmp_path):
    session = Session("네트워크", Controlled(fail=True), config(tmp_path))
    await session.start()
    try:
        await until(lambda: any(m.finished and m.text for m in session.messages))
        assert any(e["type"] == "provider_error" for e in session.events)
        assert all(m.speaker != "naru" for m in session.messages)
        assert session.status == "running"
    finally:
        await session.end()


@pytest.mark.asyncio
async def test_budget_and_length_bounds(tmp_path):
    cfg = config(tmp_path, session_max_calls=12, max_speech_chars=35)
    session = Session("한도", MockLLM(cfg), cfg)
    await session.start()
    await until(lambda: session.status == "ended")
    assert session.calls <= 12
    assert all(len(m.text) <= 35 for m in session.messages)


@pytest.mark.asyncio
async def test_stale_requests_invalidated_and_prestart_cancellation(tmp_path):
    session = Session("race", Controlled(), config(tmp_path))
    session.status = "running"
    agent = session.agents[0]
    request = Request(agent, decision(), session.revision, session.epoch)
    session.begin_speech(request)
    await session.user_message("방금 생각이 바뀌었어")
    assert len(session.messages) == 1 and session.messages[0].speaker == "user"
    assert any(e["type"] == "agent_interrupted" for e in session.events)
    assert session.active_agent is None
    session.requests[agent.id] = request
    session.discard_stale()
    assert not session.requests
    await session.end()


@pytest.mark.asyncio
async def test_global_concurrency_and_cancelled_queue(tmp_path):
    cfg = config(tmp_path, api_max_concurrency=1)
    gate = CallGate(cfg)
    a, b = Session("a", Controlled(), cfg), Session("b", Controlled(), cfg)
    acquired = asyncio.Event()
    release = asyncio.Event()

    async def first():
        async with gate.call(a):
            acquired.set()
            await release.wait()

    async def second():
        async with gate.call(b):
            raise AssertionError("queued call should have been cancelled")

    task = asyncio.create_task(first())
    await acquired.wait()
    queued = asyncio.create_task(second())
    await asyncio.sleep(0.01)
    queued.cancel()
    await asyncio.gather(queued, return_exceptions=True)
    assert a.calls == 1 and b.calls == 0
    release.set()
    await task


@pytest.mark.asyncio
async def test_unlimited_calls_skip_proactive_pacing_and_budget(tmp_path):
    cfg = config(tmp_path, session_max_calls=0)
    gate = CallGate(cfg)
    session = Session("바로 호출", Controlled(), cfg)
    session.calls = 1000
    now = time.monotonic()
    gate.starts.extend([now] * 100)
    gate.tokens.append([now, 1000000])
    async with asyncio.timeout(0.5):
        async with gate.call(session, 1000000):
            pass
    assert session.calls == 1001
    assert not any(e["type"] == "api_wait" for e in session.events)
    # Unlimited session calls must not immediately end the orchestrator or Mock.
    mock = MockLLM(cfg)
    await mock.decide(session.agents[0], session.view(), session)
    await session.start()
    try:
        await until(lambda: session.active_agent is not None)
        assert session.status == "running"
    finally:
        await session.end()


@pytest.mark.asyncio
async def test_optional_request_pacing_can_still_be_enabled(tmp_path):
    cfg = config(tmp_path, api_requests_per_minute=1)
    gate = CallGate(cfg)
    session = Session("선택한 제한", Controlled(), cfg)
    async with gate.call(session):
        pass

    async def queued_call():
        async with gate.call(session):
            raise AssertionError("request should still be queued")

    queued = asyncio.create_task(queued_call())
    await until(lambda: any(e["type"] == "api_wait" for e in session.events))
    assert session.calls == 1 and not queued.done()
    queued.cancel()
    await asyncio.gather(queued, return_exceptions=True)


@pytest.mark.asyncio
async def test_slow_subscriber_has_reconnect_signal(tmp_path):
    session = Session("slow", Controlled(), config(tmp_path))
    queue = asyncio.Queue(maxsize=1)
    session.subscribers.add(queue)
    session.emit("first")
    session.emit("second")
    assert await queue.get() is None
    assert queue not in session.subscribers


class Burst(Controlled):
    def __init__(self, error=False):
        super().__init__()
        self.generated = asyncio.Event()
        self.error = error
        self.text = "첫 생각은 이거야, 아직 화면에 안 나온 이 뒷말은 비밀이지."

    async def stream(self, agent, view, plan, session):
        self.open_streams += 1
        try:
            yield self.text
            if self.error:
                raise ProviderError("test_broken_stream", 0.01)
        finally:
            self.open_streams -= 1
            self.generated.set()


@pytest.mark.asyncio
async def test_buffered_generation_and_user_cut_discard_unseen_text(tmp_path):
    provider = Burst()
    session = Session("읽는 속도", provider, config(tmp_path, speech_chars_per_second=30))
    session.status = "running"
    session.begin_speech(Request(session.agents[0], decision(), 0, 0))
    try:
        await until(lambda: session.active_message and session.active_message.text)
        assert provider.generated.is_set() and provider.open_streams == 0
        message = session.active_message
        visible = message.text
        assert 0 < len(visible) < len(provider.text)
        assert session.view()["recent"][-1]["text"] == visible
        await session.user_message("잠깐, 다른 생각이 있어")
        assert message.interrupted and message.text == visible
        await asyncio.sleep(0.15)
        assert message.text == visible
        assert session.messages[-1].speaker == "user"
        assert not any(t.get_name().startswith("speech-buffer:") and not t.done() for t in asyncio.all_tasks())
    finally:
        await session.end()


@pytest.mark.asyncio
async def test_turn_breath_generates_ahead_without_empty_transcript(tmp_path):
    provider = Burst()
    session = Session("발언 사이 호흡", provider, config(tmp_path, turn_pause_seconds=0.15))
    session.status = "running"
    session.last_finished_at = time.monotonic()
    session.begin_speech(Request(session.agents[0], decision(), 0, 0))
    try:
        await until(provider.generated.is_set)
        assert not session.messages and session.active_agent.state == State.THINKING
        await until(lambda: session.active_message is None)
        speaking = next(e for e in session.events if e["type"] == "agent_speaking")
        assert speaking["timestamp"] - list(session.events)[0]["timestamp"] >= 0.12
        assert session.messages[0].text == provider.text
    finally:
        await session.end()


@pytest.mark.asyncio
async def test_buffered_error_preserves_visible_prefix_and_closes_producer(tmp_path):
    provider = Burst(error=True)
    session = Session("생성 오류", provider, config(tmp_path))
    session.status = "running"
    session.begin_speech(Request(session.agents[0], decision(), 0, 0))
    await until(lambda: session.active_message is None)
    assert session.messages[0].text == provider.text and session.messages[0].interrupted
    assert any(e["type"] == "provider_error" and e["data"]["code"] == "test_broken_stream" for e in session.events)
    assert provider.open_streams == 0
    await session.end()
