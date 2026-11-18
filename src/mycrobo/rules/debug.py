"""Loose ends: debugger hooks and debug prints left in non-test code."""

from __future__ import annotations

import re
from collections.abc import Iterable

from mycrobo.discover import SourceFile
from mycrobo.models import Category, Finding, Severity
from mycrobo.rules import Rule
from mycrobo.text import iter_code, strip_strings

_Pattern = tuple[re.Pattern[str], Severity]

_JS: list[_Pattern] = [
    (re.compile(r"\bconsole\.(?:log|debug|trace|dir|table)\s*\("), Severity.LOW),
    (re.compile(r"^\s*debugger\s*;?\s*$"), Severity.MEDIUM),
]
_PATTERNS: dict[str, list[_Pattern]] = {
    "javascript": _JS, "typescript": _JS, "vue": _JS, "svelte": _JS,
    "python": [
        (re.compile(r"\bbreakpoint\(\s*\)"), Severity.MEDIUM),
        (re.compile(r"\b(?:i?pdb|pudb)\.set_trace\s*\("), Severity.MEDIUM),
    ],
    "ruby": [(re.compile(r"\bbinding\.(?:pry|irb)\b|^\s*byebug\b"), Severity.MEDIUM)],
    "php": [(re.compile(r"\b(?:var_dump|print_r|dd)\s*\("), Severity.LOW)],
    "java": [
        (re.compile(r"\.printStackTrace\(\s*\)"), Severity.LOW),
        (re.compile(r"\bSystem\.(?:out|err)\.print(?:ln)?\s*\("), Severity.LOW),
    ],
    "kotlin": [(re.compile(r"\.printStackTrace\(\s*\)"), Severity.LOW)],
    "rust": [(re.compile(r"\bdbg!\s*\("), Severity.MEDIUM)],
    "go": [(re.compile(r"(?<![.\w])println\s*\(|\bspew\.Dump\s*\("), Severity.LOW)],
    "csharp": [(re.compile(r"\bDebugger\.(?:Break|Launch)\s*\("), Severity.MEDIUM)],
}

# Printing is the job of scripts and CLIs, so low-severity print checks skip them.
_SCRIPT_DIRS = {"scripts", "script", "bin", "tools", "cli"}


class DebugStatementRule(Rule):
    id = "loose-ends/debug-statement"
    category = Category.LOOSE_ENDS
    default_severity = Severity.LOW
    description = "Debugger breakpoints and debug prints left in non-test code."
    explanation = (
        "Debug output leaks internal state into logs and consoles; a debugger hook can freeze a "
        "production process waiting for input."
    )
    fix = "Remove it, or replace it with the project's logger at an appropriate level."

    def applies_to(self, source: SourceFile) -> bool:
        return source.language in _PATTERNS and not source.is_test

    def check(self, source: SourceFile) -> Iterable[Finding]:
        is_script = source.text.startswith("#!") or any(p in _SCRIPT_DIRS for p in source.parts[:-1])
        for lineno, line, raw_code, _comment in iter_code(source):
            code = strip_strings(raw_code)
            for pattern, severity in _PATTERNS[source.language]:
                if severity is Severity.LOW and is_script:
                    continue
                m = pattern.search(code)
                if m:
                    call = m.group(0).strip().rstrip("(;").strip()
                    yield self.finding(source, lineno, f"Debug code left in: `{call}`", line, severity=severity)
                    break
