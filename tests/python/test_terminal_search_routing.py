"""Static contracts for search deep-link/history behavior (no browser).

The hash carries the query (PR #79 stale-render guards still apply because
navigation flows through route() -> doSearch). These tests pin the wiring
so a future edit cannot silently drop bookmarkability again.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_JS = REPO_ROOT / "apps" / "terminal" / "mvp" / "app.js"


def _src() -> str:
    return APP_JS.read_text(encoding="utf-8")


def test_search_route_reads_query_param():
    src = _src()
    assert 'params.get("q")' in src
    assert 'head === "search"' in src


def test_search_submit_updates_hash():
    src = _src()
    assert '"#/search?q=" + encodeURIComponent(q)' in src
    assert "submitSearch(document.getElementById" in src


def test_search_results_render_disambiguation():
    src = _src()
    assert "function disambig(h)" in src
    assert "disambig(h)" in src
    assert "observed events" in src


def test_artist_markets_table_shows_first_play():
    src = _src()
    assert "<th>First played</th>" in src
    assert "m.first_play_date" in src


def test_unknown_market_renders_not_found():
    src = _src()
    assert "Market not found." in src
