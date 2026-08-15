"""Intelligence metrics schema + idempotent upsert smoke tests."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import duckdb

from festival_bloomberg.migrations import apply_pending_migrations


def test_intelligence_tables_and_idempotent_upserts(tmp_path: Path):
    db_path = tmp_path / "intelligence.duckdb"
    connection = duckdb.connect(str(db_path))
    try:
        assert apply_pending_migrations(connection) == 24
        assert apply_pending_migrations(connection) == 0

        tables = {
            row[0]
            for row in connection.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'metrics'
                  AND table_name IN (
                    'artist_attention_observations',
                    'edition_analytical_metrics',
                    'tour_date_observations',
                    'ticket_price_observations'
                  )
                """
            ).fetchall()
        }
        assert tables == {
            "artist_attention_observations",
            "edition_analytical_metrics",
            "tour_date_observations",
            "ticket_price_observations",
        }

        connection.execute(
            """
            INSERT INTO metrics.artist_attention_observations (
                observation_key, artist_key, festival_key, edition_key, edition_year,
                source_system, metric_kind, project, access_method, agent, article_title,
                granularity, period_start, period_end, value, value_sum, value_unit,
                status, source_url, retrieved_at, raw_response_json, provenance_json,
                metric_version, ingested_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (observation_key) DO UPDATE SET
                value = excluded.value,
                value_sum = excluded.value_sum,
                retrieved_at = excluded.retrieved_at,
                ingested_at = excluded.ingested_at
            """,
            [
                "attn_radiohead_v1",
                "mbid::radiohead",
                "coachella",
                "coachella_2026",
                2026,
                "wikimedia",
                "pageviews",
                "en.wikipedia",
                "all-access",
                "user",
                "Radiohead",
                "daily",
                "2026-01-01",
                "2026-01-03",
                3000,
                3000,
                "pageviews",
                "ok",
                "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/Radiohead/daily/20260101/20260103",
                datetime(2026, 2, 1),
                json.dumps({"items": [{"views": 3000}]}),
                json.dumps({"sourceSystem": "wikimedia"}),
                "intelligence_metrics_v1",
                datetime(2026, 2, 1),
            ],
        )
        connection.execute(
            """
            INSERT INTO metrics.artist_attention_observations (
                observation_key, artist_key, festival_key, edition_key, edition_year,
                source_system, metric_kind, project, access_method, agent, article_title,
                granularity, period_start, period_end, value, value_sum, value_unit,
                status, source_url, retrieved_at, raw_response_json, provenance_json,
                metric_version, ingested_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (observation_key) DO UPDATE SET
                value = excluded.value,
                value_sum = excluded.value_sum,
                retrieved_at = excluded.retrieved_at,
                ingested_at = excluded.ingested_at
            """,
            [
                "attn_radiohead_v1",
                "mbid::radiohead",
                "coachella",
                "coachella_2026",
                2026,
                "wikimedia",
                "pageviews",
                "en.wikipedia",
                "all-access",
                "user",
                "Radiohead",
                "daily",
                "2026-01-01",
                "2026-01-03",
                3100,
                3100,
                "pageviews",
                "ok",
                "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/Radiohead/daily/20260101/20260103",
                datetime(2026, 2, 2),
                json.dumps({"items": [{"views": 3100}]}),
                json.dumps({"sourceSystem": "wikimedia"}),
                "intelligence_metrics_v1",
                datetime(2026, 2, 2),
            ],
        )

        count_obs = connection.execute(
            "SELECT COUNT(*), MAX(value_sum) FROM metrics.artist_attention_observations"
        ).fetchone()
        assert count_obs[0] == 1
        assert count_obs[1] == 3100

        connection.execute(
            """
            INSERT INTO metrics.edition_analytical_metrics (
                metric_key, festival_key, edition_key, edition_year, metric_version,
                attention_hhi, attention_share_json, attention_artist_count,
                attention_coverage_ratio, attention_missing_flag,
                secondary_spread_abs, secondary_spread_pct, primary_price, secondary_price,
                primary_currency, secondary_currency, secondary_spread_missing_flag,
                input_hash, evidence_json, flags_json, computed_at, ingested_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (metric_key) DO UPDATE SET
                attention_hhi = excluded.attention_hhi,
                secondary_spread_abs = excluded.secondary_spread_abs,
                computed_at = excluded.computed_at
            """,
            [
                "edition_coachella_2026_v1",
                "coachella",
                "coachella_2026",
                2026,
                "intelligence_metrics_v1",
                0.625,
                json.dumps({"mbid::radiohead": 0.25, "mbid::beyonce": 0.75}),
                2,
                0.6667,
                True,
                251.0,
                0.419,
                599.0,
                850.0,
                "USD",
                "USD",
                False,
                "abc",
                json.dumps({}),
                json.dumps({"attention_missing_flag": True}),
                datetime(2026, 2, 1),
                datetime(2026, 2, 1),
            ],
        )

        stored = connection.execute(
            """
            SELECT attention_hhi, secondary_spread_abs, attention_share_json
            FROM metrics.edition_analytical_metrics
            WHERE edition_key = ? AND metric_version = ?
            """,
            ["coachella_2026", "intelligence_metrics_v1"],
        ).fetchone()
        assert stored is not None
        assert stored[0] == 0.625
        assert stored[1] == 251.0
        shares = stored[2]
        if isinstance(shares, str):
            shares = json.loads(shares)
        assert shares["mbid::beyonce"] == 0.75
    finally:
        connection.close()
