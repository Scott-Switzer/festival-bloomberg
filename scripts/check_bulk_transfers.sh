#!/bin/bash
# Monitor status of bulk R2 transfers
# Usage: bash scripts/check_bulk_transfers.sh

echo "============================================"
echo "  BULK R2 TRANSFER STATUS"
echo "  $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "============================================"
echo ""

CP_DIR="/tmp/r2_checkpoints"

# Check running processes
echo "=== Running Processes ==="
PROCS=$(ps aux | grep stream_url | grep -v grep | wc -l)
echo "  Active upload processes: $PROCS"
ps aux | grep stream_url | grep -v grep | awk '{printf "  PID:%s CPU:%s%% RSS:%dMB\n", $2, $3, $6/1024}'
echo ""

# MusicBrainz 7GB (should be complete)
echo "=== Checkpoint A: MusicBrainz 7GB ==="
MB_MANIFEST="$CP_DIR/festival-intelligence-raw__bulk_musicbrainz_relational_dump=20260826-002522_mbdump.tar.bz2.manifest.json"
if [ -f "$MB_MANIFEST" ]; then
    STATUS=$(python3 -c "import json; d=json.load(open('$MB_MANIFEST')); print(d.get('verification_status','UNKNOWN'))")
    SIZE=$(python3 -c "import json; d=json.load(open('$MB_MANIFEST')); print(f'{d[\"r2_size\"]/(1024**3):.2f}')")
    echo "  ✓ COMPLETE — ${SIZE} GB — Status: $STATUS"
else
    echo "  ✗ Not found"
fi
echo ""

# Wikidata 43GB (in progress)
echo "=== Checkpoint B: Wikidata 43GB ==="
WD_CP="$CP_DIR/festival-intelligence-raw__bulk_wikidata_dump=latest-truthy_latest-truthy.nt.bz2.checkpoint.json"
WD_MANIFEST="$CP_DIR/festival-intelligence-raw__bulk_wikidata_dump=latest-truthy_latest-truthy.nt.bz2.manifest.json"
if [ -f "$WD_MANIFEST" ]; then
    STATUS=$(python3 -c "import json; d=json.load(open('$WD_MANIFEST')); print(d.get('verification_status','UNKNOWN'))")
    SIZE=$(python3 -c "import json; d=json.load(open('$WD_MANIFEST')); print(f'{d[\"r2_size\"]/(1024**3):.2f}')")
    echo "  ✓ COMPLETE — ${SIZE} GB — Status: $STATUS"
elif [ -f "$WD_CP" ]; then
    python3 -c "
import json, os
d = json.load(open('$WD_CP'))
done = len(d.get('completed_parts', []))
total = d.get('total_parts', 0)
pct = done/total*100 if total else 0
gb = done * 67108864 / (1024**3)
print(f'  In progress: {done}/{total} parts ({pct:.1f}%) = {gb:.2f} GB uploaded')
print(f'  ETA at 1 MiB/s: {(total-done)*27/3600:.1f} hours')
print(f'  Updated: {d.get(\"updated_at\", \"unknown\")}')
"
else
    echo "  ✗ Not started or checkpoint missing"
fi
echo ""

# ListenBrainz 205GB (in progress)
echo "=== Checkpoint C: ListenBrainz 205GB ==="
LB_CP="$CP_DIR/festival-intelligence-raw__bulk_listenbrainz_dump=2593-20260712-000004_listenbrainz-spark-dump-2593-20260712-000004-full.tar.checkpoint.json"
LB_MANIFEST="$CP_DIR/festival-intelligence-raw__bulk_listenbrainz_dump=2593-20260712-000004_listenbrainz-spark-dump-2593-20260712-000004-full.tar.manifest.json"
if [ -f "$LB_MANIFEST" ]; then
    STATUS=$(python3 -c "import json; d=json.load(open('$LB_MANIFEST')); print(d.get('verification_status','UNKNOWN'))")
    SIZE=$(python3 -c "import json; d=json.load(open('$LB_MANIFEST')); print(f'{d[\"r2_size\"]/(1024**3):.2f}')")
    echo "  ✓ COMPLETE — ${SIZE} GB — Status: $STATUS"
elif [ -f "$LB_CP" ]; then
    python3 -c "
import json, os
d = json.load(open('$LB_CP'))
done = len(d.get('completed_parts', []))
total = d.get('total_parts', 0)
pct = done/total*100 if total else 0
gb = done * 67108864 / (1024**3)
print(f'  In progress: {done}/{total} parts ({pct:.1f}%) = {gb:.2f} GB uploaded')
print(f'  ETA at 3 MiB/s: {(total-done)*20/3600:.1f} hours')
print(f'  Updated: {d.get(\"updated_at\", \"unknown\")}')
"
else
    echo "  ✗ Not started or checkpoint missing"
fi
echo ""

echo "=== Quick Resume Commands ==="
echo "If a transfer was interrupted, simply re-run:"
echo "  bash /tmp/r2_checkpoints/run_wd_upload.sh    # Wikidata"
echo "  bash /tmp/r2_checkpoints/run_lb_upload.sh    # ListenBrainz"
echo ""
echo "The script will resume from the last completed part automatically."
