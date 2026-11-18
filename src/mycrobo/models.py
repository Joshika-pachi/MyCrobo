"""Core data model: the finding schema every rule and output format shares."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mycrobo.ai.review import AiSummary

SCHEMA_VERSION = "0.1"


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return _SEVERITY_ORDER.index(self)

    def __ge__(self, other: object) -> bool:  # type: ignore[override]
        if not isinstance(other, Severity):
            return NotImplemented
        return self.rank >= other.rank

    def __lt__(self, other: object) -> bool:  # type: ignore[override]
        if not isinstance(other, Severity):
            return NotImplemented
        return self.rank < other.rank


_SEVERITY_ORDER = [Severity.INFO, Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL]


class Category(str, Enum):
    SECURITY = "security"
    SLOP = "slop"
    LOOSE_ENDS = "loose_ends"
    CODING_GAPS = "coding_gaps"

    @property
    def label(self) -> str:
        return {
            Category.SECURITY: "Security",
            Category.SLOP: "Slop",
            Category.LOOSE_ENDS: "Loose ends",
            Category.CODING_GAPS: "Coding gaps",
        }[self]


class Confidence(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class Finding:
    rule: str
    category: Category
    severity: Severity
    confidence: Confidence
    file: str  # path relative to the scan root, forward slashes
    line_start: int  # 1-based
    line_end: int
    message: str  # one-line title
    evidence: str  # the offending source text
    explanation: str = ""
    suggested_fix: str = ""
    origin: str = "rule"  # "rule" (deterministic) or "ai" (found by the LLM review)

    @property
    def id(self) -> str:
        """Stable id: survives line shifts because it hashes content, not position."""
        key = f"{self.rule}|{self.file}|{self.evidence.strip()}"
        return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "rule": self.rule,
            "category": self.category.value,
            "severity": self.severity.value,
            "confidence": self.confidence.value,
            "file": self.file,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "message": self.message,
            "evidence": self.evidence,
            "explanation": self.explanation,
            "suggested_fix": self.suggested_fix,
            "origin": self.origin,
        }


@dataclass
class ScanResult:
    root: str
    files_scanned: int
    findings: list[Finding] = field(default_factory=list)
    suppressed: int = 0
    lines_of_code: int = 0  # non-blank lines in code files (diff mode: added lines), for the Slop Score
    diff_base: str | None = None  # set in diff mode
    changed: dict | None = field(default=None, repr=False)  # diff mode: file -> changed lines
    ai: "AiSummary | None" = None  # set when the LLM review ran

    def counts_by_severity(self) -> dict[str, int]:
        counts = {s.value: 0 for s in reversed(_SEVERITY_ORDER)}
        for f in self.findings:
            counts[f.severity.value] += 1
        return counts

    def counts_by_category(self) -> dict[str, int]:
        counts = {c.value: 0 for c in Category}
        for f in self.findings:
            counts[f.category.value] += 1
        return counts

    def score(self):
        from mycrobo.score import slop_score

        return slop_score(self.findings, self.lines_of_code)

    def max_severity(self) -> Severity | None:
        return max((f.severity for f in self.findings), key=lambda s: s.rank, default=None)
