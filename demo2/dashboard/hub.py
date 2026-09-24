"""In-memory WebSocket broadcast hub for the live dashboard.

Keeps the latest full state in memory so a browser tab that connects late
(or reconnects) gets caught up immediately via a "snapshot" message, then
receives incremental "probes" / "move" / "activity" / "status" events as
the agent runs.
"""

from __future__ import annotations

import asyncio
import json

import websockets

# A full game can run 60-100+ turns; cap how much history the snapshot
# replays to a late-joining browser tab so memory/snapshot size stay
# bounded. The live incremental events (_emit) are unaffected - a
# connected tab keeps everything it's already received.
MAX_HISTORY = 400


class DashboardHub:
    def __init__(self) -> None:
        self.clients: set = set()
        self.state: dict = {
            "probes": [],
            "activity": [],
            "moves": [],
            "board": None,
            "chips": None,
            "hand": [],
            "turn": 0,
            "status": "idle",
            "winner": None,
            "players": None,
            "sequences": None,
            "sequence_history": [],  # every completed sequence ever, for persistent board highlighting
        }

    async def _handler(self, ws) -> None:
        self.clients.add(ws)
        try:
            await ws.send(json.dumps({"type": "snapshot", "state": self.state}))
            async for _ in ws:
                pass  # the page never sends anything meaningful back
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

    async def push_probes(self, results: list[dict]) -> None:
        self.state["probes"] = results
        await self._emit({"type": "probes", "results": results})

    async def push_move(
        self, turn: int, choice: dict | None, response: dict, board, chips,
        hand: list | None = None, mover: str = "agent", sequences: dict | None = None,
    ) -> None:
        entry = {
            "turn": turn,
            "choice": choice,
            "mover": mover,
            "response": {"type": response.get("type"), "reason": response.get("payload", {}).get("reason")},
        }
        self.state["moves"].append(entry)
        del self.state["moves"][:-MAX_HISTORY]
        self.state["board"] = board
        self.state["chips"] = chips
        self.state["turn"] = turn
        if hand is not None:
            self.state["hand"] = hand
        if sequences is not None:
            self.state["sequences"] = sequences
        await self._emit({"type": "move", **entry, "board": board, "chips": chips, "hand": hand, "sequences": sequences})

    async def push_players(self, info: dict) -> None:
        self.state["players"] = info
        await self._emit({"type": "players", "info": info})

    async def push_sequence_formed(self, entry: dict) -> None:
        self.state["sequences"] = {"counts": entry.get("counts"), "needed": entry.get("needed")}
        self.state["sequence_history"].append({"team": entry.get("team"), "cells": entry.get("cells")})
        await self._emit({"type": "sequence_formed", "entry": entry})

    async def push_activity(self, entry: dict) -> None:
        """entry: {kind: 'rag_query'|'memory_read'|'memory_write'|'tool_call', turn, ...}"""
        self.state["activity"].append(entry)
        del self.state["activity"][:-MAX_HISTORY]
        await self._emit({"type": "activity", "entry": entry})

    async def push_status(self, status: str, winner: str | None = None) -> None:
        self.state["status"] = status
        self.state["winner"] = winner
        await self._emit({"type": "status", "status": status, "winner": winner})


hub = DashboardHub()
