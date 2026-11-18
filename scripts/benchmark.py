"""Score MyCrobo against the slop zoo's answer key: precision, recall, per-issue results.

    uv run python scripts/benchmark.py                          # rules only
    uv run python scripts/benchmark.py --ai-import REVIEW_DIR   # rules + a recorded AI review
    uv run python scripts/benchmark.py --ai                     # rules + live Claude review (needs a key)

Matching: a finding matches an expected issue when it is in the same file, its line range
covers the issue's line (+/- line_tolerance, default 1), and its rule (or, for AI-only
issues, its category) agrees. Each finding counts once; findings matching nothing are
unlisted: they count against precision until a human checks them (so precision is a lower bound).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from mycrobo.ai.client import EXTERNAL_MODEL, ExchangeReviewer
from mycrobo.ai.review import AiOptions
from mycrobo.cli import use_utf8_output
from mycrobo.engine import ScanOptions, scan
from mycrobo.models import Finding

ROOT = Path(__file__).resolve().parent.parent
ZOO = ROOT / "examples" / "slop-zoo"


def locate(zoo: Path, issue: dict) -> int:
    lines = (zoo / issue["file"]).read_text(encoding="utf-8").splitlines()
    hits = [n for n, line in enumerate(lines, 1) if issue["contains"] in line]
    if len(hits) != 1:
        raise SystemExit(f"answer key: {issue['id']} snippet {issue['contains']!r} matches {len(hits)} lines")
    return hits[0]


def matches(f: Finding, issue: dict, line: int) -> bool:
    if f.file != issue["file"]:
        return False
    tol = issue.get("line_tolerance", 1)
    if f.line_end < line - tol or f.line_start > line + tol:
        return False
    if "rule" in issue:
        return f.rule == issue["rule"]
    return f.category.value == issue["category"]


def evaluate(findings: list[Finding], key: dict, zoo: Path) -> dict:
    issues = key["issues"]
    lines = {i["id"]: locate(zoo, i) for i in issues}
    unused = list(findings)
    found: dict[str, Finding] = {}
    # Exact-rule issues first, so a category-only issue can't steal a rule finding.
    for issue in sorted(issues, key=lambda i: "rule" not in i):
        hit = next((f for f in unused if matches(f, issue, lines[issue["id"]])), None)
        if hit:
            found[issue["id"]] = hit
            unused.remove(hit)
    def recall(detector: str | None) -> tuple[int, int]:
        subset = [i for i in issues if detector is None or i["detector"] == detector]
        return sum(i["id"] in found for i in subset), len(subset)
    tp = len(found)
    return {
        "found": found, "lines": lines, "false_positives": unused,
        "precision": tp / len(findings) if findings else 1.0,
        "recall_rules": recall("rules"), "recall_ai": recall("ai"), "recall_all": recall(None),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--zoo", type=Path, default=ZOO)
    ap.add_argument("--ai-import", type=Path, metavar="DIR", help="Apply a recorded review (see --ai-export).")
    ap.add_argument("--ai", action="store_true", help="Live Claude review (needs ANTHROPIC_API_KEY).")
    ap.add_argument("--json", action="store_true", help="Print machine-readable results.")
    args = ap.parse_args(argv)
    use_utf8_output()

    key = json.loads((args.zoo / "expected.json").read_text(encoding="utf-8"))
    options, reviewer = ScanOptions(), None
    if args.ai_import:
        reviewer = ExchangeReviewer(args.ai_import, "import")
        options.ai = AiOptions(model=EXTERNAL_MODEL, effort="medium", max_cost=float("inf"))
    elif args.ai:
        from mycrobo.ai.client import DEFAULT_EFFORT, DEFAULT_MODEL

        options.ai = AiOptions(model=DEFAULT_MODEL, effort=DEFAULT_EFFORT, max_cost=1.0)
    result = scan(args.zoo, options, reviewer=reviewer)
    r = evaluate(result.findings, key, args.zoo)

    if args.json:
        print(json.dumps({
            "precision": r["precision"], "recall_rules": r["recall_rules"], "recall_ai": r["recall_ai"],
            "recall_all": r["recall_all"], "findings": len(result.findings),
            "false_positives": [f"{f.file}:{f.line_start} {f.rule}" for f in r["false_positives"]],
            "missed": [i["id"] for i in key["issues"] if i["id"] not in r["found"]],
            "slop_score": result.score().to_dict(),
        }, indent=2))
        return 0

    mode = "rules + AI review" if options.ai else "rules only"
    print(f"MyCrobo slop-zoo benchmark ({mode})\n")
    for issue in key["issues"]:
        hit = r["found"].get(issue["id"])
        mark = "✓" if hit else "✗"
        via = f"{hit.rule}" if hit else ("needs --ai" if issue["detector"] == "ai" and not options.ai else "MISSED")
        loc = f"{issue['file']}:{r['lines'][issue['id']]}"
        print(f"  {mark} {issue['id']} {loc:<30} {issue['what'][:56]:<56} {via}")
    for f in r["false_positives"]:
        print(f"  ? not in answer key: {f.file}:{f.line_start}  {f.rule}  {f.message[:60]}")
    (rh, rt), (ah, at), (oh, ot) = r["recall_rules"], r["recall_ai"], r["recall_all"]
    print(f"\nPrecision: {r['precision']:.0%} ({len(result.findings) - len(r['false_positives'])}/{len(result.findings)} findings in the answer key; "
          "a lower bound, since findings not in the key count as wrong until someone checks them)")
    print(f"Recall:    rules-detectable {rh}/{rt} · AI-only {ah}/{at} · overall {oh}/{ot} ({oh / ot:.0%})")
    score = result.score()
    print(f"Slop Score: {score.value}/100 ({score.label})")
    if result.ai:
        print(f"AI review: {result.ai.files_reviewed} files, {result.ai.added} new issues, "
              f"{result.ai.unverified} unverifiable claims discarded, {result.ai.dismissed} dismissed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
