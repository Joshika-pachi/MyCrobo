"""AI review stage, tested with a scripted fake model: no network, no cost."""

import json
import re
import textwrap
from types import SimpleNamespace

import pytest

from mycrobo.ai.client import AnthropicReviewer, CachedReviewer, ReviewerUnavailable, ReviewResponse
from mycrobo.ai.review import AiOptions
from mycrobo.ai.schema import FileReview, NewIssue, Verdict
from mycrobo.engine import ScanOptions, scan
from mycrobo.models import Category, Confidence, Severity
from mycrobo.report import render_json

AWS = "AKIA" + "Q7XK2M9PLR4TWZ3B"
DB_PASSWORD = "S3cr3t" + "Passw0rd"


class FakeReviewer:
    """Answers per file from a script: {path: FileReview | "refuse" | Exception}."""

    model = "fake-model"

    def __init__(self, script=None, cost=0.01):
        self.script = script or {}
        self.cost = cost
        self.prompts: list[str] = []
        self.system = ""

    def review(self, system, prompt):
        self.system = system
        self.prompts.append(prompt)
        path = re.search(r"^File: (.+)$", prompt, re.M).group(1)
        answer = self.script.get(path, FileReview(verdicts=[], new_issues=[]))
        if isinstance(answer, Exception):
            raise answer
        if answer == "refuse":
            return ReviewResponse(None, self.cost, self.model, refused=True)
        return ReviewResponse(answer, self.cost, self.model)


def write(tmp_path, files):
    for name, content in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(content).lstrip("\n"))


def run_ai(tmp_path, reviewer, **ai):
    options = ScanOptions(ai=AiOptions(model="claude-opus-5-5", effort="medium", **ai))
    return scan(tmp_path, options, reviewer=reviewer)


def rule_ids(tmp_path):
    """Ids of rule findings, from an offline scan."""
    return {(f.file, f.line_start): f.id for f in scan(tmp_path).findings}


SERVICE = """
    import requests

    def fetch_user(user_id):
        # TODO: add retries
        resp = requests.get(f"https://api.internal/users/{user_id}")
        return resp.json()

    def debug_dump(data):
        breakpoint()
        return data
"""


def test_triage_confirms_dismisses_and_marks_uncertain(tmp_path):
    write(tmp_path, {"svc.py": SERVICE, "keys.py": f'AWS_KEY = "{AWS}"\n'})
    ids = rule_ids(tmp_path)
    reviewer = FakeReviewer({
        "svc.py": FileReview(verdicts=[
            Verdict(finding_id=ids[("svc.py", 4)], verdict="confirmed", severity="medium",
                    explanation="No retry on a network call that fails often.", suggested_fix="Wrap in a retry."),
            Verdict(finding_id=ids[("svc.py", 9)], verdict="false_positive", severity="info",
                    explanation="Debug helper.", suggested_fix=""),
        ], new_issues=[]),
        "keys.py": FileReview(verdicts=[
            Verdict(finding_id=ids[("keys.py", 1)], verdict="false_positive", severity="info",
                    explanation="Looks like a test key.", suggested_fix=""),
        ], new_issues=[]),
    })
    result = run_ai(tmp_path, reviewer)
    by_loc = {(f.file, f.line_start): f for f in result.findings}

    todo = by_loc[("svc.py", 4)]
    assert todo.severity is Severity.MEDIUM and todo.confidence is Confidence.HIGH
    assert todo.explanation.startswith("No retry")
    assert ("svc.py", 9) not in by_loc  # dismissed
    secret = by_loc[("keys.py", 1)]  # security findings are never hidden by the model
    assert secret.confidence is Confidence.LOW and secret.explanation.startswith("AI doubts this")
    assert result.ai.confirmed == 1 and result.ai.dismissed == 2


