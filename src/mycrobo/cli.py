"""Command-line entry point.

    mycrobo scan [path]   scan and print a report (text / json / md / sarif)
    mycrobo ci   [path]   CI mode: diff scan, SARIF file, PR review comments + summary
    mycrobo rules         list rules

Exit codes: 0 = no finding at/above --fail-on, 1 = gate failed, 2 = usage/runtime error.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from mycrobo import __version__
from mycrobo.ai.client import DEFAULT_EFFORT, DEFAULT_MODEL, EXTERNAL_MODEL, ExchangeReviewer, ReviewerUnavailable
from mycrobo.ai.review import AiOptions
from mycrobo.diff import DiffError
from mycrobo.engine import ScanOptions, scan
from mycrobo.models import ScanResult, Severity
from mycrobo.report import render_json, render_markdown, render_sarif, render_text, use_color
from mycrobo.rules import all_rules

_SEVERITIES = [s.value for s in Severity]


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("path", nargs="?", default=".", help="Directory to scan (default: current directory).")
    p.add_argument("--severity", choices=_SEVERITIES, default="info", help="Hide findings below this severity.")
    p.add_argument(
        "--fail-on", choices=[*_SEVERITIES, "none"], default="high",
        help="Exit 1 if any finding is at or above this severity (default: high; 'none' never fails).",
    )
    ai = p.add_argument_group("AI review (optional, uses the Claude API; off by default)")
    ai.add_argument("--ai", action="store_true",
                    help="Triage rule findings and look for coding gaps with Claude. Needs ANTHROPIC_API_KEY.")
    ai.add_argument("--model", default=DEFAULT_MODEL, help=f"Claude model (default: {DEFAULT_MODEL}).")
    ai.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"], default=DEFAULT_EFFORT,
                    help=f"Reasoning effort (default: {DEFAULT_EFFORT}).")
    ai.add_argument("--max-cost", type=float, default=1.0, metavar="USD",
                    help="Stop making AI calls once this much would be spent (default: 1.00).")
    ai.add_argument("--max-files", type=int, default=20, help="Review at most this many files with AI (default: 20).")
    ai.add_argument("--no-cache", action="store_true", help="Don't reuse cached AI reviews of unchanged files.")
    exchange = ai.add_mutually_exclusive_group()
    exchange.add_argument("--ai-export", type=Path, metavar="DIR",
                          help="No API key: write review requests to DIR for any model (e.g. Claude Code) to answer.")
    exchange.add_argument("--ai-import", type=Path, metavar="DIR",
                          help="Apply the answers written into DIR after --ai-export.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mycrobo",
        description="Find security risks, AI slop, coding gaps and loose ends in any codebase.",
    )
    parser.add_argument("--version", action="version", version=f"mycrobo {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    s = sub.add_parser("scan", help="Scan a directory and report findings.")
    _add_common(s)
    s.add_argument("--format", choices=["text", "json", "md", "sarif"], default="text",
                   help="Output format (default: text). sarif = GitHub code scanning / SARIF 2.1.0.")
    s.add_argument("--out", type=Path, help="Write the report to this file instead of stdout.")
    s.add_argument("--diff", metavar="BASE", dest="diff_base",
                   help="Only report findings on lines changed since git ref BASE (e.g. origin/main).")

    c = sub.add_parser("ci", help="CI mode: scan a pull request's changes, write SARIF, comment on the PR.")
    _add_common(c)
    c.add_argument("--base", help="Git ref to diff against (default: the PR base from the GitHub event; "
                                  "without one, the whole repository is scanned).")
    c.add_argument("--sarif", type=Path, default=Path("mycrobo.sarif"),
                   help="SARIF output file (default: mycrobo.sarif).")
    c.add_argument("--no-comment", action="store_true", help="Don't post PR comments (SARIF and step summary only).")
    c.add_argument("--max-comments", type=int, default=20, help="Inline comments per run (default: 20).")
    c.add_argument("--dry-run", type=Path, metavar="FILE",
                   help="Don't call GitHub: write the API requests that would be sent to FILE (JSON).")

    sub.add_parser("rules", help="List the available rules.")
    return parser


def cmd_rules() -> int:
    for rule in all_rules():
        print(f"{rule.id:<34} {rule.category.label:<12} {rule.default_severity.value:<8} {rule.description}")
    return 0


def _reviewer(args: argparse.Namespace):
    """(reviewer, AiOptions) for the AI flags, or (None, None) when AI is off or unavailable."""
    exchange_dir = args.ai_export or args.ai_import
    if exchange_dir:
        reviewer = ExchangeReviewer(exchange_dir, "export" if args.ai_export else "import")
        return reviewer, AiOptions(model=EXTERNAL_MODEL, effort=args.effort, max_cost=float("inf"),
                                   max_files=args.max_files)
    if not args.ai:
        return None, None
    from mycrobo.ai.client import AnthropicReviewer, CachedReviewer

    try:
        reviewer = AnthropicReviewer(args.model, args.effort)
    except ReviewerUnavailable as exc:
        print(f"mycrobo: {exc}\nmycrobo: continuing with rules only (set ANTHROPIC_API_KEY to enable --ai)",
              file=sys.stderr)
        return None, None
    if not args.no_cache:
        reviewer = CachedReviewer(reviewer, args.effort)
    return reviewer, AiOptions(model=args.model, effort=args.effort, max_cost=args.max_cost, max_files=args.max_files)


def _run(args: argparse.Namespace, diff_base: str | None) -> ScanResult | None:
    root = Path(args.path)
    if not root.is_dir():
        print(f"mycrobo: not a directory: {root}", file=sys.stderr)
        return None
    reviewer, ai_options = _reviewer(args)
    options = ScanOptions(min_severity=Severity(args.severity), diff_base=diff_base, ai=ai_options)
    try:
        return scan(root, options, reviewer=reviewer)
    except DiffError as exc:
        print(f"mycrobo: cannot diff against {diff_base}: {exc}", file=sys.stderr)
        return None


def _gate(result: ScanResult, fail_on: str) -> int:
    if fail_on == "none":
        return 0
    worst = result.max_severity()
    return 1 if worst is not None and worst >= Severity(fail_on) else 0


def cmd_scan(args: argparse.Namespace) -> int:
    result = _run(args, args.diff_base)
    if result is None:
        return 2
    renderers = {"json": render_json, "md": render_markdown, "sarif": render_sarif}
    if args.format in renderers:
        report = renderers[args.format](result)
    else:
        report = render_text(result, color=args.out is None and use_color())
    if args.out:
        args.out.write_text(report, encoding="utf-8")
        print(f"mycrobo: wrote {len(result.findings)} findings to {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(report)
    return _gate(result, args.fail_on)


def cmd_ci(args: argparse.Namespace) -> int:
    from mycrobo.github import pull_request_from_env

    pr = pull_request_from_env()
    result = _run(args, args.base or (pr.base_sha if pr else None))
    if result is None:
        return 2

    args.sarif.write_text(render_sarif(result), encoding="utf-8")
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as fh:
            fh.write(render_markdown(result, title="MyCrobo review"))
    sys.stdout.write(render_text(result, color=use_color()))
    print(f"mycrobo: SARIF written to {args.sarif}", file=sys.stderr)

    if not args.no_comment:
        _comment_on_pr(args, result, pr)
    return _gate(result, args.fail_on)


def _comment_on_pr(args: argparse.Namespace, result: ScanResult, pr) -> None:
    from mycrobo.github import DryRunHttp, GitHubClient, GitHubError, publish, urllib_http

    if pr is None:
        print("mycrobo: not a pull_request event; skipping PR comments", file=sys.stderr)
        return
    recorder = DryRunHttp() if args.dry_run else None
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token and recorder is None:
        print("mycrobo: GITHUB_TOKEN not set; skipping PR comments", file=sys.stderr)
        return
    client = GitHubClient(token or "dry-run", os.environ.get("GITHUB_API_URL", "https://api.github.com"),
                          http=recorder or urllib_http)
    try:
        rep = publish(result, pr, client, result.changed, args.max_comments,
                      os.environ.get("GITHUB_SERVER_URL", "https://github.com"))
        print(f"mycrobo: PR #{pr.number}: {rep.posted} comments posted, {rep.skipped_existing} already posted, "
              f"{rep.skipped_limit} over the limit, {rep.rejected} rejected; summary {rep.summary}", file=sys.stderr)
    except GitHubError as exc:
        # Typically a fork PR with a read-only token: the scan and SARIF still count.
        print(f"mycrobo: could not comment on the PR ({exc}); results are in the SARIF and step summary",
              file=sys.stderr)
    if recorder is not None:
        args.dry_run.write_text(json.dumps(recorder.requests, indent=2), encoding="utf-8")
        print(f"mycrobo: dry run - {len(recorder.requests)} GitHub API requests written to {args.dry_run}",
              file=sys.stderr)


def use_utf8_output() -> None:
    """Windows redirects stdout/stderr in the legacy code page, which can't encode ✓ or emoji
    (Markdown/benchmark output). Force UTF-8 so `mycrobo ... > report.md` works everywhere."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure") and (stream.encoding or "").lower().replace("-", "") != "utf8":
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    use_utf8_output()
    if args.command == "scan":
        return cmd_scan(args)
    if args.command == "ci":
        return cmd_ci(args)
    if args.command == "rules":
        return cmd_rules()
    return 2
