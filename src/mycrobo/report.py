"""Output renderers: text (terminal), JSON, Markdown and SARIF."""

from __future__ import annotations

import json
import os
import sys
from itertools import groupby

from mycrobo import __version__
from mycrobo.models import SCHEMA_VERSION, Category, Finding, ScanResult, Severity

_COLORS = {
    Severity.CRITICAL: "\033[1;35m",
    Severity.HIGH: "\033[1;31m",
    Severity.MEDIUM: "\033[33m",
    Severity.LOW: "\033[36m",
    Severity.INFO: "\033[2m",
}
_BOLD, _DIM, _RESET = "\033[1m", "\033[2m", "\033[0m"


def use_color(stream=sys.stdout) -> bool:
    if not stream.isatty() or "NO_COLOR" in os.environ:
        return False
    return os.name != "nt" or _enable_windows_ansi()


def _enable_windows_ansi() -> bool:
    """Classic Windows consoles print ANSI codes literally unless virtual-terminal mode is on.

    Turn it on for stdout; if Windows refuses (very old console), report no color so the
    output stays plain text instead of showing escape codes like `←[1m`.
    """
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except (AttributeError, OSError):
        return False


def render_json(result: ScanResult) -> str:
    payload = {
        "tool": "mycrobo",
        "version": __version__,
        "schema_version": SCHEMA_VERSION,
        "root": result.root,
        "summary": {
            "files_scanned": result.files_scanned,
            "total_findings": len(result.findings),
            "suppressed": result.suppressed,
            "by_severity": result.counts_by_severity(),
            "by_category": result.counts_by_category(),
        },
        "slop_score": result.score().to_dict(),
        "ai": result.ai.to_dict() if result.ai else None,
        "findings": [f.to_dict() for f in result.findings],
    }
    return json.dumps(payload, indent=2) + "\n"


def render_text(result: ScanResult, color: bool = False) -> str:
    def c(code: str, text: str) -> str:
        return f"{code}{text}{_RESET}" if color else text

    scope = f" (changes since {result.diff_base})" if result.diff_base else ""
    out: list[str] = [c(_BOLD, f"MyCrobo {__version__}") + f"  scanned {result.files_scanned} files in {result.root}{scope}", ""]

    if not result.findings:
        out.append("No findings.")
    by_category = sorted(result.findings, key=lambda f: list(Category).index(f.category))
    for category, group in groupby(by_category, key=lambda f: f.category):
        items = list(group)
        out.append(c(_BOLD, f"{category.label} ({len(items)})"))
        for f in items:
            sev = c(_COLORS[f.severity], f"{f.severity.value.upper():<8}")
            loc = f"{f.file}:{f.line_start}"
            tag = c(_BOLD, " AI") if f.origin == "ai" else ""
            out.append(f"  {sev}{tag} {loc}  {f.message}  " + c(_DIM, f"[{f.rule}] {f.id}"))
            out.append(c(_DIM, f"           {f.evidence[:160]}"))
            if f.origin == "ai" or (result.ai and f.explanation):
                out.append(f"           why: {f.explanation[:300]}")
            if f.suggested_fix:
                out.append(f"           fix: {f.suggested_fix[:300]}")
        out.append("")

    counts = ", ".join(f"{n} {sev}" for sev, n in result.counts_by_severity().items() if n)
    summary = f"{len(result.findings)} findings" + (f" ({counts})" if counts else "")
    if result.suppressed:
        summary += f", {result.suppressed} suppressed"
    out.append(c(_BOLD, summary))
    score = result.score()
    out.append(c(_BOLD, f"Slop Score: {score.value}/100 ({score.label})") + f"  over {result.lines_of_code:,} lines of code")
    if result.ai:
        a = result.ai
        out.append(
            f"AI review ({a.model}): {a.files_reviewed} files reviewed, {a.confirmed} confirmed, "
            f"{a.dismissed} dismissed as false positives, {a.added} new issues "
            f"({a.unverified} unverifiable claims discarded), cost ${a.cost_usd:.3f}"
            + (f", {a.cached} from cache" if a.cached else "")
        )
        if a.model == "external" and a.deferred and not a.files_reviewed:
            out.append("AI review: requests written; answer each *.request.json with a matching "
                       "*.response.json, then rerun with --ai-import")
        problems = [f"{n} {label}" for n, label in ((a.files_skipped_budget, "skipped (budget)"),
                    (a.refusals, "declined by the model"), (a.errors, "failed"),
                    (a.deferred, "awaiting an external answer")) if n]
        if problems:
            out.append("AI review: " + ", ".join(problems) + " - those files kept rule-only results")
        for note in a.notes:
            out.append(c(_DIM, f"  note: {note}"))
    return "\n".join(out) + "\n"


# --- Markdown (PR summary comment, GitHub step summary, --format md) ---------------

SUMMARY_MARKER = "<!-- mycrobo-summary -->"
_ICONS = {Severity.CRITICAL: "🔴", Severity.HIGH: "🟠", Severity.MEDIUM: "🟡", Severity.LOW: "🔵", Severity.INFO: "⚪"}


