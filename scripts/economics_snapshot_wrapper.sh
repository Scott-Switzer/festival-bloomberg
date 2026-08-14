#!/bin/bash
set -eu

# Festival Bloomberg Economics Snapshot Wrapper
# Invoked by LaunchAgent to run recurring market history collection

# Expand HOME at runtime
REPO_ROOT="${FB_HOME:-$HOME}/CascadeProjects/festival-bloomberg"
cd "$REPO_ROOT"

# Set environment variables for the collector
export PYTHONPATH="${REPO_ROOT}/python"
export FESTIVAL_BLOOMBERG_WAREHOUSE_PATH="${REPO_ROOT}/data/warehouse/artist_market_event_history.duckdb"
export FESTIVAL_BLOOMBERG_ECON_CADENCE="${FESTIVAL_BLOOMBERG_ECON_CADENCE:-6h}"

# Run the snapshot-tracked command
python3.12 -m festival_bloomberg.cli economics snapshot-tracked \
    --db "$FESTIVAL_BLOOMBERG_WAREHOUSE_PATH"

# Exit with the Python process exit code
