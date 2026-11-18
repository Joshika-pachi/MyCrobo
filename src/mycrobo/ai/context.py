"""Context for the LLM: which files to review, how to show them, and the repo's conventions.

Everything here is deterministic and free. The conventions profile is what makes the
review architecture-agnostic: instead of assuming React/Django/Spring idioms, the model
is told how *this* repo handles errors, logging and tests, and judges code against that.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from mycrobo.discover import SourceFile
from mycrobo.models import Finding
from mycrobo.rules.secrets import redact_secrets
from mycrobo.text import CODE_LANGUAGES

MAX_REVIEW_LINES = 1500  # longer files are reviewed only via their findings' neighbourhoods
SNIPPET_RADIUS = 40

# Paths that usually receive outside input or do I/O: where coding gaps hurt most.
_ENTRY_PATH = re.compile(
    r"(?i)(route|handler|controller|api|endpoint|view|server|app|main|cli|command|webhook|consumer|worker|lambda|service|auth|payment|upload)"
)
_IO_HINT = re.compile(
    r"(?i)\b(request\.|req\.(body|query|params)|fetch\(|axios|requests\.(get|post)|open\(|subprocess|exec\(|eval\(|query\(|cursor\.|"
    r"os\.system|readFile|writeFile|http\.|sql|json\.loads|JSON\.parse|argv|input\()"
)

_LOGGING = [
    ("python logging", re.compile(r"\blogging\.getLogger|\blogger\.(info|warning|error|debug)")),
    ("structlog/loguru", re.compile(r"\b(structlog|loguru)\b")),
    ("console.*", re.compile(r"\bconsole\.(error|warn|info)\(")),
    ("pino/winston", re.compile(r"\b(pino|winston)\b")),
    ("Go log/slog/zap", re.compile(r"\b(slog|zap|logrus)\.|\blog\.(Printf|Println|Fatal)")),
    ("SLF4J/Log4j", re.compile(r"\b(LoggerFactory|Log4j|log4j)\b")),
]
_ERRORS = [
    ("exceptions (raise/throw)", re.compile(r"\b(raise|throw)\s+\w")),
    ("Go error returns", re.compile(r"\bif\s+err\s*!=\s*nil")),
    ("Result/Either types", re.compile(r"\b(Result<|Either<|Ok\(|Err\()")),
    ("Promise .catch", re.compile(r"\.catch\(")),
]
_VALIDATION = [
    ("pydantic", re.compile(r"\bpydantic\b|\bBaseModel\b")),
    ("zod/yup/joi", re.compile(r"\b(zod|yup|joi)\b|\bz\.object\(")),
    ("marshmallow/cerberus", re.compile(r"\b(marshmallow|cerberus)\b")),
    ("Bean Validation", re.compile(r"@(Valid|NotNull|NotBlank)\b")),
]
_MANIFESTS = {
    "package.json": "Node.js", "pyproject.toml": "Python", "requirements.txt": "Python", "go.mod": "Go",
    "Cargo.toml": "Rust", "pom.xml": "Java (Maven)", "build.gradle": "JVM (Gradle)", "Gemfile": "Ruby",
    "composer.json": "PHP", "Dockerfile": "Docker", "main.tf": "Terraform",
}


@dataclass
class ReviewTarget:
    source: SourceFile
    findings: list[Finding]
    reason: str  # why this file was picked


def conventions_profile(files: list[SourceFile]) -> str:
    """A short, deterministic description of how this repo is built."""
    code = [f for f in files if f.language in CODE_LANGUAGES]
    langs = Counter(f.language for f in code).most_common(5)
    stack = sorted({label for f in files for name, label in _MANIFESTS.items() if f.name == name})
    tests = Counter(f.parts[0] if len(f.parts) > 1 else "(root)" for f in files if f.is_test).most_common(3)

    sample = "\n".join(f.text for f in code[:200] if not f.is_test)
    def present(table: list[tuple[str, re.Pattern[str]]]) -> str:
        found = [label for label, rx in table if rx.search(sample)]
        return ", ".join(found) if found else "none detected"

    lines = [
        f"Languages: {', '.join(f'{lang} ({n} files)' for lang, n in langs) or 'unknown'}",
        f"Stack markers: {', '.join(stack) or 'none'}",
        f"Tests: {', '.join(f'{d}/ ({n} files)' for d, n in tests) if tests else 'NO test files found'}",
        f"Logging: {present(_LOGGING)}",
        f"Error handling style: {present(_ERRORS)}",
        f"Input validation libraries: {present(_VALIDATION)}",
    ]
    return "\n".join(lines)


def pick_targets(files: list[SourceFile], findings: list[Finding], max_files: int) -> list[ReviewTarget]:
    """Files with rule findings first (most severe first), then risky untouched files."""
    by_file: dict[str, list[Finding]] = {}
    for f in findings:
        by_file.setdefault(f.file, []).append(f)
    lookup = {f.rel_path: f for f in files}

    flagged = sorted(by_file, key=lambda p: (-max(x.severity.rank for x in by_file[p]), -len(by_file[p]), p))
    targets = [ReviewTarget(lookup[p], by_file[p], "rule findings") for p in flagged if p in lookup]

    seen = {t.source.rel_path for t in targets}
    hotspots = []
    for f in files:
        if f.rel_path in seen or f.is_test or f.language not in CODE_LANGUAGES:
            continue
        score = (2 if _ENTRY_PATH.search(f.rel_path) else 0) + min(len(_IO_HINT.findall(f.text)), 5)
        if score >= 3:
            hotspots.append((score, f))
    hotspots.sort(key=lambda x: (-x[0], x[1].rel_path))
    targets += [ReviewTarget(f, [], "handles input/I-O") for _, f in hotspots]
    return targets[:max_files]


def render_file(source: SourceFile, findings: list[Finding]) -> str:
    """The file with line numbers and secrets redacted; long files shrink to finding windows."""
    lines = redact_secrets(source.text).splitlines()
    if len(lines) <= MAX_REVIEW_LINES:
        keep = range(1, len(lines) + 1)
    else:
        wanted: set[int] = set()
        for f in findings:
            wanted.update(range(max(1, f.line_start - SNIPPET_RADIUS), min(len(lines), f.line_end + SNIPPET_RADIUS) + 1))
        wanted.update(range(1, min(len(lines), 60) + 1))  # imports / module header
        keep = sorted(wanted)
    out, prev = [], 0
    for n in keep:
        if n != prev + 1:
            out.append(f"     … (lines {prev + 1}-{n - 1} omitted) …")
        out.append(f"{n:>5} | {lines[n - 1]}")
        prev = n
    if prev < len(lines):
        out.append(f"     … (lines {prev + 1}-{len(lines)} omitted) …")
    return "\n".join(out)
