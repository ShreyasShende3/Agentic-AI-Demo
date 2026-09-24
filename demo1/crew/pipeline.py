"""Runs the crew step by step instead of one `Crew.kickoff()` call, so a
real browser-based approval gate can sit between the draft and commit
steps. This function is synchronous/blocking by design - main.py runs it
via `asyncio.to_thread()` on a background thread, while the dashboard's
asyncio event loop keeps servicing the browser (including the approval
click and chat questions) the whole time.

Checking and drafting run in small batches rather than one call per stage
over the whole invoice list: a single call over a large batch risks the
local 7B model quietly giving up and hallucinating a short, generic-looking
answer instead of processing every real record. A handful of records per
call is well within what it reliably tracks. The check task's guardrail
(see tasks.make_fraud_guardrail) exists purely as a safety net against
that kind of hallucination - the dataset and policy are not designed to
force a violation, so on a good run it should just pass silently.

There is no LLM-driven "commit" step at all: once a human approves the
draft in the browser, writing it to disk is a single deterministic Python
file write (see below), not an agent call. An earlier version had one
write_file agent call per invoice, which was both needlessly slow for a
demo and a weaker security story than "no agent has a write tool, ever."
Memory-flagging (a real, judgment-free record of which vendors are
flagged) still goes through an agent, because that's the one write that
legitimately benefits from the memory MCP server's read/write tools -
one call per flagged vendor, not batched into a single call (tried
batching everything into one create_entities call; a small local model
reliably drops the required `observations` field on every entity but the
first when asked to build a multi-item array like that, which shows up
as CrewAI endlessly retrying a failing tool call rather than making
progress).

A fresh Agent is built for every single execute_sync call below (see
`fresh_agents`), rather than reusing one Agent object across many calls.
`Agent.agent_executor` is created lazily on first use and then kept on the
Agent instance for its whole lifetime, accumulating that agent's full
conversation history - reusing one Agent across dozens of calls meant each
later call's prompt silently included every earlier call's entire
exchange, which is both why later batches got slower and slower and why
supposedly single-invoice commit calls were still acting on invoices from
several calls ago. A fresh Agent per call costs nothing (it's just an
object with no LLM call at construction time) and guarantees each call
only ever sees the context this run explicitly gives it.
"""

from __future__ import annotations

import asyncio
import json

from crewai_tools import MCPServerAdapter

from dashboard.hub import hub

from . import config, mcp_servers as srv
from .agents import build_agents
from .events import DashboardEventListener
from .tasks import (
    build_check_task,
    build_draft_task,
    build_fetch_task,
    build_memory_dump_task,
    build_memory_flag_task,
    build_report_task,
)

APPROVAL_TIMEOUT_S = 1800  # safety cap, not a target - a live demo clicks within seconds
BATCH_SIZE = 6  # small enough for a local 7B model to reliably handle every record in one call


