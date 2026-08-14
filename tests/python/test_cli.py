"""Offline CLI tests for the Festival Signal Fabric commands."""

from __future__ import annotations

from festival_bloomberg.cli.main import main


def test_collect_social_without_credentials_reports_not_configured(tmp_path, capsys):
    db = str(tmp_path / "cli.duckdb")
    code = main(
        [
            "evidence",
            "collect-social",
            "--artist",
            "Radiohead",
            "--providers",
            "youtube,monid",
            "--db",
            db,
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "NOT_CONFIGURED" in out
    assert "NO RECOMMENDATION" in out


def test_summarize_social_empty_reports_missing_evidence(tmp_path, capsys):
    db = str(tmp_path / "cli2.duckdb")
    code = main(
        [
            "evidence",
            "summarize-social",
            "--artist",
            "Radiohead",
            "--cutoff",
            "2026-08-01T00:00:00Z",
            "--db",
            db,
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "mention_count" in out
    assert "0" in out
    assert "NO RECOMMENDATION" in out


def test_collect_social_requires_artist(tmp_path):
    try:
        main(["evidence", "collect-social", "--db", str(tmp_path / "x.duckdb")])
        raised = False
    except SystemExit:
        raised = True
    assert raised
