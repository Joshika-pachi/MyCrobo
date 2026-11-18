"""Loose-end marker comments (TODO / FIXME / HACK / XXX).

Only counts markers inside a real comment for the file's language, so plain words
in strings or identifiers don't match. Markers are case-sensitive, so names like
`todo_list` or `hackathon` never count.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from mycrobo.discover import SourceFile
from mycrobo.models import Category, Finding, Severity
from mycrobo.rules import Rule
from mycrobo.text import iter_code

_MARKER_RE = re.compile(r"\b(TODO|FIXME|HACK|XXX)\b")

_SEVERITY = {
    "TODO": Severity.LOW,
    "XXX": Severity.MEDIUM,
    "HACK": Severity.MEDIUM,
    "FIXME": Severity.MEDIUM,
}


class MarkerCommentRule(Rule):
    id = "loose-ends/marker-comment"
    category = Category.LOOSE_ENDS
    default_severity = Severity.LOW
    description = "Unresolved TODO/FIXME/HACK/XXX comment left in code."
    fix = "Finish the work, open a tracked issue and link it, or remove the marker."

    def check(self, source: SourceFile) -> Iterable[Finding]:
        for lineno, line, _code, comment in iter_code(source):
            marker = _MARKER_RE.search(comment) if comment else None
            if not marker:
                continue
            word = marker.group(1)
            yield self.finding(
                source, lineno, f"Unresolved {word} comment", line,
                severity=_SEVERITY[word],
                explanation=(
                    f"A {word} marker means the author knew this code was unfinished. "
                    "Left in place, it usually becomes a silent gap in behaviour."
                ),
            )
