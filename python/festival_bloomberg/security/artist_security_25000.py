"""Deterministic tiered artist security universe for the 25K database."""
from __future__ import annotations
import hashlib, json
from datetime import date, datetime, timezone
from typing import Any

VERSION = "artist_security_25000_v1"

def _hash(*parts: object) -> str:
    return hashlib.sha256("|".join(str(x) for x in parts).encode()).hexdigest()[:32]

def _columns(conn, schema: str, table: str) -> set[str]:
    return {r[0] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=? AND table_name=?", [schema, table]).fetchall()}

def build_tiered_universe(conn, *, as_of: date | None = None, hot_limit: int = 1000, core_limit: int = 5000, coverage_limit: int = 25000) -> dict[str, Any]:
    as_of = as_of or date.today()
    # Existing canonical artists are the identity base. Evidence buckets are
    # observable and independently recorded; ranking only breaks ties.
    rows = conn.execute("""
      WITH perf AS (
        SELECT artist_mbid, COUNT(*) AS n FROM core.event_performers WHERE artist_mbid IS NOT NULL GROUP BY artist_mbid
      ),
      attention AS (
        SELECT artist_key, COUNT(*) AS n FROM metrics.artist_attention_observations WHERE status='ok' GROUP BY artist_key
      ),
      ids AS (
        SELECT entity_key, COUNT(*) AS n FROM core.entity_external_ids WHERE entity_type='artist' GROUP BY entity_key
      ),
      tm AS (
        SELECT artist_key, COUNT(*) AS n FROM identity.artist_provider_linkages WHERE provider='TICKETMASTER' AND resolution_status='VERIFIED' GROUP BY artist_key
      )
      SELECT a.artist_key,a.name,a.musicbrainz_id,COALESCE(p.n,0),COALESCE(att.n,0),COALESCE(i.n,0),COALESCE(t.n,0)
      FROM core.artists a LEFT JOIN perf p ON p.artist_mbid=a.musicbrainz_id
      LEFT JOIN attention att ON att.artist_key=a.artist_key LEFT JOIN ids i ON i.entity_key=a.artist_key LEFT JOIN tm t ON t.artist_key=a.artist_key
      WHERE a.artist_key IS NOT NULL
      ORDER BY (COALESCE(p.n,0)>0) DESC,(COALESCE(t.n,0)>0) DESC,(COALESCE(att.n,0)+COALESCE(i.n,0)) DESC,a.artist_key
    """).fetchall()
    selected = rows[:coverage_limit]
    # Preserve the prior 1000 exactly where possible.
    prior = {r[0] for r in conn.execute("SELECT artist_key FROM security.artist_security_universe_1000").fetchall()} if 'artist_security_universe_1000' in {r[0] for r in conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='security'").fetchall()} else set()
    selected_keys = {r[0] for r in selected}
    for r in rows:
        if r[0] in prior and r[0] not in selected_keys and len(selected) < coverage_limit:
            selected.append(r); selected_keys.add(r[0])
    conn.execute("DELETE FROM security.artist_security_tiers")
    conn.execute("DELETE FROM security.artist_security_universe_25000")
    counts = {"HOT_1000":0,"CORE_5000":0,"COVERAGE_25000":0}
    for idx, r in enumerate(selected):
        key,name,mbid,nperf,natt,nids,ntm=r
        bucket = "FUTURE_OR_LIVE_EVENT" if nperf else "TICKETMASTER_ATTRACTION" if ntm else "ATTENTION_OR_IDENTITY_COVERAGE"
        tier = "HOT_1000" if idx < hot_limit else "CORE_5000" if idx < core_limit else "COVERAGE_25000"
        counts[tier] += 1
        refs = {"event_performances":nperf,"attention_observations":natt,"external_ids":nids,"ticketmaster_links":ntm}
        conn.execute("INSERT INTO security.artist_security_universe_25000 VALUES (?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)", [key,name,mbid,tier,bucket,"deterministic observable evidence",json.dumps(refs),as_of,VERSION])
        for t in (["HOT_1000","CORE_5000","COVERAGE_25000"] if tier=="HOT_1000" else ["CORE_5000","COVERAGE_25000"] if tier=="CORE_5000" else ["COVERAGE_25000"]):
            conn.execute("INSERT INTO security.artist_security_tiers VALUES (?,?,?,?,?,?,?,CURRENT_TIMESTAMP)", [key,t,bucket,"deterministic observable evidence",json.dumps(refs),as_of,VERSION])
    return {"status":"COMPLETE","identity_source_count":len(rows),"selected_count":len(selected),"tier_counts":counts,"as_of":as_of.isoformat(),"source_version":VERSION}

def scale_report(conn, *, as_of: date | None = None) -> dict[str, Any]:
    as_of=as_of or date.today()
    def q(sql):
        try:return conn.execute(sql).fetchone()[0]
        except Exception:return 0
    report={
      "as_of":as_of.isoformat(),
      "canonical_identity_count":q("SELECT COUNT(*) FROM core.artists"),
      "coverage_security_count":q("SELECT COUNT(*) FROM security.artist_security_universe_25000"),
      "core_security_count":q("SELECT COUNT(*) FROM security.artist_security_tiers WHERE tier='CORE_5000'"),
      "hot_security_count":q("SELECT COUNT(*) FROM security.artist_security_tiers WHERE tier='HOT_1000'"),
      "canonical_event_count":q("SELECT COUNT(*) FROM events.provider_event_snapshots"),
      "future_active_event_count":q("SELECT COUNT(*) FROM events.provider_event_snapshots WHERE local_date >= CURRENT_DATE"),
      "active_ticket_pair_count":q("SELECT COUNT(DISTINCT event_key||'|'||marketplace) FROM acquisition.market_price_observations"),
      "venue_count":q("SELECT COUNT(*) FROM core.venues"),
      "capacity_evidenced_venue_count":q("SELECT COUNT(*) FROM core.venues WHERE capacity IS NOT NULL"),
      "artist_market_row_count":q("SELECT COUNT(*) FROM asm.artist_market_security_v1"),
      "factor_observation_count":q("SELECT COUNT(*) FROM metrics.artist_factor_observations"),
    }
    key=_hash(VERSION,as_of)
    conn.execute("INSERT OR REPLACE INTO security.artist_security_scale_reports (report_key,as_of,canonical_identity_count,coverage_security_count,core_security_count,hot_security_count,canonical_event_count,future_active_event_count,active_ticket_pair_count,venue_count,capacity_evidenced_venue_count,artist_market_row_count,factor_observation_count,report_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)", [key,as_of,report['canonical_identity_count'],report['coverage_security_count'],report['core_security_count'],report['hot_security_count'],report['canonical_event_count'],report['future_active_event_count'],report['active_ticket_pair_count'],report['venue_count'],report['capacity_evidenced_venue_count'],report['artist_market_row_count'],report['factor_observation_count'],json.dumps(report)])
    return report
