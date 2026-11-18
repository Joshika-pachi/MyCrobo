import json
from pathlib import Path

from mycrobo.cli import main
from mycrobo.engine import ScanOptions, scan
from mycrobo.models import Severity

SAMPLE = Path(__file__).parent / "fixtures" / "sample"


def _hits(result):
    return {(f.file, f.line_start) for f in result.findings}


def test_marker_comments_found_across_languages():
    result = scan(SAMPLE)
    assert _hits(result) == {
        ("app.py", 2),
        ("app.py", 3),
        ("server.js", 1),
        ("server.js", 3),
    }
    assert result.suppressed == 1


def test_severity_mapping_and_ordering():
    result = scan(SAMPLE)
    severities = [f.severity for f in result.findings]
    assert severities == sorted(severities, key=lambda s: -s.rank)
    todo = next(f for f in result.findings if "TODO" in f.message)
    assert todo.severity is Severity.LOW


def test_min_severity_filter():
    result = scan(SAMPLE, ScanOptions(min_severity=Severity.MEDIUM))
    assert all(f.severity >= Severity.MEDIUM for f in result.findings)
    assert len(result.findings) == 3


def test_finding_id_is_stable_across_line_shifts(tmp_path):
    (tmp_path / "a.py").write_text("# TODO: fix\n")
    first = scan(tmp_path).findings[0].id
    (tmp_path / "a.py").write_text("\n\n\n# TODO: fix\n")
    assert scan(tmp_path).findings[0].id == first


def test_cli_json_output_and_exit_codes(tmp_path, capsys):
    assert main(["scan", str(SAMPLE), "--format", "json", "--fail-on", "medium"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == "0.1"
    assert payload["summary"]["total_findings"] == 4
    assert payload["summary"]["by_category"]["loose_ends"] == 4

    assert main(["scan", str(SAMPLE), "--fail-on", "high"]) == 0
    out = capsys.readouterr().out
    assert "Loose ends (4)" in out

    assert main(["scan", str(tmp_path / "missing")]) == 2


def test_cli_writes_report_file(tmp_path):
    out = tmp_path / "report.json"
    assert main(["scan", str(SAMPLE), "--format", "json", "--out", str(out), "--fail-on", "none"]) == 0
    assert json.loads(out.read_text())["summary"]["files_scanned"] == 2


def test_marker_inside_string_literal_is_ignored(tmp_path):
    (tmp_path / "a.py").write_text(
        'x = "# TODO: not a comment"\n'
        "y = '// FIXME nope'  # TODO: real one\n"
    )
    hits = [(f.line_start, f.evidence) for f in scan(tmp_path).findings]
    assert hits == [(2, "y = '// FIXME nope'  # TODO: real one")]


def test_color_falls_back_to_plain_text_on_consoles_without_ansi(monkeypatch):
    """On Windows, colors are used only if the console accepts virtual-terminal mode."""
    import mycrobo.report as report

    class Tty:
        def isatty(self):
            return True

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(report.os, "name", "nt")
    monkeypatch.setattr(report, "_enable_windows_ansi", lambda: False)
    assert report.use_color(Tty()) is False
    monkeypatch.setattr(report, "_enable_windows_ansi", lambda: True)
    assert report.use_color(Tty()) is True
    monkeypatch.setenv("NO_COLOR", "1")
    assert report.use_color(Tty()) is False


def test_enable_windows_ansi_is_safe_off_windows():
    from mycrobo.report import _enable_windows_ansi

    assert _enable_windows_ansi() in (True, False)  # never raises, even without ctypes.windll
