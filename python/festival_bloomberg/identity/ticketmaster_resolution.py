"""Deterministic Ticketmaster attraction -> canonical artist resolution.

Ticketmaster attractions are NOT guaranteed to map 1:1 to musical artists
(festival names, tour packages, tribute acts, comedians, dance parties...).
This module resolves them against the canonical artist master + the compact
MusicBrainz reference layer WITHOUT ever auto-merging:

- exact external ID -> existing canonical mapping -> MB exact name ->
  MB exact alias -> normalized exact -> multi-signal -> fuzzy candidate
- every outcome is recorded in ``identity.ticketmaster_artist_resolutions``
  with a status that distinguishes MATCHED_ARTIST / MATCHED_EVENT_OR_PACKAGE /
  AMBIGUOUS / NO_MATCH / REJECTED_NON_ARTIST.
- special-attraction classification keeps non-artist strings OUT of
  core.artists (festival names, "&" collaboration billings, tribute acts...).

NO LLM AUTO-MERGE: an LLM may rank/explain candidates later, but this module
never creates canonical identity or overwrites an external ID on its own.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from ..identity.spotify import normalize_name

SOFTWARE_VERSION = "ticketmaster_resolution_v1"

#: Special-attraction signal -> classification (conservative; name-only hits
#: are classified as candidates, not merged identities).
SPECIAL_SIGNALS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bfestival\b", re.I), "FESTIVAL_NAME"),
    (re.compile(r"\btour\b", re.I), "TOUR_PACKAGE"),
    (re.compile(r"\bpresents\b|presents$", re.I), "PRESENTATION"),
    (re.compile(r"\b& .*\b", re.I), "COLLABORATION_BILLING"),
    (re.compile(r"\band .*\b", re.I), "COLLABORATION_BILLING"),
    (re.compile(r"\btribute\b|\brumou?rs of\b", re.I), "TRIBUTE_ACT"),
    (re.compile(r"\bcover\b|\bkaraoke\b", re.I), "COVER_BAND"),
    (re.compile(r"\bdj set\b|\bdj night\b|\blive set\b", re.I), "DJ_EVENT"),
    (re.compile(r"\bcomedy\b|\bcomedian\b", re.I), "COMEDIAN"),
    (re.compile(r"\bnight\b|\bparty\b", re.I), "DANCE_PARTY"),
    (re.compile(r"\bvs\.?\b|\bversus\b", re.I), "COLLABORATION_BILLING"),
    (re.compile(r"\bexperience\b|\bsymphony\b|\borchestra\b|\bensemble\b", re.I), "SPECIAL_EVENT"),
]


def classify_special(attraction_name: str) -> str | None:
    """Classify a name as a non-plain-artist special attraction, or None.

    Conservative: only explicit linguistic signals trigger a classification;
    a plain name like "Aerosmith" returns None (a real artist candidate).
    """
    if not attraction_name:
        return None
    for pattern, kind in SPECIAL_SIGNALS:
        if pattern.search(attraction_name):
            return kind
    return None


#: Special classifications that REJECT a plain-artist match outright.
#: COLLABORATION_BILLING, DANCE_PARTY, and SPECIAL_EVENT stay WEAK features
#: (a real band can legitimately be named "Dead & Company" and a real act can
#: be an orchestra — e.g. Trans-Siberian Orchestra), while these strongly
#: imply a package/event/tribute rather than a single artist identity.
REJECT_SPECIALS = {
    "FESTIVAL_NAME", "TOUR_PACKAGE", "PRESENTATION", "TRIBUTE_ACT",
    "COVER_BAND", "COMEDIAN", "DJ_EVENT",
}


def is_plain_artist_name(name: str) -> bool:
    """True when nothing signals a non-artist special attraction."""
    return classify_special(name) is None


def resolution_key(attraction_id: str | None, attraction_name: str, method: str, knowledge_time: str) -> str:
    material = "|".join([attraction_id or "none", normalize_name(attraction_name), method, knowledge_time[:10]])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def fetch_attraction_universe(conn) -> list[dict[str, Any]]:
    """Distinct Ticketmaster attractions from the live event snapshot corpus.

    ``attractions`` is a JSON array of {id, name, ...} objects; also fall back
    to the single ``artist_name`` column when attractions are missing.
    """
    out: dict[str, dict[str, Any]] = {}
    rows = conn.execute(
        """
        SELECT platform_object_id, attractions, artist_name, retrieved_at
        FROM events.provider_event_snapshots
        WHERE provider = 'ticketmaster'
        """
    ).fetchall()
    for _event_id, attractions, artist_name, retrieved_at in rows:
        if attractions:
            try:
                items = json.loads(attractions) if isinstance(attractions, str) else attractions
            except (ValueError, TypeError):
                items = []
            for item in items if isinstance(items, list) else []:
                if not isinstance(item, dict):
                    continue
                name = item.get("name") or item.get("attraction_name") or ""
                aid = item.get("id") or item.get("ticketmaster_attraction_id")
                if not name:
                    continue
                # Dedupe by the provider's own attraction ID when present:
                # two distinct attractions sharing a normalized name must not
                # collapse into one record (identity-resolution hazard).
                key = f"id::{aid}" if aid else f"name::{normalize_name(name)}"
                existing = out.get(key)
                if existing is None:
                    out[key] = {
                        "attraction_id": aid,
                        "attraction_name": name,
                        "first_observed_at": retrieved_at,
                        "last_observed_at": retrieved_at,
                    }
                else:
                    if (retrieved_at or "") > (existing["last_observed_at"] or ""):
                        existing["last_observed_at"] = retrieved_at
        elif artist_name:
            key = f"name::{normalize_name(artist_name)}"
            if key and key not in out:
                out[key] = {
                    "attraction_id": None,
                    "attraction_name": artist_name,
                    "first_observed_at": retrieved_at,
                    "last_observed_at": retrieved_at,
                }
    return sorted(out.values(), key=lambda r: r["attraction_name"].lower())


def _mb_exact_name_candidates(conn, normalized: str) -> list[tuple[str, str, str]]:
    """(artist_key, mbid, name) rows matching an exact reference name."""
    rows = conn.execute(
        """
        SELECT artist_key, musicbrainz_id, name FROM core.artists
        WHERE normalized_name = ?
        """,
        [normalized],
    ).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


def _mb_reference_name_candidates(conn, normalized: str) -> list[tuple[str, str]]:
    """(mbid, name) rows from the compact reference layer (exact name)."""
    rows = conn.execute(
        """
        SELECT mbid, name FROM reference.musicbrainz_artists
        WHERE normalized_name = ?
        """,
        [normalized],
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def _mb_alias_candidates(conn, normalized: str) -> list[tuple[str, str, str]]:
    """(artist_key, mbid, alias) from canonical alias index (exact alias).

    The MBID comes from ``core.artists.musicbrainz_id`` — never from the alias
    row itself, which only stores the internal artist_key.
    """
    rows = conn.execute(
        """
        SELECT a.artist_key, ar.musicbrainz_id, a.alias
        FROM core.artist_aliases a
        JOIN core.artists ar ON ar.artist_key = a.artist_key
        WHERE a.normalized_alias = ?
        """,
        [normalized],
    ).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


def _fuzzy_candidates(conn, name: str, normalized: str, limit: int = 8) -> list[dict[str, Any]]:
    """Fuzzy candidate retrieval (contains/substring + normalized edit hints).

    Returns candidates for the LLM to RANK — never an auto-merge. This uses
    cheap substring retrieval so a ranking step has something to work with.
    """
    if len(normalized) < 3:
        return []
    like = f"%{normalized}%"
    rows = conn.execute(
        """
        SELECT mbid, name FROM reference.musicbrainz_artists
        WHERE normalized_name LIKE ? OR name LIKE ?
        LIMIT ?
        """,
        [like, like, limit],
    ).fetchall()
    return [{"mbid": r[0], "name": r[1]} for r in rows]


def resolve_attraction(
    conn,
    *,
    attraction_name: str,
    attraction_id: str | None = None,
    knowledge_time: str,
) -> dict[str, Any]:
    """Resolve ONE Ticketmaster attraction to a canonical artist.

    Resolution ladder:
      A. exact external ID (attraction_id already mapped)
      B. existing canonical entity mapping (by name)
      C. MusicBrainz exact canonical name
      D. MusicBrainz exact alias
      E. normalized exact (reference layer)
      F. multi-signal (name + existing spotify/id corroboration)
      G. fuzzy candidate retrieval (LLM may rank; never auto-merge)
      H. ambiguous / unmatched

    Returns a resolution record (status, method, artist_key/mbid or None).
    """
    normalized = normalize_name(attraction_name)
    special = classify_special(attraction_name)

    # A. exact external ID mapping (explicit identity truth wins over name
    #    heuristics — a mapped attraction id is a hard fact).
    if attraction_id:
        row = conn.execute(
            """
            SELECT entity_key, id_value FROM core.entity_external_ids
            WHERE id_type = 'ticketmaster' AND id_value = ?
            """,
            [attraction_id],
        ).fetchone()
        if row:
            return {
                "resolution_status": "MATCHED_ARTIST",
                "match_method": "EXACT_EXTERNAL_ID",
                "artist_key": row[0],
                "artist_mbid": None,
                "matched_name": attraction_name,
                "match_similarity": 1.0,
                "match_features": {"attraction_id": attraction_id},
                "special_classification": special,
            }

    # A.5 Strong non-artist signals REJECT a plain-artist match before any
    #     name-based ladder runs (a tribute act that shares its name with a
    #     real band must never be merged into that band).
    if special in REJECT_SPECIALS:
        return {
            "resolution_status": "REJECTED_NON_ARTIST",
            "match_method": None,
            "artist_key": None, "artist_mbid": None, "matched_name": None,
            "match_similarity": None,
            "match_features": {"special_classification": special},
            "special_classification": special,
        }

    # B. existing canonical mapping by normalized name.
    canonical = _mb_exact_name_candidates(conn, normalized)
    if canonical:
        if len(canonical) == 1:
            key, mbid, name = canonical[0]
            return {
                "resolution_status": "MATCHED_ARTIST",
                "match_method": "EXISTING_MAPPING",
                "artist_key": key, "artist_mbid": mbid, "matched_name": name,
                "match_similarity": 1.0,
                "match_features": {"matched_via": "canonical_name"},
                "special_classification": special,
            }
        return {
            "resolution_status": "AMBIGUOUS",
            "match_method": "EXISTING_MAPPING",
            "artist_key": None, "artist_mbid": None, "matched_name": None,
            "match_similarity": None,
            "match_features": {"canonical_candidates": canonical},
            "special_classification": special,
        }

    # C/D/E. reference layer exact name / alias / normalized.
    ref = _mb_reference_name_candidates(conn, normalized)
    aliases = _mb_alias_candidates(conn, normalized)
    if ref or aliases:
        if len(ref) == 1 and not aliases:
            mbid, name = ref[0]
            return {
                "resolution_status": "MATCHED_ARTIST",
                "match_method": "MB_EXACT_NAME",
                "artist_key": None, "artist_mbid": mbid, "matched_name": name,
                "match_similarity": 1.0,
                "match_features": {"matched_via": "reference_name"},
                "special_classification": special,
            }
        if len(aliases) == 1 and not ref:
            akey, mbid, alias = aliases[0]
            return {
                "resolution_status": "MATCHED_ARTIST",
                "match_method": "MB_EXACT_ALIAS",
                "artist_key": akey, "artist_mbid": mbid, "matched_name": alias,
                "match_similarity": 1.0,
                "match_features": {"matched_via": "canonical_alias"},
                "special_classification": special,
            }
        return {
            "resolution_status": "AMBIGUOUS",
            "match_method": "MB_EXACT_NAME",
            "artist_key": None, "artist_mbid": None, "matched_name": None,
            "match_similarity": None,
            "match_features": {"reference_candidates": ref, "alias_candidates": aliases},
            "special_classification": special,
        }

    # F. multi-signal: a spotify external ID exists for this normalized name.
    spot = conn.execute(
        """
        SELECT entity_key, id_value FROM core.entity_external_ids
        WHERE id_type = 'spotify' AND entity_key = ?
        """,
        [f"name::{normalized}"],
    ).fetchall()
    if len(spot) == 1:
        return {
            "resolution_status": "MATCHED_ARTIST",
            "match_method": "MULTI_SIGNAL",
            "artist_key": spot[0][0], "artist_mbid": None, "matched_name": attraction_name,
            "match_similarity": 0.95,
            "match_features": {"spotify_id": spot[0][1]},
            "special_classification": special,
        }
    if len(spot) > 1:
        return {
            "resolution_status": "AMBIGUOUS",
            "match_method": "MULTI_SIGNAL",
            "artist_key": None, "artist_mbid": None, "matched_name": None,
            "match_similarity": None,
            "match_features": {"spotify_candidates": [s[1] for s in spot]},
            "special_classification": special,
        }

    # G. fuzzy candidate retrieval (never a merge by itself).
    candidates = _fuzzy_candidates(conn, attraction_name, normalized)
    if candidates:
        return {
            "resolution_status": "AMBIGUOUS",
            "match_method": "FUZZY_CANDIDATE",
            "artist_key": None, "artist_mbid": None, "matched_name": None,
            "match_similarity": None,
            "match_features": {"fuzzy_candidates": candidates},
            "special_classification": special,
        }

    # H. unmatched.
    status = "REJECTED_NON_ARTIST" if special else "NO_MATCH"
    return {
        "resolution_status": status,
        "match_method": None,
        "artist_key": None, "artist_mbid": None, "matched_name": None,
        "match_similarity": None,
        "match_features": {"special_classification": special},
        "special_classification": special,
    }


def persist_resolution(
    conn,
    *,
    attraction_name: str,
    attraction_id: str | None,
    source_table: str,
    knowledge_time: str,
    result: dict[str, Any],
) -> int:
    """Persist one resolution row (idempotent by key). Returns 1 if new."""
    key = resolution_key(attraction_id, attraction_name, result.get("match_method") or "NO_MATCH", knowledge_time)
    exists = conn.execute(
        "SELECT 1 FROM identity.ticketmaster_artist_resolutions WHERE resolution_key = ?",
        [key],
    ).fetchone()
    if exists:
        return 0
    conn.execute(
        """
        INSERT INTO identity.ticketmaster_artist_resolutions
            (resolution_key, attraction_id, attraction_name, normalized_name,
             artist_key, artist_mbid, matched_name, resolution_status,
             match_method, match_similarity, match_features,
             special_classification, source_table, knowledge_time,
             software_version, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        [
            key, attraction_id, attraction_name, normalize_name(attraction_name),
            result.get("artist_key"), result.get("artist_mbid"), result.get("matched_name"),
            result.get("resolution_status"), result.get("match_method"),
            result.get("match_similarity"),
            json.dumps(result.get("match_features"), default=str),
            result.get("special_classification"), source_table, knowledge_time,
            SOFTWARE_VERSION,
        ],
    )
    return 1


