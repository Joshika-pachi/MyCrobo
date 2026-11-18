"""M3: diff mode, Slop Score, Markdown/SARIF output, GitHub PR publishing.

Everything runs locally: real temporary git repos for diffs, a recording fake for GitHub.
"""

import json
import re
import subprocess
import textwrap
from pathlib import Path

import pytest

from mycrobo.cli import main
from mycrobo.diff import parse_unified_diff
from mycrobo.engine import ScanOptions, scan
from mycrobo.github import GitHubClient, PullRequest, publish
from mycrobo.models import Category, Confidence, Finding, Severity
from mycrobo.report import SUMMARY_MARKER, render_sarif
from mycrobo.score import slop_score

ROOT = Path(__file__).resolve().parent.parent


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                           "-c", "commit.gpgsign=false", *args], check=True, capture_output=True, text=True).stdout


def make_repo(tmp_path):
    """main has a clean file and an old TODO; the feature branch adds new issues."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "app.py").write_text("# TODO: old debt, not part of this PR\n\ndef add(a, b):\n    return a + b\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base")
    base = git(repo, "rev-parse", "HEAD").strip()
    git(repo, "checkout", "-q", "-b", "feature")
    (repo / "app.py").write_text(textwrap.dedent("""\
        # TODO: old debt, not part of this PR

        def add(a, b):
            return a + b


        def load(path):
            try:
                return open(path).read()
            except Exception:
                pass
    """))
    git(repo, "commit", "-q", "-am", "feature")
    (repo / "new_module.py").write_text("def run():\n    breakpoint()\n")  # untracked = new
    return repo, base


# --- diff mode -------------------------------------------------------------------

def test_parse_unified_diff():
    diff = textwrap.dedent("""\
        diff --git a/x.py b/x.py
        --- a/x.py
        +++ b/x.py
        @@ -3,0 +4,2 @@
        +a
        +b
        @@ -10 +12 @@
        -old
        +new
        diff --git a/gone.py b/gone.py
        --- a/gone.py
        +++ /dev/null
        @@ -1,3 +0,0 @@
    """)
    assert parse_unified_diff(diff) == {"x.py": {4, 5, 12}}


def test_diff_mode_reports_only_changed_lines(tmp_path):
    repo, base = make_repo(tmp_path)
    full = scan(repo)
    diff = scan(repo, ScanOptions(diff_base=base))
    assert ("app.py", 1) in {(f.file, f.line_start) for f in full.findings}  # the old marker exists...
    assert sorted((f.file, f.line_start) for f in diff.findings) == [("app.py", 10), ("new_module.py", 2)]  # ...but isn't reported
    assert diff.diff_base == base
    assert diff.lines_of_code == 7  # added non-blank lines: 5 in app.py + 2 in the new file
    assert full.lines_of_code == 10  # 8 in app.py + 2 in the new file


def test_diff_with_unknown_base_is_an_error(tmp_path, capsys):
    repo, _ = make_repo(tmp_path)
    assert main(["scan", str(repo), "--diff", "no-such-branch"]) == 2
    assert "cannot diff against no-such-branch" in capsys.readouterr().err


# --- Slop Score ---------------------------------------------------------------------

def _f(severity, confidence=Confidence.HIGH, line=1):
    return Finding(rule="x/y", category=Category.SECURITY, severity=severity, confidence=confidence,
                   file="a.py", line_start=line, line_end=line, message="m", evidence=f"e{line}")


def test_slop_score():
    assert slop_score([], 5000).value == 0 and slop_score([], 5000).label == "Clean"
    few = slop_score([_f(Severity.LOW)], 10_000)
    many = slop_score([_f(Severity.MEDIUM, line=i) for i in range(40)], 10_000)
    assert few.value < many.value < 100
    # A credible critical is never diluted by repo size; a low-confidence one is.
    assert slop_score([_f(Severity.CRITICAL)], 1_000_000).value == 75
    assert slop_score([_f(Severity.CRITICAL, Confidence.LOW)], 1_000_000).value < 10


# --- SARIF / Markdown ------------------------------------------------------------------

def test_sarif_structure(tmp_path):
    (tmp_path / "keys.py").write_text('AWS_KEY = "AKIA' + 'Q7XK2M9PLR4TWZ3B"\n# TODO: rotate\n')
    sarif = json.loads(render_sarif(scan(tmp_path)))
    assert sarif["version"] == "2.1.0"
    run = sarif["runs"][0]
    rules = run["tool"]["driver"]["rules"]
    assert len({r["id"] for r in rules}) == len(rules)
    by_rule = {r["ruleId"]: r for r in run["results"]}
    secret = by_rule["security/hardcoded-secret"]
    assert secret["level"] == "error"
    assert rules[secret["ruleIndex"]]["id"] == "security/hardcoded-secret"
    assert secret["properties"]["security-severity"] == "9.5"
    loc = secret["locations"][0]["physicalLocation"]
    assert loc["artifactLocation"] == {"uri": "keys.py", "uriBaseId": "%SRCROOT%"}
    assert loc["region"]["startLine"] == 1
    assert "Q7XK2M9PLR4TWZ3B" not in json.dumps(sarif)  # redacted everywhere
    assert by_rule["loose-ends/marker-comment"]["level"] == "note"
    assert run["properties"]["slopScore"]["value"] >= 75


def test_markdown_format(tmp_path, capsys):
    (tmp_path / "a.py").write_text("# FIXME: broken\n")
    assert main(["scan", str(tmp_path), "--format", "md", "--fail-on", "none"]) == 0
    out = capsys.readouterr().out
    assert out.startswith(SUMMARY_MARKER) and "Slop Score:" in out and "`a.py:1`" in out


# --- GitHub publishing --------------------------------------------------------------------

class FakeGitHub:
    """Scripted GitHub API: records calls, serves canned GET pages, can reject review posts."""

    def __init__(self, review_comments=(), issue_comments=(), reject_review=False, bad_lines=()):
        self.calls = []
        self.review_comments = list(review_comments)
        self.issue_comments = list(issue_comments)
        self.reject_review = reject_review
        self.bad_lines = set(bad_lines)

    def __call__(self, method, url, headers, body):
        data = json.loads(body) if body else None
        self.calls.append((method, url.split("api.github.com")[1], data))
        if method == "GET":
            page = int(re.search(r"[?&]page=(\d+)", url).group(1))
            items = self.review_comments if "/pulls/" in url else self.issue_comments
            return 200, items if page == 1 else []
        if url.endswith("/reviews") and self.reject_review:
            return 422, {"message": "Line could not be resolved"}
        if url.endswith("/comments") and "/pulls/" in url and data["line"] in self.bad_lines:
            return 422, {"message": "Line could not be resolved"}
        return 201, {"id": 1}


PR = PullRequest(repo="acme/shop", number=7, base_sha="b" * 40, head_sha="h" * 40)


def _result_for(tmp_path):
    repo, base = make_repo(tmp_path)
    return scan(repo, ScanOptions(diff_base=base))


def test_publish_posts_one_review_and_a_summary(tmp_path):
    result = _result_for(tmp_path)
    gh = FakeGitHub()
    rep = publish(result, PR, GitHubClient("t", http=gh), result.changed)
    writes = [(m, u) for m, u, _ in gh.calls if m != "GET"]
    assert writes == [("POST", "/repos/acme/shop/pulls/7/reviews"), ("POST", "/repos/acme/shop/issues/7/comments")]
    review = next(d for m, u, d in gh.calls if u.endswith("/reviews"))
    assert review["commit_id"] == PR.head_sha and review["event"] == "COMMENT"
    assert sorted((c["path"], c["line"]) for c in review["comments"]) == [("app.py", 10), ("new_module.py", 2)]
    assert all(c["side"] == "RIGHT" and "<!-- mycrobo:" in c["body"] for c in review["comments"])
    summary = next(d for m, u, d in gh.calls if u.endswith("/issues/7/comments") and m == "POST")
    assert SUMMARY_MARKER in summary["body"] and f"/blob/{PR.head_sha}/app.py#L10" in summary["body"]
    assert (rep.posted, rep.summary) == (2, "created")


def test_rerun_skips_posted_findings_and_edits_summary(tmp_path):
    result = _result_for(tmp_path)
    already = result.findings[0]
    gh = FakeGitHub(review_comments=[{"body": f"<!-- mycrobo:{already.id} -->\nold"}],
                    issue_comments=[{"id": 99, "body": f"{SUMMARY_MARKER}\nold summary"}])
    rep = publish(result, PR, GitHubClient("t", http=gh), result.changed)
    review = next(d for m, u, d in gh.calls if u.endswith("/reviews"))
    assert len(review["comments"]) == 1 and already.id not in review["comments"][0]["body"]
    assert ("PATCH", "/repos/acme/shop/issues/comments/99") in [(m, u) for m, u, _ in gh.calls]
    assert (rep.skipped_existing, rep.summary) == (1, "updated")


def test_rejected_review_falls_back_to_single_comments(tmp_path):
    result = _result_for(tmp_path)
    gh = FakeGitHub(reject_review=True, bad_lines={2})
    rep = publish(result, PR, GitHubClient("t", http=gh), result.changed)
    singles = [d for m, u, d in gh.calls if m == "POST" and u.endswith("/pulls/7/comments")]
    assert len(singles) == 2 and all(d["commit_id"] == PR.head_sha for d in singles)
    assert (rep.posted, rep.rejected) == (1, 1)


def test_ci_command_dry_run_end_to_end(tmp_path, monkeypatch, capsys):
    repo, base = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD").strip()
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": 7, "base": {"sha": base}, "head": {"sha": head}}}))
    summary_file = tmp_path / "step_summary.md"
    for k, v in {"GITHUB_EVENT_PATH": str(event), "GITHUB_REPOSITORY": "acme/shop",
                 "GITHUB_STEP_SUMMARY": str(summary_file)}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    requests_file, sarif_file = tmp_path / "requests.json", tmp_path / "out.sarif"

    code = main(["ci", str(repo), "--dry-run", str(requests_file), "--sarif", str(sarif_file), "--fail-on", "medium"])

    assert code == 1  # the swallowed exception is medium
    requests = json.loads(requests_file.read_text())
    assert [r["method"] for r in requests] == ["GET", "POST", "GET", "POST"]
    assert requests[1]["url"].endswith("/repos/acme/shop/pulls/7/reviews")
    assert len(json.loads(sarif_file.read_text())["runs"][0]["results"]) == 2
    assert "Slop Score" in summary_file.read_text()
    assert "dry run - 4 GitHub API requests" in capsys.readouterr().err


def test_ci_outside_a_pull_request_scans_everything_and_skips_comments(tmp_path, monkeypatch, capsys):
    repo, _ = make_repo(tmp_path)
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    assert main(["ci", str(repo), "--sarif", str(tmp_path / "o.sarif"), "--fail-on", "none"]) == 0
    assert "not a pull_request event" in capsys.readouterr().err
    assert len(json.loads((tmp_path / "o.sarif").read_text())["runs"][0]["results"]) == 3


# --- action.yml hardening -------------------------------------------------------------------

def test_action_never_interpolates_expressions_into_shell():
    """`${{ }}` inside `run:` is a script-injection hole; inputs must go through env vars."""
    lines = (ROOT / "action.yml").read_text().splitlines()
    in_run, run_indent = False, 0
    offenders = []
    for n, line in enumerate(lines, 1):
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        if in_run and stripped and indent <= run_indent:
            in_run = False
        if re.match(r"(- )?run: \|", stripped):
            in_run, run_indent = True, indent
            continue
        if in_run and "${{" in line:
            offenders.append(n)
    assert offenders == []


@pytest.mark.parametrize("name", ["action.yml", "examples/github-workflow.yml"])
def test_workflow_files_pin_action_versions(name):
    uses = re.findall(r"^\s*(?:- )?uses:\s*(\S+)", (ROOT / name).read_text(), re.M)
    assert uses and all(u == "./" or "@" in u for u in uses)


@pytest.mark.parametrize("name", ["action.yml", "examples/github-workflow.yml"])
def test_workflow_files_are_valid_yaml(name):
    """An invalid action.yml is rejected by GitHub as a whole; parse it with a real YAML parser."""
    import shutil

    ruby = shutil.which("ruby")
    if ruby is None:
        pytest.skip("needs ruby (stdlib YAML parser) to validate YAML without extra Python deps")
    proc = subprocess.run([ruby, "-ryaml", "-e", "YAML.load_file(ARGV[0])", str(ROOT / name)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
