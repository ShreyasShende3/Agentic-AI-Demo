"""Entry point for Demo 2: a CrewAI accounts-payable fraud-screening crew
with a live browser dashboard - free official MCP servers, least-privilege
tool scoping, a native CrewAI guardrail, a browser-based human-in-the-loop
approval gate, and a post-analysis Q&A/chart chat agent.

Prerequisites (see README.md):
  - Ollama running locally with OLLAMA_MODEL and OLLAMA_EMBED_MODEL pulled.
  - Node.js/npx available (the MCP servers run via `npx -y ...`).
"""

from __future__ import annotations

import asyncio
import queue
import sys
import webbrowser

# Must happen before anything imports crewai: CrewAI's own verbose output
# and a few of its dependencies print emoji straight to stdout, which
# crashes with UnicodeEncodeError on Windows' default cp1252 console.
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from crew.insights import InsightsAgent
from crew.pipeline import run_pipeline
from dashboard.hub import hub
from dashboard.serve import start_dashboard


async def _chat_loop(insights_agent: InsightsAgent, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            question = await asyncio.to_thread(hub.chat_queue.get, True, 1.0)
        except queue.Empty:
            continue
        answer = await asyncio.to_thread(insights_agent.ask, question)
        await hub.push_chat_answer(answer)


async def run() -> None:
    loop = asyncio.get_event_loop()

    dashboard_url = await start_dashboard()
    print(f"Dashboard: {dashboard_url}")
    try:
        webbrowser.open(dashboard_url)
    except Exception:
        pass
    print("Opening dashboard tab - starting in 5s (switch windows now)...")
    await asyncio.sleep(5)

    committed, memory_dump = await asyncio.to_thread(run_pipeline, loop)
    print(f"Pipeline finished - {len(committed)} decision(s) committed." if committed else "Draft was rejected - nothing committed.")

    insights_agent = InsightsAgent(committed, memory_dump)
    stop_event = asyncio.Event()
    chat_task = asyncio.create_task(_chat_loop(insights_agent, stop_event))

    print("\nAsk questions (and try to break it) in the dashboard's chat panel.")
    try:
        await asyncio.to_thread(input, "\nPress Enter here to shut down (dashboard included)...\n")
    except EOFError:
        pass
    stop_event.set()
    chat_task.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        sys.exit(1)