def resolve_attraction_universe(
    conn,
    *,
    source_table: str = "events.provider_event_snapshots",
    knowledge_time: str | None = None,
) -> dict[str, Any]:
    """Resolve the full distinct-attraction universe deterministically."""
    knowledge_time = knowledge_time or datetime.now(timezone.utc).isoformat()
    attractions = fetch_attraction_universe(conn)
    summary: dict[str, Any] = {
        "status": "RUNNING",
        "attractions_total": len(attractions),
        "by_status": {},
        "by_method": {},
        "by_special": {},
        "rows_persisted": 0,
    }
    for a in attractions:
        result = resolve_attraction(
            conn,
            attraction_name=a["attraction_name"],
            attraction_id=a["attraction_id"],
            knowledge_time=knowledge_time,
        )
        summary["rows_persisted"] += persist_resolution(
            conn,
            attraction_name=a["attraction_name"],
            attraction_id=a["attraction_id"],
            source_table=source_table,
            knowledge_time=knowledge_time,
            result=result,
        )
        status = result["resolution_status"]
        summary["by_status"][status] = summary["by_status"].get(status, 0) + 1
        method = result.get("match_method") or "NONE"
        summary["by_method"][method] = summary["by_method"].get(method, 0) + 1
        special = result.get("special_classification")
        if special:
            summary["by_special"][special] = summary["by_special"].get(special, 0) + 1
    summary["status"] = "COMPLETE"
    return summary


