#!/bin/sh
# LaunchAgent-suitable append-only ticket snapshot collector.
# Does not install a scheduler. Monetary cost must remain $0.
set -eu
ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="${ROOT}/python${PYTHONPATH:+:$PYTHONPATH}"
exec python3.12 -m festival_bloomberg.cli economics snapshot-upcoming --market "Chicago, IL"
