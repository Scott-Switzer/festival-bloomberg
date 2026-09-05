"""Offline tests for the cloud artist-intelligence gold materializers.

The cloud factor-tape and sentiment jobs are pure materialization over R2
staging assets written by the Worker. These tests use a fake lake so the
contract is proven deterministically without any network/credential.

Covered:
    - youtube tick normalization under the comparability contract
    - gold factor-tape build (immutable rows, CURRENT only after verify)
    - honest empty sentiment generation (NO_SAMPLES_YET, never fake rows)
    - gold sentiment aggregation with the VADER baseline
    - folding gold products into the serving artifact
"""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _tick(
    *,
    artist_key: str = "mbid::00136dd9-1047-4999-af1f-d709c5fa09f9",
    channel: str = "UCq6UY6I7vJ40YvgGRGZTv-g",
    subscribers: float | None = 7760,
    views: float | None = 4135650,
    videos: float | None = 8,
    observed_at: str = "2026-08-27T22:04:51.121Z",
) -> dict:
    tick: dict = {
        "schema_version": "youtube_channel_tick_v1",
        "tick_type": "VALUE_CHANGE",
        "artist_key": artist_key,
        "youtube_channel_id": channel,
        "observed_at": observed_at,
        "retrieved_at": observed_at,
        "knowledge_time": observed_at,
        "subscriber_count": subscribers,
        "subscriber_count_hidden": False,
        "subscriber_precision": "EXACT_AS_EXPOSED",
        "channel_view_count": views,
        "video_count": videos,
        "changed_fields": ["subscriber_count", "channel_view_count", "video_count"],
        "current_value_hash": "a105c143",
        "raw_evidence_ref": "r2://raw/youtube/37/30/abc.json",
        "source": "YOUTUBE_API",
        "quota_units": 1,
        "rights_status": "PROVIDER_TERMS_REVIEW_REQUIRED",
        "commercial_use_status": "INTERNAL_ANALYTICS_ONLY",
    }
    return {k: v for k, v in tick.items() if v is not None}


class FakeLake:
    """Minimal in-memory fake of the R2 lake surface used by the jobs."""

    def __init__(self) -> None:
        self.config = MagicMock()
        self.config.lake_bucket = "lake"
        self.config.raw_bucket = "raw"
        self.config.private_bucket = "private"
        self.config.backup_bucket = "backups"
        self.objects: dict[str, dict] = {}
        self._s3 = _FakeS3(self.objects)

    def put_bytes(self, bucket, key, data, content_type=None, metadata=None):
        self.objects[f"{bucket}/{key}"] = data

    def get_bytes(self, bucket, key) -> bytes:
        return self.objects[f"{bucket}/{key}"]

    def list_prefix(self, bucket, prefix, limit=1000) -> list[dict]:
        return [
            {"key": k.split("/", 1)[1], "size": len(v), "etag": hashlib.md5(v).hexdigest()}
            for k, v in sorted(self.objects.items())
            if k.startswith(f"{bucket}/{prefix}")
        ][:limit]

    def head(self, bucket, key):
        data = self.objects.get(f"{bucket}/{key}")
        return {"ContentLength": len(data)} if data is not None else None

    def read_versioned_json(self, bucket, key):
        data = self.objects.get(f"{bucket}/{key}")
        return (json.loads(data), hashlib.md5(data).hexdigest()) if data is not None else (None, None)

    def put_json_if_version(self, bucket, key, payload, etag):
        _, current_etag = self.read_versioned_json(bucket, key)
        if current_etag != etag:
            raise RuntimeError("PRECONDITION_FAILED")
        self.put_bytes(bucket, key, json.dumps(payload, sort_keys=True).encode())

    def get_bytes_if_match(self, bucket, key, etag):
        data = self.get_bytes(bucket, key)
        if hashlib.md5(data).hexdigest() != etag:
            raise RuntimeError("PRECONDITION_FAILED")
        return data

    def read_checkpoint(self, bucket, key) -> dict | None:
        data = self.objects.get(f"{bucket}/{key}")
        if data is None:
            return None
        return json.loads(data)

    def verify_object(self, bucket, key, expected_sha) -> bool:
        import hashlib

        data = self.objects.get(f"{bucket}/{key}")
        if data is None:
            return False
        return hashlib.sha256(data).hexdigest() == expected_sha

    def write_manifest(self, bucket, key, payload) -> None:
        self.put_bytes(bucket, key, json.dumps(payload, default=str).encode())


