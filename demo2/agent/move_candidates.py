"""Turns a raw server state snapshot into candidate legal-looking moves for
the LLM to choose from, and a plain-text listing of them - all derived
from the live snapshot the server actually sends (board layout, chips,
hands), never from a hardcoded copy of the board or a reimplementation of
legalMoves.js.
"""

from __future__ import annotations

TWO_EYED_JACKS = ("JC", "JD")
ONE_EYED_JACKS = ("JH", "JS")


def _my_team(state: dict, player_id: str) -> str | None:
    for p in state.get("players", []):
        if p["playerId"] == player_id:
            return p["teamId"]
    return None


def candidate_options(state: dict, player_id: str) -> list[dict]:
    board = state["board"]
    chips = state["chips"]
    hand = state["hands"].get(player_id, [])
    my_team = _my_team(state, player_id)
    used_cells = state.get("usedSequenceCells", {})
    options: list[dict] = []

    for card in hand:
        cells: list[tuple[int, int]] = []
        if card in TWO_EYED_JACKS:
            action = "PLACE"
            for r in range(10):
                for c in range(10):
                    if board[r][c] == "WILD":
                        continue
                    if chips[r][c] is None:
                        cells.append((r, c))
        elif card in ONE_EYED_JACKS:
            action = "REMOVE"
            for r in range(10):
                for c in range(10):
                    occ = chips[r][c]
                    if occ is None or occ == my_team:
                        continue
                    # usedSequenceCells entries are "row,col" strings
                    # (sequenceDetector.js's cellKey convention), not
                    # [row, col] pairs - matching that shape is what
                    # sequence-bug-hunter's own sparring bot got wrong on
                    # its first run (see sparring_bot/bot.mjs's comment).
                    if f"{r},{c}" in set(used_cells.get(occ, [])):
                        continue
                    cells.append((r, c))
        else:
            action = "PLACE"
            for r in range(10):
                for c in range(10):
                    if board[r][c] == card and chips[r][c] is None:
                        cells.append((r, c))

        if cells:
            for r, c in cells[:6]:  # cap so the prompt stays small for a local model
                options.append(
                    {"card": card, "type": "PLAY_CARD", "action": action, "target_row": r, "target_col": c, "is_discard": False}
                )
        else:
            options.append(
                {"card": card, "type": "DISCARD_DEAD_CARD", "action": "DISCARD", "target_row": None, "target_col": None, "is_discard": True}
            )

    return options


def format_options(options: list[dict]) -> str:
    lines = []
    for i, opt in enumerate(options):
        if opt["is_discard"]:
            lines.append(f"[{i}] discard {opt['card']} (no open target found for it)")
        else:
            lines.append(f"[{i}] {opt['action']} {opt['card']} at row {opt['target_row']}, col {opt['target_col']}")
    return "\n".join(lines)
