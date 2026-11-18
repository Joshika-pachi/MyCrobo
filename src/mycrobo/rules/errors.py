"""Loose ends: errors that are caught and silently dropped.

A handler that contains only a comment counts as intentional and is not reported.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from mycrobo.discover import SourceFile
from mycrobo.models import Category, Finding, Severity
from mycrobo.rules import Rule
from mycrobo.text import code_part, comment_start, indent_of, inside_string, is_comment_line, next_statement, strip_strings

_PY_EXCEPT = re.compile(r"^(\s*)except\b([^:]*):\s*$")
_PY_OPTIONAL_IMPORT = re.compile(r"ImportError|ModuleNotFoundError")
_PY_BROAD = re.compile(r"^\(?\s*(?:Base)?Exception\b(?:\s+as\s+\w+)?\s*\)?$")

_BRACE_LANGUAGES = {"javascript", "typescript", "java", "kotlin", "scala", "csharp", "cpp", "php",
                    "swift", "dart", "groovy", "vue", "svelte", "objc"}
_CATCH = re.compile(r"\bcatch\b\s*(?:\([^)]*\))?\s*\{")
_PROMISE_CATCH = re.compile(
    r"\.catch\(\s*(?:\(\s*\w*\s*\)\s*=>|\w+\s*=>|function\s*\(\s*\w*\s*\))\s*(?:\{\s*\}|null|undefined|void 0)\s*\)"
)
_GO_ERR = re.compile(r"^\s*if\s+(?:[^{]*;\s*)?\w*[eE]rr\w*\s*!=\s*nil\s*\{\s*(\})?\s*$")
_RUBY_RESCUE = re.compile(r"^\s*rescue\b")
_RUBY_RESCUE_NIL = re.compile(r"\brescue\s+nil\b")


class SwallowedErrorRule(Rule):
    id = "loose-ends/swallowed-error"
    category = Category.LOOSE_ENDS
    default_severity = Severity.MEDIUM
    description = "Errors caught and silently ignored (empty catch/except, `if err != nil {}`)."
    explanation = (
        "Silently swallowed errors turn failures into wrong results: the program carries on with "
        "missing data and nobody gets a log line to debug it."
    )
    fix = "Handle it (retry, fallback, re-raise with context) or at least log it; if ignoring is intended, say why in a comment."

    def applies_to(self, source: SourceFile) -> bool:
        return not source.is_test and (
            source.language in _BRACE_LANGUAGES or source.language in {"python", "go", "ruby"}
        )

    def check(self, source: SourceFile) -> Iterable[Finding]:
        lang = source.language
        if lang == "python":
            yield from self._python(source)
        elif lang == "go":
            yield from self._go(source)
        elif lang == "ruby":
            yield from self._ruby(source)
        else:
            yield from self._braces(source)

    def _python(self, source: SourceFile) -> Iterable[Finding]:
        lines = source.lines
        for i, line in enumerate(lines):
            m = _PY_EXCEPT.match(code_part(line, "python").rstrip())
            if not m or _PY_OPTIONAL_IMPORT.search(m.group(2)):
                continue
            nxt = i + 1
            while nxt < len(lines) and not lines[nxt].strip():
                nxt += 1
            if nxt >= len(lines) or is_comment_line(lines[nxt], "python"):
                continue  # a comment explains the intent
            if code_part(lines[nxt], "python").strip() not in {"pass", "..."}:
                continue
            if comment_start(lines[nxt], "python") is not None:
                continue  # `pass  # reason`
            after = next_statement(lines, nxt + 1, "python")
            if after is not None and indent_of(lines[after]) > len(m.group(1)):
                continue
            caught = m.group(2).strip()
            if not caught:
                severity, kind = Severity.HIGH, "bare except"
            elif _PY_BROAD.search(caught):
                severity, kind = Severity.MEDIUM, caught
            else:  # `except KeyError: pass` is a common, deliberate idiom
                severity, kind = Severity.LOW, caught
            yield self.finding(
                source, i + 1, f"Exception swallowed with `pass` ({kind})", line, line_end=nxt + 1,
                severity=severity,
            )

    def _braces(self, source: SourceFile) -> Iterable[Finding]:
        lines, lang = source.lines, source.language
        for i, line in enumerate(lines):
            code = code_part(line, lang)
            pm = _PROMISE_CATCH.search(strip_strings(code))
            if pm:
                yield self.finding(source, i + 1, "Promise rejection silently ignored", line)
                continue
            m = _CATCH.search(code)
            if not m or inside_string(code, m.start()) or comment_start(line, lang) is not None:
                continue
            rest = code[m.end():]
            if rest.strip():
                if re.match(r"\s*\}", rest):
                    yield self.finding(source, i + 1, "Empty catch block", line)
                continue
            for j in range(i + 1, min(i + 6, len(lines))):
                if not lines[j].strip():
                    continue
                if is_comment_line(lines[j], lang):
                    break  # intentional, explained
                if code_part(lines[j], lang).strip().startswith("}"):
                    yield self.finding(source, i + 1, "Empty catch block", line, line_end=j + 1)
                break

    def _go(self, source: SourceFile) -> Iterable[Finding]:
        lines = source.lines
        for i, line in enumerate(lines):
            m = _GO_ERR.match(code_part(line, "go"))
            if not m:
                continue
            if m.group(1):
                yield self.finding(source, i + 1, "Error checked but ignored (empty `if err != nil`)", line)
                continue
            for j in range(i + 1, min(i + 6, len(lines))):
                if not lines[j].strip():
                    continue
                if not is_comment_line(lines[j], "go") and code_part(lines[j], "go").strip() == "}":
                    yield self.finding(source, i + 1, "Error checked but ignored (empty `if err != nil`)", line, line_end=j + 1)
                break

    def _ruby(self, source: SourceFile) -> Iterable[Finding]:
        lines = source.lines
        for i, line in enumerate(lines):
            code = code_part(line, "ruby")
            if _RUBY_RESCUE_NIL.search(strip_strings(code)):
                yield self.finding(source, i + 1, "Exception swallowed with `rescue nil`", line, severity=Severity.LOW)
                continue
            if not _RUBY_RESCUE.match(code):
                continue
            nxt = i + 1
            while nxt < len(lines) and not lines[nxt].strip():
                nxt += 1
            if nxt < len(lines) and code_part(lines[nxt], "ruby").strip() == "end" and not is_comment_line(lines[nxt], "ruby"):
                yield self.finding(source, i + 1, "Empty rescue block", line, line_end=nxt + 1)