class _FakeS3:
    def __init__(self, objects: dict) -> None:
        self.objects = objects

    def upload_file(self, path, bucket, key, ExtraArgs=None) -> None:
        self.objects[f"{bucket}/{key}"] = Path(path).read_bytes()

    def download_file(self, bucket, key, dest) -> None:
        Path(dest).write_bytes(self.objects[f"{bucket}/{key}"])


@pytest.fixture()
def fake_lake(monkeypatch) -> FakeLake:
    from types import SimpleNamespace
    from festival_bloomberg.cloud import factor_history
    monkeypatch.setattr(factor_history.shutil, "disk_usage", lambda _: SimpleNamespace(free=10 * 1024**3))
    lake = FakeLake()
    from festival_bloomberg.cloud import batch_jobs

    monkeypatch.setattr(batch_jobs, "_get_lake", lambda: lake)
    return lake


def test_normalize_youtube_tick_full_comparability_contract():
    from festival_bloomberg.cloud.batch_jobs import _normalize_youtube_tick

    rows = _normalize_youtube_tick(_tick())
    assert len(rows) == 3
    names = {r["factor_name"] for r in rows}
    assert names == {"subscriber_count", "channel_view_count", "video_count"}
    for row in rows:
        assert row["platform"] == "youtube"
        assert row["measurement_basis"] == "POINT_IN_TIME"
        assert row["population_scope"] == "CHANNEL"
        assert row["measurement_window"] is None
        assert row["methodology_version"] == "youtube-data-api-v3-channels"
        assert row["coverage_generation"] == "youtube_channel_tick_v1"
        assert row["rights_status"] == "PROVIDER_TERMS_REVIEW_REQUIRED"
        assert row["commercial_use_status"] == "INTERNAL_ANALYTICS_ONLY"
        assert row["quality_status"] == "EXACT_AS_EXPOSED"
        assert row["evidence_ref"].startswith("r2://")
        assert row["observation_time"] == "2026-08-27T22:04:51.121Z"


def test_normalize_youtube_tick_unknown_stays_unknown_not_zero():
    from festival_bloomberg.cloud.batch_jobs import _normalize_youtube_tick

    # subscriber_count missing entirely → no fabricated 0 row.
    rows = _normalize_youtube_tick(_tick(subscribers=None))
    assert all(r["factor_name"] != "subscriber_count" for r in rows)
    assert {r["factor_name"] for r in rows} == {"channel_view_count", "video_count"}


def test_normalize_youtube_tick_requires_identity():
    from festival_bloomberg.cloud.batch_jobs import _normalize_youtube_tick

    assert _normalize_youtube_tick(_tick(artist_key="")) == []
    assert _normalize_youtube_tick(_tick(channel="")) == []


