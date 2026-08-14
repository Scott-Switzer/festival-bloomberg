"""Generic private historical outcome CSV import.

Customer outcomes are OBSERVED_PRIVATE and are never merged with public
observations. The importer validates a fixed column schema, dedups on a
deterministic claim hash, resolves a best-effort canonical event id, and
produces an error report + raw-source preservation (raw rows are hashed,
not committed verbatim).

V1 supports CSV only. No customer portal, no authentication.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..acquisition.contracts import content_hash_of
from .outcome_claims import (
    OBSERVED_PRIVATE,
    OutcomeClaim,
    RIGHTS_UNKNOWN,
)

# Expected CSV columns (names only). Extra columns are ignored; missing
# required columns are reported as errors.
REQUIRED_COLUMNS = {
    "external_event_id",
    "artist",
    "venue",
    "market",
    "event_date",
}

# Numeric outcome columns mapped to outcome types. Each becomes one claim row
# when the cell is non-empty.
NUMERIC_CLAIM_COLUMNS = {
    "guarantee": ("ARTIST_GUARANTEE", "USD"),
    "ticket_capacity": ("EVENT_USABLE_CAPACITY", None),
    "paid_tickets": ("PAID_TICKETS", None),
    "comp_tickets": ("COMP_TICKETS", None),
    "refunds": ("REFUNDED_TICKETS", None),
    "scanned_attendance": ("SCANNED_ATTENDANCE", None),
    "ticket_gross": ("TICKET_GROSS", "USD"),
    "ticket_net": ("TICKET_NET", "USD"),
    "marketing_spend": ("MARKETING_SPEND", "USD"),
    "venue_cost": ("VENUE_COST", "USD"),
    "production_cost": ("PRODUCTION_COST", "USD"),
    "labor_cost": ("LABOR_COST", "USD"),
    "merch_revenue": ("MERCH_REVENUE", "USD"),
    "fnb_revenue": ("FNB_REVENUE", "USD"),
    "vip_revenue": ("VIP_REVENUE", "USD"),
    "sponsor_revenue": ("SPONSOR_REVENUE", "USD"),
    "promoter_contribution": ("PROMOTER_CONTRIBUTION", "USD"),
}

DATE_COLUMNS = ("offer_date", "booking_date", "announcement_date", "onsale_date", "event_date")

TEXT_COLUMNS = ("deal_type",)


@dataclass
class ImportReport:
    rows_read: int = 0
    claims_built: int = 0
    claims_inserted: int = 0
    duplicates_skipped: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    raw_source_hash: str | None = None
    events_imported: list[str] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        return len(self.errors)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rows_read": self.rows_read,
            "claims_built": self.claims_built,
            "claims_inserted": self.claims_inserted,
            "duplicates_skipped": self.duplicates_skipped,
            "error_count": len(self.errors),
            "errors": self.errors,
            "raw_source_hash": self.raw_source_hash,
            "events_imported": sorted(set(self.events_imported)),
        }


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text == "":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _resolve_canonical_event_id(*, external_event_id: str, artist: str, venue: str, event_date: str) -> str:
    """Deterministic, best-effort canonical key for a private import row.

    No cross-provider identity merge happens here: the external id plus the
    artist/venue/date slug is the private-event key. Matching against the
    public graph is the caller's responsibility.
    """
    import re

    slug = re.sub(r"[^a-z0-9]+", "-", artist.lower()).strip("-") or "artist"
    return f"private_{external_event_id}_{slug}_{event_date}"


def _claim_for_column(
    row: dict[str, str],
    column: str,
    outcome_type: str,
    currency: str | None,
    canonical_event_id: str,
    source_provider: str,
    source_name: str,
) -> OutcomeClaim | None:
    raw = row.get(column)
    if raw is None or str(raw).strip() == "":
        return None
    value = _to_float(str(raw))
    if value is None:
        return None
    # Deterministic claim id: identical cell -> identical id (dedup); a
    # corrected value -> new id (append-only correction, never overwrite).
    claim_id = f"claim_{content_hash_of({
        'provider': source_provider,
        'event': canonical_event_id,
        'type': outcome_type,
        'value': value,
    })[:20]}"
    kwargs: dict[str, Any] = {
        "claim_id": claim_id,
        "canonical_event_id": canonical_event_id,
        "outcome_type": outcome_type,
        "value_numeric": value,
        "currency": currency,
        "source_provider": source_provider,
        "source_name": source_name,
        "source_document_id": row.get("external_event_id"),
        "event_time": row.get("event_date"),
        "observation_class": OBSERVED_PRIVATE,
        "rights_status": RIGHTS_UNKNOWN,
        "commercial_use_status": RIGHTS_UNKNOWN,
        "notes": f"private CSV import column {column}",
    }
    return OutcomeClaim.build(**kwargs)


def import_outcomes_csv(
    *,
    csv_path: str | Path,
    economics_repo,
    source_provider: str = "customer_csv",
    source_name: str | None = None,
) -> ImportReport:
    """Import a private historical outcomes CSV into the outcome claim ledger."""
    report = ImportReport()
    path = Path(csv_path)
    raw_bytes = path.read_bytes()
    report.raw_source_hash = content_hash_of(raw_bytes)

    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            report.errors.append({"row": 0, "reason": "no header row"})
            return report

        missing = REQUIRED_COLUMNS - set(reader.fieldnames)
        if missing:
            report.errors.append(
                {"row": 0, "reason": f"missing required columns: {sorted(missing)}"}
            )
            return report

        for index, row in enumerate(reader, start=1):
            report.rows_read += 1
            external_event_id = (row.get("external_event_id") or "").strip()
            if not external_event_id:
                report.errors.append({"row": index, "reason": "empty external_event_id"})
                continue

            canonical_event_id = _resolve_canonical_event_id(
                external_event_id=external_event_id,
                artist=(row.get("artist") or "").strip(),
                venue=(row.get("venue") or "").strip(),
                event_date=(row.get("event_date") or "").strip(),
            )

            for column, (outcome_type, currency) in NUMERIC_CLAIM_COLUMNS.items():
                if column not in reader.fieldnames:
                    continue
                try:
                    claim = _claim_for_column(
                        row,
                        column,
                        outcome_type,
                        currency,
                        canonical_event_id,
                        source_provider,
                        source_name or source_provider,
                    )
                except ValueError as exc:
                    report.errors.append({"row": index, "column": column, "reason": str(exc)})
                    continue
                if claim is None:
                    continue
                report.claims_built += 1
                if economics_repo.insert_outcome_claim(claim):
                    report.claims_inserted += 1
                    report.events_imported.append(canonical_event_id)
                else:
                    report.duplicates_skipped += 1

    return report
