"""Publish a scan to a GitHub pull request: inline review comments + one summary comment.

Standard library only. All HTTP goes through `GitHubClient.http`, so tests and
`mycrobo ci --dry-run` swap in a recorder and nothing is ever sent.

Behaviour on reruns (every push to the PR):
- inline comments carry a hidden `<!-- mycrobo:<finding id> -->` marker; findings
  already commented on are skipped, so a push never duplicates comments;
- the summary is one comment (marked `<!-- mycrobo-summary -->`) edited in place.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field

from mycrobo.diff import WHOLE_FILE
from mycrobo.models import Finding, ScanResult
from mycrobo.report import SUMMARY_MARKER, finding_comment, render_markdown

_COMMENT_ID = re.compile(r"<!-- mycrobo:([0-9a-f]{12}) -->")

# (method, url, headers, body) -> (status, parsed JSON or None)
Http = Callable[[str, str, dict, "bytes | None"], "tuple[int, object]"]


class GitHubError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"GitHub API {status}: {message}")
        self.status = status


@dataclass
class PullRequest:
    repo: str  # "owner/name"
    number: int
    base_sha: str
    head_sha: str


def pull_request_from_env(env: dict | None = None) -> PullRequest | None:
    """The PR this workflow run is for, from the GitHub Actions event payload."""
    env = os.environ if env is None else env
    path, repo = env.get("GITHUB_EVENT_PATH"), env.get("GITHUB_REPOSITORY")
    if not path or not repo:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            pr = json.load(fh).get("pull_request")
    except (OSError, ValueError):
        return None
    if not pr:
        return None
    return PullRequest(repo=repo, number=int(pr["number"]), base_sha=pr["base"]["sha"], head_sha=pr["head"]["sha"])


def urllib_http(method: str, url: str, headers: dict, body: bytes | None) -> tuple[int, object]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw) if raw else None
        except ValueError:
            return exc.code, {"message": raw.decode("utf-8", "replace")[:300]}


class DryRunHttp:
    """Records requests instead of sending them. GETs return empty lists; writes return ids."""

    def __init__(self):
        self.requests: list[dict] = []

    def __call__(self, method: str, url: str, headers: dict, body: bytes | None) -> tuple[int, object]:
        self.requests.append({"method": method, "url": url, "body": json.loads(body) if body else None})
        if method == "GET":
            return 200, []
        return 201, {"id": len(self.requests)}


class GitHubClient:
    def __init__(self, token: str, api_url: str = "https://api.github.com", http: Http = urllib_http):
        self.api_url = api_url.rstrip("/")
        self.http = http
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "mycrobo",
        }

    def call(self, method: str, path: str, body: dict | None = None) -> object:
        data = json.dumps(body).encode() if body is not None else None
        headers = dict(self.headers, **({"Content-Type": "application/json"} if data else {}))
        status, payload = self.http(method, f"{self.api_url}{path}", headers, data)
        if status >= 400:
            message = payload.get("message", "") if isinstance(payload, dict) else ""
            raise GitHubError(status, message)
        return payload

    def get_all(self, path: str, max_pages: int = 10) -> list:
        items: list = []
        for page in range(1, max_pages + 1):
            sep = "&" if "?" in path else "?"
            batch = self.call("GET", f"{path}{sep}per_page=100&page={page}")
            if not isinstance(batch, list) or not batch:
                break
            items.extend(batch)
            if len(batch) < 100:
                break
        return items


@dataclass
class PublishReport:
    posted: int = 0
    skipped_existing: int = 0
    skipped_limit: int = 0
    rejected: int = 0  # lines GitHub would not accept a comment on
    summary: str = ""  # "created" | "updated"
    warnings: list[str] = field(default_factory=list)


def comment_line(f: Finding, changed: dict | None) -> int:
    """A line of the finding that is part of the PR diff (GitHub rejects others)."""
    lines = changed.get(f.file) if changed else WHOLE_FILE
    if lines is WHOLE_FILE:
        return f.line_start
    return next((n for n in range(f.line_start, max(f.line_end, f.line_start) + 1) if n in lines), f.line_start)


def publish(result: ScanResult, pr: PullRequest, client: GitHubClient, changed: dict | None,
            max_comments: int = 20, server_url: str = "https://github.com") -> PublishReport:
    report = PublishReport()
    base = f"/repos/{pr.repo}"

    # 1. Inline comments for findings not already commented on.
    posted_ids = {m.group(1) for c in client.get_all(f"{base}/pulls/{pr.number}/comments")
                  for m in [_COMMENT_ID.search(c.get("body", "") if isinstance(c, dict) else "")] if m}
    fresh = [f for f in result.findings if f.id not in posted_ids]
    report.skipped_existing = len(result.findings) - len(fresh)
    report.skipped_limit = max(0, len(fresh) - max_comments)
    fresh = fresh[:max_comments]
    comments = [{"path": f.file, "line": comment_line(f, changed), "side": "RIGHT", "body": finding_comment(f)}
                for f in fresh]
    if comments:
        try:
            client.call("POST", f"{base}/pulls/{pr.number}/reviews",
                        {"commit_id": pr.head_sha, "event": "COMMENT", "comments": comments})
            report.posted = len(comments)
        except GitHubError as exc:
            if exc.status != 422:
                raise
            # One bad line rejects the whole review: post individually, skipping rejected lines.
            for c in comments:
                try:
                    client.call("POST", f"{base}/pulls/{pr.number}/comments", dict(c, commit_id=pr.head_sha))
                    report.posted += 1
                except GitHubError as single:
                    if single.status != 422:
                        raise
                    report.rejected += 1

    # 2. One summary comment, edited in place on every run.
    def link(f: Finding) -> str:
        return f"{server_url}/{pr.repo}/blob/{pr.head_sha}/{f.file}#L{f.line_start}"

    body = render_markdown(result, title="MyCrobo review", link=link)
    existing = next((c for c in client.get_all(f"{base}/issues/{pr.number}/comments")
                     if isinstance(c, dict) and SUMMARY_MARKER in c.get("body", "")), None)
    if existing:
        client.call("PATCH", f"{base}/issues/comments/{existing['id']}", {"body": body})
        report.summary = "updated"
    else:
        client.call("POST", f"{base}/issues/{pr.number}/comments", {"body": body})
        report.summary = "created"
    return report
