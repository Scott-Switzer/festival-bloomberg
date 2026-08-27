"""Build ARTIST_SECURITY_25000 from the existing local canonical estate."""
from __future__ import annotations
import argparse, json
from pathlib import Path
import duckdb
from festival_bloomberg.migrations import apply_pending_migrations
from festival_bloomberg.security.artist_security_25000 import build_tiered_universe, scale_report
from festival_bloomberg.security.bulk_scale import export_query_to_parquet, register_manifest

def main() -> None:
    p=argparse.ArgumentParser(); p.add_argument('--warehouse',default='/tmp/artist_security_1000.duckdb'); p.add_argument('--out',default='reports/artist_security_25000'); a=p.parse_args()
    c=duckdb.connect(a.warehouse); apply_pending_migrations(c)
    universe=build_tiered_universe(c)
    manifests=[]
    # Export only real existing factor observations filtered to selected artists.
    manifest=export_query_to_parquet(c, query="""
      SELECT f.* FROM metrics.artist_factor_observations f
      JOIN security.artist_security_universe_25000 u ON u.artist_key=f.artist_key
    """, params=None, output_dir=a.out, dataset='artist_factors', source='canonical Festival Intelligence factor observations', source_version='artist_factors_v1')
    register_manifest(c,manifest); manifests.append(manifest)
    report=scale_report(c)
    report.update({'milestone':'ARTIST_SECURITY_25000_DATABASE_V1','universe':universe,'manifests':manifests,'storage_root':str(Path(a.out).resolve())})
    out=Path(a.out)/'ARTIST_SECURITY_25000_DATABASE_REPORT.json'; out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(report,indent=2,default=str)+'\n')
    print(json.dumps(report,indent=2,default=str)[:12000]); c.close()
if __name__=='__main__': main()
