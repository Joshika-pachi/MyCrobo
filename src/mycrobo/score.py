"""Slop Score: one 0-100 number for how much attention a codebase (or PR) needs.

    points  = sum(SEVERITY_POINTS[severity] * CONFIDENCE_WEIGHT[confidence])   per finding
    density = points / max(KLOC, 0.5)          # per 1,000 non-blank lines of code
    score   = 100 * (1 - e^(-density / 20))    # saturates smoothly towards 100

A credible critical finding (confidence medium or high) floors the score at 75:
a leaked key or an injection is not diluted by a large, otherwise clean repo.
0 = nothing found. The formula is deliberately simple so anyone can check it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from mycrobo.models import Confidence, Finding, Severity

SEVERITY_POINTS = {
    Severity.CRITICAL: 10.0, Severity.HIGH: 5.0, Severity.MEDIUM: 2.0, Severity.LOW: 0.5, Severity.INFO: 0.0,
}
CONFIDENCE_WEIGHT = {Confidence.HIGH: 1.0, Confidence.MEDIUM: 0.75, Confidence.LOW: 0.4}
SCALE = 20.0
CRITICAL_FLOOR = 75
MIN_KLOC = 0.5

_LABELS = [(10, "Clean"), (25, "Minor"), (50, "Sloppy"), (75, "Risky"), (101, "Critical")]


@dataclass(frozen=True)
class SlopScore:
    value: int
    label: str
    points: float
    kloc: float

    def to_dict(self) -> dict:
        return {"value": self.value, "label": self.label, "points": round(self.points, 2), "kloc": round(self.kloc, 2)}


def slop_score(findings: list[Finding], lines_of_code: int) -> SlopScore:
    points = sum(SEVERITY_POINTS[f.severity] * CONFIDENCE_WEIGHT[f.confidence] for f in findings)
    kloc = lines_of_code / 1000
    value = round(100 * (1 - math.exp(-(points / max(kloc, MIN_KLOC)) / SCALE)))
    if any(f.severity is Severity.CRITICAL and f.confidence is not Confidence.LOW for f in findings):
        value = max(value, CRITICAL_FLOOR)
    label = next(name for limit, name in _LABELS if value < limit)
    return SlopScore(value=value, label=label, points=points, kloc=kloc)
