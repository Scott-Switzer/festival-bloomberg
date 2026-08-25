#!/bin/bash
set -eu

# Festival Bloomberg Ticket-Market Collector Wrapper
# Invoked by LaunchAgent to run recurring ticket-market observation waves.

# Expand HOME at runtime
REPO_ROOT="${FB_HOME:-$HOME}/CascadeProjects/festival-bloomberg"
cd "$REPO_ROOT"

export PYTHONPATH="${REPO_ROOT}/python"
# Local env (APIFY_TOKEN / MONID_API_KEY / TICKETS_DEV_API_KEY) is loaded by
# festival_bloomberg.localenv; the wrapper only needs the interpreter.
PY=${PYTHON:-"${REPO_ROOT}/.venv/bin/python"}
if [ ! -x "$PY" ]; then
    PY=python3
fi

# Cadence env: FAST=1 2x/day, DEEP=0 (deep is weekly / T-minus milestones).
FAST_PER_DAY="${TICKET_MARKET_FAST_PER_DAY:-2}"
DEEP="${TICKET_MARKET_DEEP:-0}"

BUDGET="${TICKET_MARKET_MAX_COST:-2.00}"
LOG_DIR="${HOME}/.local/state/festival-bloomberg"

ARGS=(--fast --max-cost "$BUDGET")
if [ "$DEEP" = "1" ]; then
    ARGS+=(--deep)
fi

# Run the collector (append-only; budget-guarded; never overwrites prior waves).
"$PY" scripts/collect_ticket_market.py "${ARGS[@]}" \
    >> "$LOG_DIR/ticket-market-collector.stdout.log" 2>&1 \
    || echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) collector exit=$?" >> "$LOG_DIR/ticket-market-collector.stderr.log"

echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) wave complete (fast=${FAST_PER_DAY}x/day deep=${DEEP})" \
    >> "$LOG_DIR/ticket-market-collector.stdout.log"
