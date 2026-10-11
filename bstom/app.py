import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import Settings
from .engine import Session
from .llm import CallGate, CompatibleLLM, MockLLM

settings = Settings()
gate = CallGate(settings)
mock = MockLLM(settings)
live = CompatibleLLM(settings, gate)
sessions: dict[str, Session] = {}
STATIC = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app):
    yield
    await asyncio.gather(*(session.end("server_shutdown") for session in sessions.values()))
    await live.close()


app = FastAPI(title="Bstom Conversation Engine", lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def same_origin(origin, host):
    if not origin:
        return True  # local CLI clients have no Origin header
    parsed = urlparse(origin)
    return parsed.scheme in ("http", "https") and parsed.netloc == host


@app.middleware("http")
async def origin_guard(request: Request, call_next):
    if request.method not in ("GET", "HEAD", "OPTIONS") and not same_origin(request.headers.get("origin"), request.headers.get("host")):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "origin_denied"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = "default-src 'self'; connect-src 'self' ws:; style-src 'self'; script-src 'self'; frame-ancestors 'none'"
    return response


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/config")
async def config():
    return {"name": "Bstom", "default_mode": settings.bstom_mode,
            "live_available": bool(settings.llm_api_key), "model": settings.llm_model,
            "limits": {"calls": settings.session_max_calls, "messages": settings.session_max_messages,
                       "seconds": settings.session_max_seconds, "rpm": settings.api_requests_per_minute}}


class NewSession(BaseModel):
    topic: str = Field(min_length=1, max_length=500)
    mode: Literal["mock", "compatible"] = "mock"


class UserInput(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


def get_session(session_id):
    if session_id not in sessions:
        raise HTTPException(404, "session_not_found")
    return sessions[session_id]


@app.post("/api/sessions", status_code=201)
async def create_session(body: NewSession):
    if not body.topic.strip():
        raise HTTPException(422, "topic_required")
    if sum(session.status != "ended" for session in sessions.values()) >= 4:
        raise HTTPException(429, "active_session_limit")
    if body.mode == "compatible" and not settings.llm_api_key:
        raise HTTPException(400, "api_key_not_configured")
    # Keep only bounded ended sessions in memory; their histories remain on disk.
    for key in list(sessions):
        if len(sessions) < 20:
            break
        if sessions[key].status == "ended":
            del sessions[key]
    session = Session(body.topic.strip(), live if body.mode == "compatible" else mock, settings, body.mode)
    sessions[session.id] = session
    await session.start()
    return session.snapshot()


@app.get("/api/sessions")
async def list_sessions():
    items = {key: {"id": key, "topic": value.topic, "status": value.status, "mode": value.mode}
             for key, value in sessions.items()}
    for path in Path(settings.data_dir).glob("*.json"):
        if path.stem in items:
            continue
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            items[path.stem] = {"id": saved["id"], "topic": saved["topic"], "status": "archived", "mode": saved["mode"]}
        except (ValueError, KeyError, OSError):
            continue
    return list(items.values())


@app.get("/api/sessions/{session_id}")
async def history(session_id: str):
    if session_id in sessions:
        return sessions[session_id].snapshot()
    # UUID validation avoids path traversal for archived files.
    from uuid import UUID
    try:
        UUID(session_id)
    except ValueError:
        raise HTTPException(404, "session_not_found")
    path = Path(settings.data_dir) / f"{session_id}.json"
    if not path.exists():
        raise HTTPException(404, "session_not_found")
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved["status"] = "archived"
    return saved


@app.post("/api/sessions/{session_id}/message")
async def user_message(session_id: str, body: UserInput):
    if not body.text.strip():
        raise HTTPException(422, "message_required")
    try:
        await get_session(session_id).user_message(body.text.strip())
    except ValueError:
        raise HTTPException(409, "session_ended")
    return {"ok": True}


@app.post("/api/sessions/{session_id}/{action}")
async def action(session_id: str, action: Literal["pause", "resume", "end"]):
    session = get_session(session_id)
    await getattr(session, action)()
    return session.snapshot()


@app.websocket("/ws/{session_id}")
async def websocket(websocket: WebSocket, session_id: str):
    if not same_origin(websocket.headers.get("origin"), websocket.headers.get("host")):
        await websocket.close(code=1008)
        return
    session = sessions.get(session_id)
    if not session:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    queue = asyncio.Queue(maxsize=256)
    session.subscribers.add(queue)
    # Subscribe before snapshot; the UI ignores seq <= snapshot.seq.
    await websocket.send_json({"type": "snapshot", "session_id": session.id, "agent_id": None,
                               "timestamp": __import__("time").time(), "seq": session.seq, "data": session.snapshot()})

    async def send():
        while True:
            event = await queue.get()
            if event is None:
                await websocket.close(code=1013)
                return
            await websocket.send_json(event)

    async def receive():
        # Commands use REST; receive watches disconnects, even during silence.
        while True:
            await websocket.receive_text()

    sender, receiver = asyncio.create_task(send()), asyncio.create_task(receive())
    try:
        done, pending = await asyncio.wait([sender, receiver], return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*done, *pending, return_exceptions=True)
    except WebSocketDisconnect:
        pass
    finally:
        session.subscribers.discard(queue)
        sender.cancel()
        receiver.cancel()
        await asyncio.gather(sender, receiver, return_exceptions=True)
