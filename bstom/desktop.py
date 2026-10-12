"""Native Tk window. The conversation engine runs on its own asyncio thread."""
import asyncio
import json
import queue
import threading
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import ttk
except ImportError:
    tk = ttk = None  # Headless engine/CI can still import the desktop bridge.

import httpx

from .config import Settings
from .engine import Session
from .llm import CallGate, make_provider


class DesktopEngine:
    def __init__(self, settings):
        self.settings = settings
        self.events = queue.Queue()
        self.loop = asyncio.new_event_loop()
        self.session = self.provider = self.watcher = None
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

    def submit(self, coroutine):
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)

        def report(result):
            try:
                result.result()
            except Exception as error:
                self.events.put({"type": "desktop_error", "data": {"code": str(error)}})
        future.add_done_callback(report)
        return future

    async def start(self, topic, model):
        if self.session and self.session.status in ("running", "paused"):
            raise ValueError("지금 대화를 마친 뒤 새 테이블을 열어 주세요.")
        if self.provider:
            await self.provider.close()
        settings = self.settings.model_copy(update={"llm_model": model})
        try:
            async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
                response = await client.get(settings.llm_base_url.rstrip("/") + "/api/tags")
                response.raise_for_status()
                installed = [item["name"] for item in response.json()["models"]]
        except (httpx.HTTPError, KeyError, ValueError):
            raise ValueError("Ollama가 연결되지 않았어요. Ollama를 실행한 뒤 다시 열어 주세요.") from None
        if model not in installed:
            raise ValueError(f"모델이 없어요. 먼저 ollama pull {model}로 받아 주세요.")
        self.provider = make_provider(settings, CallGate(settings))
        self.session = Session(topic, self.provider, settings, "compatible")
        events = asyncio.Queue(maxsize=512)
        self.session.subscribers.add(events)
        self.events.put({"type": "desktop_snapshot", "data": self.session.snapshot()})
        self.watcher = asyncio.create_task(self.forward(events, self.session))
        await self.session.start()

    async def forward(self, events, session):
        while True:
            event = await events.get()
            if event is None:
                self.events.put({"type": "desktop_snapshot", "data": session.snapshot()})
                session.subscribers.add(events)
                continue
            self.events.put(event)
            if event["type"] == "session_ended":
                session.subscribers.discard(events)
                return

    async def command(self, action, text=""):
        if not self.session:
            return
        if action == "message":
            await self.session.user_message(text)
        elif action == "pause":
            await (self.session.resume() if self.session.status == "paused" else self.session.pause())
        elif action == "end":
            await self.session.end()

    async def close(self):
        if self.session:
            await self.session.end("desktop_closed")
        if self.watcher:
            self.watcher.cancel()
            await asyncio.gather(self.watcher, return_exceptions=True)
        if self.provider:
            await self.provider.close()

    def shutdown(self):
        future = self.submit(self.close())
        future.add_done_callback(lambda _: self.loop.call_soon_threadsafe(self.loop.stop))
        return future


