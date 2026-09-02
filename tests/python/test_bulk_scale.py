import duckdb
from festival_bloomberg.migrations import apply_pending_migrations
from festival_bloomberg.security.bulk_scale import export_query_to_parquet, register_manifest


def test_export_query_writes_manifest_and_registers(tmp_path):
    c=duckdb.connect(':memory:')
    apply_pending_migrations(c)
    c.execute("INSERT INTO core.artists (artist_key,name,normalized_name,musicbrainz_id,type,source_system,ingested_at) VALUES ('a','A','a','00000000-0000-0000-0000-000000000001','Group','test',CURRENT_TIMESTAMP)")
    manifest=export_query_to_parquet(c,query='SELECT * FROM core.artists',params=None,output_dir=tmp_path,dataset='artists',source='test',source_version='v1')
    assert manifest['row_count']==1
    assert manifest['partition_count']==1
    register_manifest(c,manifest)
    assert c.execute("SELECT COUNT(*) FROM security.bulk_dataset_manifests").fetchone()[0]==1
    c.close()
