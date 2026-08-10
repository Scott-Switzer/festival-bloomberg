"""Tests for the DuckDB warehouse schema in ``schema/duckdb.sql`` (offline).

Covers the loader, the rich artist/festival/lineup specifications, and the
entity-resolution support tables and indexes.
"""
import os
import re

import duckdb
import pytest

from warehouse.schema_loader import (
    SCHEMA_NAMES,
    SCHEMA_PATH,
    apply_schema,
    load_schema_sql,
    schema_statements,
    split_statements,
)


@pytest.fixture
def con():
    """An in-memory DuckDB connection with the full schema applied."""
    connection = duckdb.connect(":memory:")
    apply_schema(connection)
    yield connection
    connection.close()


def columns_of(connection, schema: str, table: str) -> set:
    rows = connection.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = ? AND table_name = ?",
        [schema, table],
    ).fetchall()
    return {r[0] for r in rows}


# --------------------------------------------------------------------------- #
# Loader
# --------------------------------------------------------------------------- #
def test_schema_file_exists_at_documented_path():
    assert os.path.isfile(SCHEMA_PATH)
    assert SCHEMA_PATH.endswith(os.path.join("schema", "duckdb.sql"))


def test_split_statements_ignores_comments_and_quoted_semicolons():
    sql = """
    -- a comment with a semicolon ; inside
    CREATE TABLE t (a VARCHAR);
    /* block ; comment */
    INSERT INTO t VALUES ('one; two');
    """
    statements = split_statements(sql)

    assert len(statements) == 2
    assert statements[0].startswith("CREATE TABLE t")
    assert "'one; two'" in statements[1]
    assert "--" not in statements[0]


def test_every_statement_is_non_empty():
    statements = schema_statements()

    assert len(statements) > 50
    assert all(statement.strip() for statement in statements)


def test_apply_schema_is_idempotent(con):
    before = con.execute("SELECT COUNT(*) FROM information_schema.tables").fetchone()[0]
    applied = apply_schema(con)
    after = con.execute("SELECT COUNT(*) FROM information_schema.tables").fetchone()[0]

    assert applied > 0
    assert before == after


def test_all_layers_are_created(con):
    schemas = {
        r[0]
        for r in con.execute("SELECT schema_name FROM information_schema.schemata").fetchall()
    }

    assert set(SCHEMA_NAMES) <= schemas


def test_every_declared_table_is_created(con):
    declared = set(
        re.findall(r"CREATE TABLE IF NOT EXISTS\s+([\w.]+)", load_schema_sql())
    )
    created = {
        f"{r[0]}.{r[1]}"
        for r in con.execute(
            "SELECT table_schema, table_name FROM information_schema.tables"
        ).fetchall()
    }

    assert declared
    assert declared <= created


# --------------------------------------------------------------------------- #
# Artist specification
# --------------------------------------------------------------------------- #
def test_artists_keep_the_columns_the_repository_writes(con):
    legacy = {
        "artist_key",
        "musicbrainz_id",
        "name",
        "normalized_name",
        "disambiguation",
        "country",
        "genres",
        "type",
        "life_span_begin",
        "life_span_end",
        "source_system",
        "ingested_at",
    }

    assert legacy <= columns_of(con, "core", "artists")


def test_artists_carry_the_rich_specification(con):
    expected = {
        "aliases",
        "origin_city",
        "origin_region",
        "primary_genre",
        "subgenres",
        "formation_date",
        "disband_date",
        "active_status",
        "is_active",
        "members",
        "labels",
        "management",
        "booking_agency",
        "official_website",
        "official_domains",
        "social_handles",
        "spotify_id",
        "wikidata_id",
        "isni",
        "ipi",
        "external_ids",
        "popularity_score",
        "spotify_popularity",
        "spotify_followers",
        "monthly_listeners",
        "evidence",
        "evidence_url",
        "extraction_confidence",
        "source_url",
        "source_retrieved_at",
    }

    assert expected <= columns_of(con, "core", "artists")


def test_artist_detail_tables_exist(con):
    for table in ("artist_aliases", "artist_members", "artist_labels", "artist_social_handles"):
        assert columns_of(con, "core", table), f"core.{table} is missing"


def test_artist_confidence_check_rejects_out_of_range_values(con):
    con.execute(
        "INSERT INTO core.artists (artist_key, name, normalized_name, extraction_confidence) "
        "VALUES ('a1', 'Radiohead', 'radiohead', 0.95)"
    )

    with pytest.raises(duckdb.ConstraintException):
        con.execute(
            "INSERT INTO core.artists (artist_key, name, normalized_name, extraction_confidence) "
            "VALUES ('a2', 'Bad', 'bad', 1.5)"
        )


# --------------------------------------------------------------------------- #
# Festival specification
# --------------------------------------------------------------------------- #
def test_festivals_keep_the_columns_the_repository_writes(con):
    legacy = {
        "festival_key",
        "name",
        "normalized_name",
        "location_country",
        "location_city",
        "location_region",
        "capacity",
        "genre_focus",
        "festival_type",
        "venue_type",
        "duration_days",
        "typical_month",
        "source_system",
        "ingested_at",
    }

    assert legacy <= columns_of(con, "core", "festivals")