def test_new_issues_need_real_evidence(tmp_path):
    write(tmp_path, {"svc.py": SERVICE})
    reviewer = FakeReviewer({"svc.py": FileReview(verdicts=[], new_issues=[
        NewIssue(category="coding_gaps", title="HTTP errors are ignored", severity="high",
                 line_start=1, line_end=2,  # wrong lines: must be relocated to line 6
                 evidence="return resp.json()", explanation="A 500 response crashes json().",
                 suggested_fix="Call resp.raise_for_status() first."),
        NewIssue(category="security", title="SQL injection", severity="critical", line_start=3, line_end=3,
                 evidence='cursor.execute("SELECT * FROM users WHERE id=" + user_id)',
                 explanation="Made up.", suggested_fix="-"),
    ])})
    result = run_ai(tmp_path, reviewer)
    ai = [f for f in result.findings if f.origin == "ai"]
    assert [(f.rule, f.line_start, f.category) for f in ai] == [("ai/coding-gaps", 6, Category.CODING_GAPS)]
    assert result.ai.added == 1 and result.ai.unverified == 1


def test_secrets_never_reach_the_model(tmp_path):
    write(tmp_path, {"keys.py": f'AWS_KEY = "{AWS}"\nDB = "postgres://admin:{DB_PASSWORD}@db/app"\n'})
    reviewer = FakeReviewer()
    run_ai(tmp_path, reviewer)
    sent = reviewer.system + "".join(reviewer.prompts)
    assert AWS not in sent and DB_PASSWORD not in sent
    assert "<REDACTED>" in sent


def test_budget_refusals_and_errors_keep_rule_results(tmp_path):
    write(tmp_path, {"a.py": "# TODO: one\n", "b.py": "# TODO: two\n", "c.py": "# TODO: three\n"})
    reviewer = FakeReviewer({"a.py": "refuse", "b.py": RuntimeError("timeout")})
    result = run_ai(tmp_path, reviewer, workers=1)
    assert len(result.findings) == 3  # every rule finding survives
    assert (result.ai.refusals, result.ai.errors, result.ai.files_reviewed) == (1, 1, 1)

    broke = run_ai(tmp_path, FakeReviewer(), max_cost=0.0001)
    assert broke.ai.files_skipped_budget == 3 and len(broke.findings) == 3


def test_unavailable_reviewer_stops_after_first_call(tmp_path):
    write(tmp_path, {f"m{i}.py": "# TODO\n" for i in range(5)})
    reviewer = FakeReviewer({f"m{i}.py": ReviewerUnavailable("no credentials") for i in range(5)})
    result = run_ai(tmp_path, reviewer, workers=1)
    assert len(reviewer.prompts) == 1
    assert result.ai.notes == ["no credentials"] and len(result.findings) == 5


def test_ai_findings_honour_suppressions(tmp_path):
    write(tmp_path, {"svc.py": SERVICE.replace("return resp.json()", "return resp.json()  # mycrobo-ignore")})
    reviewer = FakeReviewer({"svc.py": FileReview(verdicts=[], new_issues=[
        NewIssue(category="coding_gaps", title="HTTP errors ignored", severity="high", line_start=6, line_end=6,
                 evidence="return resp.json()", explanation="x", suggested_fix="y"),
    ])})
    result = run_ai(tmp_path, reviewer)
    assert not [f for f in result.findings if f.origin == "ai"]


def test_hotspot_files_are_reviewed_without_findings(tmp_path):
    write(tmp_path, {
        "api/upload_handler.py": """
            def upload(request):
                name = request.args["name"]
                data = request.files["f"].read()
                with open("/srv/uploads/" + name, "wb") as fh:
                    fh.write(data)
        """,
        "util/strings.py": "def shout(s):\n    return s.upper()\n",
    })
    reviewer = FakeReviewer()
    run_ai(tmp_path, reviewer)
    assert len(reviewer.prompts) == 1 and "File: api/upload_handler.py" in reviewer.prompts[0]
    assert "handles input or I/O" in reviewer.prompts[0]


def test_cache_answers_repeat_reviews_for_free(tmp_path):
    write(tmp_path / "repo", {"a.py": "# TODO: x\n"})
    inner = FakeReviewer(cost=0.5)
    cached = CachedReviewer(inner, "medium", directory=tmp_path / "cache")
    first = run_ai(tmp_path / "repo", cached)
    second = run_ai(tmp_path / "repo", cached)
    assert len(inner.prompts) == 1
    assert first.ai.cost_usd == 0.5 and second.ai.cost_usd == 0.0 and second.ai.cached == 1


