from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class FraudCheck(BaseModel):
    id: str = Field(description="invoice id, e.g. inv_003")
    vendor_id: str
    vendor_name: str
    amount: float
    invoice_number: str
    decision: Literal["Approve", "Hold for Review"]
    justification: str = Field(description="one or two sentences citing the specific policy clause")


class FraudCheckBatch(BaseModel):
    checks: list[FraudCheck]


class Decision(BaseModel):
    id: str
    vendor_id: str
    vendor_name: str
    amount: float
    invoice_number: str
    decision: Literal["Approve", "Hold for Review"]
    justification: str
    repeat_flagged_vendor: bool = Field(description="true if this vendor has a prior flagged invoice in memory")


class DecisionBatch(BaseModel):
    decisions: list[Decision]


class ChartSpec(BaseModel):
    """Deliberately flat (one series, no nested objects) - a small local
    model's structured tool-calling is far more reliable at this shape
    than at a nested list-of-objects one, and every question this demo
    asks for ("breakdown by X", "amount by Y") only ever needs one series
    anyway. x_label/y_label are plain strings for the same reason - axis
    titles, not a nested axis object."""

    chart_type: Literal["bar", "pie", "none"]
    title: str = ""
    x_label: str = Field(default="", description="what the x-axis / category labels represent, e.g. 'Invoice ID'")
    y_label: str = Field(default="", description="what the values represent, e.g. 'Amount (USD)'")
    labels: list[str] = Field(default_factory=list)
    values: list[float] = Field(default_factory=list)


class InsightResponse(BaseModel):
    answer: str = Field(default="", description="a short, direct natural-language answer to the question")
    chart: ChartSpec | None = Field(default=None, description="omit unless a chart genuinely helps answer the question")
