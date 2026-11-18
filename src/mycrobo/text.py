"""Language-agnostic text helpers shared by rules.

Rules never parse a language fully. They separate code from comments and string
literals using the comment syntax of the file's language family, which is enough
for line-level checks across dozens of languages.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterator

_HASH = r"#"
_SLASH = r"//|/\*|^\s*\*(?=\s|/|$)"  # line-start `*` = block-comment continuation
_DASH = r"--"
_HTML = r"<!--"
_SEMI = r"^\s*;"
_PCT = r"^\s*%"


def _rx(*parts: str) -> re.Pattern[str]:
    return re.compile("|".join(parts))


_ALL = _rx(_HASH, _SLASH, _DASH, _HTML, _SEMI, _PCT)

_COMMENT_BY_LANGUAGE: dict[str, re.Pattern[str]] = {}
for _lang in ("python", "ruby", "shell", "yaml", "toml", "dockerfile", "make", "r", "elixir",
              "dotenv", "powershell", "procfile", "perl"):
    _COMMENT_BY_LANGUAGE[_lang] = _rx(_HASH)
for _lang in ("javascript", "typescript", "go", "java", "kotlin", "scala", "c", "cpp", "csharp",
              "rust", "swift", "objc", "dart", "groovy", "css"):
    _COMMENT_BY_LANGUAGE[_lang] = _rx(_SLASH)
_COMMENT_BY_LANGUAGE.update(
    php=_rx(_SLASH, _HASH), terraform=_rx(_HASH, _SLASH), ini=_rx(_HASH, _SEMI),
    sql=_rx(_DASH, _SLASH), lua=_rx(_DASH), haskell=_rx(_DASH), html=_rx(_HTML), xml=_rx(_HTML),
    markdown=_rx(_HTML), vue=_rx(_SLASH, _HTML), svelte=_rx(_SLASH, _HTML), erlang=_rx(_PCT),
    clojure=_rx(_SEMI),
)

# Languages whose files are program code (as opposed to config, data or docs).
CODE_LANGUAGES = frozenset({
    "python", "javascript", "typescript", "go", "java", "kotlin", "scala", "ruby", "php", "csharp",
    "c", "cpp", "rust", "swift", "objc", "dart", "lua", "shell", "powershell", "elixir", "erlang",
    "haskell", "clojure", "vue", "svelte", "groovy", "r",
})

_STRING_RE = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`(?:\\.|[^`\\])*`')


def inside_string(line: str, pos: int) -> bool:
    """True if `pos` sits inside a '...', "..." or `...` literal opened earlier on the line."""
    quote = None
    i = 0
    while i < pos:
        ch = line[i]
        if ch == "\\":
            i += 2
            continue
        if quote is None and ch in "'\"`":
            quote = ch
        elif ch == quote:
            quote = None
        i += 1
    return quote is not None


def comment_start(line: str, language: str) -> int | None:
    """Index where the line's first real comment starts (a token outside any string), or None."""
    for token in _COMMENT_BY_LANGUAGE.get(language, _ALL).finditer(line):
        if not inside_string(line, token.start()):
            return token.start()
    return None


def code_part(line: str, language: str) -> str:
    i = comment_start(line, language)
    return line if i is None else line[:i]


def comment_part(line: str, language: str) -> str | None:
    i = comment_start(line, language)
    return None if i is None else line[i:]


def is_comment_line(line: str, language: str) -> bool:
    i = comment_start(line, language)
    return i is not None and not line[:i].strip()


def strip_strings(code: str) -> str:
    """Blank out string literal contents so keyword patterns can't match inside them."""
    return _STRING_RE.sub('""', code)


def indent_of(line: str) -> int:
    return len(line) - len(line.lstrip())


def next_statement(lines: list[str], start: int, language: str) -> int | None:
    """Index of the next line at or after `start` that holds code (not blank, not comment-only)."""
    for i in range(start, len(lines)):
        if lines[i].strip() and not is_comment_line(lines[i], language):
            return i
    return None


def shannon_entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = Counter(value)
    n = len(value)
    return -sum(c / n * math.log2(c / n) for c in counts.values())


def redact(secret: str) -> str:
    if len(secret) <= 12:
        return "*" * 8
    return f"{secret[:4]}…{secret[-4:]}"


# Delimiters of strings that can span lines. Lines that *start* inside one are
# string content: they hold neither code nor comments.
_MULTILINE_DELIMITERS: dict[str, re.Pattern[str]] = {
    "python": re.compile(r'"""|\'\'\''),
    **{lang: re.compile(r"(?<!\\)`") for lang in ("javascript", "typescript", "vue", "svelte", "go")},
}


def string_cuts(lines: list[str], language: str) -> dict[int, int]:
    """Map 1-based line number -> index where multi-line string content starts on that line.

    Lines that begin inside the string map to 0 (no code at all); the line that opens
    a string that stays open maps to the position of the opening delimiter.
    """
    delimiters = _MULTILINE_DELIMITERS.get(language)
    if delimiters is None:
        return {}
    result: dict[int, int] = {}
    open_delim: str | None = None
    for lineno, line in enumerate(lines, start=1):
        if open_delim is not None:
            result[lineno] = 0
        opened_at: int | None = None
        cutoff = len(line)
        if open_delim is None:
            start = comment_start(line, language)
            cutoff = len(line) if start is None else start
        for m in delimiters.finditer(line):
            if open_delim is None and m.start() >= cutoff:
                break  # delimiter inside a comment
            if open_delim is None:
                open_delim, opened_at = m.group(0), m.start()
            elif m.group(0) == open_delim:
                open_delim, opened_at = None, None
        if open_delim is not None and opened_at is not None:
            result[lineno] = opened_at
    return result


def iter_code(source) -> Iterator[tuple[int, str, str, str | None]]:
    """Yield (line number, raw line, code part, comment part) for a SourceFile.

    Multi-line string content is neither code nor comment.
    """
    cuts = source.string_cuts
    for lineno, line in enumerate(source.lines, start=1):
        visible = line[:cuts[lineno]] if lineno in cuts else line
        i = comment_start(visible, source.language)
        yield lineno, line, (visible if i is None else visible[:i]), (None if i is None else visible[i:])