def test_festivals_carry_the_rich_specification(con):
    expected = {
        "venue_key",
        "venue_name",
        "venue_address",
        "daily_capacity",
        "total_capacity",
        "capacity_basis",
        "organizer",
        "organizers",
        "promoter",
        "promoters",
        "parent_company",
        "stage_count",
        "stages",
        "ticket_tiers",
        "currency",
        "ticket_price_min",
        "ticket_price_max",
        "sellout_status",
        "sold_out",
        "sold_out_at",
        "sellout_duration_hours",
        "lineup_status",
        "lineup_announced_at",
        "lineup_announcements",
        "historical_editions",
        "first_edition_year",
        "edition_count",
        "source_url",
        "source_retrieved_at",
        "source_last_modified",
    }

    assert expected <= columns_of(con, "core", "festivals")


def test_festival_editions_record_outcomes_and_sellout(con):
    expected = {
        "edition_key",
        "festival_key",
        "year",
        "edition_label",
        "weekend_number",
        "start_date",
        "end_date",
        "attendance",
        "capacity",
        "ticket_tiers",
        "sellout_status",
        "sold_out",
        "sold_out_at",
        "tickets_sold",
        "gross_revenue",
        "lineup_announced_at",
        "is_cancelled",
        "source_retrieved_at",
    }

    assert expected <= columns_of(con, "core", "festival_editions")


def test_festival_stage_and_ticket_tier_tables_exist(con):
    assert {"stage_name", "stage_type", "stage_rank", "capacity"} <= columns_of(
        con, "core", "festival_stages"
    )
    assert {"tier_name", "tier_type", "price", "currency", "sold_out", "on_sale_at"} <= columns_of(
        con, "core", "festival_ticket_tiers"
    )


def test_typical_month_check_rejects_impossible_months(con):
    con.execute(
        "INSERT INTO core.festivals (festival_key, name, normalized_name, typical_month) "
        "VALUES ('f1', 'Coachella', 'coachella', 4)"
    )

    with pytest.raises(duckdb.ConstraintException):
        con.execute(
            "INSERT INTO core.festivals (festival_key, name, normalized_name, typical_month) "
            "VALUES ('f2', 'Nope', 'nope', 13)"
        )


# --------------------------------------------------------------------------- #
# Lineup specification
# --------------------------------------------------------------------------- #
def test_lineup_slots_carry_the_full_specification(con):
    expected = {
        "billing_order",
        "billing_tier",
        "stage_name",
        "performance_date",
        "start_time",
        "end_time",
        "local_start_time",
        "local_end_time",
        "set_duration_minutes",
        "artist_role",
        "set_type",
        "genre",
        "subgenres",
        "evidence_url",
        "extraction_confidence",
        "announcement_date",
        "announced_at",
        "announcement_wave",
        "match_confidence",
        "match_method",
    }

    assert expected <= columns_of(con, "core", "lineup_slots")


def test_lineup_slot_round_trips(con):
    con.execute(
        """
        INSERT INTO core.lineup_slots
            (slot_key, festival_key, year, artist_name, normalized_artist_name,
             billing_order, billing_tier, stage_name, performance_date,
             start_time, end_time, artist_role, genre, announcement_date,
             evidence_url, extraction_confidence)
        VALUES ('s1', 'name::coachella', 2026, 'Radiohead', 'radiohead',
                1, 'headliner', 'Coachella Stage', DATE '2026-04-11',
                TIMESTAMP '2026-04-11 23:15:00', TIMESTAMP '2026-04-12 00:45:00',
                'headliner', 'alternative rock', DATE '2026-01-14',
                'https://www.coachella.com/lineup', 0.93)
        """
    )

    row = con.execute(
        "SELECT billing_order, billing_tier, stage_name, artist_role, genre, "
        "extraction_confidence, announcement_date FROM core.lineup_slots WHERE slot_key = 's1'"
    ).fetchone()

    assert row[0] == 1
    assert row[1] == "headliner"
    assert row[2] == "Coachella Stage"
    assert row[3] == "headliner"
    assert row[4] == "alternative rock"
    assert row[5] == pytest.approx(0.93)
    assert row[6].year == 2026


def test_raw_observations_keep_legacy_columns_and_gain_evidence(con):
    columns = columns_of(con, "raw", "lineup_observations")
    legacy = {
        "observation_key",
        "festival_key",
        "edition_year",
        "artist_name",
        "position",
        "stage",
        "day",
        "source_url",
        "parser_version",
        "observed_raw",
        "ingested_at",
    }
    added = {
        "normalized_artist_name",
        "billing_order",
        "billing_tier",
        "performance_date",
        "artist_role",
        "genre",
        "announcement_date",
        "evidence_url",
        "extraction_confidence",
        "resolved_artist_key",
        "match_confidence",
        "requires_review",
    }

    assert legacy <= columns
    assert added <= columns


