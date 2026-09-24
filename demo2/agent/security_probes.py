"""Deterministic protocol-level fuzz probes.

These are NOT LLM-driven on purpose: security regression checks should be
reproducible tests, not left to a 7B model's judgment. Each probe targets an
invariant that sequence-game/server/ws/protocol.js and room.js explicitly
document (and, for the first two, an invariant tied to a named historical
bug: KAN-54). A probe result of "flagged" means the server's behavior no
longer matches what those source comments promise - i.e. a regression.

Uses a throwaway room per probe so gameplay state never gets involved.
"""

from __future__ import annotations

from dataclasses import dataclass

from .ws_client import SequenceWSClient


@dataclass
class ProbeResult:
    name: str
    description: str
    passed: bool
    detail: str


async def _fresh_probe_room(ws_url: str) -> SequenceWSClient:
    client = SequenceWSClient(url=ws_url)
    await client.connect()
    await client.create_room("ProbeHost")
    return client


async def probe_rngseed_at_create(ws_url: str) -> ProbeResult:
    client = SequenceWSClient(url=ws_url)
    await client.connect()
    resp = await client.create_room("ProbeHost", config={"rngSeed": 424242})
    await client.close()
    ok = resp.get("type") == "ERROR" and "rngSeed" in resp.get("payload", {}).get("message", "")
    return ProbeResult(
        "rngSeed_at_create_room",
        "CREATE_ROOM with a client-supplied config.rngSeed must be rejected "
        "(a host could otherwise precompute the whole deck offline).",
        ok,
        f"server responded: {resp}",
    )


async def probe_rngseed_at_start(ws_url: str) -> ProbeResult:
    client = await _fresh_probe_room(ws_url)
    await client.send_raw("START_GAME", {"config": {"rngSeed": 999}})
    resp = await client.wait_for(("ERROR", "GAME_STARTED"))
    await client.close()
    ok = resp is not None and resp.get("type") == "ERROR" and "rngSeed" in resp.get("payload", {}).get("message", "")
    return ProbeResult(
        "rngSeed_at_start_game",
        "START_GAME with a client-supplied config.rngSeed must be rejected.",
        ok,
        f"server responded: {resp}",
    )


async def probe_teamcount_override(ws_url: str) -> ProbeResult:
    client = await _fresh_probe_room(ws_url)
    await client.send_raw("START_GAME", {"config": {"teamCount": 3}})
    resp = await client.wait_for(("ERROR", "GAME_STARTED"))
    await client.close()
    ok = resp is not None and resp.get("type") == "ERROR" and "teamCount" in resp.get("payload", {}).get("message", "")
    return ProbeResult(
        "teamCount_override_at_start",
        "START_GAME must not be able to change teamCount after CREATE_ROOM "
        "(this is the exact shape of the historical KAN-54 bug: lobby "
        "preview and real assignment silently disagreeing).",
        ok,
        f"server responded: {resp}",
    )


async def probe_malformed_target_cell(ws_url: str) -> ProbeResult:
    client = await _fresh_probe_room(ws_url)
    await client.send_raw(
        "SUBMIT_MOVE",
        {"moveIntent": {"type": "PLAY_CARD", "action": "PLACE", "card": "7H", "targetCell": "not-an-array"}},
    )
    resp = await client.wait_for(("ERROR", "MOVE_REJECTED", "MOVE_APPLIED"))
    await client.close()
    ok = resp is not None and resp.get("type") == "ERROR" and "targetCell" in resp.get("payload", {}).get("message", "")
    return ProbeResult(
        "malformed_target_cell_shape",
        "A non-array targetCell must be rejected at message-validation time, "
        "before it ever reaches game rules.",
        ok,
        f"server responded: {resp}",
    )


async def probe_unknown_message_type(ws_url: str) -> ProbeResult:
    client = SequenceWSClient(url=ws_url)
    await client.connect()
    await client.send_raw("DELETE_ROOM", {})
    resp = await client.wait_for(("ERROR",))
    await client.close()
    ok = resp is not None and resp.get("type") == "ERROR" and "unknown message type" in resp.get("payload", {}).get("message", "")
    return ProbeResult(
        "unknown_message_type",
        "An unrecognized message type must produce a clean ERROR, not a "
        "crash or a silent drop.",
        ok,
        f"server responded: {resp}",
    )


async def probe_oversized_frame(ws_url: str) -> ProbeResult:
    client = await _fresh_probe_room(ws_url)
    huge_card = "A" * 20000  # server's WS_MAX_PAYLOAD_BYTES is 16 * 1024
    try:
        await client.send_raw(
            "SUBMIT_MOVE",
            {"moveIntent": {"type": "PLAY_CARD", "action": "PLACE", "card": huge_card, "targetCell": [0, 1]}},
        )
        resp = await client.wait_for(("ERROR", "MOVE_REJECTED", "_CONNECTION_CLOSED"), timeout=5.0)
    except Exception as exc:  # local send-side rejection also counts as "held"
        resp = {"type": "_CONNECTION_CLOSED", "payload": {"reason": str(exc)}}
    await client.close()
    # Either an application-level rejection or the socket being force-closed
    # by the server's maxPayload guard counts as the defense holding.
    ok = resp is not None and resp.get("type") in ("ERROR", "MOVE_REJECTED", "_CONNECTION_CLOSED")
    return ProbeResult(
        "oversized_frame",
        "A frame over the server's 16KB WS_MAX_PAYLOAD_BYTES must not be "
        "silently accepted. NOTE: this deliberately makes the sequence-game "
        "server print a '[ws] connection error: Max payload size exceeded' "
        "line in its own console - that line IS this probe passing, not a "
        "crash.",
        ok,
        f"server responded: {resp}",
    )


# Order matters for the live demo: the "loud" probe (oversized_frame prints
# a scary-looking line in the sequence-game server's own console) runs
# FIRST, so it doesn't look like something broke right before moving on to
# phase 2 - and so it's the first thing you narrate ("watch the server
# console - that error line is the pass signal") rather than a surprise.
ALL_PROBES = [
    probe_oversized_frame,
    probe_rngseed_at_create,
    probe_rngseed_at_start,
    probe_teamcount_override,
    probe_malformed_target_cell,
    probe_unknown_message_type,
]


async def run_all_probes(ws_url: str) -> list[ProbeResult]:
    results = []
    for probe in ALL_PROBES:
        try:
            results.append(await probe(ws_url))
        except Exception as exc:  # a crashing probe is itself a finding
            results.append(ProbeResult(probe.__name__, "probe raised an exception", False, str(exc)))
    return results
