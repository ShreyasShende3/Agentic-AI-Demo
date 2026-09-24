"""Task builders. Tasks are built individually (not as one upfront list
with constructor-time `context=[...]`) because `pipeline.py` runs each one
via `Task.execute_sync(agent, context=...)` directly instead of
`Crew.kickoff()` - that's what makes a browser-based human-in-the-loop
pause between draft and commit possible. `execute_sync`'s `context`
parameter is a plain string, not a list of Task objects, so `pipeline.py`
builds it from the previous stage's TaskOutput itself.
"""

from __future__ import annotations

from crewai import Agent, Task

from . import config
from .schemas import DecisionBatch, FraudCheckBatch

BANK_CHANGE_THRESHOLD_DAYS = 14
BANK_CHANGE_AMOUNT_FLOOR = 5000
PO_MISMATCH_PCT = 10


def _policy_text() -> str:
    return "\n\n".join(f.read_text(encoding="utf-8") for f in sorted(config.POLICY_KB_DIR.glob("*.md")))


def build_fetch_task(agent: Agent) -> Task:
    return Task(
        name="Fetch invoices",
        description=(
            "First call list_allowed_directories to learn the exact path of your allowed "
            "directory. Then call read_multiple_files with that exact path plus "
            "'/invoices.json' as the single entry in the paths list (do not use read_file or "
            "read_text_file - use read_multiple_files). Report back the exact JSON content - do "
            "not summarize or drop any invoice."
        ),
        expected_output="The verbatim JSON array of invoices.",
        agent=agent,
    )


def _duplicate_ids(invoices: list[dict]) -> set[str]:
    by_number: dict[str, list[str]] = {}
    for inv in invoices:
        by_number.setdefault(inv["invoice_number"], []).append(inv["id"])
    return {inv_id for ids in by_number.values() if len(ids) > 1 for inv_id in ids[1:]}


def make_fraud_guardrail(invoices: list[dict]):
    """A real CrewAI Task guardrail (not a hand-rolled check): CrewAI calls
    this after every attempt and automatically retries (feeding the error
    back to the agent) up to `guardrail_max_retries` times if it returns
    False. This is a safety net for hallucination, not a scripted demo
    beat - the invoice data isn't engineered to force a violation, so on a
    typical run the model gets it right the first time and this passes
    silently. It only earns its keep on the runs where the model slips.
    The invariants checked here are copied straight from
    policy_kb/ap_fraud_policy.md's "Guardrail invariants" section - they're
    independent of whatever reasoning the agent gives, which is the point:
    a plausible-sounding justification does not get a policy-violating
    decision past this.
    """
    by_id = {t["id"]: t for t in invoices}
    duplicate_ids = _duplicate_ids(invoices)

    # No return-type annotation on purpose: this module uses `from __future__
    # import annotations`, which makes annotations unevaluated strings -
    # CrewAI's guardrail validator calls plain inspect.signature() (no
    # eval_str=True) and can't parse a string annotation's get_args(), so
    # any annotation here fails its "must be Tuple[bool, Any]" check
    # regardless of what it actually says. Leaving it unannotated skips
    # that check entirely (see crewai/task.py's guardrail validator).
    def guardrail(result):
        batch = result.pydantic
        if batch is None:
            return False, "Output did not match the required FraudCheckBatch schema - return valid JSON matching it."
        violations = []
        for c in batch.checks:
            inv = by_id.get(c.id)
            if not inv:
                continue
            if (
                inv["amount"] >= BANK_CHANGE_AMOUNT_FLOOR
                and inv["bank_account_age_days"] <= BANK_CHANGE_THRESHOLD_DAYS
                and c.decision == "Approve"
            ):
                violations.append(
                    f"{c.id}: a ${inv['amount']:.0f} invoice whose bank account changed "
                    f"{inv['bank_account_age_days']} days ago can never be Approve."
                )
            po_amount = inv.get("po_amount")
            if po_amount:
                diff_pct = abs(inv["amount"] - po_amount) / po_amount * 100
                if diff_pct > PO_MISMATCH_PCT and c.decision == "Approve":
                    violations.append(
                        f"{c.id}: invoice amount ${inv['amount']:.0f} differs from PO amount "
                        f"${po_amount:.0f} by more than {PO_MISMATCH_PCT}% - cannot be Approve."
                    )
            if c.id in duplicate_ids and c.decision == "Approve":
                violations.append(f"{c.id}: invoice_number {inv['invoice_number']} duplicates an earlier invoice - cannot be Approve.")
        if violations:
            return False, "Guardrail invariant(s) violated - fix these specific decisions:\n" + "\n".join(violations)
        return True, batch

    return guardrail


