"""Failure-oriented preservation and replay tests; no provider calls."""
import io
import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from festival_bloomberg.cloud import batch_jobs
from festival_bloomberg.cloud.factor_history import CURRENT, SOURCE
from test_cloud_gold_artist_intelligence import FakeLake, _tick


@pytest.fixture
def lake(monkeypatch):
    lake = FakeLake()
    monkeypatch.setattr(batch_jobs, '_get_lake', lambda: lake)
    return lake


def add(lake, key, **kw):
    lake.objects[f'lake/{SOURCE}{key}.json'] = json.dumps(_tick(**kw)).encode()


def run(lake, tmp_path, job, **params):
    return batch_jobs.run_artist_factor_tape_build({'job_id': job, 'params': params}, tmp_path)


def rows(lake):
    current = lake.read_checkpoint('lake', CURRENT)
    return pq.read_table(io.BytesIO(lake.get_bytes('lake', current['object_key']))).to_pylist()


def test_bounded_refresh_preserves_parent_and_catches_backdated_arrival(lake, tmp_path):
    add(lake, 'b')
    first = run(lake, tmp_path, 'first', max_ticks=1)
    old = rows(lake)
    add(lake, 'a', observed_at='2026-08-26T22:00:00Z')
    add(lake, 'c', observed_at='2026-08-29T22:00:00Z')
    second = run(lake, tmp_path, 'second', max_ticks=1)
    assert second['factor_rows'] == 6
    assert second['pending_ticks'] == 1
    assert all(r in rows(lake) for r in old)
    third = run(lake, tmp_path, 'third', max_ticks=1)
    assert third['factor_rows'] == 9 and third['pending_ticks'] == 0
    assert run(lake, tmp_path, 'third', max_ticks=1) == third
    assert first['generation'] != third['generation']


def test_no_new_inputs_reads_no_ticks_and_keeps_exact_rows(lake, tmp_path, monkeypatch):
    add(lake, 'a')
    run(lake, tmp_path, 'first')
    before = rows(lake)
    monkeypatch.setattr(lake, 'get_bytes_if_match', lambda *a: pytest.fail('reread tick'))
    result = run(lake, tmp_path, 'again')
    assert result['added_rows'] == 0 and result['tick_rows_read'] == 0
    assert rows(lake) == before


def test_artist_cap_retains_repeats_and_rejects_new_artist(lake, tmp_path):
    add(lake, 'a')
    add(lake, 'b', observed_at='2026-08-28T22:00:00Z')
    assert run(lake, tmp_path, 'first', max_artists=1)['factor_rows'] == 6
    previous = lake.objects[f'lake/{CURRENT}']
    add(lake, 'c', artist_key='mbid::second')
    with pytest.raises(RuntimeError, match='ARTIST_LIMIT_EXCEEDED'):
        run(lake, tmp_path, 'second', max_artists=1)
    assert lake.objects[f'lake/{CURRENT}'] == previous


def test_interruption_resume_reuses_verified_chunk(lake, tmp_path, monkeypatch):
    add(lake, 'a')
    add(lake, 'b', observed_at='2026-08-28T22:00:00Z')
    original = lake.get_bytes_if_match
    def interrupt(bucket, key, etag):
        if key.endswith('b.json'):
            raise RuntimeError('INTERRUPTED')
        return original(bucket, key, etag)
    monkeypatch.setattr(lake, 'get_bytes_if_match', interrupt)
    with pytest.raises(RuntimeError, match='INTERRUPTED'):
        run(lake, tmp_path, 'resume', batch_size=1)
    assert f'lake/{CURRENT}' not in lake.objects
    assert not (tmp_path / 'factor_tape').exists()
    reads = []
    def tracked(*args):
        reads.append(args[1])
        return original(*args)
    monkeypatch.setattr(lake, 'get_bytes_if_match', tracked)
    result = run(lake, tmp_path, 'resume', batch_size=1)
    assert result['factor_rows'] == 6 and result['reused_chunks'] == 1
    assert len(reads) == 1 and reads[0].endswith('b.json')


def test_stale_publisher_keeps_new_current_and_verified_candidate(lake, tmp_path, monkeypatch):
    add(lake, 'a')
    original = lake.put_json_if_version
    winner = {'generation': 'concurrent-winner'}
    def race(bucket, key, payload, etag):
        if key == CURRENT:
            lake.put_bytes(bucket, key, json.dumps(winner).encode())
        return original(bucket, key, payload, etag)
    monkeypatch.setattr(lake, 'put_json_if_version', race)
    with pytest.raises(RuntimeError, match='PRECONDITION_FAILED'):
        run(lake, tmp_path, 'loser')
    assert lake.read_checkpoint('lake', CURRENT) == winner
    manifest = lake.read_checkpoint('lake', 'control/jobs/artist_factor_tape_build_v1/loser/manifest.json')
    assert manifest['status'] == manifest['publication_state'] == 'VERIFIED'


def test_inventory_cap_fails_before_partial_publication(lake, tmp_path):
    add(lake, 'a'); add(lake, 'b')
    with pytest.raises(RuntimeError, match='INVENTORY_LIMIT_EXCEEDED'):
        run(lake, tmp_path, 'cap', max_inventory=1)
    assert f'lake/{CURRENT}' not in lake.objects


