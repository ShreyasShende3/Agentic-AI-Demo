from __future__ import annotations

from crewai import LLM, Agent

from . import config
from .tools import policy_lookup


def build_llm() -> LLM:
    return LLM(model=config.CREWAI_LLM_STRING, base_url=config.OLLAMA_BASE_URL, temperature=0.2)


def build_agents(
    read_invoices_tools: list,
    read_decisions_tools: list,
    memory_tools: list,
    read_memory_tools: list | None = None,
) -> dict[str, Agent]:
    llm = build_llm()

    intake_agent = Agent(
        role="Invoice Intake Agent",
        goal="Retrieve the raw invoice batch and hand it off exactly as recorded, with no edits or omissions.",
        backstory=(
            "You work in the AP data intake team. You only ever read from the invoices "
            "directory - you have no ability to write anywhere, by design."
        ),
        tools=read_invoices_tools,
        llm=llm,
        verbose=True,
    )

    fraud_analyst = Agent(
        role="Fraud & Anomaly Analyst",
        goal="Screen each invoice for payment fraud red flags strictly per the AP fraud policy, citing the specific clause.",
        backstory=(
            "You are an accounts-payable fraud analyst. You never guess at policy - you look "
            "it up via the policy lookup tool before deciding anything, and you cite what you "
            "found."
        ),
        tools=[policy_lookup],
        llm=llm,
        verbose=True,
    )

    approver = Agent(
        role="Payment Approver",
        goal=(
            "Check each invoice against previously flagged vendors in memory, draft a decision "
            "for human sign-off, and record any newly-flagged vendor in memory once approved. "
            "You never write a decision file yourself - once a human approves the draft, the "
            "decision is committed automatically, deterministically, and without any further "
            "LLM involvement."
        ),
        backstory=(
            "You are the AP manager of record. You never finalize a payment decision without a "
            "human's explicit approval, and you always check whether this vendor has been "
            "flagged before recording anything."
        ),
        tools=memory_tools,
        llm=llm,
        verbose=True,
    )

    reporter = Agent(
        role="Audit Reporter",
        goal="Read every recorded decision and produce a short audit summary: counts by decision and a list of every Hold-for-Review case with its justification.",
        backstory="You produce the report AP leadership actually reads. You only read decision records - you never alter them.",
        tools=read_decisions_tools,
        llm=llm,
        verbose=True,
    )

    agents = {"intake": intake_agent, "fraud": fraud_analyst, "approver": approver, "reporter": reporter}

    if read_memory_tools is not None:
        agents["memory_reader"] = Agent(
            role="Memory Reader",
            goal="Retrieve everything recorded in memory so far, verbatim, for the insights assistant to use.",
            backstory="You only ever call read_graph and report back exactly what it returns - you never write anything.",
            tools=read_memory_tools,
            llm=llm,
            verbose=True,
        )

    return agents