def build_check_task(agent: Agent, invoices: list[dict]) -> Task:
    return Task(
        name="Screen invoices for fraud",
        description=(
            "You have been given a JSON array of vendor invoices. Here is the FULL text of the "
            "AP fraud policy - base every decision strictly on it, do not invent thresholds or "
            "clauses that are not written here:\n\n"
            f"{_policy_text()}\n\n"
            "For EVERY invoice above, decide Approve or Hold for Review, copying through its id, "
            "vendor_id, vendor_name, amount, and invoice_number exactly as given, and quoting "
            "the specific policy clause that applies in your justification. If you are unsure "
            "which clause applies to a specific detail, you may also use the 'policy_lookup' "
            "tool to search the same policy text for a more targeted passage."
        ),
        expected_output="A decision for every invoice id in the input, each citing an actual clause from the policy text given above.",
        agent=agent,
        output_pydantic=FraudCheckBatch,
        guardrail=make_fraud_guardrail(invoices),
        guardrail_max_retries=2,
    )


def build_draft_task(agent: Agent) -> Task:
    return Task(
        name="Draft payment decisions",
        description=(
            "You have fraud checks for every invoice. Do NOT write any files yet. For each "
            "invoice, use the memory tools (search_nodes) to check whether this vendor_id has a "
            "prior flagged invoice recorded from an earlier run - if so, set "
            "repeat_flagged_vendor to true and note it. Produce your draft decision for every "
            "invoice, copying through id, vendor_id, vendor_name, amount, invoice_number, "
            "decision, and justification, plus repeat_flagged_vendor. This draft is for "
            "AP-manager review before anything is committed - Hold-for-Review cases in "
            "particular must not be finalized without that sign-off."
        ),
        expected_output="A draft decision for every invoice id, ready for human review.",
        agent=agent,
        output_pydantic=DecisionBatch,
    )


def build_memory_flag_task(agent: Agent) -> Task:
    """Records one flagged vendor in memory. Which vendors qualify is
    decided in Python (pipeline.py), not left to the LLM. One call per
    vendor, deliberately not batched: asking a small local model to build
    a single create_entities call covering several entities at once means
    it has to emit a multi-item array where every item independently
    needs a nested `observations` list - tried, and it reliably dropped
    that field for every entity but the first, which reads as CrewAI
    endlessly retrying a failed tool call ("stuck") rather than
    progressing. One entity per call has no such nested-array shape, so
    it doesn't hit that failure mode."""
    return Task(
        name="Record flagged vendor in memory",
        description=(
            "Use create_entities to create an entity for the vendor_id given above (entityType "
            "'Vendor') if one doesn't already exist, then use add_observations to add the "
            "observation described above to that entity. Do not call write_file or any other "
            "tool."
        ),
        expected_output="Confirmation that the vendor was recorded as flagged in memory.",
        agent=agent,
    )


def build_report_task(agent: Agent) -> Task:
    return Task(
        name="Write audit report",
        description=(
            "First call list_allowed_directories to learn the exact path of your allowed "
            "directory. Then call read_multiple_files with that exact path plus "
            "'/decisions.json' as the single entry in the paths list (do not use read_file or "
            "read_text_file - use read_multiple_files). It contains a JSON array with one entry "
            "per invoice. A numeric summary (totals and counts) is added separately after your "
            "report, so do NOT state any totals or counts yourself. Produce only a numbered list "
            "of every Hold-for-Review case, each with its invoice id copied EXACTLY as written "
            "(e.g. 'inv_010', not 'inv_10' - do not drop leading zeros), vendor name, and "
            "justification - nothing else."
        ),
        expected_output="A numbered list of every Hold-for-Review case with its justification, and nothing else.",
        agent=agent,
    )


def build_memory_dump_task(agent: Agent) -> Task:
    return Task(
        name="Read memory graph",
        description="Call read_graph exactly once and report back the full raw JSON result verbatim. Do not summarize, filter, or omit any entity or observation. Do not call any other tool.",
        expected_output="The verbatim JSON output of read_graph.",
        agent=agent,
    )
