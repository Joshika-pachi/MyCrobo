"""Diff mode: which lines did this branch add or change, relative to a base ref?

Uses plain `git` (no GitHub API), so it works the same locally and in CI:
    changed = merge-base(BASE, HEAD) .. working tree   +  untracked files
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

WHOLE_FILE = None  # value in the changed-lines map for new/untracked files

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


class DiffError(Exception):
    pass


def _git(root: Path, *args: str) -> str:
    try:
        # Explicit UTF-8: diffs contain source code, and Windows' default code page can't decode it.
        proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        raise DiffError(f"git failed: {exc}") from exc
    if proc.returncode != 0:
        raise DiffError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout


def parse_unified_diff(diff: str) -> dict[str, set[int]]:
    """Map each changed file to the line numbers (new side) that were added or modified."""
    changed: dict[str, set[int]] = {}
    current: set[int] | None = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            target = line[4:].strip()
            if target == "/dev/null":
                current = None  # deleted file: nothing to report on
            else:
                path = target[2:] if target.startswith("b/") else target
                current = changed.setdefault(path, set())
            continue
        m = _HUNK.match(line)
        if m and current is not None:
            start, count = int(m.group(1)), int(m.group(2) or 1)
            current.update(range(start, start + count))
    return changed


def changed_lines(root: Path, base: str) -> dict[str, set[int] | None]:
    """Changed lines relative to `base` (a branch, tag or SHA). None = the whole file is new."""
    _git(root, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}")
    try:
        start = _git(root, "merge-base", base, "HEAD").strip()
    except DiffError:
        start = base  # unrelated histories / shallow clone: fall back to a direct diff
    diff = _git(root, "diff", "--unified=0", "--no-color", "--no-ext-diff", "--relative", start)
    result: dict[str, set[int] | None] = dict(parse_unified_diff(diff))
    for path in _git(root, "ls-files", "--others", "--exclude-standard").splitlines():
        result[path] = WHOLE_FILE
    return result


def touches(changed: dict[str, set[int] | None], file: str, line_start: int, line_end: int) -> bool:
    if file not in changed:
        return False
    lines = changed[file]
    return lines is WHOLE_FILE or any(n in lines for n in range(line_start, max(line_end, line_start) + 1))
