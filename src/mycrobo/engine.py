"""Scan pipeline: discover -> run rules -> suppress -> filter -> sort.

The LLM reasoning and verification stages from the PRD (sections 8.3-8.5) plug in
between rule execution and filtering in later milestones.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from mycrobo.ai.client import Reviewer
from mycrobo.ai.review import AiOptions
from mycrobo.diff import WHOLE_FILE, changed_lines, touches
from mycrobo.discover import SourceFile, discover
from mycrobo.models import Finding, ScanResult, Severity
from mycrobo.rules import RepoRule, Rule, all_rules
from mycrobo.text import CODE_LANGUAGES

# `mycrobo-ignore` alone suppresses everything; `mycrobo-ignore rule-a, rule-b` only those rules.
_IGNORE_RE = re.compile(r"mycrobo-ignore(?:[:\s]+([\w./,\s-]+))?")

Suppressions = dict[int, "set[str] | None"]


@dataclass
class ScanOptions:
    min_severity: Severity = Severity.INFO
    ai: AiOptions | None = None  # None = deterministic rules only (offline, free)
    diff_base: str | None = None  # report only lines changed since this git ref (PR mode)


def _suppressions(source: SourceFile) -> Suppressions:
    """Map line number -> suppressed rule ids (None = all). Covers the same and next line."""
    result: Suppressions = {}
    for lineno, line in enumerate(source.lines, start=1):
        m = _IGNORE_RE.search(line)
        if not m:
            continue
        rules = {r for r in re.split(r"[,\s]+", m.group(1) or "") if "/" in r} or None
        for target in (lineno, lineno + 1):
            existing = result.get(target, set())
            result[target] = None if rules is None or existing is None else existing | rules
    return result


def _is_suppressed(finding: Finding, suppressions: Suppressions) -> bool:
    if finding.line_start not in suppressions:
        return False
    rules = suppressions[finding.line_start]
    return rules is None or finding.rule in rules


def _readable(files: list[SourceFile]) -> list[SourceFile]:
    ok = []
    for source in files:
        try:
            source.lines  # noqa: B018 - load once, surface I/O errors here
        except OSError as exc:
            print(f"mycrobo: skipping {source.rel_path}: {exc}", file=sys.stderr)
            continue
        ok.append(source)
    return ok


def scan(
    root: Path,
    options: ScanOptions | None = None,
    rules: list[Rule | RepoRule] | None = None,
    reviewer: Reviewer | None = None,
) -> ScanResult:
    options = options or ScanOptions()
    rules = rules if rules is not None else all_rules()
    files = _readable(discover(root))
    suppressions = {f.rel_path: _suppressions(f) for f in files}

    raw: list[Finding] = []
    for rule in rules:
        if isinstance(rule, RepoRule):
            raw.extend(rule.check_repo(files))
        else:
            for source in files:
                if rule.applies_to(source):
                    raw.extend(rule.check(source))

    findings, suppressed = _unsuppressed(raw, suppressions)

    changed = None
    review_files = files
    if options.diff_base:
        # Files are analysed in full (rules need context); only changed lines are reported.
        changed = changed_lines(Path(root), options.diff_base)
        findings = [f for f in findings if touches(changed, f.file, f.line_start, f.line_end)]
        review_files = [f for f in files if f.rel_path in changed]

    ai_summary = None
    if options.ai is not None:
        from mycrobo.ai.review import ai_review

        if reviewer is None:
            from mycrobo.ai.client import AnthropicReviewer, CachedReviewer

            reviewer = CachedReviewer(AnthropicReviewer(options.ai.model, options.ai.effort), options.ai.effort)
        # Severity filtering waits until after triage: the AI may raise a low finding's severity.
        findings, ai_summary = ai_review(review_files, findings, options.ai, reviewer)
        findings, more = _unsuppressed(findings, suppressions)  # AI findings honour mycrobo-ignore too
        suppressed += more
        if changed is not None:
            findings = [f for f in findings if touches(changed, f.file, f.line_start, f.line_end)]

    shown = [f for f in findings if f.severity >= options.min_severity]
    ordered = sorted(shown, key=lambda f: (-f.severity.rank, f.category.value, f.file, f.line_start))
    return ScanResult(root=str(Path(root).resolve()), files_scanned=len(files), findings=ordered,
                      suppressed=suppressed, lines_of_code=_lines_of_code(files, changed),
                      diff_base=options.diff_base, changed=changed, ai=ai_summary)


def _lines_of_code(files: list[SourceFile], changed: dict | None) -> int:
    """Non-blank code lines; in diff mode only the lines the change added (the PR's size)."""
    total = 0
    for f in files:
        if f.language not in CODE_LANGUAGES:
            continue
        if changed is None or (f.rel_path in changed and changed[f.rel_path] is WHOLE_FILE):
            total += sum(1 for line in f.lines if line.strip())
        elif f.rel_path in changed:
            total += sum(1 for n in changed[f.rel_path] if 0 < n <= len(f.lines) and f.lines[n - 1].strip())
    return total


def _unsuppressed(raw: Iterable[Finding], suppressions: dict[str, Suppressions]) -> tuple[list[Finding], int]:
    kept: list[Finding] = []
    suppressed = 0
    for finding in raw:
        if _is_suppressed(finding, suppressions.get(finding.file, {})):
            suppressed += 1
        else:
            kept.append(finding)
    return kept, suppressed