class ThreadBridge:
    """Runs a coroutine on the asyncio loop from this (plain, blocking)
    background thread and waits for it to finish."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def push(self, coro) -> None:
        asyncio.run_coroutine_threadsafe(coro, self._loop).result()


def _load_invoices() -> list[dict]:
    path = config.INVOICES_DIR / "invoices.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _chunks(items: list, size: int) -> list[list]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def run_pipeline(loop: asyncio.AbstractEventLoop) -> tuple[list[dict], str]:
    """Returns (committed decisions, raw memory-graph dump text). Decisions
    is [] if the reviewer rejected the draft."""
    bridge = ThreadBridge(loop)
    DashboardEventListener(loop)  # subscribes itself to CrewAI's global event bus

    invoices = _load_invoices()
    bridge.push(hub.push_transactions(invoices))
    bridge.push(hub.push_stage("fetching"))

    with MCPServerAdapter(srv.read_only_invoices_server(), connect_timeout=60) as invoice_tools, \
         MCPServerAdapter(srv.decisions_server(), connect_timeout=60) as decisions_tools, \
         MCPServerAdapter(srv.memory_server(), connect_timeout=60) as memory_tools:

        read_invoices_tools = srv.filter_tools(invoice_tools, srv.READ_ONLY_TOOL_NAMES)
        read_decisions_tools = srv.filter_tools(decisions_tools, srv.READ_DECISIONS_TOOL_NAMES)
        memory_tools_list = list(memory_tools)
        read_memory_tools = srv.filter_tools(memory_tools, srv.READ_MEMORY_TOOL_NAMES)

        def fresh_agents():
            return build_agents(
                read_invoices_tools=read_invoices_tools,
                read_decisions_tools=read_decisions_tools,
                memory_tools=memory_tools_list,
                read_memory_tools=read_memory_tools,
            )

        intake_agent = fresh_agents()["intake"]
        build_fetch_task(intake_agent).execute_sync(agent=intake_agent)

        # --- fraud check, in batches ---------------------------------------
        bridge.push(hub.push_stage("checking"))
        invoice_batches = _chunks(invoices, BATCH_SIZE)
        all_checks = []
        for i, batch in enumerate(invoice_batches, start=1):
            bridge.push(hub.push_log({"kind": "batch", "status": "checking", "name": f"batch {i}/{len(invoice_batches)} ({len(batch)} invoices)"}))
            fraud_agent = fresh_agents()["fraud"]
            out = build_check_task(fraud_agent, batch).execute_sync(
                agent=fraud_agent, context=json.dumps(batch, indent=2)
            )
            all_checks.extend(out.pydantic.checks if out.pydantic else [])
            bridge.push(hub.push_classifications([c.model_dump() for c in all_checks]))

        # --- draft, in the same batches -------------------------------------
        bridge.push(hub.push_stage("drafting"))
        check_batches = _chunks(all_checks, BATCH_SIZE)
        all_draft = []
        for i, batch in enumerate(check_batches, start=1):
            bridge.push(hub.push_log({"kind": "batch", "status": "drafting", "name": f"batch {i}/{len(check_batches)}"}))
            batch_json = json.dumps([c.model_dump() for c in batch], indent=2)
            approver_agent = fresh_agents()["approver"]
            out = build_draft_task(approver_agent).execute_sync(agent=approver_agent, context=batch_json)
            all_draft.extend(out.pydantic.decisions if out.pydantic else [])
        draft_dicts = [d.model_dump() for d in all_draft]

        hub.approval_gate.reset()
        bridge.push(hub.push_draft(draft_dicts))
        decision = hub.approval_gate.wait(timeout=APPROVAL_TIMEOUT_S)

        if decision != "approve":
            bridge.push(hub.push_log({"kind": "approval", "status": "rejected", "feedback": hub.approval_gate.feedback}))
            bridge.push(hub.push_stage("rejected"))
            return [], ""

        bridge.push(hub.push_log({"kind": "approval", "status": "approved"}))

        # --- commit: one deterministic bulk write, no agent involved ------
        # The draft has already been reviewed and approved by a human;
        # writing it to disk needs no judgment, so no agent touches it at
        # all. No agent in this crew has a write tool for decisions/ at any
        # point - the only way a decision record is ever created is this
        # direct write, gated on the human's Approve click above. That's a
        # stronger least-privilege story than an LLM-issued write_file
        # call, and it's instant instead of one LLM round-trip per invoice.
        bridge.push(hub.push_stage("committing"))
        config.DECISIONS_DIR.mkdir(parents=True, exist_ok=True)
        (config.DECISIONS_DIR / "decisions.json").write_text(json.dumps(draft_dicts, indent=2), encoding="utf-8")
        committed_dicts = draft_dicts
        bridge.push(hub.push_committed(committed_dicts))
        bridge.push(hub.push_log({"kind": "batch", "status": "committed", "name": f"wrote {len(committed_dicts)} decision(s) to decisions.json"}))

        # --- memory: one agent call per flagged vendor --------------------
        # Which vendors qualify is decided in Python, not left to the LLM.
        # Deliberately one call each, not batched into one - see
        # build_memory_flag_task's docstring for why a single bulk call
        # doesn't work with a small local model.
        flagged = [d for d in all_draft if d.decision == "Hold for Review"]
        for i, decision_obj in enumerate(flagged, start=1):
            bridge.push(hub.push_log({"kind": "batch", "status": "recording memory", "name": f"{i}/{len(flagged)}: {decision_obj.vendor_id}"}))
            context_text = (
                f"vendor_id: {decision_obj.vendor_id}\n"
                f"observation to add: \"Flagged for review on {decision_obj.id}: {decision_obj.justification}\""
            )
            memory_agent = fresh_agents()["approver"]
            build_memory_flag_task(memory_agent).execute_sync(agent=memory_agent, context=context_text)

        bridge.push(hub.push_stage("reporting"))
        reporter_agent = fresh_agents()["reporter"]
        report_output = build_report_task(reporter_agent).execute_sync(
            agent=reporter_agent, context="All decisions above have been committed to the decisions directory."
        )
        # The numeric header is computed in Python, not asked of the LLM -
        # a small model reliably enumerates every Hold-for-Review case
        # correctly (verified: it lists every one with an accurate
        # justification) but is not reliable at then also counting its own
        # list correctly in a separate summary line. Same principle as
        # `flagged` above: Python owns the count, the LLM owns the prose.
        approve_count = sum(1 for d in all_draft if d.decision == "Approve")
        header = (
            "### Audit Summary\n\n"
            f"- **Total Invoices:** {len(all_draft)}\n"
            f"- **Approve:** {approve_count}\n"
            f"- **Hold for Review:** {len(flagged)}\n\n"
            "### Hold-for-Review Cases\n\n"
        )
        bridge.push(hub.push_report(header + report_output.raw))

        # A one-shot verbatim dump of everything recorded in memory during
        # this run, handed to the Insights chat agent (main.py) as extra
        # text context so it can answer questions like "which vendors were
        # flagged more than once?" using the analysis actually stored
        # during the run - not just the final decisions list. The read-only
        # `read_graph` tool is the only thing this agent is given (see
        # mcp_servers.READ_MEMORY_TOOL_NAMES), and it never touches the
        # chat LLM itself - see insights.py for why.
        memory_reader_agent = fresh_agents()["memory_reader"]
        memory_dump = build_memory_dump_task(memory_reader_agent).execute_sync(agent=memory_reader_agent).raw

    bridge.push(hub.push_stage("done"))
    return draft_dicts, memory_dump