# ---------------------------------------------------------------------------
# Identity QA (phase 14): MBID-ground-truth sample + honest metrics.
# ---------------------------------------------------------------------------
# Positive cases carry the EXPECTED MUSICBRAINZ MBID (real identity truth,
# taken from the local reference estate — never name equality). Negative
# cases expect a non-artist classification. Categories cover same-name
# artists, diacritics, punctuation, aliases, DJs, orchestras, international
# names, tribute acts, tour packages, festival names, collaboration
# billings, and common-word band names.
# MBID ground truth is the identity ACTUALLY used by MusicBrainz event
# performer relations (core.event_performers) — the real live-performance
# identity, not an arbitrary name match from the 2.2M reference estate.
QA_SAMPLE: list[tuple[str, str | None, str]] = [
    # -- major artists (MBID verified against core.event_performers)
    ("Bruce Springsteen", "70248960-cb53-4ea4-943a-edb18f7d336f", "major_artist"),
    ("The E Street Band", "d6652e7b-33fe-49ef-8336-4c863b4f996f", "leading_the"),
    ("Taylor Swift", "20244d07-534f-4eff-b4d4-930878889970", "major_artist"),
    ("Bad Bunny", "89aa5ecb-59ad-46f5-b3eb-2d424e941f19", "major_artist"),
    ("Billie Eilish", "f4abc0b5-3f7a-4eff-8f78-ac078dbce533", "major_artist"),
    ("Peso Pluma", "75e4f8ef-34c3-44fd-8467-88a7d9599f77", "international"),
    ("Alicia Keys", "8ef1df30-ae4f-4dbd-9351-1a32b208a01e", "major_artist"),
    ("Metallica", "65f4f0c5-ef9e-490c-aee3-909e7ae6b2ab", "major_artist"),
    ("Beyoncé", "859d0860-d480-4efd-970c-c05d5f1776b8", "diacritics"),
    ("Drake", "9fff2f8a-21e6-47de-a2b8-7f449929d43f", "common_word"),
    ("The Weeknd", "c8b03190-306c-4120-bb0b-6f2ebfc06ea9", "major_artist"),
    ("Kendrick Lamar", "381086ea-f511-4aba-bdf9-71c753dc5077", "major_artist"),
    ("Ed Sheeran", "b8a7c51f-362c-4dcb-a259-bc6e0095f0a6", "major_artist"),
    ("Coldplay", "cc197bad-dc9c-440d-a5b5-d52ba2e14234", "major_artist"),
    ("Red Hot Chili Peppers", "8bfac288-ccc5-448d-9573-c33ea2aa5c30", "major_artist"),
    ("Foo Fighters", "67f66c07-6e61-4026-ade5-7e782fad3a5d", "major_artist"),
    ("Pearl Jam", "83b9cbe7-9857-49e2-ab8e-b57b01038103", "major_artist"),
    ("U2", "a3cb23fc-acd3-4ce0-8f36-1e5aa6a18432", "common_word"),
    ("The Rolling Stones", "b071f9fa-14b0-4217-8e97-eb41da73f598", "major_artist"),
    ("Queen", "0383dadf-2a4e-4d10-a46a-e9e041da8eb3", "common_word"),
    ("AC/DC", "66c662b6-6e2f-4930-8610-912e24c63ed1", "punctuation"),
    ("Lady Gaga", "650e7db6-b795-4eb5-a702-5ea2fc46c848", "major_artist"),
    ("Adele", "cc2c9c3c-b7bc-4b8b-84d8-4fbd8779e493", "major_artist"),
    ("Post Malone", "b1e26560-60e5-4236-bbdb-9aa5a8d5ee19", "major_artist"),
    ("Travis Scott", "e4a51f17-a57b-47b1-b37b-f552d0f8e9e6", "major_artist"),
    ("Sabrina Carpenter", "1882fe91-cdd9-49c9-9956-8e06a3810bd4", "major_artist"),
    ("Chappell Roan", "56a55378-f155-48de-80a5-d80104221267", "major_artist"),
    ("Dua Lipa", "6f1a58bf-9b1b-49cf-a44a-6cefad7ae04f", "major_artist"),
    ("Olivia Rodrigo", "6925db17-f35e-42f3-a4eb-84ee6bf5d4b0", "major_artist"),
    ("Miley Cyrus", "7e9bd05a-117f-4cce-87bc-e011527a8b18", "major_artist"),
    ("Bruno Mars", "afb680f2-b6eb-4cd7-a70b-a63b25c763d5", "major_artist"),
    ("The Killers", "95e1ead9-4d31-4808-a7ac-32c3614c116b", "major_artist"),
    ("Imagine Dragons", "012151a8-0f9a-44c9-997f-ebd68b5389f9", "major_artist"),
    ("Maroon 5", "0ab49580-c84f-44d4-875f-d83760ea2cfe", "major_artist"),
    ("Phish", "e01646f2-2a04-450d-8bf2-0d993082e058", "major_artist"),
    ("Dead & Company", "94f8947c-2d9c-4519-bcf9-6d11a24ad006", "punctuation"),
    ("Billy Strings", "640db492-34c4-47df-be14-96e2cd4b9fe4", "major_artist"),
    ("Zach Bryan", "51e90731-08c0-4f60-89b6-5b78e5844de8", "major_artist"),
    ("Morgan Wallen", "2077273e-eaa1-4f49-903c-f286ededecb9", "major_artist"),
    ("Luke Combs", "c20ee61f-071f-4e65-9c81-45ee931a54ce", "major_artist"),
    ("Stevie Nicks", "b7f2cca2-72c6-41fb-ae33-53370fc62fe7", "solo_group"),
    ("Fleetwood Mac", "bd13909f-1c29-4c27-a874-d4aaf27c5b1a", "legacy_group"),
    ("Oasis", "39ab1aed-75e0-4140-bd47-540276886b60", "common_word"),
    ("Rage Against the Machine", "3798b104-01cb-484c-a3b0-56adc6399b80", "major_artist"),
    ("Nine Inch Nails", "b7ffd2af-418f-4be2-bdd1-22f8b48613da", "major_artist"),
    ("Kacey Musgraves", "d1393ecb-431b-4fde-a6ea-d769f2f040cb", "major_artist"),
    ("Noah Kahan", "a2a3f910-b188-43e7-81d0-f1ac2a2f3e12", "major_artist"),
    ("Lana Del Rey", "b7539c32-53e7-4908-bda3-81449c367da6", "major_artist"),
    ("Fred again..", "bca46a0c-25c9-42ca-98c2-e64c8a5e337e", "punctuation"),
    ("DJ Khaled", "081a2d60-9791-4e05-a075-f1890355eeee", "dj_artist"),
    ("Die Ärzte", "f2fb0ff0-5679-42ec-a55c-15109ce6e320", "diacritics"),
    ("KISS", "e1f1e33e-2e4c-4d43-b91b-7064068d3283", "common_word"),
    ("Tyler, The Creator", "f6beac20-5dfe-4d1f-ae02-0b0a740aafd6", "punctuation"),
    # -- non-artist / package / special cases: expect NO plain-artist match
    ("Coachella Music Festival", None, "festival_name"),
    ("Aerosmith & Journey Tour", None, "tour_package"),
    ("Tribute to Queen", None, "tribute_act"),
    ("Rumours of Fleetwood Mac", None, "tribute_act"),
    ("Rage UK – A Tribute to Rage Against the Machine", None, "tribute_act"),
    ("Lana Del Rey Karaoke Band", None, "tribute_act"),
]


