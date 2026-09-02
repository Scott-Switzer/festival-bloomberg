#!/usr/bin/env bash
#
# Talent Buyer MVP launcher — self-healing, cloud-first.
#
#   ./scripts/run_terminal.sh
#
# Bootstraps ONLY the compact serving artifact:
#   1. Looks for a cached serving DB + CURRENT.json
#   2. Validates the cached SHA-256 against CURRENT metadata
#   3. If absent/stale: fetches CURRENT metadata, downloads only
#      terminal.duckdb, verifies the SHA-256 (streaming), then swaps it in
#   4. Starts the MVP terminal and prints the URL
#
# It never rebuilds raw data, never requires manual R2 key lookup, and never
# opens the canonical warehouse. The compact artifact is fetched from the
# cloud Worker's admin-protected bootstrap endpoint, which reads the serving
# object from the LAKE R2 binding (no local R2 credentials involved).
#
# Environment:
#   TERMINAL_WORKER_URL   bootstrap base URL (default: deployed worker)
#   TERMINAL_PORT         server port (default: 8931)
#   TERMINAL_SKIP_UPDATE  reuse cache even when stale (debug only)
#   TERMINAL_ADMIN_TOKEN  admin token for the bootstrap endpoint (transient;
#                         falls back to ADMIN_TOKEN= in $ROOT/.env; never
#                         printed or written to disk)
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TERMINAL_DIR="$ROOT/serving/artist_security_terminal_v1"
CURRENT_JSON="$TERMINAL_DIR/CURRENT.json"
DB_PATH="$TERMINAL_DIR/terminal.duckdb"
WORKER_URL="${TERMINAL_WORKER_URL:-https://fi-acquisition-runtime.scswitzer.workers.dev}"
PORT="${TERMINAL_PORT:-8931}"

# Pick a python interpreter that can import duckdb.
PY_BIN=""
for cand in "$ROOT/.venv/bin/python" "$ROOT/venv/bin/python" python3 python; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c "import duckdb" >/dev/null 2>&1; then
    PY_BIN="$(command -v "$cand")"
    break
  fi
done
if [ -z "$PY_BIN" ]; then
  echo "ERROR: no python with duckdb found (tried .venv/bin/python, python3)." >&2
  echo "  Create one with:  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

echo "Talent Buyer MVP launcher"
echo "  terminal dir : $TERMINAL_DIR"

mkdir -p "$TERMINAL_DIR"

# ── Admin token (transient, never printed, never written) ──────────
ADMIN_TOKEN="${TERMINAL_ADMIN_TOKEN:-}"
if [ -z "$ADMIN_TOKEN" ] && [ -f "$ROOT/.env" ]; then
  # .env uses NAME=VALUE lines; read only ADMIN_TOKEN.
  ADMIN_TOKEN="$(sed -n 's/^ADMIN_TOKEN=//p' "$ROOT/.env" | head -1)"
fi

# ── sha256 of a file (streaming) ────────────────────────────────────
shasum256() {
  shasum -a 256 "$1" | awk '{print $1}'
}

fetch_json() {
  local url="$1"
  if [ -n "$ADMIN_TOKEN" ]; then
    curl -fsS -H "Authorization: Bearer $ADMIN_TOKEN" "$url"
  else
    curl -fsS "$url"
  fi
}