def test_tape_build_publishes_verified_generation(fake_lake, tmp_path: Path):
    from festival_bloomberg.cloud import batch_jobs

    fake_lake.objects["lake/staging/youtube/date=2026-08-27/hour=22/minute=04/a.json"] = json.dumps(
        _tick()
    ).encode()
    fake_lake.objects["lake/staging/youtube/date=2026-08-28/hour=09/minute=01/b.json"] = json.dumps(
        _tick(observed_at="2026-08-28T09:01:00.000Z", subscribers=7900, views=4200000)
    ).encode()

    result = batch_jobs.run_artist_factor_tape_build(
        {"job_id": "tape_test", "params": {}}, tmp_path / "scratch"
    )
    assert result["status"] == "COMPLETED"
    assert result["factor_rows"] == 6
    assert result["artists"] == 1

    current = fake_lake.read_checkpoint("lake", "gold/artist_factor_tape/CURRENT.json")
    assert current is not None
    assert current["factor_rows"] == 6
    tape_key = current["object_key"]
    assert fake_lake.verify_object("lake", tape_key, current["sha256"])
    # Generation object is immutable: key embeds the generation.
    assert "artist_factor_tape_v1_" in tape_key


def test_tape_build_fails_closed_without_ticks(fake_lake, tmp_path: Path):
    from festival_bloomberg.cloud import batch_jobs

    with pytest.raises(RuntimeError, match="FACTOR_TAPE_EMPTY"):
        batch_jobs.run_artist_factor_tape_build(
            {"job_id": "tape_empty", "params": {}}, tmp_path / "scratch"
        )


def test_sentiment_build_honest_empty_generation(fake_lake, tmp_path: Path):
    from festival_bloomberg.cloud import batch_jobs

    result = batch_jobs.run_artist_sentiment_build(
        {"job_id": "sent_empty", "params": {}}, tmp_path / "scratch"
    )
    assert result["status"] == "COMPLETED"
    assert result["rows"] == 0
    assert result["note"].startswith("NO_SAMPLES_YET")
    current = fake_lake.read_checkpoint("lake", "gold/artist_sentiment/CURRENT.json")
    assert current is not None
    assert current["status"] == "NO_SAMPLES_YET"
    assert current["object_key"] is None  # no fake rows


def test_sentiment_build_aggregates_vader_over_samples(fake_lake, tmp_path: Path):
    from festival_bloomberg.cloud import batch_jobs

    sample = {
        "schema_version": "youtube_comment_sample_v1",
        "artist_key": "mbid::abc",
        "platform": "youtube",
        "text": "This is absolutely fantastic, best show ever!",
        "engagement": 12,
        "language": "en",
        "observed_at": "2026-08-28T10:00:00.000Z",
        "retrieved_at": "2026-08-28T10:00:00.000Z",
        "knowledge_time": "2026-08-28T10:00:00.000Z",
        "source": "YOUTUBE_API",
        "rights_status": "PROVIDER_TERMS_REVIEW_REQUIRED",
        "commercial_use_status": "INTERNAL_ANALYTICS_ONLY",
    }
    negative = dict(sample)
    negative["text"] = "This was absolutely the worst show I have ever seen. Terrible, terrible, awful."
    negative["observed_at"] = "2026-08-28T11:00:00.000Z"
    fake_lake.objects["lake/staging/sentiment_samples/2026-08-28/pos.json"] = json.dumps(sample).encode()
    fake_lake.objects["lake/staging/sentiment_samples/2026-08-28/neg.json"] = json.dumps(negative).encode()

    result = batch_jobs.run_artist_sentiment_build(
        {"job_id": "sent_real", "params": {}}, tmp_path / "scratch"
    )
    assert result["status"] == "COMPLETED"
    assert result["rows"] == 1
    assert result["artists"] == 1

    current = fake_lake.read_checkpoint("lake", "gold/artist_sentiment/CURRENT.json")
    assert current is not None
    assert current["rows"] == 1
    assert current["model"]["name"] == "vader"

    import duckdb

    key = current["object_key"]
    data = fake_lake.get_bytes("lake", key)
    parquet_path = tmp_path / "out.parquet"
    parquet_path.write_bytes(data)
    conn = duckdb.connect()
    rows = conn.execute(f"SELECT * FROM read_parquet({str(parquet_path)!r})").fetchall()
    cols = [d[0] for d in conn.execute(f"DESCRIBE SELECT * FROM read_parquet({str(parquet_path)!r})").fetchall()]
    conn.close()
    assert len(rows) == 1
    row = dict(zip(cols, rows[0], strict=False))
    assert row["mention_count"] == 2
    assert row["analyzed_count"] == 2
    # One positive + one negative sample: even split, negative mean because
    # the negative sample has the larger magnitude.
    assert row["positive_share"] == 0.5
    assert row["negative_share"] == 0.5
    assert row["neutral_share"] == 0.0
    assert row["sentiment_mean"] < 0
    assert json.loads(row["languages"]) == ["en"]


