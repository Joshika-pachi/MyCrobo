"""Rule interface and registry.

Two kinds of rule:
- ``Rule`` inspects one file at a time and yields findings.
- ``RepoRule`` sees every file at once (e.g. cross-file duplicate detection).

Rules must not depend on a specific language or framework; per-language hints go
through ``SourceFile.language`` and the helpers in ``mycrobo.rules._text``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable

from mycrobo.discover import SourceFile
from mycrobo.models import Category, Confidence, Finding, Severity


class _RuleBase:
    id: str
    category: Category
    default_severity: Severity
    description: str
    explanation: str = ""
    fix: str = ""

    def finding(
        self,
        source: SourceFile,
        line: int,
        message: str,
        evidence: str,
        *,
        severity: Severity | None = None,
        confidence: Confidence = Confidence.HIGH,
        explanation: str | None = None,
        fix: str | None = None,
        line_end: int | None = None,
    ) -> Finding:
        return Finding(
            rule=self.id,
            category=self.category,
            severity=severity or self.default_severity,
            confidence=confidence,
            file=source.rel_path,
            line_start=line,
            line_end=line_end or line,
            message=message,
            evidence=evidence.strip()[:300],
            explanation=self.explanation if explanation is None else explanation,
            suggested_fix=self.fix if fix is None else fix,
        )


class Rule(_RuleBase, ABC):
    def applies_to(self, source: SourceFile) -> bool:
        return True

    @abstractmethod
    def check(self, source: SourceFile) -> Iterable[Finding]:
        ...


class RepoRule(_RuleBase, ABC):
    @abstractmethod
    def check_repo(self, files: list[SourceFile]) -> Iterable[Finding]:
        ...


def all_rules() -> list[Rule | RepoRule]:
    from mycrobo.rules.debug import DebugStatementRule
    from mycrobo.rules.duplicates import DuplicateBlockRule
    from mycrobo.rules.errors import SwallowedErrorRule
    from mycrobo.rules.markers import MarkerCommentRule
    from mycrobo.rules.placeholders import MockDataRule, PlaceholderCommentRule
    from mycrobo.rules.secrets import CommittedEnvFileRule, HardcodedSecretRule
    from mycrobo.rules.stubs import StubRule

    return [
        HardcodedSecretRule(),
        CommittedEnvFileRule(),
        PlaceholderCommentRule(),
        DuplicateBlockRule(),
        MarkerCommentRule(),
        StubRule(),
        SwallowedErrorRule(),
        DebugStatementRule(),
        MockDataRule(),
    ]
