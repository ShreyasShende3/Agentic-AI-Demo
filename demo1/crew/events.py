"""Bridges CrewAI's own event bus straight to the dashboard - this is where
"logging" comes from: every tool call (MCP-backed or the plain
`policy_lookup` @tool), every guardrail check, and every task
start/finish is emitted by CrewAI itself, not something this project
manually instruments at each call site.

CrewAI's Task.execute_sync() runs synchronously and is driven from a
background thread (see pipeline.py), while the dashboard's WebSocket hub
runs on the main asyncio event loop - so every push here has to hop
threads via `asyncio.run_coroutine_threadsafe`.
"""

from __future__ import annotations

import asyncio

from crewai.events import (
    BaseEventListener,
    LLMGuardrailCompletedEvent,
    LLMGuardrailStartedEvent,
    TaskCompletedEvent,
    TaskStartedEvent,
    ToolUsageFinishedEvent,
    ToolUsageStartedEvent,
)

from dashboard.hub import hub


class DashboardEventListener(BaseEventListener):
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        super().__init__()

    def _push(self, entry: dict) -> None:
        asyncio.run_coroutine_threadsafe(hub.push_log(entry), self._loop)

    def setup_listeners(self, bus) -> None:
        @bus.on(TaskStartedEvent)
        def on_task_started(source, event: TaskStartedEvent) -> None:
            name = getattr(event.task, "name", None) or getattr(event.task, "description", "task")
            agent_role = getattr(getattr(event.task, "agent", None), "role", None)
            self._push({"kind": "task", "status": "started", "name": str(name)[:80], "agent": agent_role})

        @bus.on(TaskCompletedEvent)
        def on_task_completed(source, event: TaskCompletedEvent) -> None:
            name = getattr(event.task, "name", None) or getattr(event.task, "description", "task")
            agent_role = getattr(getattr(event.task, "agent", None), "role", None)
            self._push({"kind": "task", "status": "completed", "name": str(name)[:80], "agent": agent_role})

        @bus.on(ToolUsageStartedEvent)
        def on_tool_started(source, event: ToolUsageStartedEvent) -> None:
            self._push({
                "kind": "tool", "status": "started", "agent": event.agent_role,
                "tool": event.tool_name, "args": event.tool_args,
            })

        @bus.on(ToolUsageFinishedEvent)
        def on_tool_finished(source, event: ToolUsageFinishedEvent) -> None:
            self._push({
                "kind": "tool", "status": "finished", "agent": event.agent_role,
                "tool": event.tool_name, "output": str(event.output)[:300],
            })

        @bus.on(LLMGuardrailStartedEvent)
        def on_guardrail_started(source, event: LLMGuardrailStartedEvent) -> None:
            self._push({
                "kind": "guardrail", "status": "started",
                "name": event.guardrail_name, "retry": event.retry_count,
            })

        @bus.on(LLMGuardrailCompletedEvent)
        def on_guardrail_completed(source, event: LLMGuardrailCompletedEvent) -> None:
            self._push({
                "kind": "guardrail", "status": "completed", "success": event.success,
                "error": event.error, "retry": event.retry_count,
            })