def run_identity_qa(conn, *, knowledge_time: str | None = None) -> dict[str, Any]:
    """Deterministic MBID-ground-truth QA over the labeled sample.

    Identity truth is the EXPECTED MUSICBRAINZ MBID, never name equality:
    a positive case counts correct only when the resolution returns
    MATCHED_ARTIST with that exact MBID. A negative (special) case counts
    correct only when the attraction is NOT matched as a plain artist.

    Metrics: TP / FP / TN / FN, precision, recall, false-positive rate,
    ambiguous rate, unmatched rate. PRECISION is the acceptance metric —
    a false merge is worse than UNKNOWN.
    """
    knowledge_time = knowledge_time or datetime.now(timezone.utc).isoformat()
    results = []
    tp = fp = tn = fn = 0
    ambiguous = unmatched = 0
    for name, expected_mbid, category in QA_SAMPLE:
        result = resolve_attraction(
            conn, attraction_name=name, knowledge_time=knowledge_time
        )
        status = result["resolution_status"]
        got_mbid = result.get("artist_mbid")
        if expected_mbid is None:
            # Negative case: correct when NOT matched as a plain artist.
            ok = status in ("REJECTED_NON_ARTIST", "AMBIGUOUS", "NO_MATCH")
            if ok:
                tn += 1
            else:
                fp += 1
        else:
            if status == "MATCHED_ARTIST" and got_mbid == expected_mbid:
                tp += 1
                ok = True
            elif status == "MATCHED_ARTIST":
                fp += 1  # matched, but to the WRONG identity
                ok = False
            elif status == "AMBIGUOUS":
                ambiguous += 1
                ok = False
            else:
                fn += 1  # NO_MATCH / REJECTED for an artist that should match
                ok = False
        if not ok and status == "NO_MATCH":
            unmatched += 1
        results.append({
            "attraction_name": name,
            "category": category,
            "expected_mbid": expected_mbid,
            "status": status,
            "method": result.get("match_method"),
            "matched_name": result.get("matched_name"),
            "artist_mbid": got_mbid,
            "correct": ok,
        })
    total_pos = sum(1 for _, e, _ in QA_SAMPLE if e is not None)
    total_neg = len(QA_SAMPLE) - total_pos
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / total_pos if total_pos else 0.0
    fpr = fp / total_neg if total_neg else 0.0
    return {
        "sample_size": len(QA_SAMPLE),
        "positive_cases": total_pos,
        "negative_cases": total_neg,
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "ambiguous": ambiguous,
        "unmatched": unmatched,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "false_positive_rate": round(fpr, 4),
        "ambiguous_rate": round(ambiguous / len(QA_SAMPLE), 4) if QA_SAMPLE else 0.0,
        "unmatched_rate": round(unmatched / len(QA_SAMPLE), 4) if QA_SAMPLE else 0.0,
        "by_status": {},
        "results": results,
    }