@pytest.mark.parametrize('change', [{'is_fixture': True}, {'source': 'SANDBOX'}, {'youtube_channel_id': 'ambiguous'}, {'subscriber_count': float('nan')}, {'knowledge_time': None}])
def test_invalid_evidence_blocks_publication(lake, tmp_path, change):
    tick = _tick(); tick.update(change)
    lake.objects[f'lake/{SOURCE}a.json'] = json.dumps(tick).encode()
    with pytest.raises((RuntimeError, ValueError)):
        run(lake, tmp_path, 'bad')
    assert f'lake/{CURRENT}' not in lake.objects


def test_source_mutation_and_parent_corruption_fail_closed(lake, tmp_path):
    add(lake, 'a')
    run(lake, tmp_path, 'first')
    before = lake.objects[f'lake/{CURRENT}']
    add(lake, 'a', subscribers=42)
    with pytest.raises(RuntimeError, match='SOURCE_MUTATED'):
        run(lake, tmp_path, 'mutation')
    add(lake, 'a')
    parent = lake.read_checkpoint('lake', CURRENT)
    lake.objects[f"lake/{parent['object_key']}"] = b'corrupt'
    with pytest.raises(RuntimeError, match='INPUT_HASH_MISMATCH'):
        run(lake, tmp_path, 'corrupt')
    assert lake.objects[f'lake/{CURRENT}'] == before


def test_zero_and_unknown_and_conflicting_values_are_preserved(lake, tmp_path):
    add(lake, 'a', subscribers=0, views=None)
    add(lake, 'b', subscribers=1, views=None)
    run(lake, tmp_path, 'values')
    data = rows(lake)
    assert sorted(r['value'] for r in data if r['factor_name'] == 'subscriber_count') == [0, 1]
    assert not any(r['factor_name'] == 'channel_view_count' for r in data)
    assert all(r['geographic_scope'] is None for r in data)


def test_same_observation_key_different_lineage_fails_closed(lake, tmp_path):
    add(lake, 'a')
    tick = _tick(); tick['rights_status'] = 'DIFFERENT_CLAIM'
    lake.objects[f'lake/{SOURCE}b.json'] = json.dumps(tick).encode()
    with pytest.raises(RuntimeError, match='OBSERVATION_KEY_CONFLICT'):
        run(lake, tmp_path, 'conflict')
    assert f'lake/{CURRENT}' not in lake.objects


def test_output_verification_failure_keeps_parent(lake, tmp_path, monkeypatch):
    add(lake, 'a')
    run(lake, tmp_path, 'first')
    previous = lake.objects[f'lake/{CURRENT}']
    add(lake, 'b', observed_at='2026-08-29T22:00:00Z')
    original = lake.verify_object
    def corrupt(bucket, key, sha):
        return False if key.startswith('gold/') else original(bucket, key, sha)
    monkeypatch.setattr(lake, 'verify_object', corrupt)
    with pytest.raises(RuntimeError, match='Verification failed'):
        run(lake, tmp_path, 'verify')
    assert lake.objects[f'lake/{CURRENT}'] == previous


def test_changed_resume_parameters_are_rejected(lake, tmp_path):
    add(lake, 'a')
    run(lake, tmp_path, 'first', max_ticks=1)
    with pytest.raises(RuntimeError, match='RESUME_CONTRACT_MISMATCH'):
        run(lake, tmp_path, 'first', max_ticks=2)


def test_r2_listing_uses_exact_bounded_pages():
    from unittest.mock import Mock
    from festival_bloomberg.cloud.r2_lake import R2Lake
    lake = object.__new__(R2Lake)
    lake._s3 = Mock()
    lake._s3.list_objects_v2.side_effect = [
        {'Contents': [{'Key': str(i), 'Size': 1} for i in range(1000)], 'IsTruncated': True, 'NextContinuationToken': 'next'},
        {'Contents': [{'Key': str(i), 'Size': 1} for i in range(1000, 1003)], 'IsTruncated': True, 'NextContinuationToken': 'unused'},
    ]
    assert len(lake.list_prefix('lake', 'staging/', limit=1003)) == 1003
    calls = lake._s3.list_objects_v2.call_args_list
    assert calls[0].kwargs['MaxKeys'] == 1000
    assert calls[1].kwargs['MaxKeys'] == 3
    assert calls[1].kwargs['ContinuationToken'] == 'next'
    assert lake.list_prefix('lake', '', limit=0) == []


def test_r2_publication_uses_conditional_writes():
    from unittest.mock import Mock
    from festival_bloomberg.cloud.r2_lake import R2Lake
    lake = object.__new__(R2Lake); lake._s3 = Mock()
    lake.put_json_if_version('lake', CURRENT, {'generation': 'a'}, None)
    assert lake._s3.put_object.call_args.kwargs['IfNoneMatch'] == '*'
    lake.put_json_if_version('lake', CURRENT, {'generation': 'b'}, 'original-etag')
    assert lake._s3.put_object.call_args.kwargs['IfMatch'] == 'original-etag'