def test_fold_gold_into_serving_artifact(fake_lake, tmp_path: Path):
    """The fold tolerates missing gold products and materializes when present."""
    from festival_bloomberg.cloud import batch_jobs

    # No gold products → fold succeeds with zero counts.
    work = tmp_path / "fold"
    work.mkdir()
    import duckdb

    conn = duckdb.connect(str(work / "terminal.duckdb"))
    conn.execute("CREATE TABLE artists (artist_key VARCHAR)")
    conn.close()

    manifest = MagicMock()
    manifest.source_paths = []
    manifest.r2_read_bytes = 0
    counts = batch_jobs._fold_gold_artist_intelligence(
        fake_lake, work,
        factor_tape_current="gold/artist_factor_tape/CURRENT.json",
        sentiment_current="gold/artist_sentiment/CURRENT.json",
        manifest=manifest,
    )
    assert counts == {"artist_factor_observations": 0, "artist_sentiment_observations": 0}

    # With gold products present → tables materialize with the tape rows.
    fake_lake.objects["lake/staging/youtube/date=2026-08-27/hour=22/minute=04/a.json"] = json.dumps(
        _tick()
    ).encode()
    tape_result = batch_jobs.run_artist_factor_tape_build(
        {"job_id": "tape_fold", "params": {}}, tmp_path / "scratch_tape"
    )
    assert tape_result["status"] == "COMPLETED"

    counts = batch_jobs._fold_gold_artist_intelligence(
        fake_lake, work,
        factor_tape_current="gold/artist_factor_tape/CURRENT.json",
        sentiment_current="gold/artist_sentiment/CURRENT.json",
        manifest=manifest,
    )
    assert counts["artist_factor_observations"] == 3

    conn = duckdb.connect(str(work / "terminal.duckdb"))
    n = conn.execute("SELECT COUNT(*) FROM artist_factor_observations").fetchone()[0]
    has_measurement = conn.execute(
        "SELECT COUNT(*) FROM artist_factor_observations WHERE measurement_basis = 'POINT_IN_TIME'"
    ).fetchone()[0]
    conn.close()
    assert n == 3
    assert has_measurement == 3

def test_fold_rejects_gold_hash_mismatch(fake_lake, tmp_path):
    from festival_bloomberg.cloud import batch_jobs
    from festival_bloomberg.cloud.job_manifest import new_manifest
    fake_lake.put_bytes('lake', 'staging/youtube/test.json', json.dumps(_tick()).encode())
    batch_jobs.run_artist_factor_tape_build({'job_id':'hash_test','params':{}}, tmp_path / 'build')
    current = fake_lake.read_checkpoint('lake', 'gold/artist_factor_tape/CURRENT.json')
    current['sha256'] = '0' * 64
    fake_lake.put_bytes('lake', 'gold/artist_factor_tape/CURRENT.json', json.dumps(current).encode())
    work = tmp_path / 'serving'
    work.mkdir()
    with pytest.raises(RuntimeError, match='FACTOR_GOLD_HASH_MISMATCH'):
        batch_jobs._fold_gold_artist_intelligence(fake_lake, work,
            factor_tape_current='gold/artist_factor_tape/CURRENT.json',
            sentiment_current='gold/artist_sentiment/CURRENT.json',
            manifest=new_manifest('terminal_serving_build_v1', 'hash_test'))
