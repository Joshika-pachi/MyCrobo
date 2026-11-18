"""Loose ends: stub implementations that look finished but do nothing.

- Python functions whose whole body is `pass`, `...` or `raise NotImplementedError`
  (skipping abstract methods, overloads, Protocol/ABC/Base classes and dunders).
  Medium when the signature promises a value (`-> User`); a `pass` method returning
  None is a deliberate no-op override and is not reported.
- `throw/raise/panic(... "not implemented" ...)` in any language.
- Language stub idioms: C# NotImplementedException, Kotlin TODO(), Rust todo!/unimplemented!,
  Scala `???`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from mycrobo.discover import SourceFile
from mycrobo.models import Category, Finding, Severity
from mycrobo.rules import Rule
from mycrobo.text import code_part, indent_of, inside_string, iter_code, next_statement, strip_strings

_PY_DEF = re.compile(r"^(\s*)(?:async\s+)?def\s+(\w+)")
_PY_CLASS = re.compile(r"^\s*class\s+(\w+)\s*(\([^)]*\))?")
_ABSTRACT_DECORATOR = re.compile(r"^\s*@(?:abc\.)?(?:abstractmethod|abstractproperty|abstractclassmethod|abstractstaticmethod)\b|^\s*@(?:typing\.)?overload\b")
_ABSTRACT_CLASS = re.compile(r"Protocol|ABC|ABCMeta|Interface|^Base|^Abstract|Mixin")
_RETURNS = re.compile(r"->\s*(.+?)\s*:\s*$")
_PY_STUB_BODY = re.compile(r"^(?:pass|\.\.\.|raise\s+NotImplementedError\b.*)$")

_NOT_IMPLEMENTED_TEXT = re.compile(r"(?i)\b(?:throw|raise|panic|fatalError|abort|die)\b.*?[\"'`][^\"'`]*?\b(?:not\s+(?:yet\s+)?implemented|implement\s+me|unimplemented)\b")
_LANG_STUBS: dict[str, re.Pattern[str]] = {
    "csharp": re.compile(r"\bthrow\s+new\s+NotImplementedException\b"),
    "kotlin": re.compile(r"(?<![\w.])TODO\s*\("),
    "rust": re.compile(r"\b(?:todo|unimplemented)!\s*\("),
    "scala": re.compile(r"=\s*\?\?\?"),
}


def _skip_docstring(lines: list[str], i: int) -> int:
    """If line i opens a docstring, return the index after it closes; else i."""
    stripped = lines[i].strip()
    for quote in ('"""', "'''"):
        if stripped.lstrip("rRuUbBfF").startswith(quote):
            body = stripped.lstrip("rRuUbBfF")[3:]
            if quote in body:
                return i + 1
            for j in range(i + 1, len(lines)):
                if quote in lines[j]:
                    return j + 1
            return len(lines)
    return i


def _enclosing_class(lines: list[str], def_idx: int, indent: int) -> re.Match[str] | None:
    """The `class` line directly enclosing the def at def_idx, if any."""
    for k in range(def_idx - 1, -1, -1):
        line = lines[k]
        if not line.strip() or indent_of(line) >= indent:
            continue
        return _PY_CLASS.match(line)
    return None


def _is_abstract_context(lines: list[str], def_idx: int, indent: int) -> bool:
    j = def_idx - 1
    while j >= 0 and lines[j].strip().startswith("@"):
        if _ABSTRACT_DECORATOR.match(lines[j]):
            return True
        j -= 1
    cls = _enclosing_class(lines, def_idx, indent)
    return bool(cls and (_ABSTRACT_CLASS.search(cls.group(1)) or _ABSTRACT_CLASS.search(cls.group(2) or "")))


class StubRule(Rule):
    id = "loose-ends/stub-implementation"
    category = Category.LOOSE_ENDS
    default_severity = Severity.MEDIUM
    description = "Functions that are placeholders: empty bodies, 'not implemented' throws, todo!() etc."
    explanation = (
        "A stub compiles and looks finished in review, but any caller silently gets no behaviour "
        "or a runtime crash on a path nobody tested."
    )
    fix = "Implement it, delete it if unused, or make it explicitly abstract so callers can't reach it."

    def applies_to(self, source: SourceFile) -> bool:
        return not source.is_test and source.language not in {"markdown", "json", "yaml", "toml"}

    def check(self, source: SourceFile) -> Iterable[Finding]:
        lines = source.lines
        if source.language == "python":
            yield from self._python_stubs(source, lines)
        lang_stub = _LANG_STUBS.get(source.language)
        for lineno, line, code, _comment in iter_code(source):
            if source.language == "python" and "NotImplementedError" in code:
                continue  # handled with abstract-method awareness above
            if lang_stub and lang_stub.search(strip_strings(code)):
                yield self.finding(source, lineno, "Stub left in place of an implementation", line)
                continue
            m = _NOT_IMPLEMENTED_TEXT.search(code)
            if m and not inside_string(code, m.start()):
                yield self.finding(source, lineno, "Code path throws 'not implemented'", line)

    def _python_stubs(self, source: SourceFile, lines: list[str]) -> Iterable[Finding]:
        for i, line in enumerate(lines):
            m = _PY_DEF.match(line)
            if not m:
                continue
            def_indent, name = len(m.group(1)), m.group(2)
            if name.startswith("__") and name.endswith("__"):
                continue
            header_end = next(
                (j for j in range(i, min(i + 15, len(lines))) if code_part(lines[j], "python").rstrip().endswith(":")),
                None,
            )
            if header_end is None:
                continue
            body = next_statement(lines, header_end + 1, "python")
            if body is None or indent_of(lines[body]) <= def_indent:
                continue
            body = next_statement(lines, _skip_docstring(lines, body), "python")
            if body is None or indent_of(lines[body]) <= def_indent:
                continue  # docstring-only bodies are rare and usually intentional
            statement = code_part(lines[body], "python").strip()
            if not _PY_STUB_BODY.match(statement):
                continue
            after = next_statement(lines, body + 1, "python")
            if after is not None and indent_of(lines[after]) > def_indent:
                continue  # body has more statements
            if _is_abstract_context(lines, i, def_indent):
                continue
            returns = _RETURNS.search(" ".join(code_part(lines[j], "python").strip() for j in range(i, header_end + 1)))
            promises_value = returns is not None and returns.group(1).strip() not in {"None", "NoReturn", "Never"}
            in_class = _enclosing_class(lines, i, def_indent)
            raises = statement.startswith("raise")
            if promises_value:
                severity = Severity.MEDIUM
            elif raises:
                severity = Severity.LOW if in_class else Severity.MEDIUM  # base-class hook vs dead function
            elif not in_class:
                severity = Severity.LOW
            else:
                continue  # no-op method override, e.g. `def close(self) -> None: pass`
            kind = statement.split("(")[0]
            message = (f"Function `{name}` promises `{returns.group(1).strip()}` but its body is only `{kind}`"
                       if promises_value else f"Function `{name}` is a stub (body is only `{kind}`)")
            yield self.finding(source, i + 1, message, lines[i], line_end=body + 1, severity=severity)
