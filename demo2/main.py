"""Entry point for Demo 1: LangGraph bug-hunting agent vs. a legal-move
sparring bot, played over sequence-game's real WebSocket protocol.

Prerequisites (see README.md):
  - sequence-game's server running locally: `node server/server.js` from
    inside the sequence-game folder (this repo never starts or modifies it).
  - Ollama running locally with OLLAMA_MODEL and OLLAMA_EMBED_MODEL pulled.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

# Must happen before anything prints: Phoenix's launch_app() and a few
# other libraries print emoji (e.g. a globe U+1F30D) straight to stdout,
# which crashes with UnicodeEncodeError on Windows' default cp1252 console
# - and since that crash happens inside launch_app() itself, it also
# silently prevents the LangChainInstrumentor().instrument() call right
# after it from ever running. Reconfiguring stdout/stderr to UTF-8 up
# front avoids both problems; a no-op on platforms already UTF-8.
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from agent import config
from agent.graph import build_graph, initial_state
from dashboard.serve import start_dashboard

ROOT_DIR = Path(__file__).resolve().parent
BOT_SCRIPT = ROOT_DIR / "sparring_bot" / "bot.mjs"


def _try_optional_observability() -> bool:
    if not config.ENABLE_PHOENIX:
        return False
    try:
        import phoenix as px
        from phoenix.otel import register

        px.launch_app()
        # launch_app() only starts the UI/local storage - it does NOT wire up
        # trace export. register(auto_instrument=True) is the separate step
        # that creates the OpenTelemetry pipeline AND auto-instruments every
        # installed OpenInference-supported library (LangChain included), so
        # nothing shows up in the UI without this call.
        register(project_name="sequence-bug-hunter", auto_instrument=True, verbose=False)
        print("[observability] Phoenix tracing UI: http://localhost:6006")
        return True
    except ImportError:
        print("[observability] arize-phoenix not installed - skipping tracing "
              "(pip install arize-phoenix)")
    except Exception as exc:
        print(f"[observability] Phoenix failed to start ({exc}); continuing without tracing")
    return False


def start_sparring_bot() -> tuple[subprocess.Popen, str]:
    proc = subprocess.Popen(
        ["node", str(BOT_SCRIPT), "NEW", config.WS_URL],
        cwd=str(ROOT_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    room_code = None
    deadline = time.time() + config.BOT_STARTUP_TIMEOUT_S
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                break
            continue
        print(f"[sparring_bot] {line.rstrip()}")
        m = re.match(r"ROOM_CODE=(\S+)", line)
        if m:
            room_code = m.group(1)
            break
    if room_code is None:
        proc.terminate()
        raise RuntimeError(
            "sparring bot did not print a room code in time - is the sequence-game "
            f"server reachable at {config.WS_URL}? (run `node server/server.js` in the sequence-game folder)"
        )
    return proc, room_code


def drain_bot_output_in_background(proc: subprocess.Popen) -> None:
    import threading

    def _pump():
        for line in proc.stdout:
            print(f"[sparring_bot] {line.rstrip()}")

    threading.Thread(target=_pump, daemon=True).start()


async def run() -> None:
    phoenix_started = _try_optional_observability()

    dashboard_url = await start_dashboard()
    print(f"Dashboard: {dashboard_url}")
    try:
        webbrowser.open(dashboard_url)
    except Exception:
        pass
    print("Opening dashboard tab - starting in 5s (switch windows now)...")
    await asyncio.sleep(5)

    print("Starting sparring bot (imports sequence-game/shared/engine read-only)...")
    proc, room_code = start_sparring_bot()
    drain_bot_output_in_background(proc)
    print(f"Sparring match room code: {room_code}")

    app, client = build_graph(room_code)
    state = initial_state(room_code)

    try:
        await app.ainvoke(state, config={"recursion_limit": 200})
    finally:
        await client.close()
        proc.terminate()

    # Phoenix and the dashboard both run in-process (a background thread and
    # an asyncio server respectively) - returning from run() here would exit
    # the process and take both down with it, right when there's the most to
    # show (the finished board, the full trace history). Block on a
    # background thread (not a plain input() on the event loop itself) so
    # the dashboard's WebSocket connection keeps being serviced normally
    # while waiting.
    print("\nGame complete. Dashboard is still live for review" + (" - Phoenix too:" if phoenix_started else ":"))
    print(f"  Dashboard: {dashboard_url}")
    if phoenix_started:
        print("  Phoenix:   http://localhost:6006")
    try:
        await asyncio.to_thread(input, "\nPress Enter here to shut down (dashboard + Phoenix included)...\n")
    except EOFError:
        pass  # no interactive stdin (e.g. launched non-interactively) - shut down immediately instead


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        sys.exit(1)
