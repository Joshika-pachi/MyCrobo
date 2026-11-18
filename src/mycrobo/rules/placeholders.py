"""Slop and loose ends written in prose: placeholder comments, truncated code, mock data.

Placeholder comments are the clearest fingerprint of unreviewed AI-generated code:
"... rest of the code ...", "in a real app you would ...", "for now, just return ...".
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from mycrobo.discover import SourceFile
from mycrobo.models import Category, Confidence, Finding, Severity
from mycrobo.rules import Rule
from mycrobo.text import CODE_LANGUAGES, iter_code, strip_strings

_TRUNCATION = re.compile(
    r"(?i)\.\.\.\s*(?:the\s+)?(?:rest|remaining|existing|other|previous|more|same)\b"
    r"|\b(?:rest|remainder)\s+of\s+(?:the\s+)?(?:code|implementation|file|function|class|logic|methods?|component)\b"
    r"|\b(?:existing|previous|original|unchanged)\s+(?:code|implementation|logic)\b"
    r"|\b(?:omitted|truncated|elided)\s+for\s+brevity\b|\bfor\s+brevity\b"
    r"|\bkeep\s+(?:the\s+)?(?:existing|rest)\b"
)
_PLACEHOLDER = re.compile(
    r"(?i)\byour\s+(?:own\s+)?(?:code|logic|implementation|api\s+key|key|token|secret|credentials?)\s+(?:goes\s+)?here\b"
    r"|\b(?:add|insert|put|implement|write)\s+(?:your|the\s+actual|actual|real)\s+\w+"
    r"|\bplaceholder\s+(?:implementation|logic|code|value|function|for\s+now)\b|^\W*placeholder\W*$"
    r"|\bthis\s+is\s+(?:just\s+)?an?\s+(?:stub|mock|dummy|fake|simplified|placeholder)\b"
    r"|\bin\s+a\s+real\s+(?:app|application|implementation|world|scenario|system|project|codebase|environment)\b"
    r"|\bin\s+production,?\s+(?:you|we|this)\s+(?:would|should)\b"
    r"|\bfor\s+(?:now|simplicity|demo(?:\s+purposes)?|the\s+demo),?\s+(?:we(?:'ll|\s+will)?\s+|let'?s\s+|i'?ll\s+)?(?:just\s+)?"
    r"(?:return|hard-?code|mock|stub|fake|assume|use\s+(?:an?\s+)?(?:dummy|mock|fake|static|hard-?coded|placeholder|sample))\b"
    r"|\bsimplified\s+(?:version|implementation|logic|example|check)\b"
    r"|\breplace\s+(?:this\s+)?with\s+(?:a\s+)?(?:real|actual|proper)\b"
    r"|\bhard-?coded\s+for\s+(?:now|demo|testing|simplicity)\b"
    r"|\b(?:would|should)\s+(?:normally|typically|usually)\s+\w+"
    r"|\bmock(?:ed)?\s+(?:implementation|response|data|logic)\b"
)


class PlaceholderCommentRule(Rule):
    id = "slop/placeholder-comment"
    category = Category.SLOP
    default_severity = Severity.MEDIUM
    description = "Comments that stand in for missing code ('... rest of the code', 'in a real app you would ...')."
    fix = "Write the real implementation (or restore the omitted code), then delete the placeholder comment."

    def applies_to(self, source: SourceFile) -> bool:
        return not source.is_test and source.language not in {"markdown", "unknown", "json"}

    def check(self, source: SourceFile) -> Iterable[Finding]:
        for lineno, line, _code, comment in iter_code(source):
            if not comment:
                continue
            if _TRUNCATION.search(comment):
                yield self.finding(
                    source, lineno, "Code replaced by a 'rest of the code' comment", line,
                    severity=Severity.HIGH,
                    explanation=(
                        "This comment is where code used to be, typically left by an AI edit that "
                        "elided unchanged sections. The code it refers to is missing."
                    ),
                )
            elif _PLACEHOLDER.search(comment):
                yield self.finding(
                    source, lineno, "Placeholder comment: the real logic may be missing", line,
                    confidence=Confidence.MEDIUM,
                    explanation=(
                        "The comment admits the code is a stand-in ('in a real app...', 'for now...'). "
                        "It reads as finished in review but behaves like a demo."
                    ),
                )


_MOCK_IDENT = re.compile(
    r"\b(?:(?:mock|fake|dummy)(?:ed)?(?:[A-Z]\w*|_\w+)|(?:MOCK|FAKE|DUMMY)_[A-Z0-9_]+|[a-z]\w*_(?:mock|fake|dummy))"
    r"\s*(?::\s*[\w\[\]<>, .|]+)?\s*=(?![=>])\s*(?:[\[{\"'`\d]|new\s|\(\s*[\[{\"'`\d])"
)
_FAKE_IDENTITY = re.compile(
    r"(?i)[\w.+-]+@(?:example\.(?:com|org|net)|test\.com|foo\.(?:com|bar)|bar\.com|email\.com|domain\.com|acme\.com)\b"
    r"|[\"'](?:john|jane)[._ ]?doe[\"']"
)
_LOREM = re.compile(r"(?i)\blorem\s+ipsum\s+dolor\b")
_MOCK_DIRS = {"mocks", "__mocks__", "mock", "fixtures", "fixture", "seed", "seeds", "stories", "storybook",
              "demo", "demos", "examples", "example", "samples", "sample", "testdata"}
_MOCK_FILE = re.compile(r"(?i)mock|fake|stub|fixture|seed|\.stories\.|example|sample")


class MockDataRule(Rule):
    id = "loose-ends/mock-data"
    category = Category.LOOSE_ENDS
    default_severity = Severity.MEDIUM
    description = "Mock/fake data, fake identities or lorem ipsum in production code paths."
    explanation = (
        "Mock data in a non-test path usually means a feature was wired to sample data during "
        "development and never connected to the real source."
    )
    fix = "Replace it with the real data source, or move it under tests/ or fixtures/."

    def applies_to(self, source: SourceFile) -> bool:
        if source.is_test or source.language not in CODE_LANGUAGES:
            return False
        if any(p.lower() in _MOCK_DIRS for p in source.parts[:-1]):
            return False
        return not _MOCK_FILE.search(source.name)

    def check(self, source: SourceFile) -> Iterable[Finding]:
        for lineno, line, code, _comment in iter_code(source):
            m = _MOCK_IDENT.search(strip_strings(code))
            if m:
                name = re.split(r"[\s:=]", m.group(0), maxsplit=1)[0]
                yield self.finding(source, lineno, f"Mock data `{name}` defined in production code", line,
                                   confidence=Confidence.MEDIUM)
            elif _LOREM.search(code):
                yield self.finding(source, lineno, "Lorem ipsum placeholder text", line, severity=Severity.LOW)
            elif _FAKE_IDENTITY.search(code):
                yield self.finding(source, lineno, "Fake identity (example email / John Doe) hard-coded", line,
                                   severity=Severity.LOW, confidence=Confidence.MEDIUM)