def _md_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def render_markdown(result: ScanResult, title: str = "MyCrobo review", max_rows: int = 15, link=None) -> str:
    """`link(finding) -> url` turns locations into links (used on GitHub)."""
    score = result.score()
    out = [SUMMARY_MARKER, f"## {title}", ""]
    out.append(f"**Slop Score: {score.value}/100 ({score.label})** · {len(result.findings)} findings "
               f"in {result.files_scanned} files ({result.lines_of_code:,} lines of code)")
    out.append("")
    if not result.findings:
        out.append("No issues found. ✨")
    else:
        sev = result.counts_by_severity()
        cat = result.counts_by_category()
        out.append("| Severity | Count | | Category | Count |")
        out.append("|---|---|---|---|---|")
        cats = [(c.label, cat[c.value]) for c in Category]
        sevs = [(f"{_ICONS[s]} {s.value}", sev[s.value]) for s in sorted(Severity, key=lambda s: -s.rank)]
        for i in range(max(len(sevs), len(cats))):
            left = sevs[i] if i < len(sevs) else ("", "")
            right = cats[i] if i < len(cats) else ("", "")
            out.append(f"| {left[0]} | {left[1]} | | {right[0]} | {right[1]} |")
        out.append("")
        out.append("| | Location | Issue | Rule |")
        out.append("|---|---|---|---|")
        for f in result.findings[:max_rows]:
            loc = f"{f.file}:{f.line_start}"
            loc = f"[`{loc}`]({link(f)})" if link else f"`{loc}`"
            ai = " · AI" if f.origin == "ai" else ""
            out.append(f"| {_ICONS[f.severity]} | {loc} | {_md_cell(f.message)} | `{f.rule}`{ai} |")
        if len(result.findings) > max_rows:
            out.append(f"\n…and {len(result.findings) - max_rows} more. Full details in the SARIF report / code scanning.")
    if result.ai:
        a = result.ai
        out.append("")
        out.append(f"<sub>AI review ({a.model}): {a.files_reviewed} files, {a.confirmed} confirmed, "
                   f"{a.dismissed} dismissed, {a.added} new issues, {a.unverified} unverifiable claims discarded, "
                   f"cost ${a.cost_usd:.3f}</sub>")
    out.append("")
    out.append("<sub>Suppress a finding with a `mycrobo-ignore <rule-id>` comment on or above the line.</sub>")
    return "\n".join(out) + "\n"


def finding_comment(f: Finding) -> str:
    """Body of an inline PR review comment. The marker lets reruns skip already-posted findings."""
    ai = " · found by AI review" if f.origin == "ai" else ""
    parts = [f"<!-- mycrobo:{f.id} -->",
             f"{_ICONS[f.severity]} **{_md_cell(f.message)}** ({f.severity.value}, `{f.rule}`{ai})"]
    if f.explanation:
        parts.append("")
        parts.append(f.explanation)
    if f.suggested_fix:
        parts.append("")
        parts.append(f"**Fix:** {f.suggested_fix}")
    return "\n".join(parts)


# --- SARIF 2.1.0 (GitHub code scanning, IDEs, other SAST tooling) ------------------

_SARIF_LEVEL = {Severity.CRITICAL: "error", Severity.HIGH: "error", Severity.MEDIUM: "warning",
                Severity.LOW: "note", Severity.INFO: "note"}
# GitHub ranks security alerts by this 0-10 property.
_SECURITY_SEVERITY = {Severity.CRITICAL: "9.5", Severity.HIGH: "8.0", Severity.MEDIUM: "5.5",
                      Severity.LOW: "3.0", Severity.INFO: "1.0"}


def render_sarif(result: ScanResult) -> str:
    from mycrobo.rules import all_rules

    rule_meta = {r.id: r for r in all_rules()}
    rule_ids = sorted({f.rule for f in result.findings} | set(rule_meta))
    index = {rid: i for i, rid in enumerate(rule_ids)}

    rules = []
    for rid in rule_ids:
        meta = rule_meta.get(rid)
        category = meta.category.value if meta else rid.split("/")[1].replace("-", "_")
        description = meta.description if meta else f"Issue found by the AI review ({category.replace('_', ' ')})."
        severity = meta.default_severity if meta else Severity.MEDIUM
        props: dict = {"tags": ["mycrobo", category] + (["security"] if category == "security" else [])}
        if category == "security":
            props["security-severity"] = _SECURITY_SEVERITY[severity]
        rule: dict = {
            "id": rid,
            "name": rid.replace("/", "-"),
            "shortDescription": {"text": description},
            "fullDescription": {"text": (meta.explanation if meta and meta.explanation else description)},
            "defaultConfiguration": {"level": _SARIF_LEVEL[severity]},
            "properties": props,
        }
        if meta and meta.fix:
            rule["help"] = {"text": meta.fix, "markdown": meta.fix}
        rules.append(rule)

    results = []
    for f in result.findings:
        text = f.message + (f"\n\n{f.explanation}" if f.explanation else "") + (
            f"\n\nFix: {f.suggested_fix}" if f.suggested_fix else "")
        props = {"severity": f.severity.value, "confidence": f.confidence.value,
                 "category": f.category.value, "origin": f.origin}
        if f.category is Category.SECURITY:
            props["security-severity"] = _SECURITY_SEVERITY[f.severity]
        results.append({
            "ruleId": f.rule,
            "ruleIndex": index[f.rule],
            "level": _SARIF_LEVEL[f.severity],
            "message": {"text": text},
            "locations": [{
                "physicalLocation": {
                    "artifactLocation": {"uri": f.file, "uriBaseId": "%SRCROOT%"},
                    "region": {"startLine": f.line_start, "endLine": max(f.line_end, f.line_start),
                               "snippet": {"text": f.evidence}},
                }
            }],
            "partialFingerprints": {"mycrobo/v1": f.id},
            "properties": props,
        })

    score = result.score()
    sarif = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {"name": "MyCrobo", "version": __version__, "semanticVersion": __version__,
                                "rules": rules}},
            "automationDetails": {"id": "mycrobo/"},
            "results": results,
            "properties": {"slopScore": score.to_dict(), "linesOfCode": result.lines_of_code},
        }],
    }
    return json.dumps(sarif, indent=2) + "\n"
