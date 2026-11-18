"""File discovery: find scannable text files in any repo, whatever its stack."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

from mycrobo.text import string_cuts

MAX_FILE_BYTES = 1_000_000

SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "vendor", "third_party", ".venv", "venv", "env",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".tox", "dist", "build", "target", "out",
    ".next", ".nuxt", ".gradle", ".idea", ".vscode", "coverage", ".terraform", "Pods",
}

SKIP_SUFFIXES = {
    ".min.js", ".min.css", ".map", ".lock", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg",
    ".pdf", ".zip", ".gz", ".tar", ".jar", ".class", ".so", ".dylib", ".dll", ".exe", ".woff",
    ".woff2", ".ttf", ".eot", ".mp4", ".mp3", ".pyc", ".bin", ".db", ".sqlite",
}

SKIP_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Cargo.lock", "go.sum"}

LANGUAGE_BY_SUFFIX = {
    ".py": "python", ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".go": "go", ".java": "java", ".kt": "kotlin",
    ".kts": "kotlin", ".scala": "scala", ".rb": "ruby", ".php": "php", ".cs": "csharp", ".c": "c",
    ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".hpp": "cpp", ".rs": "rust", ".swift": "swift",
    ".m": "objc", ".dart": "dart", ".lua": "lua", ".sh": "shell", ".bash": "shell", ".zsh": "shell",
    ".ps1": "powershell", ".sql": "sql", ".r": "r", ".ex": "elixir", ".exs": "elixir",
    ".erl": "erlang", ".hs": "haskell", ".clj": "clojure", ".vue": "vue", ".svelte": "svelte",
    ".html": "html", ".css": "css", ".scss": "css", ".yaml": "yaml", ".yml": "yaml",
    ".json": "json", ".toml": "toml", ".ini": "ini", ".cfg": "ini", ".tf": "terraform",
    ".hcl": "terraform", ".xml": "xml", ".md": "markdown", ".env": "dotenv", ".gradle": "groovy",
}

LANGUAGE_BY_NAME = {
    "Dockerfile": "dockerfile", "Makefile": "make", "Jenkinsfile": "groovy", ".env": "dotenv",
    "Gemfile": "ruby", "Rakefile": "ruby", "Procfile": "procfile",
}

TEST_DIRS = {
    "test", "tests", "__tests__", "spec", "specs", "testing", "e2e", "cypress", "playwright",
    "testdata", "test_data",
}
_TEST_NAME = re.compile(
    r"(^test_.*\.py$|_test\.(py|go|rb|exs?)$|\.(test|spec)\.[cm]?[jt]sx?$"
    r"|Tests?\.(java|kt|cs|swift|scala)$|_spec\.rb$|^conftest\.py$)"
)

SHEBANG_LANGUAGES = {"python": "python", "node": "javascript", "bash": "shell", "sh": "shell", "ruby": "ruby"}


@dataclass
class SourceFile:
    root: Path
    rel_path: str  # forward slashes, relative to root
    language: str = "unknown"
    _text: str | None = field(default=None, repr=False)

    @property
    def abs_path(self) -> Path:
        return self.root / self.rel_path

    @property
    def parts(self) -> list[str]:
        return self.rel_path.split("/")

    @property
    def name(self) -> str:
        return self.parts[-1]

    @cached_property
    def is_test(self) -> bool:
        """Test code, by directory or file-name convention across common ecosystems."""
        if any(p.lower() in TEST_DIRS for p in self.parts[:-1]):
            return True
        return bool(_TEST_NAME.search(self.name))

    @cached_property
    def text(self) -> str:
        if self._text is not None:
            return self._text
        return self.abs_path.read_text(encoding="utf-8", errors="replace")

    @cached_property
    def lines(self) -> list[str]:
        return self.text.splitlines()

    @cached_property
    def string_cuts(self) -> dict[int, int]:
        return string_cuts(self.lines, self.language)


def _git_files(root: Path) -> list[str] | None:
    """Tracked + untracked-but-not-ignored files, so .gitignore is respected."""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            capture_output=True, check=True, timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return [p for p in out.decode("utf-8", errors="replace").split("\0") if p]


def _walk_files(root: Path) -> list[str]:
    files: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            files.append(Path(dirpath, name).relative_to(root).as_posix())
    return files


def _is_skipped(rel_path: str) -> bool:
    parts = rel_path.split("/")
    if any(p in SKIP_DIRS for p in parts[:-1]):
        return True
    name = parts[-1]
    if name in SKIP_NAMES:
        return True
    lower = name.lower()
    return any(lower.endswith(s) for s in SKIP_SUFFIXES)


def _is_binary(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return b"\0" in fh.read(8192)
    except OSError:
        return True


def detect_language(path: Path) -> str:
    if path.name in LANGUAGE_BY_NAME:
        return LANGUAGE_BY_NAME[path.name]
    if path.name.startswith(".env"):
        return "dotenv"
    lang = LANGUAGE_BY_SUFFIX.get(path.suffix.lower())
    if lang:
        return lang
    try:
        with path.open("rb") as fh:
            first = fh.readline(200).decode("utf-8", errors="ignore")
    except OSError:
        return "unknown"
    if first.startswith("#!"):
        for key, value in SHEBANG_LANGUAGES.items():
            if key in first:
                return value
    return "unknown"


def discover(root: Path) -> list[SourceFile]:
    root = root.resolve()
    candidates = _git_files(root)
    if candidates is None:
        candidates = _walk_files(root)

    files: list[SourceFile] = []
    for rel in sorted(set(candidates)):
        if _is_skipped(rel):
            continue
        path = root / rel
        try:
            if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        if _is_binary(path):
            continue
        files.append(SourceFile(root=root, rel_path=rel, language=detect_language(path)))
    return files