ensure_current() {
  if [ -n "${TERMINAL_SKIP_UPDATE:-}" ]; then
    echo "  [skip] TERMINAL_SKIP_UPDATE set — using existing cache."
    return
  fi

  local meta=""
  echo "  fetching CURRENT metadata from $WORKER_URL ..."
  meta="$(fetch_json "$WORKER_URL/terminal/bootstrap/current?artifact=metadata")" || {
    echo "WARN: could not fetch CURRENT metadata: $?" >&2
  }

  local expected_sha="" object_key="" generation=""
  if [ -n "$meta" ]; then
    expected_sha="$(printf '%s' "$meta" | "$PY_BIN" -c "import json,sys; print((json.load(sys.stdin).get('sha256') or ''))")"
    object_key="$(printf '%s' "$meta" | "$PY_BIN" -c "import json,sys; print((json.load(sys.stdin).get('object_key') or ''))")"
    generation="$(printf '%s' "$meta" | "$PY_BIN" -c "import json,sys; print((json.load(sys.stdin).get('generation') or ''))")"
    echo "  remote generation : ${generation:-unknown} (${object_key:-})"
  fi

  local local_sha=""
  if [ -f "$CURRENT_JSON" ]; then
    local_sha="$(printf '%s' "$("$PY_BIN" -c "
import json
try:
    m = json.load(open('$CURRENT_JSON'))
    print(m.get('sha256') or '')
except Exception:
    print('')
")")"
  fi

  local actual_sha=""
  if [ -f "$DB_PATH" ]; then
    actual_sha="$(shasum256 "$DB_PATH")"
  fi

  if [ -n "$expected_sha" ] && [ "$local_sha" = "$expected_sha" ] && [ "$actual_sha" = "$expected_sha" ]; then
    echo "  cached DB is current and SHA-verified ($actual_sha)."
    return
  fi

  if [ -z "$expected_sha" ]; then
    echo "WARN: no remote CURRENT metadata available; checking cached DB integrity only." >&2
    if [ -f "$DB_PATH" ] && [ -n "$actual_sha" ] && [ -f "$CURRENT_JSON" ]; then
      local cached_expected
      cached_expected="$(printf '%s' "$("$PY_BIN" -c "
import json
try:
    print(json.load(open('$CURRENT_JSON')).get('sha256') or '')
except Exception:
    print('')
")")"
      if [ -n "$cached_expected" ] && [ "$actual_sha" != "$cached_expected" ]; then
        echo "ERROR: cached DB fails SHA-256 vs local CURRENT.json." >&2
        exit 1
      fi
      echo "  cached DB SHA-verified against local CURRENT.json."
      return
    fi
    if [ -f "$DB_PATH" ]; then
      echo "  cached DB present (no metadata to verify) — using it."
      return
    fi
    echo "ERROR: no remote CURRENT metadata AND no cached DB." >&2
    echo "  Set ADMIN_TOKEN in $ROOT/.env to bootstrap from the cloud Worker." >&2
    exit 1
  fi

  echo "  downloading compact serving DB only ($expected_sha) ..."
  local tmp_db="$DB_PATH.part.$$"
  if [ -n "$ADMIN_TOKEN" ]; then
    curl -fsS -H "Authorization: Bearer $ADMIN_TOKEN" "$WORKER_URL/terminal/bootstrap/current?artifact=db" -o "$tmp_db"
  else
    curl -fsS "$WORKER_URL/terminal/bootstrap/current?artifact=db" -o "$tmp_db"
  fi

  local down_sha
  down_sha="$(shasum256 "$tmp_db")"
  if [ "$down_sha" != "$expected_sha" ]; then
    rm -f "$tmp_db"
    echo "ERROR: SHA-256 mismatch after download: got $down_sha expected $expected_sha" >&2
    exit 1
  fi

  mv -f "$tmp_db" "$DB_PATH"
  printf '%s' "$meta" > "$CURRENT_JSON.tmp"
  mv -f "$CURRENT_JSON.tmp" "$CURRENT_JSON"
  echo "  installed generation ${generation:-} (${down_sha}), bytes: $(stat -f %z "$DB_PATH" 2>/dev/null || stat -c %s "$DB_PATH")"
}

ensure_current

# ── port availability ───────────────────────────────────────────────
while lsof -n -i "TCP:$PORT" >/dev/null 2>&1; do
  echo "  port $PORT busy; trying $((PORT + 1))"
  PORT=$((PORT + 1))
done

echo "  starting MVP terminal on http://127.0.0.1:$PORT ..."
cd "$ROOT"
export PYTHONPATH="$ROOT/python${PYTHONPATH:+:$PYTHONPATH}"
exec "$PY_BIN" -m festival_bloomberg.terminal.mvp_server \
  --serving-db "$DB_PATH" \
  --current-json "$CURRENT_JSON" \
  --port "$PORT"