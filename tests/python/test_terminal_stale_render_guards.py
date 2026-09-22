"""Stale-render guard contract for the Talent Buyer Terminal SPA.

Regression test for the PRODUCTION_RED_TEAM findings: rapid navigation
(search A -> search B, artist A -> artist B, any-route transitions while
API calls are pending) must never display stale wrong-entity data or throw
``Cannot set properties of null`` from writes into a replaced view.

The contract, extending PR #78 (home -> search) to every route:

* each top-level ``async function`` in ``apps/terminal/mvp/app.js`` (except
  the ``api()`` transport helper, which performs no DOM writes) captures
  ``const renderVersion = routeVersion;`` on entry;
* every awaited call is followed by an early return when navigation moved
  on (``renderVersion !== routeVersion``), either on the same line
  (single-line ``try { x = await ...; <guard> }``) or within the next few
  lines, so no DOM write can land on a stale view.

This is a static structural test: it cannot prove runtime absence of
races, but it fails loudly if a future render path is added without the
guard that every current path carries.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_JS = REPO_ROOT / "apps" / "terminal" / "mvp" / "app.js"

CAPTURE = "const renderVersion = routeVersion;"
GUARD_RE = re.compile(r"renderVersion\s*!==\s*routeVersion")
AWAIT_RE = re.compile(r"(?<![_a-zA-Z])await(?![_a-zA-Z])")
FUNC_RE = re.compile(r"^async function (\w+)", re.M)
# The transport helper performs no DOM writes and needs no guard.
EXEMPT = {"api"}
# Lines that match an await but need no guard: the token capture itself and
# full-line comments.
SKIP_LINE_RES = (re.compile(r"^\s*//"), re.compile(r"^\s*\*"))


def _function_spans(src: str) -> list[tuple[str, int, int]]:
    starts = [(m.group(1), m.start()) for m in FUNC_RE.finditer(src)]
    bounds = [pos for _, pos in starts] + [len(src)]
    return [(name, bounds[i], bounds[i + 1]) for i, (name, _) in enumerate(starts)]


def _read() -> str:
    assert APP_JS.exists(), f"terminal bundle missing: {APP_JS}"
    return APP_JS.read_text(encoding="utf-8")


def test_every_async_render_captures_route_token():
    src = _read()
    missing = [
        name
        for name, start, end in _function_spans(src)
        if name not in EXEMPT and CAPTURE not in src[start:end]
    ]
    assert not missing, f"async renders without route token capture: {missing}"


def test_search_capture_follows_version_bump():
    """doSearch bumps the token to invalidate in-flight home loaders (PR #78);
    its own capture must come after that bump."""
    src = _read()
    (start, end) = next(
        (s, e) for name, s, e in _function_spans(src) if name == "doSearch"
    )
    body = src[start:end]
    assert body.index("routeVersion += 1;") < body.index(CAPTURE)


def test_every_await_is_followed_by_stale_guard():
    src = _read()
    lines = src.split("\n")
    violations: list[str] = []
    for name, start, end in _function_spans(src):
        if name in EXEMPT:
            continue
        first = src.count("\n", 0, start)
        last = src.count("\n", 0, end)
        for idx in range(first, last + 1):
            line = lines[idx]
            if CAPTURE in line or not AWAIT_RE.search(line):
                continue
            stripped = line.strip()
            if any(rx.match(line) for rx in SKIP_LINE_RES):
                continue
            # Same-line guard covers single-line try/await shapes.
            if GUARD_RE.search(line):
                continue
            # Otherwise a guard must appear within the next few lines
            # (covers multi-line api(...) calls: guard lands after the
            # closing paren, never mid-expression).
            window = "\n".join(lines[idx + 1 : idx + 11])
            if not GUARD_RE.search(window):
                violations.append(f"{name}:{idx + 1}: {stripped[:100]}")
    assert not violations, "awaits without stale-render guard:\n" + "\n".join(violations)


def test_no_unescaped_template_breakout_in_shell():
    """The shell must not inline the deployment access path and the search
    header must escape the query (XSS red-team pin)."""
    src = _read()
    assert "TERMINAL_ACCESS_PATH" not in src
    assert "Search: ${esc(q)}" in src
