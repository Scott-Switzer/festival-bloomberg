from datetime import date
import json
import duckdb
import pytest
from festival_bloomberg.migrations import apply_pending_migrations
from festival_bloomberg.security.youtube_identity_scale import resolve_universe_youtube, channel_id_from_url
from festival_bloomberg.security.factor_coverage import compute_factor_coverage
from festival_bloomberg.security.artist_market_scale import expand_artist_market, row_key


@pytest.fixture
def c():
    conn = duckdb.connect(":memory:", config={"enable_external_access": False})
    apply_pending_migrations(conn)
    return conn


def _seed(c):
    apply_pending_migrations(c)
    c.execute("INSERT INTO core.artists (artist_key,name,normalized_name,musicbrainz_id,type,source_system,ingested_at) VALUES ('mbid::1','Artist One','artist one','1','Group','test',CURRENT_TIMESTAMP)")
    c.execute("INSERT INTO core.artists (artist_key,name,normalized_name,musicbrainz_id,type,source_system,ingested_at) VALUES ('mbid::2','Artist Two','artist two','2','Group','test',CURRENT_TIMESTAMP)")
    # 25K universe membership
    c.execute("INSERT INTO security.artist_security_universe_25000 (artist_key,artist_name,mbid,tier,selection_bucket,selection_reason,evidence_refs,as_of,source_version,ingested_at) VALUES ('mbid::1','Artist One','1','HOT_1000','TEST','test','{}',CURRENT_DATE,'test',CURRENT_TIMESTAMP)")
    c.execute("INSERT INTO security.artist_security_universe_25000 (artist_key,artist_name,mbid,tier,selection_bucket,selection_reason,evidence_refs,as_of,source_version,ingested_at) VALUES ('mbid::2','Artist Two','2','CORE_5000','TEST','test','{}',CURRENT_DATE,'test',CURRENT_TIMESTAMP)")
    return c


def test_channel_id_from_url():
    assert channel_id_from_url("https://www.youtube.com/channel/UC1234567890abcdef") == "UC1234567890abcdef"
    assert channel_id_from_url("https://www.youtube.com/@artist") == "artist"
    assert channel_id_from_url("https://example.com") is None


def test_resolve_universe_youtube_mb_url(c):
    c = _seed(c)
    # MB URL relation keyed by MBID
    c.execute("INSERT INTO reference.musicbrainz_artists (mbid,name,urls,dump_source_id,knowledge_time,ingested_at) VALUES ('1','Artist One',?,NULL,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
              [json.dumps([{"type": "youtube", "resource": "https://www.youtube.com/channel/UC11111"}])])
    r = resolve_universe_youtube(c)
    assert r["status"] == "COMPLETE"
    assert r["tier2_mb_url"] == 1
    assert r["tier4_name_only_candidate"] == 1
    row = c.execute("SELECT resolution_status FROM identity.artist_provider_linkages WHERE artist_key='mbid::1' AND provider='YOUTUBE'").fetchone()
    assert row[0] == "VERIFIED"


def test_factor_coverage_distribution(c):
    c = _seed(c)
    cov = compute_factor_coverage(c, as_of=date(2026, 8, 27))
    assert cov["universe_size"] == 2
    assert "p50" in cov["factor_observations"]
    assert cov["factor_observations"]["nonzero_artists"] == 0


def test_artist_market_expansion(c):
    c = _seed(c)
    # MB event + performer + event-at-place relationship with IL area
    c.execute("INSERT INTO raw.musicbrainz_event (mbid,name,begin_date,event_type,payload,dump_source_id,knowledge_time,ingested_at) VALUES ('e1','E1','2026-06-01','Concert','{}',NULL,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
    c.execute("INSERT INTO raw.musicbrainz_place (mbid,name,area,payload,dump_source_id,knowledge_time,ingested_at) VALUES ('p1','Venue One','Illinois','{}',NULL,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
    c.execute("INSERT INTO core.event_performers (performer_key,event_mbid,artist_mbid,artist_name,performer_role,source_system,knowledge_time,ingested_at) VALUES ('pk','e1','1','Artist One','main performer','test',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
    # relationship: EVENT_AT_PLACE from event e1 to place p1 (area 'Illinois' -> 'IL')
    key = row_key(artist_key='mbid::1', market_key='chicago-il', as_of=date(2026, 8, 27))
    c.execute("INSERT INTO core.entity_relationships (relationship_key,subject_entity_type,subject_key,predicate,object_entity_type,object_key,source_system,knowledge_time,ingested_at) VALUES (?, 'EVENT','mbid::e1','EVENT_AT_PLACE','PLACE','mbid::p1','test',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)", [key])
    r = expand_artist_market(c, as_of=date(2026, 8, 27))
    assert r["status"] == "COMPLETE"
    assert r["rows_written"] >= 1
