"""Slop: copy-pasted code blocks, within or across files, in any language.

Lines are normalised (comments dropped, whitespace collapsed, numbers replaced)
so copies with tweaked numeric constants still match. String text is kept on
purpose: blanking it made unrelated declarative code look identical. Every window of
WINDOW consecutive meaningful lines is hashed; windows seen in two places mark a
copy, and overlapping windows merge into one finding per copied block. The first
location (by path) is treated as the original; each later copy is reported.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Iterable

from mycrobo.discover import SourceFile
from mycrobo.models import Category, Confidence, Finding, Severity
from mycrobo.rules import RepoRule
from mycrobo.text import CODE_LANGUAGES, iter_code

WINDOW = 6  # meaningful lines per window
MIN_CHARS = 160  # skip low-information windows (closing braces, short calls)
MAX_OCCURRENCES = 8  # more copies than this is boilerplate/generated, not slop
LONG_BLOCK = 15

_NUMBER = re.compile(r"\b\d+(?:\.\d+)?\b")
_SKIP = re.compile(r"^(?:import\b|from\s+\S+\s+import\b|#include\b|using\b|package\b|require\b|use\b|@)")
_WORD = re.compile(r"\w")
# `name: Type,` / `name=default,` / `Type name,` lines of a parameter list: repetitive by nature.
_PARAMETER = re.compile(r"^[\w*]+(?:\s*:\s*[^=,]+?|\s+\w+)?(?:\s*=\s*[^,]+)?\s*(?:,|\)\s*(?:->.*)?[:{]?)$")


def _normalize(source: SourceFile) -> list[tuple[int, str]]:
    """(original line number, normalised text) for each meaningful line."""
    out: list[tuple[int, str]] = []
    for lineno, _line, code, _comment in iter_code(source):
        code = code.strip()
        if not code:
            continue
        code = " ".join(_NUMBER.sub("0", code).split())
        if len(_WORD.findall(code)) < 3 or _SKIP.match(code) or (len(code) < 40 and _PARAMETER.match(code)):
            continue
        out.append((lineno, code))
    return out


class DuplicateBlockRule(RepoRule):
    id = "slop/duplicate-block"
    category = Category.SLOP
    default_severity = Severity.LOW
    description = f"Copy-pasted blocks of {WINDOW}+ meaningful lines (numbers may differ)."
    explanation = (
        "Copy-paste is the most common shape of AI-generated slop: the same logic in two places "
        "drifts apart, and a bug fixed in one copy survives in the other."
    )
    fix = "Extract the shared logic into one function or module and call it from both places."

    def check_repo(self, files: list[SourceFile]) -> Iterable[Finding]:
        files = [f for f in files if f.language in CODE_LANGUAGES and not f.is_test]
        normalized = [_normalize(f) for f in files]

        index: dict[bytes, list[tuple[int, int]]] = defaultdict(list)
        for fi, norm in enumerate(normalized):
            for pos in range(len(norm) - WINDOW + 1):
                chunk = [text for _, text in norm[pos:pos + WINDOW]]
                if sum(map(len, chunk)) < MIN_CHARS:
                    continue
                key = hashlib.blake2b("\n".join(chunk).encode(), digest_size=12).digest()
                index[key].append((fi, pos))

        # copies[file][window start] = (original file, original window start)
        copies: dict[int, dict[int, tuple[int, int]]] = defaultdict(dict)
        for occurrences in index.values():
            if not 2 <= len(occurrences) <= MAX_OCCURRENCES:
                continue
            kept: list[tuple[int, int]] = []
            for fi, pos in sorted(occurrences, key=lambda o: (files[o[0]].rel_path, o[1])):
                if kept and kept[-1][0] == fi and pos - kept[-1][1] < WINDOW:
                    continue  # overlaps the previous occurrence: a repeated pattern, not a copy
                kept.append((fi, pos))
            for fi, pos in kept[1:]:
                copies[fi].setdefault(pos, kept[0])

        for fi in sorted(copies, key=lambda i: files[i].rel_path):
            marks, norm, source = copies[fi], normalized[fi], files[fi]
            positions = sorted(marks)
            start = prev = positions[0]
            for pos in positions[1:] + [None]:
                if pos is not None and pos < prev + WINDOW:
                    prev = pos
                    continue
                orig_fi, orig_pos = marks[start]
                line_start, line_end = norm[start][0], norm[prev + WINDOW - 1][0]
                size = prev - start + WINDOW
                yield self.finding(
                    source, line_start,
                    f"Duplicated block ({size} lines) copied from {files[orig_fi].rel_path}:{normalized[orig_fi][orig_pos][0]}",
                    source.lines[line_start - 1],
                    line_end=line_end,
                    severity=Severity.MEDIUM if size >= LONG_BLOCK else Severity.LOW,
                    confidence=Confidence.HIGH,
                )
                if pos is not None:
                    start = prev = pos
