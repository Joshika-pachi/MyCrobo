"""Structured output the model must return for one file (validated by the SDK)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SeverityName = Literal["critical", "high", "medium", "low", "info"]


class Verdict(BaseModel):
    """Judgement on one candidate finding produced by the deterministic rules."""

    finding_id: str = Field(description="The id of the candidate finding being judged, copied exactly.")
    verdict: Literal["confirmed", "false_positive", "uncertain"]
    severity: SeverityName = Field(description="Severity in the context of this repository.")
    explanation: str = Field(description="One or two sentences: why this matters (or why it is a false positive) here.")
    suggested_fix: str = Field(description="A concrete fix: a short code change or precise instruction. Empty if false_positive.")


class NewIssue(BaseModel):
    """An issue the rules could not express, found by reading the file."""

    category: Literal["security", "slop", "loose_ends", "coding_gaps"]
    title: str = Field(description="Short title, under 80 characters.")
    severity: SeverityName
    line_start: int
    line_end: int
    evidence: str = Field(description="1-3 lines copied verbatim from the file (without the line-number prefix).")
    explanation: str
    suggested_fix: str


class FileReview(BaseModel):
    verdicts: list[Verdict]
    new_issues: list[NewIssue]
