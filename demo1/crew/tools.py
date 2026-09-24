"""Custom (non-MCP) CrewAI tools - just the local RAG lookup. Demonstrates
that framework-native tools and MCP-derived tools mix freely in the same
agent's tools list."""

from __future__ import annotations

from crewai.tools import tool

from .rag import PolicyRetriever

_retriever: PolicyRetriever | None = None


def _get_retriever() -> PolicyRetriever:
    global _retriever
    if _retriever is None:
        _retriever = PolicyRetriever()
    return _retriever


@tool("policy_lookup")
def policy_lookup(question: str) -> str:
    """Search the AP fraud policy knowledge base for text relevant to a
    question, e.g. 'is a recently changed bank account on a $6000 invoice
    a red flag?'. Returns the most relevant policy passages."""
    passages = _get_retriever().query(question, k=4)
    if not passages:
        return "No relevant policy text found."
    return "\n\n".join(f"- {p}" for p in passages)