class DesktopWindow:
    def __init__(self, root, settings, topic="", auto_start=False):
        self.root, self.settings = root, settings
        self.engine = DesktopEngine(settings)
        self.agents = {}
        self.status = "created"
        self.closing = False
        self.tick = 0
        root.title("Bstom · 로컬 대화")
        root.geometry("1050x800")
        root.minsize(760, 600)
        root.configure(bg="#f6f5f0")
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TFrame", background="#f6f5f0")
        style.configure("TLabel", background="#f6f5f0", font=("맑은 고딕", 10))
        style.configure("TButton", font=("맑은 고딕", 10), padding=8)
        outer = ttk.Frame(root, padding=22)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="bstom", font=("맑은 고딕", 26, "bold"), foreground="#225c48").pack(anchor="w")
        ttk.Label(outer, text="이 PC에서 생각하는 세 친구 · API 키와 외부 요청 제한 없이").pack(anchor="w")
        form = ttk.Frame(outer)
        form.pack(fill="x", pady=(16, 12))
        self.topic = tk.StringVar(value=topic)
        self.model = tk.StringVar(value=settings.llm_model)
        ttk.Entry(form, textvariable=self.topic, font=("맑은 고딕", 11)).pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.model_picker = ttk.Combobox(form, textvariable=self.model, width=18, values=["qwen2.5:3b", "qwen3.5:2b", "qwen3.5:4b", "qwen3:4b-instruct-2507-q4_K_M"])
        self.model_picker.pack(side="left", padx=(0, 8))
        self.start_button = ttk.Button(form, text="테이블 열기", command=self.start)
        self.start_button.pack(side="left")
        cards = ttk.Frame(outer)
        cards.pack(fill="x", pady=8)
        self.card_labels = {}
        for agent_id, name in (("naru", "나루"), ("sol", "솔"), ("mio", "미오")):
            label = ttk.Label(cards, text=f"{name} · 기다리는 중", anchor="w", padding=10, relief="solid")
            label.pack(side="left", fill="x", expand=True, padx=4)
            self.card_labels[agent_id] = label
        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True, pady=10)
        transcript_frame = ttk.Frame(body)
        transcript_frame.pack(side="left", fill="both", expand=True)
        self.transcript = tk.Text(transcript_frame, wrap="word", font=("맑은 고딕", 12), bg="white", relief="flat", padx=18, pady=14, state="disabled")
        scroll = ttk.Scrollbar(transcript_frame, command=self.transcript.yview)
        scroll.pack(side="right", fill="y")
        self.transcript.configure(yscrollcommand=scroll.set)
        self.transcript.pack(fill="both", expand=True)
        for name, color in (("나루", "#225c48"), ("솔", "#9e661e"), ("미오", "#715798"), ("나", "#334155")):
            self.transcript.tag_configure(name, foreground=color, font=("맑은 고딕", 10, "bold"))
        side = ttk.Frame(body, padding=(12, 0, 0, 0))
        side.pack(side="right", fill="y")
        ttk.Label(side, text="이전 대화").pack(anchor="w")
        self.history = tk.Listbox(side, width=23, height=8, font=("맑은 고딕", 9), relief="flat")
        self.history.pack(fill="x", pady=5)
        self.history.bind("<Double-1>", self.open_history)
        ttk.Button(side, text="기록 보기", command=self.open_history).pack(fill="x")
        ttk.Label(side, text="엔진 이벤트").pack(anchor="w", pady=(14, 4))
        self.log = tk.Text(side, width=28, height=8, font=("Consolas", 9), state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True)
        self.typing = tk.StringVar()
        ttk.Label(outer, textvariable=self.typing, foreground="#225c48").pack(anchor="w")
        composer = ttk.Frame(outer)
        composer.pack(fill="x", pady=8)
        self.input = tk.Text(composer, height=2, wrap="word", font=("맑은 고딕", 11))
        self.input.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.input.bind("<Return>", self.on_enter)
        self.send_button = ttk.Button(composer, text="끼어들기", command=self.send)
        self.send_button.pack(side="right", fill="y")
        controls = ttk.Frame(outer)
        controls.pack(fill="x")
        self.pause_button = ttk.Button(controls, text="잠깐 쉬기", command=lambda: self.engine.submit(self.engine.command("pause")))
        self.pause_button.pack(side="left", padx=(0, 8))
        self.end_button = ttk.Button(controls, text="대화 마치기", command=lambda: self.engine.submit(self.engine.command("end")))
        self.end_button.pack(side="left")
        self.notice = tk.StringVar(value="주제를 적고 테이블을 열어 주세요. Enter로 보내기 · Shift+Enter로 줄바꿈")
        ttk.Label(outer, textvariable=self.notice, wraplength=950).pack(anchor="w", pady=(8, 0))
        self.refresh_history()
        self.controls()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(50, self.poll)
        root.after(250, self.animate)
        if auto_start and topic:
            root.after(300, self.start)

    def controls(self):
        active = self.status in ("running", "paused")
        self.start_button.configure(state="disabled" if active or self.status == "connecting" else "normal")
        for button in (self.pause_button, self.end_button, self.send_button):
            button.configure(state="normal" if active else "disabled")
        self.pause_button.configure(text="다시 이야기하기" if self.status == "paused" else "잠깐 쉬기")

    def start(self):
        topic = self.topic.get().strip()
        if not topic:
            self.notice.set("넓은 주제 하나만 적어 주세요.")
            return
        self.status = "connecting"
        self.controls()
        self.notice.set("로컬 모델을 연결하는 중이에요. 처음에는 모델을 불러오는 시간이 필요해요.")
        self.engine.submit(self.engine.start(topic, self.model.get().strip()))

    def send(self):
        text = self.input.get("1.0", "end-1c").strip()
        if text and self.status in ("running", "paused"):
            self.engine.submit(self.engine.command("message", text))

    def on_enter(self, event):
        if event.state & 1:
            return
        self.send()
        return "break"

    def append(self, name, text):
        self.transcript.configure(state="normal")
        self.transcript.insert("end", name + "\n", name)
        self.transcript.insert("end", text + "\n\n")
        self.transcript.configure(state="disabled")
        self.transcript.see("end")

    def poll(self):
        for _ in range(150):
            try:
                event = self.engine.events.get_nowait()
            except queue.Empty:
                break
            kind, data = event["type"], event["data"]
            if kind == "desktop_error":
                self.notice.set(data["code"])
                if self.status == "connecting":
                    self.status = "created"
                self.controls()
                continue
            if kind == "desktop_snapshot":
                self.agents = {a["id"]: a for a in data["agents"]}
                self.transcript.configure(state="normal")
                self.transcript.delete("1.0", "end")
                self.transcript.configure(state="disabled")
                for message in data["messages"]:
                    if message["text"]:
                        self.append(message["name"], message["text"])
                self.status = data["status"]
            agent = self.agents.get(event.get("agent_id"))
            if agent and "state" in data:
                agent.update(state=data["state"], typing=data.get("typing", False))
            if kind == "speech_chunk":
                self.append(agent["name"] if agent else "AI", data["text"])
                self.notice.set(f"{self.model.get()} · 이 PC에서 생성 중 · 추가 대기 없음")
            if kind == "user_spoke":
                self.append("나", data["message"]["text"])
                if self.input.get("1.0", "end-1c").strip() == data["message"]["text"]:
                    self.input.delete("1.0", "end")
            transitions = {"session_started": "running", "session_resumed": "running", "session_paused": "paused", "session_ended": "ended"}
            if kind in transitions:
                self.status = transitions[kind]
                self.notice.set({"running": "대화 중 · 떠오르는 말이 있으면 언제든 끼어들어요.", "paused": "잠깐 쉬는 중 · 메시지를 남길 수 있어요.", "ended": "대화 기록을 저장했어요."}[self.status])
                self.refresh_history()
            if kind == "provider_error":
                self.notice.set("로컬 모델 상태: " + data["code"] + " · 잠시 후 다시 시도해요.")
            if kind != "speech_chunk":
                self.log.configure(state="normal")
                self.log.insert("end", kind + " " + str(event.get("agent_id") or "") + "\n")
                if int(self.log.index("end-1c").split(".")[0]) > 200:
                    self.log.delete("1.0", "2.0")
                self.log.configure(state="disabled")
                self.log.see("end")
            self.controls()
        if not self.closing:
            self.root.after(50, self.poll)

    def animate(self):
        states = {"LISTENING": "듣는 중", "THINKING": "생각 중", "WANTS_TO_SPEAK": "말이 떠올랐어요", "SPEAKING": "말하는 중", "INTERRUPTED": "듣는 중", "IDLE": "쉬는 중"}
        writers = []
        for key, agent in self.agents.items():
            typing = self.status == "running" and agent.get("typing")
            label = "입력 중…" if typing else states.get(agent["state"], "듣는 중")
            self.card_labels[key].configure(text=agent["name"] + " · " + label)
            if typing:
                writers.append(agent["name"])
        self.tick += 1
        self.typing.set((", ".join(writers) + " 입력 중" + "." * (self.tick % 3 + 1)) if writers else "")
        if not self.closing:
            self.root.after(250, self.animate)

    def refresh_history(self):
        self.history_paths = sorted(Path(self.settings.data_dir).glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:40]
        self.history.delete(0, "end")
        for path in self.history_paths:
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                self.history.insert("end", saved["topic"])
            except (OSError, ValueError, KeyError):
                self.history.insert("end", "읽을 수 없는 기록")

    def open_history(self, event=None):
        choice = self.history.curselection()
        if not choice:
            return
        try:
            saved = json.loads(self.history_paths[choice[0]].read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.notice.set("기록을 읽을 수 없어요.")
            return
        window = tk.Toplevel(self.root)
        window.title("Bstom 기록 · " + saved["topic"])
        window.geometry("700x600")
        text = tk.Text(window, wrap="word", font=("맑은 고딕", 11), padx=16, pady=16)
        text.pack(fill="both", expand=True)
        for message in saved["messages"]:
            if message["text"]:
                text.insert("end", message["name"] + "\n" + message["text"] + "\n\n")
        text.configure(state="disabled")

    def close(self):
        if self.closing:
            return
        self.closing = True
        self.notice.set("진행 중인 생성을 취소하고 대화를 저장하는 중이에요.")
        future = self.engine.shutdown()

        def finish():
            if future.done():
                self.root.destroy()
            else:
                self.root.after(50, finish)
        self.root.after(50, finish)


def main(topic="", auto_start=False):
    if tk is None:
        raise SystemExit("Tk support is required; install python3-tk on Linux.")
    configured = Settings()
    settings = configured.model_copy(update={
        "llm_provider": "ollama", "llm_base_url": "http://127.0.0.1:11434",
        "llm_model": configured.llm_model if configured.llm_provider == "ollama" else "qwen2.5:3b",
        "api_max_concurrency": 1, "api_requests_per_minute": 0, "api_tokens_per_minute": 0,
        "session_max_calls": 0, "session_max_messages": 0, "session_max_seconds": 0,
        "turn_pause_seconds": 0, "speech_cooldown": 0, "max_output_tokens": 256,
        "ollama_fast_mode": True})
    root = tk.Tk()
    DesktopWindow(root, settings, topic, auto_start)
    root.mainloop()
