#!/bin/bash
set -eu

# Ticket-Market Collector scheduler control (LaunchAgent, local pattern).
# Usage:
#   scripts/ticket_market_scheduler.sh enable
#   scripts/ticket_market_scheduler.sh disable
#   scripts/ticket_market_scheduler.sh status
#   scripts/ticket_market_scheduler.sh run-once [--deep] [--max-cost 2.00]

LABEL="com.festival-bloomberg.ticket-market-collector"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FB_HOME="${HOME}"
LOG_DIR="${HOME}/.local/state/festival-bloomberg"
PLIST_DEST="${HOME}/Library/LaunchAgents/${LABEL}.plist"

mkdir -p "$LOG_DIR"

enable() {
    TEMPLATE="${REPO_ROOT}/scripts/com.festival-bloomberg.ticket-market-collector.plist.template"
    sed -e "s|{{FB_HOME}}|${FB_HOME}|g" \
        -e "s|{{REPO_ROOT}}|${REPO_ROOT}|g" \
        -e "s|{{LOG_DIR}}|${LOG_DIR}|g" \
        "$TEMPLATE" > "$PLIST_DEST"
    chmod +x "${REPO_ROOT}/scripts/ticket_market_collector_wrapper.sh"
    if launchctl list | grep -q "$LABEL"; then
        launchctl unload "$PLIST_DEST" 2>/dev/null || true
    fi
    launchctl load "$PLIST_DEST"
    echo "Enabled: $LABEL (every 12h, fast rail, budget guard \$2.00)"
    echo "Logs: $LOG_DIR"
}

disable() {
    if [ -f "$PLIST_DEST" ]; then
        launchctl unload "$PLIST_DEST" 2>/dev/null || true
        rm -f "$PLIST_DEST"
    fi
    echo "Disabled: $LABEL"
}

status() {
    if launchctl list | grep -q "$LABEL"; then
        echo "RUNNING: $LABEL"
        launchctl list | grep "$LABEL"
    else
        echo "STOPPED: $LABEL (not loaded)"
    fi
}

run_once() {
    DEEP=0
    MAX_COST="2.00"
    for arg in "$@"; do
        case "$arg" in
            --deep) DEEP=1 ;;
            --max-cost) shift; MAX_COST="${1:-2.00}" ;;
        esac
    done
    export TICKET_MARKET_DEEP="$DEEP" TICKET_MARKET_MAX_COST="$MAX_COST"
    echo "run-once: deep=$DEEP max_cost=\$$MAX_COST"
    bash "${REPO_ROOT}/scripts/ticket_market_collector_wrapper.sh"
}

case "${1:-}" in
    enable) enable ;;
    disable) disable ;;
    status) status ;;
    run-once) shift; run_once "$@" ;;
    *) echo "Usage: $0 {enable|disable|status|run-once [--deep] [--max-cost N]}"; exit 1 ;;
esac
