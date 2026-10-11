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
                       "mock_chunk_delay": 0.001, "session_max_calls": 180, **kwargs})


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
        assert sum(a.state == State.SPEAKING for a in session.agents) == 1
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
async def test_silence_and_resume_from_actual_decision(tmp_path):
    provider = Controlled(silent=True)
    session = Session("정적", provider, config(tmp_path))
    await session.start()
    try:
        await until(lambda: session.silent)
        assert not session.messages
        provider.silent = False
        await until(lambda: len(session.messages) > 0)
        assert any(e["type"] == "silence_ended" for e in session.events)
        assert any(v["reflection"] > 0 for _, v in provider.views)
    finally:
        await session.end()


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
    assert session.messages[0].interrupted
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
async def test_slow_subscriber_has_reconnect_signal(tmp_path):
    session = Session("slow", Controlled(), config(tmp_path))
    queue = asyncio.Queue(maxsize=1)
    session.subscribers.add(queue)
    session.emit("first")
    session.emit("second")
    assert await queue.get() is None
    assert queue not in session.subscribers
