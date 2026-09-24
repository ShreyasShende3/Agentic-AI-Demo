# Demo 1: Sequence agent (LangGraph)

An agent that plays a real, full game of the `sequence-game` board game
over its actual WebSocket protocol, alongside a deterministic sparring
bot. It never modifies `../sequence-game` - only imports its rules engine
read-only (`sparring_bot/bot.mjs`), talks to its server over the network
like any other client, and (for the dashboard) reuses
`client/style.css`'s "Felt & Brass" board/card design directly (copied,
not linked - this repo doesn't serve any of sequence-game's own files).

## File structure

```
main.py                    entry point: starts the dashboard + bot, runs the graph
agent/
  graph.py                 the LangGraph: security probes, then the per-turn play loop
  config.py                all env-configurable settings
  ws_client.py              minimal client for sequence-game's WebSocket protocol
  move_candidates.py        turns a live board snapshot into options the LLM can pick from
  rag.py                   retrieval over agent/rules_kb/*.md
  memory.py                 persistent cross-turn/cross-run "game journal" (Chroma)
  security_probes.py        6 deterministic protocol-fuzzing checks, run once at startup
  rules_kb/*.md              the small knowledge base RAG retrieves from
dashboard/
  hub.py                    WebSocket broadcast hub (pushes live events to the browser)
  serve.py                  starts the hub + a static file server
  static/index.html          the dashboard page itself
sparring_bot/bot.mjs         Node opponent; always plays a legal move via sequence-game's own rules engine
```

## Concept -> file map (for the talk)

| Concept        | Where |
|----------------|-------|
| LangGraph      | `agent/graph.py` - a StateGraph with a loop, two phases |
| Tools          | `PickMoveChoice` (LLM structured tool call) + `submit_move` (WebSocket tool call) - both logged live in Agent Activity |
| RAG            | `agent/rag.py` + `agent/rules_kb/*.md` - every turn, retrieves rules text relevant to the hand before deciding |
| Persistent memory | `agent/memory.py` - a `game_journal` Chroma collection, never cleared; every turn writes a note and recalls the most similar past one (this run or an earlier one) |
| Security       | `agent/security_probes.py` - deterministic protocol fuzzing at startup, not LLM-driven |
| Observability  | Arize Phoenix tracing, auto-started by `main.py` |
| Live dashboard | `dashboard/` - real board/cards, and a live feed of every RAG query, memory read/write, and tool call |

## What happens on a run

The agent plays a complete game to its natural conclusion
(`MAX_GAMEPLAY_TURNS=300` is a safety cap, not a target - a real game ends
long before that). Every one of the agent's turns does, visibly, in this
order:

1. **RAG query** - retrieves rules text relevant to what's in hand (jack
   rules vs. general sequence-building) from `agent/rules_kb/`.
2. **Memory read** - recalls the most similar past turn from the game
   journal (this run, or any earlier run - it's never cleared).
3. **Tool call** - the LLM's move choice, via forced structured output
   (`PickMoveChoice`).
4. **Tool call** - `submit_move` over the real WebSocket protocol.
5. **Memory write** - a short note about what happened, for future recall.
   The note also records what was recalled in step 2 (or "nothing similar"
   on an early turn) - a recall is a read-only query with no trace of its
   own, so folding it into the write is what makes it visible later when
   browsing the journal directly (e.g. in a SQLite viewer on
   `.chroma/chroma.sqlite3`), not just live in the dashboard during the run.

All five are pushed to the dashboard's **Agent Activity** panel as they
happen - this is the thing to point at when explaining "here's a RAG
lookup, here's a memory write, here's a tool call." Before any of that,
`agent/security_probes.py` runs its 6 deterministic protocol checks
(rngSeed injection, the historical KAN-54 team-count override, oversized
frame, etc.) once at startup - fast, cheap, and separate from gameplay.

## Setup

1. **Sequence-game server** (separate terminal, this repo never starts or
   touches it):
   ```
   cd ../sequence-game
   node server/server.js
   ```
2. **Ollama**, running locally with two models pulled:
   ```
   ollama pull qwen2.5:7b-instruct
   ollama pull nomic-embed-text
   ```
3. **This project**:
   ```
   npm install
   pip install -r requirements.txt
   ```
4. Copy `.env.example` to `.env` (or just export the same variables) if
   you want to override any default (model name, turn count, ports).

## Run

```
python main.py
```

This starts the local dashboard, spawns the Node sparring bot
(`sparring_bot/bot.mjs`, always plays a legal move via the real
`legalMoves.js`), joins it as a second player, runs the 6 security probes,
then plays a full game.

Phoenix's trace UI is at `http://localhost:6006` once it starts (printed in
the terminal) - useful as a second screen/tab showing the raw LLM calls
(prompt, tool schema, response) behind every decision in the Activity feed.
`launch_app()` alone does NOT export traces - `register(auto_instrument=True)`
(in `main.py`) is the piece that actually wires LangChain up to it; without
it Phoenix opens but stays empty.

When the game ends, **the process doesn't exit** - dashboard and Phoenix
both run in-process, so returning would kill them right when there's the
most to review. It prints a short message and blocks on Enter in the
terminal; both stay live and browsable until you press it.

## The dashboard

`main.py` opens `http://localhost:8000` automatically (a 5-second pause
first, so you can switch windows) and prints the URL in case it doesn't.
It shows, live:

- **Who's who** - the turn bar labels which color is the agent and which
  is the sparring bot by name, each with its team's chip color.
- **The real board and hand** - the exact "Felt & Brass" board/card design
  from `sequence-game/client/style.css`, driven by the server's own state
  snapshot (not a static mockup or a lookalike). The cell that just
  changed flashes; jacks render with the real bust illustrations. The
  board updates on *both* players' turns, not just the agent's (the
  sparring bot's moves are tagged `(bot)` in the move log).
- **Sequence tracking** - a live "Red: 1/2  Blue: 0/2"-style tally. When
  either side completes a sequence, its 5 cells get a persistent
  team-colored ring (on top of a one-shot gold flash + a toast banner)
  that stays for the rest of the game, so multiple completed sequences by
  either or both sides remain visible and distinguishable at once.
- **Agent Activity** - every RAG query, memory read/write, and tool call
  (including the LLM's stated reasoning for its move), as they happen.
- **Security probes** - a checklist for the 6 startup probes.
- **Move log** - a compact scrolling record of every move and its result.

Static page + WebSocket broadcast hub, both served locally
(`dashboard/serve.py`, `dashboard/hub.py`) - no external hosting, no
network dependency, and no files served from `sequence-game`.

## Notes for Q&A

- The game journal persists across runs by design - a fresh clone will
  have no recall hits on its first few turns; after that (or on a second
  run) recall starts surfacing real past entries. To reset it for a "cold
  start" demo: delete `.chroma/` (the RAG cache rebuilds automatically).
- Candidate move generation (`agent/move_candidates.py`) reads live
  board/chip data from the server's own snapshot - it does not hardcode
  the board layout or re-implement `legalMoves.js`.
- `qwen2.5:7b-instruct` via Ollama is tool-calling capable but not
  perfectly reliable; the LLM node falls back to a safe default if the
  model doesn't return a structured tool call, so a live demo won't stall.
