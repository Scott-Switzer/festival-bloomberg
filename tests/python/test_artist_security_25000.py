from datetime import date
import duckdb
from festival_bloomberg.migrations import apply_pending_migrations
from festival_bloomberg.security.artist_security_25000 import build_tiered_universe, scale_report


def test_tiered_universe_preserves_cumulative_membership():
    c = duckdb.connect(":memory:")
    apply_pending_migrations(c)
    for i in range(30):
        c.execute("INSERT INTO core.artists (artist_key,name,normalized_name,musicbrainz_id,type,source_system,ingested_at) VALUES (?,?,?,?,?,?,CURRENT_TIMESTAMP)", [f"mbid::{i:04d}", f"Artist {i}", f"artist {i}", f"{i:08d}-0000-0000-0000-000000000000", "Group", "test"])
    result = build_tiered_universe(c, as_of=date(2026, 8, 27), hot_limit=5, core_limit=10, coverage_limit=20)
    assert result["selected_count"] == 20
    assert result["tier_counts"] == {"HOT_1000": 5, "CORE_5000": 5, "COVERAGE_25000": 10}
    report = scale_report(c, as_of=date(2026, 8, 27))
    assert report["coverage_security_count"] == 20
    assert report["core_security_count"] == 10
    assert report["hot_security_count"] == 5
    c.close()
