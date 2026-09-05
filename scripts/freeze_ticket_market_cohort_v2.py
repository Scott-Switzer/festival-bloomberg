"""CLI: freeze TICKET_MARKET_COHORT_V2 from draft candidates."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "python"))

from festival_bloomberg.evidence_rails.cohort_v2 import DEFAULT_DRAFT, DEFAULT_OUT, DEFAULT_DB, freeze_from_draft


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--draft", default=str(DEFAULT_DRAFT))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    report = freeze_from_draft(
        Path(args.draft),
        out_path=Path(args.out),
        db_path=Path(args.db),
        force=args.force,
    )
    print(report)
    return 0 if report.get("status") in {"FROZEN", "ALREADY_FROZEN"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
