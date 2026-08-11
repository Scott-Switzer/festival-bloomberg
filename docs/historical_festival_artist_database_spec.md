# Historical Festival Artist Database: Schema, Ingestion, and Billing-Weight Specification

Status: Proposed
Scope: Historical music-festival editions from the earliest major modern festivals onward, including Newport Jazz Festival (1954), Woodstock (1969), Glastonbury (1970), and additional regional, genre, and contemporary festivals.

## 1. Goals and design principles

The system should preserve both facts and evidence. A lineup is not merely a list of artists: it is an edition-specific claim with a source, confidence, date context, and potentially several representations (poster, programme, schedule, press report, archival recording, or community transcription).

Principles:

- Separate festival identity from a particular edition.
- Preserve raw source observations and normalized interpretations; never overwrite provenance.
- Treat announced, cancelled, substituted, surprise, guest, house-band, and actual-performance statuses separately.
- Treat billing order as an observation, not an objective truth. A poster, website, programme, and schedule can disagree.
- Use stable external identifiers wherever possible, but permit unresolved artists and later entity reconciliation.
- Make uncertainty first-class: dates, artist identity, stage, set time, and billing tier may all be unknown.
- Store poster-derived measurements as reproducible computer-vision observations, not hand-entered “importance.”
- Respect source terms, robots directives, copyright, and rate limits. Store metadata, hashes, and links by default; store archival images only where permitted.

## 2. Relational schema extension

The existing artist table remains the canonical artist entity. Add the following logical tables (PostgreSQL/Supabase types are recommended).

### festival

- id UUID primary key
- canonical_name TEXT
- normalized_name TEXT unique-ish index
- aliases JSONB
- genre_scope TEXT[]
- organizer TEXT
- official_url TEXT
- wikidata_qid TEXT
- musicbrainz_event_gid TEXT nullable
- country_code TEXT, region TEXT, city TEXT
- latitude NUMERIC, longitude NUMERIC
- first_known_year INTEGER
- last_known_year INTEGER nullable
- status TEXT: active, inactive, renamed, merged, unknown
- created_at, updated_at TIMESTAMPTZ

### festival_edition

One row per occurrence/year edition, even when an edition spans multiple days or has multiple locations.

- id UUID primary key
- festival_id FK
- edition_label TEXT (e.g. “1969”, “50th anniversary”)
- edition_number INTEGER nullable
- start_date DATE nullable, end_date DATE nullable
- date_precision TEXT: day, month, year, circa, unknown
- timezone TEXT nullable
- venue_id FK nullable
- attendance_estimate INTEGER nullable
- cancelled BOOLEAN default false
- edition_notes TEXT
- canonical_source_id FK nullable
- unique(festival_id, edition_label)

### festival_stage

- id UUID primary key
- edition_id FK
- name TEXT
- normalized_name TEXT
- stage_type TEXT: main, secondary, tent, club, acoustic, broadcast, workshop, unknown
- stage_tier INTEGER nullable (1 = most prominent)
- capacity_estimate INTEGER nullable
- source_id FK nullable

### festival_performance

This is the central roster fact: an artist or act connected to an edition.

- id UUID primary key
- edition_id FK
- artist_id FK nullable
- raw_artist_name TEXT not null
- act_type TEXT: solo, band, DJ, orchestra, ensemble, speaker, house_band, guest, unknown
- performance_status TEXT: announced, scheduled, performed, cancelled, substituted, surprise, unverified
- stage_id FK nullable
- performance_date DATE nullable
- set_start TIMESTAMPTZ nullable
- set_end TIMESTAMPTZ nullable
- time_precision TEXT: exact, approximate, day, unknown
- set_duration_minutes NUMERIC nullable
- source_confidence NUMERIC(4,3) default 0
- identity_confidence NUMERIC(4,3) default 0
- notes TEXT
- created_at, updated_at

Use a uniqueness constraint on edition_id + artist_id + stage_id + performance_date where possible, but retain a separate source observation table for conflicting claims.

### billing_observation

One row per source-specific billing claim. This prevents conflating poster order with actual set order.

- id UUID primary key
- performance_id FK nullable
- edition_id FK
- artist_id FK nullable
- source_id FK
- raw_artist_name TEXT
- billing_context TEXT: poster, flyer, programme, website, press_release, ticket, schedule, announcement, retrospective
- printed_order INTEGER nullable (top-to-bottom/left-to-right order after recording layout rules)
- printed_tier INTEGER nullable
- stage_order INTEGER nullable
- set_time_order INTEGER nullable
- headline_flag BOOLEAN nullable
- co_headliner_flag BOOLEAN nullable
- first_line_flag BOOLEAN nullable
- closing_act_flag BOOLEAN nullable
- billing_group TEXT nullable (e.g. “with”, “plus”, “special guest”)
- font_family TEXT nullable
- font_size_pt NUMERIC nullable
- font_weight_numeric INTEGER nullable
- uppercase_ratio NUMERIC(5,4) nullable
- text_area_px INTEGER nullable
- text_width_px INTEGER nullable
- text_height_px INTEGER nullable
- color_contrast NUMERIC nullable
- logo_area_px INTEGER nullable
- visual_prominence_score NUMERIC nullable
- extraction_method TEXT: manual, OCR, layout_model, CV, official_structured_data
- extraction_version TEXT
- ambiguity_notes TEXT

