"""Bounded real-R2 retention/restart pilot. Writes ONLY under validation/<run>/.

Run with PYTHONPATH=python. Uses existing rclone/env R2 credentials in memory.
No provider calls, production pointer changes, or customer data. Source slice
is explicit; the output is a pilot, never a full-estate coverage claim.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
from pathlib import Path
from types import SimpleNamespace

import duckdb

from festival_bloomberg.cloud import batch_jobs
from festival_bloomberg.cloud.factor_history import CURRENT, SOURCE
from festival_bloomberg.cloud.r2_lake import R2Lake
from festival_bloomberg.lake.r2 import r2_client


class IsolatedLake(R2Lake):
    def __init__(self, client, run, source_date):
        self.client = client
        self.prefix = f'validation/factor_history_v2/{run}/'
        self.source_date = source_date
        self.config = SimpleNamespace(lake_bucket='festival-intelligence-lake')
        owner = self
        class Files:
            def upload_file(self, path, bucket, key, **kwargs):
                return client.upload_file(path, bucket, owner.prefix + key, **kwargs)
            def download_file(self, bucket, key, dest):
                return client.download_file(bucket, owner.read_key(key), dest)
        self._s3 = Files()
        self.tick_reads = 0
        self.interrupt_after = None

    def read_key(self, key):
        if key.startswith(self.prefix):
            return key
        scoped = self.prefix + key
        if key.startswith('control/') or key == CURRENT:
            return scoped
        try:
            self.client.head_object(Bucket=self.config.lake_bucket, Key=scoped)
            return scoped
        except self.client.exceptions.ClientError as exc:
            if exc.response['Error']['Code'] not in {'404', 'NoSuchKey'}:
                raise
            return key

    def head(self, bucket, key):
        try:
            return self.client.head_object(Bucket=bucket, Key=self.read_key(key))
        except self.client.exceptions.ClientError as exc:
            if exc.response['Error']['Code'] in {'404', 'NoSuchKey'}:
                return None
            raise

    def get_bytes(self, bucket, key):
        with self.client.get_object(Bucket=bucket, Key=self.read_key(key))['Body'] as body:
            return body.read()

    def read_versioned_json(self, bucket, key):
        try:
            response = self.client.get_object(Bucket=bucket, Key=self.read_key(key))
        except self.client.exceptions.ClientError as exc:
            if exc.response['Error']['Code'] in {'404', 'NoSuchKey'}:
                return None, None
            raise
        with response['Body'] as body:
            return json.loads(body.read()), response['ETag']

    def read_checkpoint(self, bucket, key):
        return self.read_versioned_json(bucket, key)[0]

    def put_bytes(self, bucket, key, data, content_type='application/octet-stream', metadata=None):
        return self.client.put_object(Bucket=bucket, Key=self.prefix + key, Body=data, ContentType=content_type)

    def put_json_if_version(self, bucket, key, payload, etag):
        condition = {'IfMatch': etag} if etag else {'IfNoneMatch': '*'}
        return self.client.put_object(Bucket=bucket, Key=self.prefix + key, Body=json.dumps(payload, sort_keys=True).encode(), ContentType='application/json', **condition)

    def get_bytes_if_match(self, bucket, key, etag):
        self.tick_reads += 1
        if self.interrupt_after is not None and self.tick_reads > self.interrupt_after:
            raise RuntimeError('INJECTED_PILOT_INTERRUPTION')
        with self.client.get_object(Bucket=bucket, Key=key, IfMatch=etag)['Body'] as body:
            return body.read()

    def list_prefix(self, bucket, prefix, limit=1000):
        assert prefix == SOURCE
        result = []
        for page in self.client.get_paginator('list_objects_v2').paginate(Bucket=bucket, Prefix=f'{prefix}date={self.source_date}/', PaginationConfig={'MaxItems': limit, 'PageSize': 1000}):
            result.extend({'key': o['Key'], 'size': o['Size'], 'etag': o['ETag']} for o in page.get('Contents', []))
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    parser.add_argument('--source-date', required=True)
    parser.add_argument('--max-ticks', type=int, default=512)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', args.run) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', args.source_date):
        parser.error('Invalid run/date')
    client = r2_client()
    lake = IsolatedLake(client, args.run, args.source_date)
    bucket = lake.config.lake_bucket
    production = client.get_object(Bucket=bucket, Key=CURRENT)['Body'].read()
    parent = json.loads(production)
    if lake.read_checkpoint(bucket, CURRENT) is None:
        lake.put_json_if_version(bucket, CURRENT, parent, None)
    batch_jobs._get_lake = lambda: lake
    params = {'max_ticks': args.max_ticks, 'batch_size': 128}
    first_spec = {'job_id': 'refresh_one', 'params': params}
    with tempfile.TemporaryDirectory(prefix='factor-history-pilot-') as tmp:
        scratch = Path(tmp)
        lake.interrupt_after = 128
        interrupted = False
        try:
            batch_jobs.run_artist_factor_tape_build(first_spec, scratch)
        except RuntimeError as exc:
            if str(exc) != 'INJECTED_PILOT_INTERRUPTION':
                raise
            interrupted = True
        assert lake.read_checkpoint(bucket, CURRENT)['generation'] == parent['generation']
        lake.interrupt_after = None
        first = batch_jobs.run_artist_factor_tape_build(first_spec, scratch)
        second_spec = {'job_id': 'refresh_two', 'params': params}
        second = batch_jobs.run_artist_factor_tape_build(second_spec, scratch)
        before = lake.tick_reads
        replay = batch_jobs.run_artist_factor_tape_build(second_spec, scratch)
        assert replay == second and lake.tick_reads == before
        for filename, key, sha in [('parent.parquet', parent['object_key'], parent['sha256']), ('candidate.parquet', second['object_key'], second['sha256'])]:
            lake._s3.download_file(bucket, key, str(scratch / filename))
            with (scratch / filename).open('rb') as stream:
                assert hashlib.file_digest(stream, 'sha256').hexdigest() == sha
        con = duckdb.connect()
        con.execute('CREATE TABLE parent AS SELECT * FROM read_parquet(?)', [str(scratch / 'parent.parquet')])
        con.execute('CREATE TABLE candidate AS SELECT * FROM read_parquet(?)', [str(scratch / 'candidate.parquet')])
        missing = con.execute('SELECT count(*) FROM (SELECT * FROM parent EXCEPT SELECT * FROM candidate)').fetchone()[0]
        assert missing == 0
        counts = con.execute('SELECT count(*),count(DISTINCT artist_key),min(observation_time),max(observation_time) FROM candidate').fetchone()
        con.close()
        # Exercise the unchanged serving fold + real product factor readmodel.
        fold = batch_jobs._fold_gold_artist_intelligence(lake, scratch, factor_tape_current=CURRENT, sentiment_current='control/no_sentiment.json', manifest=batch_jobs.new_manifest('pilot', args.run))
        con = duckdb.connect(str(scratch / 'terminal.duckdb'), read_only=True)
        from festival_bloomberg.terminal.artist_security import _artist_factor_tape
        artist = con.execute('SELECT artist_key FROM artist_factor_observations ORDER BY observation_time DESC LIMIT 1').fetchone()[0]
        product = _artist_factor_tape(con, artist)
        con.close()
        assert fold['artist_factor_observations'] == counts[0]
        unchanged = client.get_object(Bucket=bucket, Key=CURRENT)['Body'].read() == production
        assert unchanged
        report = {'status': 'PILOTED', 'production_current_unchanged': unchanged, 'source_slice': f'{SOURCE}date={args.source_date}/', 'namespace': f'r2://{bucket}/{lake.prefix}', 'parent': parent,
                  'interrupted': interrupted, 'missing_parent_rows': missing, 'replay_reads': lake.tick_reads - before,
                  'first': first, 'second': second, 'rows': counts[0], 'artists': counts[1], 'time_min': counts[2], 'time_max': counts[3],
                  'serving_fold': fold, 'product_artist': artist, 'product_status': product.get('status'), 'product_changes': len(product.get('changes', [])),
                  'provider_requests': 0, 'billed_cost_usd': None, 'cost_status': 'NOT_AVAILABLE'}
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
        lake.put_bytes(bucket, 'acceptance.json', json.dumps(report, sort_keys=True).encode(), content_type='application/json')
        print(json.dumps({k: report[k] for k in ('status','rows','artists','missing_parent_rows','interrupted','replay_reads','production_current_unchanged','product_status')}))


if __name__ == '__main__':
    main()