def test_json_report_includes_ai_summary_and_origin(tmp_path):
    write(tmp_path, {"svc.py": SERVICE})
    reviewer = FakeReviewer({"svc.py": FileReview(verdicts=[], new_issues=[
        NewIssue(category="coding_gaps", title="HTTP errors ignored", severity="high", line_start=6, line_end=6,
                 evidence="return resp.json()", explanation="x", suggested_fix="y"),
    ])})
    payload = json.loads(render_json(run_ai(tmp_path, reviewer)))
    assert payload["ai"]["added"] == 1 and payload["ai"]["model"] == "claude-opus-5-5"
    assert {f["origin"] for f in payload["findings"]} == {"rule", "ai"}


def test_anthropic_request_shape(monkeypatch):
    """The real client sends structured output, effort, caching and refusal fallbacks."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-placeholder")
    reviewer = AnthropicReviewer("claude-opus-5-5", "high")
    captured = {}

    def fake_parse(**kwargs):
        captured.update(kwargs)
        usage = SimpleNamespace(input_tokens=1000, output_tokens=500, cache_creation_input_tokens=0,
                                cache_read_input_tokens=0)
        return SimpleNamespace(stop_reason="end_turn", model="claude-opus-5-5", usage=usage,
                               parsed_output=FileReview(verdicts=[], new_issues=[]))

    monkeypatch.setattr(reviewer.client.beta.messages, "parse", fake_parse)
    response = reviewer.review("system text", "prompt text")
    assert captured["model"] == "claude-opus-5-5"
    assert captured["output_format"] is FileReview
    assert captured["output_config"] == {"effort": "high"}
    assert captured["fallbacks"] == "default" and captured["betas"] == ["server-side-fallback-2026-07-01"]
    assert captured["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert response.cost_usd == pytest.approx((1000 * 4 + 500 * 20) / 1_000_000)


def test_exchange_round_trip_without_api_key(tmp_path, monkeypatch, capsys):
    """--ai-export writes requests; answers dropped in the folder are applied by --ai-import."""
    from mycrobo.cli import main

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    write(tmp_path / "repo", {"svc.py": SERVICE})
    repo, box = str(tmp_path / "repo"), tmp_path / "box"

    assert main(["scan", repo, "--ai-export", str(box), "--fail-on", "none"]) == 0
    assert "requests written" in capsys.readouterr().out
    [request_path] = box.glob("*.request.json")
    request = json.loads(request_path.read_text())
    assert request["file"] == "svc.py" and "verdicts" in request["schema"]["properties"]

    answer = FileReview(verdicts=[], new_issues=[
        NewIssue(category="coding_gaps", title="HTTP errors ignored", severity="high", line_start=6, line_end=6,
                 evidence="return resp.json()", explanation="x", suggested_fix="y"),
    ])
    (box / request["respond_with"]).write_text(answer.model_dump_json())

    assert main(["scan", repo, "--ai-import", str(box), "--format", "json", "--fail-on", "none"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ai"]["model"] == "external" and payload["ai"]["added"] == 1
    assert any(f["origin"] == "ai" and f["line_start"] == 6 for f in payload["findings"])


def test_exchange_ignores_answers_for_changed_code(tmp_path):
    from mycrobo.ai.client import ExchangeReviewer

    write(tmp_path / "repo", {"a.py": "# TODO: x\n"})
    box = tmp_path / "box"
    run_ai(tmp_path / "repo", ExchangeReviewer(box, "export"))
    [req] = box.glob("*.request.json")
    (box / json.loads(req.read_text())["respond_with"]).write_text(FileReview(verdicts=[], new_issues=[]).model_dump_json())

    (tmp_path / "repo" / "a.py").write_text("# TODO: y\n")  # code changed after export
    result = run_ai(tmp_path / "repo", ExchangeReviewer(box, "import"))
    assert result.ai.files_reviewed == 0 and result.ai.deferred == 1
