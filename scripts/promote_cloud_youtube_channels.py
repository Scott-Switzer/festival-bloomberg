"""Build a compact active YouTube identity artifact for cloud scheduling."""
from __future__ import annotations
import argparse, json
from datetime import datetime, timezone
from pathlib import Path
import duckdb

def main() -> None:
    p=argparse.ArgumentParser(); p.add_argument('--warehouse',default='/tmp/artist_security_1000.duckdb'); p.add_argument('--out',default='/tmp/active_youtube_channels.json'); a=p.parse_args()
    c=duckdb.connect(a.warehouse,read_only=True)
    rows=c.execute("""
      WITH attention AS (SELECT artist_key, COUNT(*) n FROM metrics.artist_attention_observations WHERE status='ok' GROUP BY artist_key),
      ids AS (SELECT entity_key, COUNT(*) n FROM core.entity_external_ids WHERE entity_type='artist' GROUP BY entity_key),
      perf AS (SELECT artist_mbid, COUNT(*) n FROM core.event_performers WHERE artist_mbid IS NOT NULL GROUP BY artist_mbid),
      selected AS (
        SELECT a.artist_key FROM core.artists a
        LEFT JOIN attention x ON x.artist_key=a.artist_key LEFT JOIN ids i ON i.entity_key=a.artist_key LEFT JOIN perf f ON f.artist_mbid=a.musicbrainz_id
        WHERE a.artist_key IS NOT NULL ORDER BY (COALESCE(f.n,0)>0) DESC,(COALESCE(i.n,0)+COALESCE(x.n,0)) DESC,a.artist_key LIMIT 1000
      ), candidates AS (
        SELECT e.entity_key artist_key,e.id_value youtube_channel_id,ROW_NUMBER() OVER(PARTITION BY e.entity_key ORDER BY e.id_value) rn
        FROM core.entity_external_ids e JOIN selected s ON s.artist_key=e.entity_key
        WHERE e.entity_type='artist' AND e.id_type='youtube'
      ) SELECT artist_key,youtube_channel_id FROM candidates WHERE rn=1 ORDER BY artist_key
    """).fetchall(); c.close()
    channels=[{'artist_key':x,'youtube_channel_id':y,'hot':i<250,'status':'ACTIVE'} for i,(x,y) in enumerate(rows)]
    data={'version':'youtube_channels_v1','created_at':datetime.now(timezone.utc).isoformat(),'source':'ARTIST_SECURITY_1000 verified identities','channels':channels,'counts':{'active_candidates':len(channels),'hot_candidates':min(250,len(channels))}}
    Path(a.out).write_text(json.dumps(data,indent=2)+'\n'); print(json.dumps(data['counts']))
if __name__=='__main__': main()