### source

- id UUID primary key
- publisher TEXT
- title TEXT
- url TEXT not null
- source_type TEXT: official, archive, library, newspaper, database, wiki, poster, programme, recording, user_submission
- retrieved_at TIMESTAMPTZ
- publication_date DATE nullable
- license TEXT nullable
- rights_notes TEXT nullable
- content_hash TEXT nullable
- archived_url TEXT nullable
- language TEXT nullable
- reliability_prior NUMERIC(4,3)

### source_assertion

Generic evidence layer for claims not fitting a simple row.

- id UUID primary key
- source_id FK
- subject_type TEXT
- subject_id UUID/TEXT
- predicate TEXT
- object_value JSONB
- quoted_text TEXT nullable
- page_or_locator TEXT nullable
- assertion_confidence NUMERIC(4,3)
- reviewed_by TEXT nullable
- reviewed_at TIMESTAMPTZ nullable

### artist_identity_alias

- artist_id FK
- raw_name TEXT
- normalized_name TEXT
- alias_type TEXT: stage_name, spelling_variant, translated, historical, ensemble_member, OCR_variant
- external_namespace TEXT nullable
- external_id TEXT nullable
- confidence NUMERIC(4,3)
- source_id FK nullable

### poster_asset and poster_text_region

poster_asset: asset_id, source_id, object_storage_uri nullable, image_sha256, width_px, height_px, dpi nullable, crop_notes, rights_status.

poster_text_region: asset_id, polygon JSONB, OCR_text, artist_candidate_id nullable, confidence, reading_order, font_size_px, font_weight_estimate, text_area_px, color_stats JSONB, model_name, model_version, human_review_status.

### derived_artist_festival_metrics

Materialized or versioned feature table, never the sole source of truth.

- artist_id, edition_id, metric_version
- normalized_billing_percentile
- weighted_billing_score
- lineup_headliner_score
- stage_prominence_score
- schedule_prominence_score
- poster_prominence_score
- source_confidence
- observed_flag
- trajectory_features JSONB
- calculated_at

### indexes and quality constraints

Index festival_edition(start_date), festival_performance(artist_id, performance_date), billing_observation(edition_id, printed_order), source(source_type), and external IDs. Add a full-text index on raw_artist_name, festival names, and notes. Enforce nonnegative order, font dimensions, and 0–1 confidence ranges. Keep a validation report for duplicate editions, impossible dates, unresolved high-frequency names, and conflicting “performed” claims.

## 3. Ingest architecture and workflow

Use a bronze/silver/gold pipeline.

Bronze: retain fetched HTML/JSON, OCR output, image hash, retrieval timestamp, parser version, and source URL. Do not silently mutate raw input.

Silver: normalize festival/edition names, parse dates, extract artist strings, map external IDs, assign source assertions, and record conflicts.

Gold: publish deduplicated rosters, edition-level billing features, artist trajectories, and confidence-weighted scores.

Recommended stages:

1. Seed festival identities and date ranges from Wikidata, official histories, and curated research lists.
2. Discover editions by festival name plus year; create an edition even if only year-level evidence exists.
3. Fetch official historical pages, programmes, posters, archival catalog records, and reputable transcriptions.
4. Extract roster strings from structured HTML first; use OCR for scans/posters; use manual review for low-confidence or visually ambiguous regions.
5. Resolve artist identities against MusicBrainz, Wikidata, and local aliases. Never force a match when multiple artists are plausible.
6. Create one festival_performance per act claim and one billing_observation per source/layout claim.
7. Link setlists and recordings when available, but do not infer performance solely from a setlist without event/venue/date corroboration.
8. Cross-check with at least two independent sources for historically important editions or any claim driving a published score.
9. Recompute metrics only after provenance and confidence checks pass.
10. Export a change log: new claims, changed claims, unresolved conflicts, and source failures.

Suggested batch priority:

- Tier A: Newport Jazz (1954 onward), Newport Folk, Monterey Pop, Woodstock, Glastonbury, Isle of Wight, Montreux, Reading/Leeds, Roskilde, Pinkpop, Jazz Fest, Coachella, Lollapalooza, Primavera, and major regional equivalents.
- Tier B: genre-specific festivals with stable archives.
- Tier C: long-tail festivals discovered through linked data and source expansion.

