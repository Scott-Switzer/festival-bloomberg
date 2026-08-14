#!/bin/bash
set -eu

# Install Festival Bloomberg Economics Snapshot LaunchAgent
# This script generates the plist from template and loads it into launchd

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FB_HOME="${HOME}"
LOG_DIR="${HOME}/.local/state/festival-bloomberg"
PLIST_DEST="${HOME}/Library/LaunchAgents/com.festival-bloomberg.economics-snapshot.plist"

# Create log directory
mkdir -p "$LOG_DIR"

# Generate plist from template
TEMPLATE="${REPO_ROOT}/scripts/com.festival-bloomberg.economics-snapshot.plist.template"
sed -e "s|{{FB_HOME}}|${FB_HOME}|g" \
    -e "s|{{REPO_ROOT}}|${REPO_ROOT}|g" \
    -e "s|{{LOG_DIR}}|${LOG_DIR}|g" \
    "$TEMPLATE" > "$PLIST_DEST"

# Make wrapper script executable
chmod +x "${REPO_ROOT}/scripts/economics_snapshot_wrapper.sh"

# Load the LaunchAgent
launchctl load "$PLIST_DEST"

echo "LaunchAgent installed and loaded:"
echo "  Plist: $PLIST_DEST"
echo "  Logs: $LOG_DIR"
echo "  Cadence: 6 hours (21600 seconds)"
echo ""
echo "To start immediately: launchctl start com.festival-bloomberg.economics-snapshot"
echo "To unload: launchctl unload com.festival-bloomberg.economics-snapshot"
