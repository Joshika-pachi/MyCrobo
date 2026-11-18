"""Security: hard-coded secrets and committed .env files.

Three layers, cheapest and most precise first:
1. Known provider key formats (AWS, GitHub, Anthropic, OpenAI, Stripe, ...).
2. Credentials embedded in URLs (scheme://user:password@host).
3. Generic `secret-ish name = "high-entropy literal"` assignments, with placeholder
   and benign-name filters to keep noise down.

Secret values are always redacted in findings.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from mycrobo.discover import SourceFile
from mycrobo.models import Category, Confidence, Finding, Severity
from mycrobo.rules import Rule
from mycrobo.text import redact, shannon_entropy

_PROVIDERS: list[tuple[str, re.Pattern[str], Severity]] = [
    ("AWS access key ID", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), Severity.CRITICAL),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{22,255})\b"), Severity.CRITICAL),
    ("Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}"), Severity.CRITICAL),
    ("OpenAI API key", re.compile(r"\bsk-(?!ant-)(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{32,}"), Severity.CRITICAL),
    ("Stripe live key", re.compile(r"\b[sr]k_live_[0-9a-zA-Z]{20,}\b"), Severity.CRITICAL),
    ("SendGrid API key", re.compile(r"\bSG\.[A-Za-z0-9_\-]{22}\.[A-Za-z0-9_\-]{43}\b"), Severity.CRITICAL),
    ("Slack token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b"), Severity.HIGH),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), Severity.HIGH),
    ("JSON Web Token", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"), Severity.HIGH),
    ("Private key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY(?: BLOCK)?-----"), Severity.CRITICAL),
]

_URL_CREDENTIALS = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s:/@'\"`]+:([^\s@/'\"`]+)@[^\s'\"`]+")

_SECRET_WORD = r"(?:password|passwd|passphrase|pwd|secret|token|api[_\-]?key|apikey|access[_\-]?key|private[_\-]?key|auth[_\-]?key|credentials?)"
_NAME = r"[A-Za-z0-9_.\-]*" + _SECRET_WORD + r"[A-Za-z0-9_.\-]*"

# name = "value" / name: 'value' / "name" => `value` / name == "value"
_QUOTED_ASSIGN = re.compile(
    r"(?i)(?<![\w.])[\"']?(" + _NAME + r")[\"']?\s*(?::=|=>|==?|:)\s*[rbuf]?([\"'`])([^\"'`\s]{8,})\2"
)
# NAME=value in .env / ini / yaml / toml / shell without quotes
_BARE_ASSIGN = re.compile(r"(?i)^\s*(?:export\s+)?[\"']?(" + _NAME + r")[\"']?\s*[=:]\s*([^\s#'\"]{8,})\s*(?:#.*)?$")
_BARE_ASSIGN_LANGUAGES = {"dotenv", "ini", "yaml", "toml", "shell", "procfile", "dockerfile", "unknown"}

_BENIGN_NAME = re.compile(
    r"(?i)(?:url|uri|endpoint|path|file|filename|dir|name|field|label|type|length|len|min|max|count|regex|pattern"
    r"|prompt|placeholder|header|env|var|policy|hint|expiry|expires|expiration|ttl|timeout|limit|mode|kind|format"
    r"|style|class|id|prefix|suffix|_at|input|form|validator|strength|reset|confirm|required|enabled|rotation"
    r"|hash|hashed|salt|digest|size|index|column|param|query|key_id|keyid)$"
    r"|tokens|tokeniz"
)
_PLACEHOLDER = re.compile(
    r"(?i)change.?me|your[_\-]|example|xxx|placeholder|dummy|sample|redacted|replace|insert|todo|fixme|foobar"
    r"|_here$|^<.*>$|\$\{|\{\{|%\(|%s|process\.env|os\.environ|getenv|env\(|^(?:none|null|nil|undefined|true|false)$"
    r"|^(?:test|fake|mock)|^(?:password|secret|token)$|^\*+$|^\.+$"
)

_EXPLANATION = (
    "Anyone with read access to the repo (or its git history, forks, CI logs) can use this "
    "credential. Deleting the line later does not remove it from history."
)
_FIX = "Load it from an environment variable or secret manager, rotate the exposed value, and purge it from git history."


def _looks_real(value: str) -> bool:
    if _PLACEHOLDER.search(value) or len(set(value)) <= 2:
        return False
    if re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", value):  # names another env var, e.g. "GITHUB_TOKEN"
        return False
    if value.startswith(("/", "./", "../", "http://", "https://", "file:")):
        return False
    if re.search(r"[()\[\]{}|\\<>]", value):  # regex, template or code, not a credential
        return False
    return shannon_entropy(value) >= 2.5


class HardcodedSecretRule(Rule):
    id = "security/hardcoded-secret"
    category = Category.SECURITY
    default_severity = Severity.HIGH
    description = "API keys, tokens, private keys or passwords written directly in source or config."
    explanation = _EXPLANATION
    fix = _FIX

    def check(self, source: SourceFile) -> Iterable[Finding]:
        generic = not source.is_test and source.language != "markdown"
        for lineno, line in enumerate(source.lines, start=1):
            found = self._provider(source, lineno, line) or self._url(source, lineno, line)
            if not found and generic:
                found = self._generic(source, lineno, line)
            if found:
                yield found

    def _provider(self, source: SourceFile, lineno: int, line: str) -> Finding | None:
        for label, pattern, severity in _PROVIDERS:
            m = pattern.search(line)
            if not m or "EXAMPLE" in m.group(0).upper():
                continue
            evidence = line if label == "Private key" else line.replace(m.group(0), redact(m.group(0)))
            return self.finding(source, lineno, f"{label} committed in code", evidence, severity=severity)
        return None

    def _url(self, source: SourceFile, lineno: int, line: str) -> Finding | None:
        m = _URL_CREDENTIALS.search(line)
        if not m:
            return None
        password = m.group(1)
        if _PLACEHOLDER.search(password) or password.lower() in {"pass", "password", "pwd", "secret"}:
            return None
        if re.search(r"[{}()\[\]<>$%]", password):  # interpolated: {var}, ${VAR}, $VAR, %(var)s, <password>
            return None
        return self.finding(
            source, lineno, "Password embedded in a connection URL",
            line.replace(password, redact(password)), confidence=Confidence.MEDIUM,
        )

    def _generic(self, source: SourceFile, lineno: int, line: str) -> Finding | None:
        candidates: list[tuple[str, str]] = [(m.group(1), m.group(3)) for m in _QUOTED_ASSIGN.finditer(line)]
        if source.language in _BARE_ASSIGN_LANGUAGES:
            m = _BARE_ASSIGN.match(line)
            if m:
                candidates.append((m.group(1), m.group(2)))
        for name, value in candidates:
            if _BENIGN_NAME.search(name) or not _looks_real(value):
                continue
            return self.finding(
                source, lineno, f"Hard-coded secret assigned to `{name}`",
                line.replace(value, redact(value)), confidence=Confidence.MEDIUM,
            )
        return None


_ENV_TEMPLATE_SUFFIXES = (".example", ".sample", ".template", ".dist", ".defaults", ".tmpl", ".tpl")
_ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


class CommittedEnvFileRule(Rule):
    id = "security/committed-env-file"
    category = Category.SECURITY
    default_severity = Severity.HIGH
    description = ".env file with real values that is tracked or not git-ignored."
    explanation = (
        ".env files usually hold local credentials. If the file is tracked (or not ignored) its "
        "values end up in the repo for everyone who can clone it."
    )
    fix = "Add .env* to .gitignore, remove the file from git, and commit a .env.example with placeholder values."

    def applies_to(self, source: SourceFile) -> bool:
        name = source.name.lower()
        return source.language == "dotenv" and not name.endswith(_ENV_TEMPLATE_SUFFIXES) and not source.is_test

    def check(self, source: SourceFile) -> Iterable[Finding]:
        for lineno, line in enumerate(source.lines, start=1):
            m = _ENV_LINE.match(line)
            if not m:
                continue
            value = m.group(2).strip("'\"")
            if value and not _PLACEHOLDER.search(value):
                yield self.finding(
                    source, lineno, f"{source.name} with real values is not git-ignored",
                    f"{m.group(1)}={redact(value)}",
                )
                return


REDACTED = "<REDACTED>"


def redact_secrets(text: str) -> str:
    """Replace every secret value this rule would flag with <REDACTED>.

    Used before any code leaves the machine (LLM prompts), so a scan never forwards
    the credentials it is warning about.
    """
    out = []
    for line in text.splitlines(keepends=True):
        for _label, pattern, _sev in _PROVIDERS:
            line = pattern.sub(REDACTED, line)
        m = _URL_CREDENTIALS.search(line)
        if m and not _PLACEHOLDER.search(m.group(1)):
            line = line.replace(m.group(1), REDACTED)
        for m in _QUOTED_ASSIGN.finditer(line):
            if not _BENIGN_NAME.search(m.group(1)) and _looks_real(m.group(3)):
                line = line.replace(m.group(3), REDACTED)
        m = _BARE_ASSIGN.match(line.rstrip("\n"))
        if m and not _BENIGN_NAME.search(m.group(1)) and _looks_real(m.group(2)):
            line = line.replace(m.group(2), REDACTED)
        out.append(line)
    return "".join(out)
