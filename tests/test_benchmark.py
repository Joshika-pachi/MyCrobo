"""The slop zoo benchmark as a regression guard: rules must keep 100% precision and
recall on what they're designed to catch, and the recorded AI review must stay valid."""

import importlib.util
import json
from pathlib import Path

from mycrobo.ai.client import EXTERNAL_MODEL, ExchangeReviewer
from mycrobo.ai.review import AiOptions
from mycrobo.engine import ScanOptions, scan

ROOT = Path(__file__).resolve().parent.parent
ZOO = ROOT / "examples" / "slop-zoo"
REVIEW = ROOT / "examples" / "slop-zoo-review"

_spec = importlib.util.spec_from_file_location("benchmark", ROOT / "scripts" / "benchmark.py")
benchmark = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(benchmark)
KEY = json.loads((ZOO / "expected.json").read_text())


def test_rules_only_benchmark():
    r = benchmark.evaluate(scan(ZOO).findings, KEY, ZOO)
    assert [f"{f.file}:{f.line_start} {f.rule}" for f in r["false_positives"]] == []  # no trap fires
    assert r["recall_rules"] == (20, 20)
    assert r["recall_ai"] == (0, 5)


def test_recorded_ai_review_still_applies():
    """Fails if the zoo or MyCrobo's prompts changed: re-record with --ai-export (see the zoo README)."""
    options = ScanOptions(ai=AiOptions(model=EXTERNAL_MODEL, effort="medium", max_cost=float("inf")))
    result = scan(ZOO, options, reviewer=ExchangeReviewer(REVIEW, "import"))
    assert result.ai.deferred == 0, "recorded review is stale: regenerate examples/slop-zoo-review"
    assert result.ai.unverified == 0 and result.ai.dismissed == 0
    r = benchmark.evaluate(result.findings, KEY, ZOO)
    assert r["recall_all"] == (25, 25)
    assert len(r["false_positives"]) <= 1  # the known real-but-unlisted /tmp issue
