"""Design Partner data contract, flexible column mapping, and PII detection.

The canonical customer historical-event contract is a *closed* set of fields.
Real promoter files will not use those exact names, so a conservative mapping
engine translates arbitrary headers into canonical fields. The engine never
silently maps an ambiguous header: ambiguous columns become REVIEW_REQUIRED
with their candidate list, and PII columns are quarantined before any value
is read into an analytical table.

Semantic guards (the "never silently map" rules):

* attendance  is never mapped to tickets_sold
* gross       is never mapped to promoter_contribution
* cap         is never mapped to paid attendance
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Canonical data contract
# ---------------------------------------------------------------------------
# category -> field -> metadata. Every field has a definition, python type,
# unit semantics, null semantics, and a sensitivity flag (all customer fields
# are private; `sensitive` here means "never print, even hashed, in reports").
FIELD_CATEGORIES = {
    "identity": [
        "customer_event_id", "artist_name", "artist_external_id", "venue_name",
        "venue_external_id", "market", "city", "state", "country",
        "event_date", "event_time", "timezone",
    ],
    "decision_timing": [
        "offer_date", "booking_date", "announcement_date", "presale_date", "onsale_date",
    ],
    "event_configuration": [
        "venue_capacity", "event_usable_capacity", "ticket_capacity",
        "configuration_name", "event_status",
    ],
    "deal": [
        "deal_type", "artist_guarantee", "artist_backend_pct", "artist_bonus",
        "artist_expenses",
    ],
    "ticketing": [
        "tickets_sold", "paid_tickets", "comp_tickets", "refunded_tickets",
        "scanned_attendance", "paid_attendance", "reported_attendance",
        "ticket_gross", "ticket_net", "average_paid_ticket",
        "face_value_min", "face_value_max", "sold_out",
    ],
    "costs": [
        "marketing_spend", "venue_cost", "production_cost", "labor_cost",
        "security_cost", "insurance_cost", "other_cost",
    ],
    "ancillary": [
        "merch_revenue", "fnb_revenue", "parking_revenue", "vip_revenue",
        "sponsor_revenue", "other_revenue",
    ],
    "settlement": [
        "promoter_contribution", "settlement_gross", "settlement_net",
    ],
    "metadata": [
        "currency", "source_system", "source_file", "notes",
    ],
}

_DATE_FIELDS = frozenset(
    FIELD_CATEGORIES["decision_timing"]
    + ["event_date"]
)
_NUMERIC_FIELDS = frozenset(
    FIELD_CATEGORIES["event_configuration"][:3]  # venue_capacity, event_usable_capacity, ticket_capacity
    + FIELD_CATEGORIES["deal"][1:]                # guarantee, backend_pct, bonus, expenses
    + FIELD_CATEGORIES["ticketing"][:9]           # tickets/attendance/counts
    + FIELD_CATEGORIES["ticketing"][9:12]         # ticket_gross, ticket_net, average_paid_ticket
    + FIELD_CATEGORIES["costs"]
    + FIELD_CATEGORIES["ancillary"]
    + FIELD_CATEGORIES["settlement"]
)
_BOOLEAN_FIELDS = frozenset({"sold_out"})

CANONICAL_FIELDS: list[str] = []
for _category_fields in FIELD_CATEGORIES.values():
    CANONICAL_FIELDS.extend(_category_fields)

FIELD_DEFINITIONS: dict[str, dict[str, Any]] = {
    # identity
    "customer_event_id": {"definition": "customer's unique event identifier", "type": "text", "sensitive": False},
    "artist_name": {"definition": "headline artist display name", "type": "text", "sensitive": False},
    "artist_external_id": {"definition": "customer's artist identifier", "type": "text", "sensitive": False},
    "venue_name": {"definition": "venue display name", "type": "text", "sensitive": False},
    "venue_external_id": {"definition": "customer's venue identifier", "type": "text", "sensitive": False},
    "market": {"definition": "market label (e.g. Chicago)", "type": "text", "sensitive": False},
    "city": {"definition": "city proper", "type": "text", "sensitive": False},
    "state": {"definition": "state or province", "type": "text", "sensitive": False},
    "country": {"definition": "country code", "type": "text", "sensitive": False},
    "event_date": {"definition": "event local date", "type": "date", "sensitive": False},
    "event_time": {"definition": "event local time", "type": "text", "sensitive": False},
    "timezone": {"definition": "IANA timezone", "type": "text", "sensitive": False},
    # decision timing
    "offer_date": {"definition": "date the offer was made", "type": "date", "sensitive": False},
    "booking_date": {"definition": "date the booking was confirmed", "type": "date", "sensitive": False},
    "announcement_date": {"definition": "date the show was publicly announced", "type": "date", "sensitive": False},
    "presale_date": {"definition": "presale start date", "type": "date", "sensitive": False},
    "onsale_date": {"definition": "public on-sale date", "type": "date", "sensitive": False},
    # event configuration
    "venue_capacity": {"definition": "venue maximum capacity", "type": "number", "sensitive": False},
    "event_usable_capacity": {"definition": "event-specific usable capacity (configuration)", "type": "number", "sensitive": False},
    "ticket_capacity": {"definition": "tickets placed on sale", "type": "number", "sensitive": False},
    "configuration_name": {"definition": "seating/GA configuration name", "type": "text", "sensitive": False},
    "event_status": {"definition": "performed / cancelled / postponed", "type": "text", "sensitive": False},
    # deal
    "deal_type": {"definition": "deal structure label", "type": "text", "sensitive": False},
    "artist_guarantee": {"definition": "artist guarantee", "type": "money", "sensitive": False},
    "artist_backend_pct": {"definition": "artist backend percentage", "type": "percent", "sensitive": False},
    "artist_bonus": {"definition": "artist bonus", "type": "money", "sensitive": False},
    "artist_expenses": {"definition": "artist-expense allowance", "type": "money", "sensitive": False},
    # ticketing
    "tickets_sold": {"definition": "tickets sold (all types)", "type": "number", "sensitive": False},
    "paid_tickets": {"definition": "paid tickets", "type": "number", "sensitive": False},
    "comp_tickets": {"definition": "complimentary tickets", "type": "number", "sensitive": False},
    "refunded_tickets": {"definition": "refunded tickets", "type": "number", "sensitive": False},
    "scanned_attendance": {"definition": "scanned/checked-in attendance", "type": "number", "sensitive": False},
    "paid_attendance": {"definition": "paid attendance", "type": "number", "sensitive": False},
    "reported_attendance": {"definition": "reported attendance", "type": "number", "sensitive": False},
    "ticket_gross": {"definition": "ticket gross revenue", "type": "money", "sensitive": False},
    "ticket_net": {"definition": "ticket net revenue", "type": "money", "sensitive": False},
    "average_paid_ticket": {"definition": "average paid ticket price", "type": "money", "sensitive": False},
    "face_value_min": {"definition": "minimum face value", "type": "money", "sensitive": False},
    "face_value_max": {"definition": "maximum face value", "type": "money", "sensitive": False},
    "sold_out": {"definition": "explicit sold-out flag", "type": "boolean", "sensitive": False},
    # costs
    "marketing_spend": {"definition": "marketing spend", "type": "money", "sensitive": False},
    "venue_cost": {"definition": "venue rental/fee", "type": "money", "sensitive": False},
    "production_cost": {"definition": "production cost", "type": "money", "sensitive": False},
    "labor_cost": {"definition": "labor cost", "type": "money", "sensitive": False},
    "security_cost": {"definition": "security cost", "type": "money", "sensitive": False},
    "insurance_cost": {"definition": "insurance cost", "type": "money", "sensitive": False},
    "other_cost": {"definition": "other costs", "type": "money", "sensitive": False},
    # ancillary
    "merch_revenue": {"definition": "merchandise revenue", "type": "money", "sensitive": False},
    "fnb_revenue": {"definition": "food & beverage revenue", "type": "money", "sensitive": False},
    "parking_revenue": {"definition": "parking revenue", "type": "money", "sensitive": False},
    "vip_revenue": {"definition": "VIP revenue", "type": "money", "sensitive": False},
    "sponsor_revenue": {"definition": "sponsorship revenue", "type": "money", "sensitive": False},
    "other_revenue": {"definition": "other revenue", "type": "money", "sensitive": False},
    # settlement
    "promoter_contribution": {"definition": "promoter net contribution", "type": "money", "sensitive": False},
    "settlement_gross": {"definition": "settlement gross", "type": "money", "sensitive": False},
    "settlement_net": {"definition": "settlement net", "type": "money", "sensitive": False},
    # metadata
    "currency": {"definition": "ISO currency code", "type": "text", "sensitive": False},
    "source_system": {"definition": "customer source system", "type": "text", "sensitive": False},
    "source_file": {"definition": "originating file name", "type": "text", "sensitive": False},
    "notes": {"definition": "free-text notes", "type": "text", "sensitive": False},
}


# ---------------------------------------------------------------------------
# Sharing policies (default is strictly private; pooling is opt-in only)
# ---------------------------------------------------------------------------
SHARING_PRIVATE_ONLY = "PRIVATE_ONLY"
SHARING_ANONYMIZED_POOL_OPT_IN = "ANONYMIZED_POOL_OPT_IN"
SHARING_AGGREGATE_BENCHMARK_OPT_IN = "AGGREGATE_BENCHMARK_OPT_IN"
SHARING_POLICIES = frozenset({
    SHARING_PRIVATE_ONLY, SHARING_ANONYMIZED_POOL_OPT_IN, SHARING_AGGREGATE_BENCHMARK_OPT_IN,
})


# ---------------------------------------------------------------------------
# Flexible column mapping
# ---------------------------------------------------------------------------
AUTO_ACCEPTED = "AUTO_ACCEPTED"
REVIEW_REQUIRED = "REVIEW_REQUIRED"
UNMAPPED = "UNMAPPED"
REJECTED = "REJECTED"
MAPPING_STATUSES = frozenset({AUTO_ACCEPTED, REVIEW_REQUIRED, UNMAPPED, REJECTED})


def _norm(header: str) -> str:
    """Normalize a header for comparison: lowercase, non-alnum -> underscore."""
    return re.sub(r"[^a-z0-9]+", "_", header.strip().lower()).strip("_")


# Unambiguous aliases: normalized header -> canonical field (auto-accepted).
# Kept deliberately small; anything else requires review.
UNAMBIGUOUS_ALIASES: dict[str, str] = {
    "show_date": "event_date",
    "concert_date": "event_date",
    "date": "event_date",
    "gig_date": "event_date",
    "artist": "artist_name",
    "act": "artist_name",
    "venue": "venue_name",
    "room": "venue_name",
    "market": "market",
    "city": "city",
    "state": "state",
    "country": "country",
    "capacity": "venue_capacity",
    "venue_cap": "venue_capacity",
    "guarantee": "artist_guarantee",
    "artist_guarantee": "artist_guarantee",
    "settle_net": "settlement_net",
    "settlement": "settlement_net",
    "gross": "ticket_gross",
    "ticket_gross": "ticket_gross",
    "net": "ticket_net",
    "scanned": "scanned_attendance",
    "scan": "scanned_attendance",
    "currency": "currency",
    "onsale": "onsale_date",
    "onsale_date": "onsale_date",
    "announcement": "announcement_date",
    "booking_date": "booking_date",
    "presale": "presale_date",
    "offer_date": "offer_date",
}

# Ambiguous aliases: normalized header -> candidate canonical fields. These are
# NEVER auto-mapped; a human (or an explicit customer mapping) must choose.
AMBIGUOUS_ALIASES: dict[str, list[str]] = {
    "final_sold": ["tickets_sold", "paid_tickets"],
    "paid": ["paid_tickets", "paid_attendance"],
    "tickets": ["tickets_sold", "paid_tickets"],
    "qty_sold": ["tickets_sold", "paid_tickets"],
    "sold": ["tickets_sold", "paid_tickets"],
    "cap": ["venue_capacity", "event_usable_capacity", "ticket_capacity"],
    "attendance": ["paid_attendance", "scanned_attendance", "reported_attendance"],
    "crowd": ["reported_attendance", "scanned_attendance"],
    "sold_out": ["sold_out"],
    "status": ["event_status"],
    "event_status": ["event_status"],
}


@dataclass
class ColumnMapping:
    header: str
    normalized: str
    canonical_field: str | None = None
    confidence: float = 0.0
    status: str = UNMAPPED
    reason: str = ""
    candidates: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "header": self.header,
            "canonical_field": self.canonical_field,
            "confidence": round(self.confidence, 3),
            "status": self.status,
            "reason": self.reason,
            "candidates": self.candidates,
        }


def map_column(header: str) -> ColumnMapping:
    normalized = _norm(header)
    if normalized in CANONICAL_FIELDS or header.strip().lower() in CANONICAL_FIELDS:
        return ColumnMapping(
            header=header,
            normalized=normalized,
            canonical_field=normalized if normalized in CANONICAL_FIELDS else header.strip().lower(),
            confidence=1.0,
            status=AUTO_ACCEPTED,
            reason="exact canonical field name",
        )
    if normalized in UNAMBIGUOUS_ALIASES:
        return ColumnMapping(
            header=header,
            normalized=normalized,
            canonical_field=UNAMBIGUOUS_ALIASES[normalized],
            confidence=0.9,
            status=AUTO_ACCEPTED,
            reason="unambiguous alias",
        )
    if normalized in AMBIGUOUS_ALIASES:
        candidates = AMBIGUOUS_ALIASES[normalized]
        return ColumnMapping(
            header=header,
            normalized=normalized,
            canonical_field=None,
            confidence=0.0,
            status=REVIEW_REQUIRED,
            reason="ambiguous alias; multiple candidate fields",
            candidates=candidates,
        )
    return ColumnMapping(
        header=header,
        normalized=normalized,
        status=UNMAPPED,
        reason="no canonical field or alias matched",
    )


def map_columns(headers: list[str]) -> list[ColumnMapping]:
    return [map_column(h) for h in headers]


# ---------------------------------------------------------------------------
# PII minimization
# ---------------------------------------------------------------------------
SAFE = "SAFE"
POTENTIAL_PII = "POTENTIAL_PII"
PROHIBITED = "PROHIBITED"
PII_STATUSES = frozenset({SAFE, POTENTIAL_PII, PROHIBITED})

# Buyer-level fields we never need. These are quarantined on sight.
_PROHIBITED_PATTERNS = [
    r"email", r"e_mail", r"mail_address",
    r"phone", r"mobile", r"cell",
    r"credit_card", r"card_number", r"cc_num", r"cvv", r"cvc", r"security_code",
    r"expiry", r"expiration",
    r"ssn", r"social_security", r"date_of_birth", r"dob",
    r"transaction_id", r"order_id", r"payment_method", r"last4", r"bank",
    r"billing_address", r"shipping_address",
]
# Possibly buyer-level; flagged for review, never ingested by default.
_POTENTIAL_PII_PATTERNS = [
    r"first_name", r"last_name", r"full_name", r"buyer", r"customer",
    r"street", r"address", r"zip", r"postal",
    r"^name$", r"^email$",
]
# Business-entity names that must NOT be treated as buyer PII.
_PII_EXEMPT_NAMES = {
    "artist_name", "venue_name", "event_name", "configuration_name",
    "customer_event_id", "venue_external_id", "artist_external_id",
}


def classify_pii(header: str) -> str:
    normalized = _norm(header)
    if normalized in _PII_EXEMPT_NAMES:
        return SAFE
    for pattern in _PROHIBITED_PATTERNS:
        if re.search(pattern, normalized):
            return PROHIBITED
    for pattern in _POTENTIAL_PII_PATTERNS:
        if re.search(pattern, normalized):
            return POTENTIAL_PII
    return SAFE


def scan_pii_columns(headers: list[str]) -> dict[str, str]:
    """Map each header to its PII classification. Values are never read here."""
    return {h: classify_pii(h) for h in headers}


# ---------------------------------------------------------------------------
# Type inference (used for mapping confidence and audit, never to coerce)
# ---------------------------------------------------------------------------
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T].*)?$")
_US_DATE = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")


def infer_scalar_type(values: list[Any]) -> str:
    non_empty = [v for v in values if v is not None and str(v).strip() != ""]
    if not non_empty:
        return "empty"
    if all(isinstance(v, bool) for v in non_empty):
        return "boolean"
    if all(_looks_int(v) for v in non_empty):
        return "integer"
    if all(_looks_float(v) for v in non_empty):
        return "float"
    if all(_looks_date(v) for v in non_empty):
        return "date"
    if all(_looks_bool_text(v) for v in non_empty):
        return "boolean"
    return "text"


def _looks_int(value: Any) -> bool:
    try:
        int(str(value).strip().replace(",", ""))
        return True
    except (ValueError, TypeError):
        return False


def _looks_float(value: Any) -> bool:
    try:
        float(str(value).strip().replace(",", "").replace("$", ""))
        return True
    except (ValueError, TypeError):
        return False


def _looks_date(value: Any) -> bool:
    text = str(value).strip()
    return bool(_ISO_DATE.match(text) or _US_DATE.match(text))


def _looks_bool_text(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "false", "yes", "no", "1", "0", "y", "n"}
