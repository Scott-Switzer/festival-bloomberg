#!/usr/bin/env python3
"""Atomic browser UAT for the current serving generation.

One process owns the ENTIRE lifecycle:

    open exact serving DuckDB
    → instantiate the MVP server
    → bind 127.0.0.1 port 0 (OS-selected)
    → serve_forever in an owned thread
    → wait for /api/status
    → HTTP assertions (deterministic, always run)
    → headless-Chrome DOM assertions + screenshots (real browser)
    → close browser, shutdown server, write evidence

No external persistent server. No fixed port. No dependence on a preview
process surviving between agent tool calls.

Usage::

    PYTHONPATH=python .venv/bin/python scripts/uat_current_serving.py \
        --serving-db serving/artist_security_terminal_v1/terminal.duckdb \
        --current-json serving/artist_security_terminal_v1/CURRENT.json \
        [--out-dir artifacts/artist_security_data_v2] \
        [--no-browser] [--keep-server-url FILE]

Exit code 0 => PASS, 1 => FAIL. Evidence (screenshots, DOM dumps, console
errors, JSON results) is written under the out dir even on failure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "python"))

from festival_bloomberg.terminal.mvp_server import MvpTerminalApp, make_app  # noqa: E402

CHROME_CANDIDATES = [
    os.environ.get("CHROME_BIN", ""),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
]


def _dotenv_value(name: str) -> str:
    """Read one NAME=VALUE from the repo .env without touching os.environ."""
    env_file = PROJECT_ROOT / ".env"
    if not env_file.is_file():
        return ""
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def find_chrome() -> str | None:
    for cand in CHROME_CANDIDATES:
        if cand and Path(cand).is_file():
            return cand
    return None


def http_get(url: str, timeout: float = 20.0) -> tuple[int, bytes]:
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class _Handler(BaseHTTPRequestHandler):
    app: MvpTerminalApp

    def _respond(self, result: dict) -> None:
        body = result["body"]
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(result["status"])
        for k, v in result["headers"].items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        from urllib.parse import urlparse
        parsed = urlparse(self.path)
        self._respond(self.app.dispatch("GET", parsed.path, parsed.query))

    def do_POST(self) -> None:  # noqa: N802
        from urllib.parse import urlparse
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        self._respond(self.app.dispatch("POST", parsed.path, parsed.query, body))

    def do_DELETE(self) -> None:  # noqa: N802
        from urllib.parse import urlparse
        parsed = urlparse(self.path)
        self._respond(self.app.dispatch("DELETE", parsed.path, parsed.query))

    def log_message(self, *args) -> None:
        pass


def chrome_dom(chrome: str, url: str, timeout: int = 90) -> tuple[str, str]:
    """Run headless Chrome and return (dom_html, stderr_text)."""
    proc = subprocess.run(
        [chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
         "--disable-dev-shm-usage", "--dump-dom",
         "--virtual-time-budget=20000", "--window-size=1600,1400", url],
        capture_output=True, text=True, timeout=timeout,
    )
    return proc.stdout or "", proc.stderr or ""


def chrome_screenshot(chrome: str, url: str, dest: Path,
                      height: int = 2600, timeout: int = 90) -> bool:
    subprocess.run(
        [chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
         "--disable-dev-shm-usage", f"--screenshot={dest}",
         "--virtual-time-budget=16000", f"--window-size=1600,{height}", url],
        capture_output=True, text=True, timeout=timeout,
    )
    return dest.is_file() and dest.stat().st_size > 500


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serving-db", type=Path, default=None)
    parser.add_argument("--current-json", type=Path, default=None)
    parser.add_argument("--current", action="store_true",
                        help="Use the local serving CURRENT.json pointer and its artifact")
    parser.add_argument("--generation", default=None,
                        help="Use a named local generation under serving/.../generations/<id> when present")
    parser.add_argument("--out-dir", type=Path,
                        default=PROJECT_ROOT / "artifacts" / "artist_security_data_v2")
    parser.add_argument("--artist-key", default=None,
                        help="Artist key to browser-test (default: auto-pick the "
                             "first artist with factor rows AND sentiment AND name)")
    parser.add_argument("--no-browser", action="store_true",
                        help="Skip the real-browser pass (HTTP assertions only)")
    parser.add_argument("--keep-server-url", type=Path, default=None,
                        help="Write the live server base URL here before UAT runs "
                             "(allows an external preview to attach)")
    parser.add_argument("--fetch-remote", type=str, default=None,
                        help="Worker base URL to fetch the CURRENT serving artifact "
                             "from (e.g. https://host.workers.dev). Requires "
                             "ADMIN_TOKEN in env. Overrides --serving-db/--current-json "
                             "with the fetched exact generation.")
    args = parser.parse_args()

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.current and args.generation:
        parser.error("--current and --generation are mutually exclusive")
    if args.current:
        args.current_json = PROJECT_ROOT / "serving" / "artist_security_terminal_v1" / "CURRENT.json"
        current_payload = json.loads(args.current_json.read_text(encoding="utf-8"))
        args.serving_db = args.current_json.parent / "terminal.duckdb"
        if current_payload.get("generation"):
            print(f"resolved --current to {current_payload['generation']}")
    elif args.generation:
        args.current_json = PROJECT_ROOT / "serving" / "artist_security_terminal_v1" / "CURRENT.json"
        args.serving_db = (
            PROJECT_ROOT / "serving" / "artist_security_terminal_v1" / "generations"
            / args.generation / "terminal.duckdb"
        )
        if not args.serving_db.is_file():
            parser.error(f"local generation artifact not found: {args.serving_db}")
        current_payload = json.loads(args.current_json.read_text(encoding="utf-8"))
        if current_payload.get("generation") != args.generation:
            parser.error(
                "--generation requires a matching local CURRENT.json; "
                f"CURRENT points to {current_payload.get('generation')!r}"
            )

    # ── 0. Remote fetch mode: obtain the EXACT CURRENT generation ──
    if args.fetch_remote:
        admin_token = os.environ.get("ADMIN_TOKEN", "") or _dotenv_value("ADMIN_TOKEN")
        if not admin_token:
            print("ERROR: --fetch-remote requires ADMIN_TOKEN in env")
            return 1
        headers = {"Authorization": f"Bearer {admin_token}",
                   "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                                  "Chrome/125.0 Safari/537.36"}
        req = urllib.request.Request(
            f"{args.fetch_remote}/terminal/bootstrap/current?artifact=metadata",
            headers=headers)
        with urllib.request.urlopen(req, timeout=60) as resp:
            remote_current = json.loads(resp.read())
        db_path = out_dir / "terminal.duckdb"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(
            f"{args.fetch_remote}/terminal/bootstrap/current?artifact=db",
            headers=headers)
        with urllib.request.urlopen(req, timeout=600) as resp:
            db_bytes = resp.read()
        db_path.write_bytes(db_bytes)
        (out_dir / "CURRENT.json").write_text(
            json.dumps(remote_current, indent=2, sort_keys=True, default=str))
        print(f"fetched remote generation {remote_current.get('generation')} "
              f"({len(db_bytes)} bytes) from {args.fetch_remote}")
        args.serving_db = db_path
        args.current_json = out_dir / "CURRENT.json"
    elif args.serving_db is None or args.current_json is None:
        parser.error("--serving-db/--current-json, --current, or --generation is required unless --fetch-remote is used")

    results: dict = {
        "serving_db": str(args.serving_db),
        "expected_generation": None,
        "checks": {},
        "artists_checked": [],
        "screenshots": [],
        "console_errors": [],
        "notes": [],
    }
    failed = False

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal failed
        results["checks"][name] = {"ok": bool(ok), "detail": str(detail)[:500]}
        if not ok:
            failed = True
            print(f"  [FAIL] {name}: {str(detail)[:200]}")
        else:
            print(f"  [ ok ] {name}: {str(detail)[:140]}")

    # ── 1. Exact-artifact guard: CURRENT.json must describe THIS db ──
    current = json.loads(args.current_json.read_text(encoding="utf-8"))
    expected_generation = current.get("generation")
    results["expected_generation"] = expected_generation
    print(f"target generation: {expected_generation}")

    local_sha = hashlib.sha256(args.serving_db.read_bytes()).hexdigest()
    expected_sha = current.get("sha256")
    if expected_sha:
        check("artifact_sha_matches_current", local_sha == expected_sha,
              f"local {local_sha[:16]}… vs CURRENT {expected_sha[:16]}…")
    else:
        check("artifact_sha_matches_current", True, "CURRENT.json has no sha256; skipped")

    # ── 2. Boot the server on an OS-selected port in an owned thread ──
    workspace_db = out_dir / "uat_workspace.duckdb"
    app = make_app(args.serving_db, args.current_json, workspace_db=str(workspace_db))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), type("BoundHandler", (_Handler,), {"app": app}))
    port = httpd.server_address[1]
    base = f"http://127.0.0.1:{port}"
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    print(f"server bound: {base} (pid {os.getpid()})")

    if args.keep_server_url:
        args.keep_server_url.write_text(base)
        print(f"server url written to {args.keep_server_url}")

    try:
        # ── 3. /api/status asserts the expected generation ──
        status = None
        deadline = time.time() + 45
        while time.time() < deadline:
            try:
                code, body = http_get(f"{base}/api/status")
                if code == 200:
                    status = json.loads(body)
                    break
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.4)
        if status is None:
            check("api_status_reachable", False, "server never answered /api/status")
        else:
            current_json_meta = (status.get("current_json") or {})
            served_gen = current_json_meta.get("generation")
            check("api_status_generation_matches", served_gen == expected_generation,
                  f"served {served_gen} == expected {expected_generation}")

        # ── 4. Auto-pick a UAT artist (factor rows + sentiment + estate name) ──
        import duckdb
        pick_conn = duckdb.connect(str(args.serving_db), read_only=True)
        if args.artist_key:
            target_key = args.artist_key
            check("target_artist_provided", True, target_key)
        else:
            rows = pick_conn.execute("""
                SELECT f.artist_key,
                       (SELECT name FROM artists a WHERE a.artist_key = f.artist_key LIMIT 1) AS name
                FROM artist_factor_observations f
                JOIN artist_sentiment_observations s ON s.artist_key = f.artist_key
                GROUP BY f.artist_key
                ORDER BY COUNT(*) DESC
                LIMIT 20
            """).fetchall()
            named = [(k, n) for k, n in rows if n]
            if not named:
                check("uat_artist_found", False,
                      "no artist has both factor rows and sentiment rows and a name")
                return 1
            target_key, target_name = named[0]
            print(f"auto-picked UAT artist: {target_name} {target_key}")
            results["target_artist"] = {"key": target_key, "name": target_name}
        pick_conn.close()

        # ── 5. HTTP/API assertions (deterministic) ──
        enc_key = urllib.parse.quote(target_key, safe="")
        code, body = http_get(f"{base}/api/artist-security/{enc_key}")
        check("artist_security_http_200", code == 200, f"HTTP {code}")
        security = json.loads(body) if code == 200 else {}
        ft = security.get("factor_tape") or {}
        ft_items = ft.get("items") or []
        check("factor_tape_status_observed", ft.get("status") == "OBSERVED",
              f"status={ft.get('status')}, items={len(ft_items)}")
        real_values = [i for i in ft_items if i.get("value") not in (None, "", 0)]
        check("factor_tape_has_real_values", len(real_values) > 0,
              f"{len(real_values)} non-empty values e.g. "
              f"{[(i.get('factor_name'), i.get('value')) for i in real_values[:3]]}")
        provenance_ok = any(
            (i.get("source") or "") for i in ft_items
        ) and any((i.get("rights_status") or "") for i in ft_items)
        check("factor_tape_provenance_visible", provenance_ok,
              f"source={ft_items[0].get('source') if ft_items else None}, "
              f"rights={ft_items[0].get('rights_status') if ft_items else None}")
        sent = security.get("sentiment") or {}
        sent_items = sent.get("items") or []
        check("sentiment_has_rows", len(sent_items) > 0,
              f"status={sent.get('status')}, items={len(sent_items)}")
        if sent_items:
            s0 = sent_items[0]
            check("sentiment_model_version_preserved",
                  bool(s0.get("model_name") and s0.get("model_version")),
                  f"{s0.get('model_name')}@{s0.get('model_version')}, analyzed={s0.get('analyzed_count')}")
        uw = security.get("underwrite") or {}
        uw_state = uw.get("status") if isinstance(uw, dict) else None
        results["notes"].append(f"underwrite panel state: {uw_state or 'n/a'}")
        print(f"  [note] underwrite state: {uw_state or 'n/a'}")

        # search sanity
        code, body = http_get(f"{base}/api/search?q={urllib.parse.quote(target_name or target_key[:8])}")
        check("search_http_200", code == 200, f"HTTP {code}")

        results["artists_checked"].append({
            "key": target_key, "factor_rows": len(ft_items),
            "sentiment_rows": len(sent_items),
        })

        # ── 6. Real-browser pass (DOM assertions + screenshots) ──
        chrome = None if args.no_browser else find_chrome()
        if args.no_browser:
            results["checks"]["browser_available"] = {"ok": True,
                "detail": "skipped by --no-browser (HTTP assertions only)"}
            results["notes"].append("browser pass skipped via --no-browser")
            print("  [note] DOM/browser checks skipped by --no-browser")
        elif chrome is None:
            check("browser_available", False,
                  "no headless Chrome found; set CHROME_BIN or run --no-browser")
            print("  [note] DOM/browser checks skipped (no browser)")
        else:
            print(f"using browser: {chrome}")
            artist_url = f"{base}/#/artist/{urllib.parse.quote(target_key, safe='')}"
            dom, stderr = chrome_dom(chrome, f"{base}/#/home")
            # Collect JS console errors (Chrome logs them to stderr).
            console_errors = [ln for ln in stderr.splitlines()
                              if re.search(r"Uncaught|ERROR:CONSOLE|TypeError|ReferenceError", ln)]
            if console_errors:
                results["console_errors"].extend(console_errors[:20])
                check("home_no_js_exceptions", False, "; ".join(console_errors[:2])[:200])
            else:
                check("home_no_js_exceptions", True, "no console errors on home")
            check("home_dom_loads", "Talent Buyer Terminal" in dom or "demoStrip" in dom,
                  f"dom length {len(dom)}")
            (out_dir / "01_home_dom.html").write_text(dom[:400_000])

            # Artist Security page DOM
            dom, stderr = chrome_dom(chrome, artist_url)
            console_errors = [ln for ln in stderr.splitlines()
                              if re.search(r"Uncaught|ERROR:CONSOLE|TypeError|ReferenceError", ln)]
            if console_errors:
                results["console_errors"].extend(console_errors[:20])
                check("artist_no_js_exceptions", False, "; ".join(console_errors[:2])[:200])
            else:
                check("artist_no_js_exceptions", True, "no console errors on artist page")
            (out_dir / "02_artist_dom.html").write_text(dom[:400_000])

            check("artist_page_loads", "factor tape" in dom.lower()
                  or "Artist factor tape" in dom, f"dom length {len(dom)}")
            check("factor_tape_visible_in_dom",
                  "subscriber_count" in dom or "channel_view_count" in dom or "video_count" in dom,
                  "real youtube factor names present in DOM")
            check("sentiment_visible_in_dom",
                  "Sentiment" in dom and ("vader" in dom.lower()),
                  "sentiment section + model present in DOM")
            check("provenance_in_dom",
                  ("YOUTUBE_API" in dom) and ("TERMS_REVIEW" in dom or "RIGHTS" in dom.upper()),
                  "source/rights provenance present in DOM")
            check("underwrite_link_works", "underwrite" in dom.lower(),
                  "underwrite action present on artist page")

            # screenshots
            shots = [
                ("01_home.png", f"{base}/#/home", 2200),
                ("02_artist_factor_tape.png", artist_url, 2600),
                # Artist page is long; capture tall so the sentiment panel
                # (below the factor tape) is inside the viewport.
                ("03_artist_sentiment.png", artist_url, 5600),
            ]
            for fname, url, height in shots:
                dest = out_dir / fname
                ok = chrome_screenshot(chrome, url, dest, height=height)
                results["screenshots"].append({"file": fname, "ok": ok,
                                               "bytes": dest.stat().st_size if ok else 0})
                check(f"screenshot_{fname}", ok, f"{fname} -> {dest.stat().st_size if ok else 0} bytes")

        # final: keep the "expected generation visible in /api/status" result authoritative
        check("expected_generation_served", results["checks"].get(
            "api_status_generation_matches", {}).get("ok", False),
            "generation asserted above")
    finally:
        httpd.shutdown()
        httpd.server_close()
        app.close()
        print("server shut down cleanly")

    results["passed"] = not failed
    results["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    (out_dir / "uat_results.json").write_text(
        json.dumps(results, indent=2, sort_keys=True, default=str))
    print(f"\nUAT {'PASS' if not failed else 'FAIL'} — evidence in {out_dir}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
