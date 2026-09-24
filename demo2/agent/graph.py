"""LangGraph definition for the agent.

Two phases, one graph:
  1. security probes - deterministic (see security_probes.py), no LLM.
  2. gameplay loop - the LLM makes exactly one structured decision per turn
     (which candidate move to play), via bind_tools()-forced structured
     output. This is a narrower use of "tool calling" than open-ended
     ReAct, chosen deliberately: it's far more reliable with a small local
     model, while still being a real LangGraph with a loop.
"""

from __future__ import annotations

from typing import Literal, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

from dashboard.hub import hub as dash

from . import config, move_candidates
from .memory import GameJournal
from .rag import RulesRetriever
from .security_probes import ProbeResult, run_all_probes
from .ws_client import SequenceWSClient

TWO_EYED_JACKS = ("JC", "JD")
ONE_EYED_JACKS = ("JH", "JS")


class PickMoveChoice(BaseModel):
    """Choose exactly one option from the numbered candidate list."""

    choice_index: int = Field(description="the index number of the chosen option")
    reasoning: str = Field(description="one short sentence on why")


class AgentState(TypedDict):
    phase: Literal["security", "join", "gameplay", "done"]
    turn: int
    max_turns: int
    room_code: str
    probe_results: list[dict]
    last_choice: dict | None
    last_response: dict | None
    last_recall: list[dict] | None


