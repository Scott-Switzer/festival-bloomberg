"""Offline tests for canonical local `.env` loading.

Precedence: PROCESS ENV > LOCAL `.env` > NOT_CONFIGURED. Loading never prints
values, never overwrites process env, and is disabled by
``FESTIVAL_BLOOMBERG_SKIP_ENV_FILE=1`` (the hermetic test default).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from festival_bloomberg.localenv import SKIP_ENV_VAR, load_local_env


def _write_env(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_loads_env_when_process_var_absent(monkeypatch, tmp_path):
    env_file = _write_env(tmp_path / ".env", "YOUTUBE_API_KEY=abc123\nMONID_API_KEY=def456\n")
    monkeypatch.delenv(SKIP_ENV_VAR, raising=False)  # loader must be active here
    for name in ("YOUTUBE_API_KEY", "MONID_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    loaded = load_local_env(env_file)
    assert loaded == 2
    assert os.environ["YOUTUBE_API_KEY"] == "abc123"
    assert os.environ["MONID_API_KEY"] == "def456"


def test_process_env_wins_over_env_file(monkeypatch, tmp_path):
    env_file = _write_env(tmp_path / ".env", "YOUTUBE_API_KEY=from-file\n")
    monkeypatch.delenv(SKIP_ENV_VAR, raising=False)
    monkeypatch.setenv("YOUTUBE_API_KEY", "from-process")
    loaded = load_local_env(env_file)
    assert loaded == 0  # already present -> not overwritten
    assert os.environ["YOUTUBE_API_KEY"] == "from-process"


def test_skip_flag_disables_loading(monkeypatch, tmp_path):
    env_file = _write_env(tmp_path / ".env", "YOUTUBE_API_KEY=abc123\n")
    monkeypatch.setenv(SKIP_ENV_VAR, "1")
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    assert load_local_env(env_file) == 0
    assert "YOUTUBE_API_KEY" not in os.environ


def test_loading_never_prints_values(monkeypatch, tmp_path, capsys):
    env_file = _write_env(tmp_path / ".env", "YOUTUBE_API_KEY=super-secret-value\n")
    monkeypatch.delenv(SKIP_ENV_VAR, raising=False)
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    load_local_env(env_file)
    captured = capsys.readouterr()
    assert "super-secret-value" not in captured.out
    assert "super-secret-value" not in captured.err


def test_missing_file_returns_zero():
    assert load_local_env("/nonexistent/.env") == 0


def test_dotenv_is_git_ignored_and_untracked():
    # The canonical .env must never be committed.
    out = subprocess.run(
        ["git", "check-ignore", ".env"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert out.returncode == 0
    assert ".env" in out.stdout