# --------------------------------------------------------------------------- #
# Entity resolution
# --------------------------------------------------------------------------- #
def test_match_candidates_expose_weighted_fuzzy_features(con):
    expected = {
        "name_similarity",
        "alias_similarity",
        "external_id_match",
        "musicbrainz_id_match",
        "country_match",
        "genre_similarity",
        "social_handle_match",
        "domain_match",
        "date_proximity",
        "context_similarity",
        "weighted_score",
        "blocking_key",
        "decision",
        "requires_review",
        "feature_scores",
    }

    assert expected <= columns_of(con, "core", "entity_match_candidates")


def test_default_match_weights_are_seeded(con):
    rows = con.execute(
        "SELECT feature_name, weight FROM core.entity_match_weights "
        "WHERE entity_type = 'artist' ORDER BY weight DESC"
    ).fetchall()
    weights = dict(rows)

    assert weights["musicbrainz_id_match"] == 1.0
    assert weights["name_similarity"] == pytest.approx(0.45)
    assert weights["country_match"] == pytest.approx(0.05)
    assert all(0.0 <= weight <= 1.0 for weight in weights.values())


def test_seeded_weights_are_not_duplicated_on_reapply(con):
    before = con.execute("SELECT COUNT(*) FROM core.entity_match_weights").fetchone()[0]
    apply_schema(con)
    after = con.execute("SELECT COUNT(*) FROM core.entity_match_weights").fetchone()[0]

    assert before == after


def test_blocking_indexes_exist(con):
    indexes = {
        r[0] for r in con.execute("SELECT index_name FROM duckdb_indexes()").fetchall()
    }
    expected = {
        "idx_artists_normalized_name",
        "idx_artists_country",
        "idx_artists_primary_genre",
        "idx_artists_blocking_key",
        "idx_artist_aliases_normalized",
        "idx_artist_handles_lookup",
        "idx_entity_external_ids_lookup",
        "idx_match_candidates_blocking",
        "idx_lineup_slots_artist_name",
    }

    assert expected <= indexes


def test_resolution_key_view_unions_names_aliases_ids_and_handles(con):
    con.execute(
        "INSERT INTO core.artists (artist_key, musicbrainz_id, name, normalized_name, "
        "country, primary_genre) VALUES "
        "('mb-1', 'a74b1b7f-36a9-4d22-a1cf-017dc00396d0', 'Radiohead', 'radiohead', "
        "'GB', 'alternative rock')"
    )
    con.execute(
        "INSERT INTO core.artist_aliases (alias_key, artist_key, alias, normalized_alias, "
        "confidence) VALUES ('al-1', 'mb-1', 'On a Friday', 'on a friday', 0.9)"
    )
    con.execute(
        "INSERT INTO core.entity_external_ids (external_id_key, entity_type, entity_key, "
        "id_type, id_value) VALUES ('x-1', 'artist', 'mb-1', 'spotify', '4Z8W4fKeB5YxbusRsdQVPb')"
    )
    con.execute(
        "INSERT INTO core.artist_social_handles (handle_key, artist_key, platform, handle, "
        "normalized_handle) VALUES ('h-1', 'mb-1', 'instagram', '@radiohead', 'radiohead')"
    )

    rows = con.execute(
        "SELECT key_type, key_value, country, primary_genre FROM core.artist_resolution_keys "
        "WHERE artist_key = 'mb-1' ORDER BY key_type"
    ).fetchall()
    key_types = [r[0] for r in rows]

    assert key_types == ["alias", "external_id:spotify", "name", "social:instagram"]
    assert all(r[2] == "GB" and r[3] == "alternative rock" for r in rows)


def test_audit_tables_match_the_manager_write_paths(con):
    assert {
        "run_id",
        "source_system",
        "started_at",
        "finished_at",
        "status",
        "records_read",
        "records_written",
        "error_count",
        "parser_version",
        "parameters",
    } <= columns_of(con, "audit", "ingest_run")
    assert {
        "run_id",
        "source_url",
        "record_key",
        "error_type",
        "error_message",
        "payload",
        "created_at",
    } <= columns_of(con, "audit", "ingest_error")


# --------------------------------------------------------------------------- #
# Repository compatibility
# --------------------------------------------------------------------------- #
def test_repository_writes_land_in_the_file_backed_schema(repo):
    """The seeded fixture repository uses schema/duckdb.sql end to end."""
    assert repo.count_artists() == 2
    assert repo.count_festivals() == 2

    tables = {
        f"{r[0]}.{r[1]}"
        for r in repo.conn.execute(
            "SELECT table_schema, table_name FROM information_schema.tables"
        ).fetchall()
    }

    assert {"core.lineup_slots", "core.entity_match_candidates", "audit.ingest_run"} <= tables


def test_repository_can_log_an_ingest_run(repo):
    from datetime import datetime

    repo.warehouse.log_run(
        run_id="run-1",
        source_system="musicbrainz",
        started_at=datetime(2026, 1, 1, 12, 0, 0),
        status="succeeded",
        records_read=10,
        records_written=10,
    )

    row = repo.conn.execute(
        "SELECT source_system, status, records_written FROM audit.ingest_run WHERE run_id = 'run-1'"
    ).fetchone()

    assert row == ("musicbrainz", "succeeded", 10)
