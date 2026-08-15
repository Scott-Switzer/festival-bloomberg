"""Festival discovery seeds transcribed from the accepted research document.

``docs/historical_lineups_and_billing_analysis.md`` reconstructed six landmark
editions with analytical billing tiers, per-act confidence, and rationale.
This module turns that research into deterministic seed rows:

- festivals / editions (identity + date precision, never invented days)
- lineup_slots (one row per named act; performance_status=unverified because
  the roster is a reconstruction, not an observed performance)
- festival_billing_observations (one source-specific row per act with the
  research tier, confidence, rationale, and the cited source URL)

Every row carries evidence_class=RESEARCH_DISCOVERY_SEED and
commercial_use_status=RESEARCH_ONLY. These are leads to corroborate, not facts.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

SOFTWARE_VERSION = "festival_spine_v1"

#: evidence / rights labeling for research-seed rows.
EVIDENCE_CLASS = "RESEARCH_DISCOVERY_SEED"
RIGHTS_STATUS = "RESEARCH_REFERENCE"
COMMERCIAL_USE_STATUS = "RESEARCH_ONLY"
BILLING_CONTEXT = "retrospective"
EXTRACTION_METHOD = "manual"

# ---------------------------------------------------------------------------
# Seed corpus. ``tiers`` is a list of (tier_label, [ (artist, confidence,
# rationale) ]). tier_label "2/3" and "3/4" collapse to 2 and 3 with a note.
# ---------------------------------------------------------------------------
SEED = [
    {
        "key": "newport-jazz-festival",
        "name": "Newport Jazz Festival",
        "country": "US",
        "city": "Newport",
        "region": "Rhode Island",
        "first_year": 1954,
        "edition": {
            "year": 1954,
            "date_precision": "month",
            "venue_name": "Newport Casino",
            "city": "Newport",
            "region": "Rhode Island",
            "country": "US",
            "note": "Inaugural edition, held July 1954 at Newport Casino.",
            "sources": [
                "https://newportjazz.org/how-a-boston-club-birthed-the-newport-jazz-festival-in-1954",
                "https://newporthistory.org/history-bytes-the-1954-newport-jazz-festival-in-memory-of-george-wein/",
                "http://www.rirocks.net/Band%20Articles/Newport%20Jazz%20Festival%201954.htm",
            ],
        },
        "tiers": [
            ("1", [
                ("Dizzy Gillespie Quintet", "B", "contemporary bebop marquee; named in surviving lineup tables"),
                ("Billie Holiday", "B", "major vocalist and period draw"),
                ("Ella Fitzgerald", "B", "major vocalist and commercial/cultural anchor"),
                ("Oscar Peterson Trio", "B", "international piano-trio draw"),
            ]),
            ("2", [
                ("Gerry Mulligan Quartet", "B", "high-status modern-jazz ensemble"),
                ("Gene Krupa Trio", "B", "major bandleader/drummer draw"),
                ("George Shearing Quintet", "C", "period reputation; verify programme"),
                ("Erroll Garner Trio", "C", "appears in historical performance references"),
                ("Lester Young", "C", "verify soloist/ensemble credit"),
            ]),
            ("3", [
                ("Eddie Condon and ensemble", "B", "established traditional-jazz audience"),
                ("Lee Wiley", "C", "historical lineup reference"),
                ("Lee Konitz Quartet", "C", "historical lineup reference"),
                ("Johnnie Smith", "C", "period lineup reference"),
                ("Bob Wilber Sextet", "B", "origin-story first act; likely early/promotional rather than top commercial tier"),
            ]),
        ],
    },
    {
        "key": "monterey-international-pop-festival",
        "name": "Monterey International Pop Festival",
        "country": "US",
        "city": "Monterey",
        "region": "California",
        "first_year": 1967,
        "edition": {
            "year": 1967,
            "date_precision": "day",
            "start_date": "1967-06-16",
            "end_date": "1967-06-18",
            "venue_name": "Monterey County Fairgrounds",
            "city": "Monterey",
            "region": "California",
            "country": "US",
            "note": "Ran June 16-18, 1967 at the Monterey County Fairgrounds.",
            "sources": [
                "https://www.criterionchannel.com/the-complete-monterey-pop-festival/season:2?sort=alphabetical",
                "https://www.setlist.fm/festival/1967/monterey-pop-festival-1967-53d6bba1.html",
            ],
        },
        "tiers": [
            ("1", [
                ("The Mamas & the Papas", "B", "anchor; closing position signal"),
                ("Otis Redding", "B", "closing position is a strong Tier 1 signal"),
                ("The Who", "B", "major marquee draw"),
                ("Jefferson Airplane", "B", "major marquee draw"),
                ("Jimi Hendrix Experience", "B", "major marquee draw; breakout"),
            ]),
            ("2", [
                ("Simon & Garfunkel", "B", "major draw"),
                ("The Byrds", "B", "major draw"),
                ("Grateful Dead", "B", "major draw"),
                ("Janis Joplin with Big Brother and the Holding Company", "B", "major draw"),
                ("Ravi Shankar", "B", "unusually long, high-attention afternoon set"),
                ("Buffalo Springfield", "B", "major draw"),
                ("Eric Burdon & the Animals", "B", "major draw"),
            ]),
            ("3", [
                ("Hugh Masekela", "B", "curated breadth"),
                ("Country Joe and the Fish", "B", "curated breadth"),
                ("Paul Butterfield Blues Band", "B", "curated breadth"),
                ("Al Kooper", "B", "curated breadth"),
                ("Booker T. & the M.G.'s", "B", "curated breadth"),
                ("Lou Rawls", "B", "curated breadth"),
                ("Johnny Rivers", "B", "curated breadth"),
                ("The Association", "B", "curated breadth"),
                ("Laura Nyro", "B", "curated breadth"),
            ]),
            ("3/4", [
                ("Beverly Martyn", "C", "pending artifact reconciliation"),
                ("Canned Heat", "C", "pending artifact reconciliation"),
                ("Steve Miller Band", "C", "pending artifact reconciliation"),
                ("Scott McKenzie", "C", "pending artifact reconciliation"),
            ]),
        ],
    },
    {
        "key": "woodstock-music-and-art-fair",
        "name": "Woodstock Music & Art Fair",
        "country": "US",
        "city": "Bethel",
        "region": "New York",
        "first_year": 1969,
        "edition": {
            "year": 1969,
            "date_precision": "year",
            "venue_name": None,
            "city": "Bethel",
            "region": "New York",
            "country": "US",
            "note": "One main stage, four calendar days after weather delay; official performer list, delay-adjusted order required.",
            "sources": [
                "https://www.woodstock.com/lineup/",
                "https://www.bethelwoodscenter.org/museum/woodstock-history",
            ],
        },
        "tiers": [
            ("1", [
                ("Richie Havens", "B", "opened the festival"),
                ("Joan Baez", "B", "marquee draw"),
                ("The Band", "B", "marquee draw"),
                ("Creedence Clearwater Revival", "B", "marquee draw"),
                ("The Who", "B", "marquee draw"),
                ("Jefferson Airplane", "B", "marquee draw"),
                ("Joe Cocker", "B", "marquee draw"),
                ("Ten Years After", "B", "marquee draw"),
                ("Crosby, Stills, Nash & Young", "B", "marquee draw"),
                ("Jimi Hendrix", "B", "closed the festival"),
            ]),
            ("2", [
                ("Arlo Guthrie", "B", "major draw"),
                ("Janis Joplin", "B", "major draw"),
                ("Sly & the Family Stone", "B", "major draw"),
                ("Santana", "B", "major draw"),
                ("Grateful Dead", "B", "major draw"),
                ("Blood, Sweat & Tears", "B", "major draw"),
            ]),
            ("3", [
                ("Sweetwater", "C", "running-order reconstruction"),
                ("Bert Sommer", "C", "running-order reconstruction"),
                ("Tim Hardin", "C", "running-order reconstruction"),
                ("Ravi Shankar", "C", "running-order reconstruction"),
                ("Country Joe McDonald", "C", "running-order reconstruction"),
                ("Country Joe & the Fish", "C", "running-order reconstruction"),
                ("Canned Heat", "C", "running-order reconstruction"),
                ("Mountain", "C", "running-order reconstruction"),
                ("Sha Na Na", "C", "running-order reconstruction"),
                ("John Sebastian", "C", "running-order reconstruction"),
                ("Melanie", "C", "running-order reconstruction"),
            ]),
        ],
    },
    {
        "key": "glastonbury-fayre",
        "name": "Glastonbury Fayre",
        "country": "GB",
        "city": "Pilton",
        "region": "Somerset",
        "first_year": 1971,
        "edition": {
            "year": 1971,
            "date_precision": "year",
            "venue_name": None,
            "city": "Pilton",
            "region": "Somerset",
            "country": "GB",
            "note": "Pivotal Pyramid-stage edition; archive reconciliation required.",
            "sources": [
                "https://www.glastonburyfestivals.co.uk/history/",
                "https://www.vam.ac.uk/collections/the-glastonbury-festival-archive",
                "https://www.ukrockfestivals.com/glasto-71-recollections.html",
            ],
        },
        "tiers": [
            ("1", [
                ("David Bowie", "B", "highest recognizable marquee draw"),
                ("Joan Baez", "B", "highest recognizable marquee draw"),
            ]),
            ("2", [
                ("Traffic", "B", "major British/alternative draw"),
                ("Fairport Convention", "B", "major British/alternative draw"),
                ("Family", "B", "major British/alternative draw"),
                ("Hawkwind", "B", "major British/alternative draw"),
            ]),
            ("2/3", [
                ("Gilberto Gil", "B", "international/high-cultural-significance programming"),
                ("Arthur Brown", "B", "international/high-cultural-significance programming"),
                ("Gong", "B", "international/high-cultural-significance programming"),
            ]),
            ("3", [
                ("Edgar Broughton Band", "B", "established alternative/underground act"),
                ("Pink Fairies", "B", "established alternative/underground act"),
                ("Skin Alley", "B", "established alternative/underground act"),
                ("Mighty Baby", "B", "established alternative/underground act"),
            ]),
        ],
    },
    {
        "key": "lollapalooza",
        "name": "Lollapalooza",
        "country": "US",
        "city": None,
        "region": None,
        "first_year": 1991,
        "edition": {
            "year": 1991,
            "date_precision": "year",
            "venue_name": None,
            "city": None,
            "region": None,
            "country": "US",
            "note": "Travelling package; venue-specific attractions may differ. Rock Hall record: June 17 1991 press release/poster for an Aug 5 Blossom Music Center date.",
            "sources": [
                "https://www.nme.com/features/music-features/every-lollapalooza-chicago-line-up-poster-3475808",
                "https://catalog.rockhall.com/rrhof-ais/Details/archive/110000155",
            ],
        },
        "tiers": [
            ("1", [
                ("Jane's Addiction", "B", "package anchor and farewell-context headliner"),
            ]),
            ("2", [
                ("Siouxsie and the Banshees", "B", "high-profile international support"),
                ("Living Colour", "B", "high-profile national support"),
                ("Nine Inch Nails", "B", "high-profile support"),
            ]),
            ("3", [
                ("Ice-T & Body Count", "B", "strong genre/cultural draw"),
                ("Butthole Surfers", "B", "strong genre/cultural draw"),
            ]),
            ("4", [
                ("Rollins Band", "B", "specialist alternative value"),
                ("Violent Femmes", "B", "specialist alternative value"),
            ]),
        ],
    },
    {
        "key": "coachella-valley-music-and-arts-festival",
        "name": "Coachella Valley Music and Arts Festival",
        "country": "US",
        "city": "Indio",
        "region": "California",
        "first_year": 1999,
        "edition": {
            "year": 1999,
            "date_precision": "month",
            "venue_name": "Empire Polo Club",
            "city": "Indio",
            "region": "California",
            "country": "US",
            "note": "Inaugural edition at the Empire Polo Club in October 1999; headline layer reliable, side-stage extraction required.",
            "sources": [
                "https://en.wikipedia.org/wiki/Coachella",
            ],
        },
        "tiers": [
            ("1", [
                ("Beck", "B", "cross-genre destination draw"),
                ("Rage Against the Machine", "B", "cross-genre destination draw"),
                ("Tool", "B", "cross-genre destination draw"),
            ]),
            ("2", [
                ("The Chemical Brothers", "B", "major international electronic draw"),
                ("Morrissey", "B", "major international alternative draw"),
                ("Underworld", "B", "major international electronic draw"),
            ]),
            ("2/3", [
                ("Ben Harper", "B", "established national draw"),
                ("Toad the Wet Sprocket", "B", "established national draw"),
                ("Jurassic 5", "B", "high-momentum draw"),
            ]),
        ],
    },
]


def slug(value: str) -> str:
    """Stable lowercase slug for identity keys (never an identity claim)."""
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")


def _tier_int(label: str) -> int:
    return int(label.split("/")[0])


def _dedupe(*parts: object) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()


def build_seed_rows() -> dict[str, list[dict[str, Any]]]:
    """Build deterministic seed rows for festivals/editions/lineups/billing.

    Pure function: no database access. Returns lists of dicts keyed by
    ``festivals``, ``editions``, ``lineup_slots``, and ``billing_observations``.
    """
    festivals: list[dict[str, Any]] = []
    editions: list[dict[str, Any]] = []
    lineup_slots: list[dict[str, Any]] = []
    billing: list[dict[str, Any]] = []

    for fest in SEED:
        key = fest["key"]
        edition_key = f"{key}::{fest['edition']['year']}"
        festivals.append(
            {
                "festival_key": key,
                "name": fest["name"],
                "normalized_name": fest["name"].lower(),
                "aliases": None,
                "location_country": fest["country"],
                "location_city": fest["city"],
                "location_region": fest["region"],
                "first_edition_year": fest["first_year"],
                "source_system": "research_doc",
                "source_url": fest["edition"]["sources"][0],
                "evidence": {"seed": "docs/historical_lineups_and_billing_analysis.md"},
            }
        )
        editions.append(
            {
                "edition_key": edition_key,
                "festival_key": key,
                "year": fest["edition"]["year"],
                "start_date": fest["edition"].get("start_date"),
                "end_date": fest["edition"].get("end_date"),
                "venue_name": fest["edition"].get("venue_name"),
                "location_city": fest["edition"].get("city"),
                "location_region": fest["edition"].get("region"),
                "location_country": fest["edition"].get("country"),
                "date_precision": fest["edition"]["date_precision"],
                "source_system": "research_doc",
                "source_url": fest["edition"]["sources"][0],
                "evidence": {"note": fest["edition"]["note"]},
            }
        )
        order = 0
        for tier_label, acts in fest["tiers"]:
            tier = _tier_int(tier_label)
            for artist, confidence, rationale in acts:
                order += 1
                slot_key = f"{edition_key}::{slug(artist)}"
                lineup_slots.append(
                    {
                        "slot_key": slot_key,
                        "festival_key": key,
                        "edition_key": edition_key,
                        "year": fest["edition"]["year"],
                        "artist_key": None,  # unresolved — never forced
                        "artist_name": artist,
                        "normalized_artist_name": artist.lower(),
                        "performance_status": "unverified",
                        "identity_confidence": None,
                        "source_system": "research_doc",
                        "source_url": fest["edition"]["sources"][0],
                        "evidence": {
                            "seed": "docs/historical_lineups_and_billing_analysis.md",
                            "confidence": confidence,
                        },
                    }
                )
                billing.append(
                    {
                        "observation_id": _dedupe(edition_key, artist, "billing")[:24],
                        "festival_key": key,
                        "edition_key": edition_key,
                        "artist_key": None,
                        "raw_artist_name": artist,
                        "billing_context": BILLING_CONTEXT,
                        "printed_order": order,
                        "printed_tier": tier,
                        "billing_group": tier_label if "/" in tier_label else None,
                        "headline_flag": tier == 1,
                        "co_headliner_flag": None,
                        "first_line_flag": tier == 1,
                        "closing_act_flag": None,
                        "stage_name": None,
                        "day_label": None,
                        "set_time_order": None,
                        "extraction_method": EXTRACTION_METHOD,
                        "extraction_version": SOFTWARE_VERSION,
                        "identity_confidence": None,
                        "source_provider": "research_doc",
                        "source_url": fest["edition"]["sources"][0],
                        "source_document_id": "docs/historical_lineups_and_billing_analysis.md",
                        "publication_date": None,
                        "rights_status": RIGHTS_STATUS,
                        "commercial_use_status": COMMERCIAL_USE_STATUS,
                        "evidence_class": EVIDENCE_CLASS,
                        "notes": f"confidence={confidence}; {rationale}",
                        "dedupe_key": _dedupe(edition_key, artist, "billing", tier),
                        "software_version": SOFTWARE_VERSION,
                    }
                )

    return {
        "festivals": festivals,
        "editions": editions,
        "lineup_slots": lineup_slots,
        "billing_observations": billing,
    }
