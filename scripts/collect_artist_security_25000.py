"""ARTIST_SECURITY_25000 database orchestration.

Network-heavy dump downloads are explicit opt-in. The default command builds
from already present local evidence and emits honest coverage metrics.
"""
from __future__ import annotations
import argparse, json
from pathlib import Path
import duckdb
from festival_bloomberg.migrations import apply_pending_migrations
from festival_bloomberg.security.artist_security_25000 import build_tiered_universe, scale_report
from festival_bloomberg.security.bulk_scale import export_query_to_parquet, register_manifest

def run(conn, out: Path) -> dict:
    universe=build_tiered_universe(conn)
    manifests=[]
    queries={
      'factor_observations': "SELECT f.* FROM metrics.artist_factor_observations f JOIN security.artist_security_universe_25000 u ON u.artist_key=f.artist_key",
      'attention_observations': "SELECT o.* FROM metrics.artist_attention_observations o JOIN security.artist_security_universe_25000 u ON u.artist_key=o.artist_key",
      'performance_observations': "SELECT p.* FROM metrics.artist_performance_observations p JOIN security.artist_security_universe_25000 u ON u.artist_key=p.artist_key",
    }
    for name,q in queries.items():
        m=export_query_to_parquet(conn,query=q,params=None,output_dir=out,dataset=name,source='existing canonical Festival Intelligence estate',source_version='25k_v1')
        register_manifest(conn,m); manifests.append(m)
    report=scale_report(conn); report.update({'milestone':'ARTIST_SECURITY_25000_DATABASE_V1','universe':universe,'manifests':manifests,'status':'PARTIAL_DATA_ESTATES_EXIST'})
    (out/'ARTIST_SECURITY_25000_DATABASE_REPORT.json').write_text(json.dumps(report,indent=2,default=str)+'\n')
    return report

def main():
    p=argparse.ArgumentParser(); p.add_argument('--warehouse',default='/tmp/artist_security_1000.duckdb'); p.add_argument('--out',default='/tmp/artist_security_25000_full'); a=p.parse_args()
    out=Path(a.out); out.mkdir(parents=True,exist_ok=True); c=duckdb.connect(a.warehouse); apply_pending_migrations(c); print(json.dumps(run(c,out),indent=2,default=str)[:15000]); c.close()
if __name__=='__main__': main()
