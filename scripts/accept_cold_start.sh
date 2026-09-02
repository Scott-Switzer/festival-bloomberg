#!/usr/bin/env bash
#
# Cold-start acceptance test for the Talent Buyer MVP terminal.
#
#   ./scripts/accept_cold_start.sh
#
# Starts from TRUE ZERO every run:
#   - no server running
#   - no cached serving DB
# then requires, in order:
#   1. fetch CURRENT metadata from the cloud Worker
#   2. download ONLY the compact serving DB
#   3. stream-SHA-verify the download against CURRENT
#   4. launch ./scripts/run_terminal.sh
#   5. /api/status reports the current generation
#   6. real artist search returns hits
#   7. artist-security contract resolves a real artist
#
# We never want to "manually rediscover" this again — this is the automated
# proof that the product can bootstrap from nothing but a network + a token.
#
# Exit code 0 = PASS, nonzero = FAIL.
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${COLD_START_PORT:-8971}"
TERMINAL_DIR="$ROOT/serving/artist_security_terminal_v1"
LOG="/tmp/talent_mvp_cold_start.log"
ADMIN_TOKEN="${TERMINAL_ADMIN_TOKEN:-}"

fail() { echo "COLD_START_FAIL: $*" >&2; exit 1; }

echo "== Talent Buyer MVP cold-start acceptance =="
echo "  root: $ROOT"

# 0. True zero: no server, no cache.
if pgrep -f "festival_bloomberg.terminal.mvp_server" >/dev/null 2>&1; then
  echo "  killing running mvp_server (must start from zero)"
  pkill -f "festival_bloomberg.terminal.mvp_server" || true
  sleep 1
fi
rm -rf "$TERMINAL_DIR"
rm -f "$LOG"
[ -d "$TERMINAL_DIR" ] && fail "could not remove cached serving dir"

# 1–4. Run the real user command in the background.
echo "  running: TERMINAL_PORT=$PORT ./scripts/run_terminal.sh"
(
  cd "$ROOT"
  TERMINAL_PORT="$PORT" TERMINAL_ADMIN_TOKEN="$ADMIN_TOKEN" \
    bash scripts/run_terminal.sh > "$LOG" 2>&1
) &

# 5. Poll /api/status.
ok=0
for _ in $(seq 1 60); do
  if curl -fsS --max-time 3 "http://127.0.0.1:$PORT/api/status" > /tmp/cs_status.json 2>/dev/null; then
    ok=1; break
  fi
  sleep 2
done
[ "$ok" = 1 ] || { echo "  --- launcher log ---"; cat "$LOG" 2>/dev/null; fail "server did not come up in 120s"; }

gen="$(python3 -c "import json; print(json.load(open('/tmp/cs_status.json')).get('generation',''))" 2>/dev/null || true)"
sha="$(python3 -c "import json; print(json.load(open('/tmp/cs_status.json')).get('sha256',''))" 2>/dev/null || true)"
[ -n "$gen" ] || fail "/api/status missing generation"
[ -n "$sha" ] && [ ${#sha} -eq 64 ] || fail "sha256 not published in status"
echo "  generation : $gen"
echo "  sha256     : $sha"

# 6. Real search.
curl -fsS --max-time 8 "http://127.0.0.1:$PORT/api/search?q=alice%20cooper&limit=5" > /tmp/cs_search.json 2>/dev/null \
  || fail "search endpoint failed"
python3 - <<'EOF' || fail "search returned no real artist hits"
import json
hits = json.load(open("/tmp/cs_search.json"))
assert hits, "no hits"
assert any(h.get("name") and h.get("tier") for h in hits), "hits lack name/tier"
print("  search     :", ", ".join(f"{h['name']} ({h['tier']})" for h in hits[:3]))
EOF

# 7. Artist-security contract on the first real hit.
ARTIST_KEY="$(python3 -c "import json; print(json.load(open('/tmp/cs_search.json'))[0]['entity_id'])")"
curl -fsS --max-time 8 "http://127.0.0.1:$PORT/api/artist-security/$(python3 -c "import urllib.parse as u; print(u.quote('$ARTIST_KEY'))")" \
  > /tmp/cs_artist.json 2>/dev/null || fail "artist-security endpoint failed"
python3 - <<'EOF' || fail "artist-security contract missing panels"
import json
p = json.load(open("/tmp/cs_artist.json"))
assert p.get("artist") and p["artist"].get("name"), "no artist"
for panel in ("attention", "markets", "evidence", "alternatives"):
    assert panel in p, f"missing panel: {panel}"
print("  artist     :", p["artist"]["name"], "| panels:", sorted(p.keys()))
EOF

echo
echo "COLD_START_ACCEPTANCE = PASS"
echo "  generation: $gen"
echo "  url       : http://127.0.0.1:$PORT"
echo "  log       : $LOG"