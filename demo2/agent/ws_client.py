"""Minimal async client for sequence-game's WebSocket protocol.

Speaks the exact wire format defined in
sequence-game/server/ws/protocol.js (read only, never modified here):
every message is {"type": ..., "payload": ...}.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field

import websockets


@dataclass
class SequenceWSClient:
    url: str
    player_id: str | None = None
    room_code: str | None = None
    latest_state: dict | None = None
    _ws: object = field(default=None, repr=False)
    _inbox: asyncio.Queue = field(default_factory=asyncio.Queue, repr=False)
    _recv_task: asyncio.Task | None = field(default=None, repr=False)

    async def connect(self) -> None:
        self._ws = await websockets.connect(self.url, max_size=64 * 1024)
        self._recv_task = asyncio.create_task(self._recv_loop())

    async def close(self) -> None:
        if self._recv_task:
            self._recv_task.cancel()
        if self._ws:
            await self._ws.close()

    async def _recv_loop(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    msg = {"type": "_UNPARSEABLE", "payload": {"raw": str(raw)[:200]}}
                self._apply_state_update(msg)
                await self._inbox.put(msg)
        except websockets.ConnectionClosed as exc:
            await self._inbox.put({"type": "_CONNECTION_CLOSED", "payload": {"code": exc.code, "reason": exc.reason}})

    def _apply_state_update(self, msg: dict) -> None:
        t = msg.get("type")
        if t in ("GAME_STARTED", "SYNC_STATE", "MOVE_APPLIED"):
            snap = msg["payload"].get("stateSnapshot")
            if snap:
                self.latest_state = snap
        elif t == "ROOM_CREATED":
            self.player_id = msg["payload"]["playerId"]
            self.room_code = msg["payload"]["roomCode"]

    async def send_raw(self, msg_type: str, payload: dict) -> None:
        await self._ws.send(json.dumps({"type": msg_type, "payload": payload}))

    async def wait_for(self, types: tuple[str, ...], timeout: float = 5.0) -> dict | None:
        """Drain the inbox until a message of one of `types` arrives, or timeout.

        Messages that don't match are still processed by _apply_state_update
        (already done in the recv loop) - they're just not returned here.
        """
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                return None
            try:
                msg = await asyncio.wait_for(self._inbox.get(), timeout=remaining)
            except asyncio.TimeoutError:
                return None
            if msg.get("type") in types or msg.get("type") == "_CONNECTION_CLOSED":
                return msg

    # ---- high-level game actions ----------------------------------------

    async def create_room(self, host_name: str, config: dict | None = None) -> dict:
        await self.send_raw("CREATE_ROOM", {"hostName": host_name, **({"config": config} if config else {})})
        msg = await self.wait_for(("ROOM_CREATED", "ERROR", "_CONNECTION_CLOSED"))
        return msg or {"type": "_TIMEOUT"}

    async def join_room(self, room_code: str, player_name: str) -> dict:
        await self.send_raw("JOIN_ROOM", {"roomCode": room_code, "playerName": player_name})
        msg = await self.wait_for(("ROOM_CREATED", "ERROR", "_CONNECTION_CLOSED"))
        return msg or {"type": "_TIMEOUT"}

    async def wait_for_game_start(self, timeout: float = 15.0) -> dict | None:
        return await self.wait_for(("GAME_STARTED", "SYNC_STATE"), timeout=timeout)

    async def submit_move(self, move_intent: dict, timeout: float = 5.0) -> dict:
        client_move_id = f"agent_{time.time_ns()}"
        await self.send_raw("SUBMIT_MOVE", {"moveIntent": {**move_intent, "clientMoveId": client_move_id}})
        msg = await self.wait_for(("MOVE_APPLIED", "MOVE_REJECTED", "ERROR", "_CONNECTION_CLOSED"), timeout=timeout)
        return msg or {"type": "_TIMEOUT"}

    def my_turn(self) -> bool:
        s = self.latest_state
        if not s or s.get("status") != "in_progress":
            return False
        order = s.get("turnOrder", [])
        idx = s.get("currentPlayerIndex", -1)
        return 0 <= idx < len(order) and order[idx] == self.player_id