def build_graph(room_code: str, max_turns: int = config.MAX_GAMEPLAY_TURNS):
    llm = ChatOllama(model=config.OLLAMA_MODEL, base_url=config.OLLAMA_BASE_URL, temperature=0.2)
    picker = llm.bind_tools([PickMoveChoice], tool_choice="PickMoveChoice")

    journal = GameJournal()
    retriever = RulesRetriever()
    client = SequenceWSClient(url=config.WS_URL)

    def _extract_tool_args(response, schema_name: str) -> dict | None:
        for call in getattr(response, "tool_calls", []) or []:
            if call.get("name") == schema_name:
                return call.get("args") or {}
        return None

    def _sequence_info(state: dict) -> dict:
        counts = {tid: len(seqs) for tid, seqs in (state.get("sequences") or {}).items()}
        needed = (state.get("config") or {}).get("sequencesToWin")
        return {"counts": counts, "needed": needed}

    async def _maybe_announce_sequence(move_record: dict, state: dict, turn_num: int, mover: str) -> None:
        resulting = move_record.get("resultingSequences") or []
        if not resulting:
            return
        info = _sequence_info(state)
        await dash.push_sequence_formed({
            "team": move_record.get("teamId"), "cells": resulting, "mover": mover,
            "turn": turn_num, "counts": info["counts"], "needed": info["needed"],
        })

    async def node_security(state: AgentState) -> AgentState:
        print("Phase 1: running deterministic security probes...")
        await dash.push_status("probing")
        results: list[ProbeResult] = await run_all_probes(config.WS_URL)
        state["probe_results"] = [r.__dict__ for r in results]
        await dash.push_probes(state["probe_results"])
        flagged = sum(1 for r in results if not r.passed)
        print(f"  {len(results) - flagged}/{len(results)} probes passed" + (f", {flagged} FLAGGED" if flagged else ""))
        state["phase"] = "join"
        return state

    async def node_join(state: AgentState) -> AgentState:
        print("Phase 2: joining sparring match...")
        await dash.push_status("joining")
        await client.connect()
        resp = await client.join_room(state["room_code"], "BugHunterAgent")
        if resp.get("type") != "ROOM_CREATED":
            raise RuntimeError(f"failed to join room {state['room_code']}: {resp}")
        await client.wait_for_game_start(timeout=15.0)
        if client.latest_state:
            s = client.latest_state
            hand = s.get("hands", {}).get(client.player_id, [])
            players = {p["playerId"]: p for p in s.get("players", [])}
            me = players.get(client.player_id, {})
            other = next((p for pid, p in players.items() if pid != client.player_id), {})
            await dash.push_players({
                "agent": {"team": me.get("teamId"), "name": me.get("name") or "Agent"},
                "bot": {"team": other.get("teamId"), "name": other.get("name") or "Sparring Bot"},
            })
            await dash.push_move(
                0, None, {"type": "GAME_STARTED"}, s.get("board"), s.get("chips"),
                hand=hand, sequences=_sequence_info(s),
            )
        await dash.push_status("playing")
        print(f"  joined as playerId={client.player_id} - open the dashboard to watch")
        state["phase"] = "gameplay"
        return state

    async def node_wait_turn(state: AgentState) -> AgentState:
        # Opponent (sparring bot) moves happen entirely inside this wait -
        # without pushing them here, the dashboard board would only ever
        # visibly update on the agent's own turns.
        if not client.latest_state or client.latest_state.get("status") == "in_progress":
            attempts = 0
            while not client.my_turn() and attempts < 40:
                if client.latest_state and client.latest_state.get("status") == "finished":
                    break
                msg = await client.wait_for(("MOVE_APPLIED", "SYNC_STATE", "GAME_STARTED", "GAME_OVER"), timeout=20.0)
                if msg and msg.get("type") == "MOVE_APPLIED":
                    record = msg.get("payload", {}).get("moveRecord", {})
                    s = client.latest_state
                    if s:
                        hand = s.get("hands", {}).get(client.player_id, [])
                        bot_choice = {
                            "card": record.get("card"),
                            "action": "REMOVE" if record.get("type") == "REMOVE_CHIP" else "PLACE",
                            "target_row": record["cell"][0] if record.get("cell") else None,
                            "target_col": record["cell"][1] if record.get("cell") else None,
                            "is_discard": record.get("type") == "DEAD_CARD_DISCARD",
                        }
                        await dash.push_move(
                            state["turn"], bot_choice, {"type": "MOVE_APPLIED"},
                            s.get("board"), s.get("chips"), hand=hand, mover="bot",
                            sequences=_sequence_info(s),
                        )
                        await _maybe_announce_sequence(record, s, state["turn"], "bot")
                attempts += 1
        return state

    def _rag_query_for_hand(hand: list[str]) -> str:
        if any(c in TWO_EYED_JACKS for c in hand):
            return "how do two-eyed jacks work, where can they be placed"
        if any(c in ONE_EYED_JACKS for c in hand):
            return "when can a one-eyed jack remove a chip, what can't it remove"
        return "how do I build a sequence and what counts as a completed sequence"

    async def node_pick_move(state: AgentState) -> AgentState:
        s = client.latest_state
        turn_num = state["turn"] + 1

        options = move_candidates.candidate_options(s, client.player_id)
        print(f"turn {turn_num}: agent thinking ({len(options)} candidate moves)...")

        if not options:
            state["last_choice"] = None
            return state

        hand = s["hands"].get(client.player_id, [])

        # 1. RAG: ground this turn's decision in the actual rules text
        # relevant to what's in hand, rather than the LLM's own guess.
        rag_query = _rag_query_for_hand(hand)
        rules_context = retriever.query(rag_query, k=2)
        await dash.push_activity({
            "kind": "rag_query", "turn": turn_num, "query": rag_query, "results": rules_context,
        })

        # 2. Memory: recall the most similar past turn (this run or any
        # earlier one - the journal is never cleared) before deciding.
        recall_query = f"hand {hand} with {len(options)} candidate moves"
        recalled = journal.recall(recall_query, k=1)
        state["last_recall"] = recalled
        await dash.push_activity({
            "kind": "memory_read", "turn": turn_num, "query": recall_query, "recalled": recalled,
        })

        memory_note = f"\nA similar past turn: {recalled[0]['text']}" if recalled else ""
        prompt = (
            "You are playing the card game Sequence. Relevant rules:\n"
            + "\n".join(rules_context)
            + memory_note
            + "\n\nPick exactly one option below to play this turn.\n\n"
            + move_candidates.format_options(options)
        )
        # 3. Tool call: the LLM's move choice, via forced structured output.
        try:
            resp = picker.invoke([SystemMessage(content="Respond only via the PickMoveChoice tool."), HumanMessage(content=prompt)])
            args = _extract_tool_args(resp, "PickMoveChoice")
            idx = int(args["choice_index"]) if args else 0
            reasoning = (args or {}).get("reasoning", "")
        except Exception as exc:
            print(f"  [llm] pick_move failed ({exc}); falling back to option 0")
            idx, reasoning = 0, f"fallback after LLM error: {exc}"
        idx = idx if 0 <= idx < len(options) else 0
        state["last_choice"] = options[idx]
        await dash.push_activity({
            "kind": "tool_call", "turn": turn_num, "tool": "PickMoveChoice (LLM structured tool call)",
            "args": {"num_options": len(options)}, "result": f"chose #{idx}: {options[idx]}", "reasoning": reasoning,
        })
        return state

    async def node_submit_move(state: AgentState) -> AgentState:
        choice = state["last_choice"]
        turn_num = state["turn"] + 1
        if choice is None:
            state["turn"] = state["max_turns"]
            return state
        intent = {
            "type": choice["type"],
            "action": choice["action"],
            "card": choice["card"],
            "targetCell": None if choice["is_discard"] else [choice["target_row"], choice["target_col"]],
        }
        resp = await client.submit_move(intent)
        state["last_response"] = resp
        print(f"  turn {turn_num}: {choice['action']} {choice['card']} -> {resp.get('type')}")
        await dash.push_activity({
            "kind": "tool_call", "turn": turn_num, "tool": "submit_move (WebSocket -> sequence-game server)",
            "args": intent, "result": resp.get("type"),
        })

        s = client.latest_state
        hand = s.get("hands", {}).get(client.player_id, []) if s else []

        # Memory write: a short journal note about this turn, for future
        # recall (by this run's later turns, or a future run entirely). The
        # recall that informed THIS turn's decision (computed in
        # node_pick_move, read-only, and otherwise never persisted anywhere)
        # is folded into the note text itself, so it's visible sitting right
        # next to the move it influenced when browsing the journal directly
        # (e.g. in a SQLite viewer on .chroma/chroma.sqlite3) - a recall is
        # a query, not a write, so without this it leaves no trace at all
        # once the run ends.
        recalled = state.get("last_recall") or []
        # Truncated: a recalled note can itself already contain an earlier
        # "| recalled: ..." suffix, so quoting it in full would let each
        # note nest and grow without bound over a long game (or across
        # many runs, since the journal is never cleared). Capping the
        # quote keeps every note's own growth bounded regardless of how
        # long a chain it was recalled from.
        recalled_preview = recalled[0]["text"][:80] if recalled else ""
        recall_suffix = f" | recalled: \"{recalled_preview}\"" if recalled else " | recalled: nothing similar"
        note = f"Turn {turn_num}: {choice['action']} {choice['card']} -> {resp.get('type')}" + (
            f" ({resp.get('payload', {}).get('reason')})" if resp.get("type") == "MOVE_REJECTED" else ""
        ) + recall_suffix
        metadata = {"turn": turn_num, "card": choice["card"], "action": choice["action"], "result": resp.get("type")}
        if recalled:
            # Chroma's metadata values must be str/int/float/bool - a None
            # entry (the common case on an early turn, before there's
            # anything to recall yet) makes the whole add() call fail with
            # "Cannot convert Python object to MetadataValue", so these
            # keys are only present at all when there was something to
            # recall, never set to None.
            metadata["recalled_text"] = recalled_preview
            metadata["recalled_distance"] = float(recalled[0]["distance"])
        journal.remember(note, metadata)
        await dash.push_activity({"kind": "memory_write", "turn": turn_num, "text": note})

        await dash.push_move(
            turn_num, choice, resp,
            s.get("board") if s else None, s.get("chips") if s else None,
            hand=hand, sequences=_sequence_info(s) if s else None,
        )
        if resp.get("type") == "MOVE_APPLIED" and s:
            await _maybe_announce_sequence(resp.get("payload", {}).get("moveRecord", {}), s, turn_num, "agent")
        state["turn"] += 1
        return state

    async def node_after_move(state: AgentState) -> AgentState:
        s = client.latest_state
        if s and s.get("status") == "finished":
            print(f"  game finished, winner={s.get('winner')}")
            await dash.push_status("finished", s.get("winner"))
            state["phase"] = "done"
        elif state["turn"] >= state["max_turns"]:
            print("  turn limit reached")
            await dash.push_status("finished")
            state["phase"] = "done"
        return state

    def route_after_after_move(state: AgentState) -> str:
        return "done" if state["phase"] == "done" else "wait_turn"

    graph = StateGraph(AgentState)
    graph.add_node("security", node_security)
    graph.add_node("join", node_join)
    graph.add_node("wait_turn", node_wait_turn)
    graph.add_node("pick_move", node_pick_move)
    graph.add_node("submit_move", node_submit_move)
    graph.add_node("after_move", node_after_move)

    graph.set_entry_point("security")
    graph.add_edge("security", "join")
    graph.add_edge("join", "wait_turn")
    graph.add_edge("wait_turn", "pick_move")
    graph.add_edge("pick_move", "submit_move")
    graph.add_edge("submit_move", "after_move")
    graph.add_conditional_edges("after_move", route_after_after_move, {"wait_turn": "wait_turn", "done": END})

    compiled = graph.compile()
    return compiled, client


def initial_state(room_code: str, max_turns: int = config.MAX_GAMEPLAY_TURNS) -> AgentState:
    return AgentState(
        phase="security",
        turn=0,
        max_turns=max_turns,
        room_code=room_code,
        probe_results=[],
        last_choice=None,
        last_response=None,
        last_recall=None,
    )