## 4. Open and relatively open data sources

Use sources according to the following hierarchy and verify current terms before production use.

1. Official festival archives and histories. Glastonbury’s official history provides year-by-year historical snapshots. The Newport Jazz Festival’s official Storyville/archive is useful for institutional context. Official programmes and schedules are the preferred evidence for billing and cancellations.
   - https://www.glastonburyfestivals.co.uk/history/
   - https://newportjazz.org/category/storyville/feed

2. Wikidata and Wikimedia projects. Wikidata is open, machine-readable, and queryable through SPARQL. Use it for festival identity, dates, locations, aliases, and discovery of performers; expect incomplete edition-level lineups and qualify every claim.
   - https://query.wikidata.org/
   - https://www.wikidata.org/wiki/Wikidata:Main_Page

3. MusicBrainz. Its API exposes artist and event entities and relationships. Use it for canonical artist identity, aliases, areas, and event matching; festival edition/lineup coverage is uneven, so it is an identity backbone rather than a complete lineup source.
   - https://musicbrainz.org/doc/MusicBrainz_API
   - https://musicbrainz.org/doc/MusicBrainz_API/Search

4. Setlist.fm. It provides setlists and related event/artist/venue data through a documented API, but API access requires applying for an API key. Treat it as optional/credential-dependent, not as a no-credential foundation. Its data is crowd-sourced and better for corroborating actual performances and set times than for original poster billing.
   - https://api.setlist.fm/
   - https://api.setlist.fm/docs/1.0/ui/index.html

5. Library, museum, and institutional archives. Prioritize Library of Congress, National Jazz Archive, Victoria and Albert Museum, Rock & Roll Hall of Fame collections, and festival-specific programme repositories. These are particularly valuable for poster assets, programmes, and scans, subject to item-level rights.
   - https://www.loc.gov/pictures/
   - https://nationaljazzarchive.org.uk/collection/posters-top
   - https://www.vam.ac.uk/collections/the-glastonbury-festival-archive
   - https://library.rockhall.com/greatest_festival_moments/glastonbury_1997

6. Internet Archive. Use it for legally accessible scans, recordings, metadata, and web archive material. Query its advanced search/API where available, cache identifiers and metadata, and comply with item licenses and access restrictions.
   - https://archive.org/

7. Jazz Fest Database. This is a strong festival-specific structured source: it states that its database lists performers from New Orleans Jazz & Heritage Festival programme books beginning in 1970. Use it as a targeted source and preserve its attribution.
   - https://jfdb.jazzandheritage.org/

8. Wikipedia/Wikimedia as discovery and lead generation. Extract citations and candidate names, then verify against primary/institutional sources. Do not make an uncited wiki list the sole evidence for a billing score.

9. Songkick and Concert Archives. These may contain useful historical leads, but Songkick’s developer terms mention a partnership/license arrangement, and Concert Archives is community-driven without a clearly open bulk-data license. Do not scrape or redistribute at scale without permission; use only permitted pages or manually verified leads.
   - https://www.songkick.com/developer
   - https://www.concertarchives.org/

Avoid treating Ticketmaster, Bandsintown, Soundcharts, Pollstar, or similar commercial feeds as open-data dependencies. They can be optional licensed enrichments later.

## 5. Billing model

Billing has multiple observable dimensions:

- Position: normalized printed rank among all named acts.
- Tier: headline, sub-headline, upper-card, mid-card, lower-card, emerging/local, or unclassified.
- Schedule: stage, day, set start, duration, and whether the act closes a stage/day.
- Visual treatment: font size, weight, area, case, contrast, color, logo scale, and spatial prominence.
- Context: poster versus programme versus schedule; stage-specific versus festival-wide; announced versus final roster.

Do not collapse these into one field. Store raw dimensions, then derive normalized variables within each edition and source. Poster layout needs a declared reading-order rule (usually columns, then top-to-bottom, unless the design or source explicitly indicates another order).

Suggested tier mapping when an explicit tier is unavailable:

- Headliner: first line, largest visual group, or closing act on the primary/main stage.
- Upper-card: top quartile of printed rank or stage schedule, with corroborating visual prominence.
- Mid-card: middle 50% after stage/context normalization.
- Lower-card: bottom quartile or explicitly supporting/local slot.
- Unknown: insufficient evidence.

These are analytical labels, not historical facts; preserve the underlying observations.

## 6. Festival Billing Score

Let an artist a perform at edition e. First compute edition-relative features so a 1954 three-act bill is comparable to a 2026 multi-stage poster.

Position feature:

p(a,e) = 1 - (r(a,e)-1) / max(1, N_e-1)

