"""WebSocket broadcast hub for the dashboard - bidirectional this time.

Outgoing: pipeline.py and crew/events.py push live progress/log/approval
events to every connected browser tab.

Incoming: the browser sends two message types back -
  {"type": "approval", "decision": "approve"|"reject", "feedback": "..."}
  {"type": "chat", "question": "..."}
Both cross from the asyncio event loop (where this hub lives) into the
background worker thread that's actually running CrewAI (which is
synchronous/blocking) via the thread-safe primitives below - see
pipeline.py and main.py for the other side of each handoff.
"""

from __future__ import annotations

import asyncio
import json
import queue
import threading

import websockets

MAX_HISTORY = 400


class ApprovalGate:
    """One draft-decision approval cycle. `wait()` blocks the pipeline's
    background thread; `resolve()` is called from the asyncio thread when
    the browser's Approve/Reject click arrives."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self.decision: str | None = None
        self.feedback: str | None = None

    def reset(self) -> None:
        self._event.clear()
        self.decision = None
        self.feedback = None

    def wait(self, timeout: float | None = None) -> str | None:
        self._event.wait(timeout)
        return self.decision

    def resolve(self, decision: str, feedback: str | None = None) -> None:
        self.decision = decision
        self.feedback = feedback
        self._event.set()


class DashboardHub:
    def __init__(self) -> None:
        self.clients: set = set()
        self.approval_gate = ApprovalGate()
        self.chat_queue: queue.Queue[str] = queue.Queue()
        self.state: dict = {
            "stage": "idle",
            "log": [],
            "transactions": [],
            "classifications": [],
            "draft": None,
            "committed": [],
            "report": None,
            "chat": [],
        }

    async def _handler(self, ws) -> None:
        self.clients.add(ws)
        try:
            await ws.send(json.dumps({"type": "snapshot", "state": self.state}))
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if msg.get("type") == "approval":
                    self.approval_gate.resolve(msg.get("decision", "reject"), msg.get("feedback"))
                elif msg.get("type") == "chat":
                    question = (msg.get("question") or "").strip()
                    if question:
                        self._append_chat({"role": "user", "text": question})
                        self.chat_queue.put(question)
        except websockets.ConnectionClosed:
            pass
        finally:
            self.clients.discard(ws)

    async def start(self, host: str = "localhost", port: int = 8765):
        return await websockets.serve(self._handler, host, port)

    async def _emit(self, event: dict) -> None:
        if not self.clients:
            return
        msg = json.dumps(event)
        await asyncio.gather(*(c.send(msg) for c in list(self.clients)), return_exceptions=True)

    def _append_chat(self, entry: dict) -> None:
        self.state["chat"].append(entry)
        del self.state["chat"][:-MAX_HISTORY]

    # ---- outgoing pushes (called via asyncio.run_coroutine_threadsafe from
    # the background pipeline thread, or awaited directly from the asyncio
    # side) -----------------------------------------------------------------

    async def push_stage(self, stage: str) -> None:
        self.state["stage"] = stage
        await self._emit({"type": "stage", "stage": stage})

    async def push_log(self, entry: dict) -> None:
        self.state["log"].append(entry)
        del self.state["log"][:-MAX_HISTORY]
        await self._emit({"type": "log", "entry": entry})

    async def push_transactions(self, transactions: list[dict]) -> None:
        self.state["transactions"] = transactions
        await self._emit({"type": "transactions", "transactions": transactions})

    async def push_classifications(self, classifications: list[dict]) -> None:
        self.state["classifications"] = classifications
        await self._emit({"type": "classifications", "classifications": classifications})

    async def push_draft(self, decisions: list[dict]) -> None:
        self.state["draft"] = decisions
        self.state["stage"] = "awaiting_approval"
        await self._emit({"type": "draft", "decisions": decisions})

    async def push_committed(self, decisions: list[dict]) -> None:
        self.state["committed"] = decisions
        await self._emit({"type": "committed", "decisions": decisions})

    async def push_report(self, report_text: str) -> None:
        self.state["report"] = report_text
        await self._emit({"type": "report", "report": report_text})

    async def push_chat_answer(self, entry: dict) -> None:
        self._append_chat({"role": "assistant", **entry})
        await self._emit({"type": "chat_answer", **entry})


hub = DashboardHub()