where r is the printed rank and N is the number of ranked acts in the same billing context. Thus 1 is top billing and 0 is bottom billing.

Tier feature:

h(a,e) = 1 - (t(a,e)-1) / max(1, T_e-1)

where t is the explicit or inferred stage/card tier. If no tier exists, set h missing and redistribute weights rather than zeroing the artist.

Schedule feature:

s(a,e) = 0.5 * close_stage(a,e) + 0.3 * close_day(a,e) + 0.2 * normalized_stage_capacity(a,e)

Set-time detail can be omitted or weight-renormalized when unavailable.

Visual feature:

v(a,e) = 0.35*z(log(1+area)) + 0.25*z(font_size) + 0.15*z(font_weight) + 0.15*contrast + 0.10*logo_prominence

where z is a robust within-poster standardization and all components are clipped to [0,1]. For posters without reliable OCR/CV, v is missing.

Evidence feature:

g(a,e) = 1 - product_i (1 - q_i)

where q_i is the reliability-adjusted confidence of each independent source assertion. Cap correlated sources so copied lists do not count as independent evidence.

Edition-level score:

FBS(a,e) = 100 * g(a,e) * [0.35*p + 0.25*h + 0.20*s + 0.20*v]

Renormalize the four dimension weights over observed dimensions. Report both the score and a completeness vector, e.g. position=1, tier=1, schedule=0, visual=1. This prevents missing schedules from looking like weak billing.

Optional Bayesian version: model observed rank/tier as an ordinal outcome with latent artist demand B_a,e. Use a cumulative-logit model:

Pr(rank <= k) = logistic(theta_k - (B_a,e + edition_effect_e + stage_effect + genre_effect))

with a random walk for career trajectory:

B_a,e = B_a,e-1 + drift_a,e + noise_a,e

drift can depend on recency-weighted signals such as festival frequency, closing-stage rate, and cross-festival promotion. Fit only after sufficient coverage; the deterministic FBS remains the explainable baseline.

## 7. Career trajectory metrics and prediction

For an artist, use edition year y and a recency half-life H (default 5 years):

Trajectory(a,y) = sum_e exp(-ln(2)*(y-year_e)/H) * FBS(a,e) / sum_e exp(-ln(2)*(y-year_e)/H)

Report:

- rolling weighted FBS;
- slope from robust regression over the last 3, 5, and 10 years;
- percentile within genre and festival scale;
- headliner probability;
- promotion rate: probability of moving up at least one tier between appearances at the same festival;
- cross-festival breadth and highest observed stage tier;
- uncertainty interval via bootstrap over editions and sources.

Prediction target should be placement percentile or tier, not a single exact poster coordinate. Candidate model features include prior FBS, slope, recency, release/recording activity if available, prior attendance/venue scale, festival genre, edition size, region, and whether the artist is a repeat act. Avoid leakage: train on information available before the target edition.

## 8. Validation and human review

Create a gold set of at least 20 editions across eras, genres, single-stage and multi-stage formats. Two reviewers independently annotate roster, rank, tier, and cancellation status. Measure:

- artist extraction precision/recall;
- identity resolution precision;
- edition match accuracy;
- rank correlation (Spearman/Kendall);
- tier agreement (weighted kappa);
- calibration of confidence and headliner predictions.

Require human review when OCR confidence is low, names collide, a poster has ambiguous reading order, or sources disagree about performed versus announced. Every published score must be reproducible from source IDs and metric version.

## 9. Licensing, ethics, and operational controls

Respect robots.txt, site terms, API terms, copyright, rate limits, and takedown requests. Store links, citations, hashes, OCR text, and derived measurements where possible instead of redistributing copyrighted scans. Keep a rights_status and provenance record for every image. Use an explicit User-Agent, throttling, retries with backoff, and a source-specific ingest adapter. Never represent a community transcription as official fact.

## 10. Initial implementation plan

Phase 1: implement tables, provenance, aliases, and a small seed of 10–15 landmark festivals.

Phase 2: ingest Wikidata/MusicBrainz identity and edition candidates; add official histories and structured festival databases.

Phase 3: add poster/programme asset workflow, OCR/layout extraction, and reviewer UI or CSV review queue.

Phase 4: add optional Setlist.fm adapter behind a credential/configuration flag and enrich actual-performance status.

Phase 5: calculate FBS v1, publish confidence/completeness, validate against the gold set, and only then train ordinal/trajectory models.

Phase 6: expand by festival family and geography, monitoring coverage bias toward English-language and well-archived events.

## 11. Minimum viable records

A publishable MVP record requires: festival identity, edition year, raw artist name, source URL, source type, assertion status, and identity confidence. Billing score requires at least one normalized billing observation. A score should be marked incomplete when stage, schedule, visual, or independent corroboration is absent.
