"""Offline tests for the deterministic human-labeling export."""

from __future__ import annotations

from festival_bloomberg.labels import MANUAL_FIELDS, export_fan_text, stable_sample


def test_stable_sample_is_deterministic():
    ids = [f"obs_{i}" for i in range(500)]
    assert stable_sample(ids, 100) == stable_sample(ids, 100)
    assert len(stable_sample(ids, 100)) == 100


def test_stable_sample_no_cherry_picking_order():
    ids = [f"obs_{i}" for i in range(20)]
    sample = stable_sample(ids, 5)
    # deterministic hash ranking, not insertion order
    assert sample != ids[:5]


def test_stable_sample_bounds():
    assert stable_sample([], 10) == []
    assert stable_sample(["a", "b"], 0) == []
    assert set(stable_sample(["a", "b"], 99)) == {"a", "b"}  # capped at available


def test_export_fan_text_only_fan_roles_and_null_labels(tmp_path):
    from festival_bloomberg.acquisition.contracts import (
        AcquisitionResult,
        AcquisitionStatus,
        utc_now,
    )
    from festival_bloomberg.evidence.repository import EvidenceRepository
    from festival_bloomberg.warehouse.repository import FestivalRepository

    repo = FestivalRepository(str(tmp_path / "labels.duckdb"))
    try:
        evidence = EvidenceRepository(repo.conn)
        from festival_bloomberg.acquisition.contracts import AcquisitionRequest

        request = AcquisitionRequest.new(
            entity_id="radiohead",
            entity_type="artist",
            platform="youtube",
            query="Radiohead",
            correlation_id="labels-test",
        )
        records = [
            {
                "platform": "youtube",
                "object_type": "comment",
                "platform_object_id": "c1",
                "text": "amazing show, going again",
                "content_role": "FAN_GENERATED",
                "content_role_method": "source_type",
                "resolution_method": "EXACT_PLATFORM_ID",
                "published_at": "2026-08-01T00:00:00Z",
                "author_public_id": "u1",
            },
            {
                # encyclopedic text must NOT be exported as fan text
                "platform": "wikipedia",
                "object_type": "encyclopedic_article",
                "platform_object_id": "rev1",
                "text": "Radiohead are an English rock band.",
                "content_role": "ENCYCLOPEDIC",
                "resolution_method": "EXACT_CANONICAL_URL",
            },
        ]
        result = AcquisitionResult(
            request_id=request.request_id,
            provider="youtube",
            provider_endpoint=None,
            status=AcquisitionStatus.SUCCESS,
            started_at=utc_now(),
            completed_at=utc_now(),
            record_count=2,
            cost_usd=0.0,
            provider_metadata={},
            records=tuple(records),
        )
        evidence.ingest(request, result)

        rows = export_fan_text(evidence, artist_id="radiohead", sample_size=100)
        assert len(rows) == 1  # only the FAN_GENERATED comment
        assert rows[0]["platform"] == "youtube"
        assert rows[0]["content_role"] == "FAN_GENERATED"
        for field in MANUAL_FIELDS:
            assert field in rows[0]
            assert rows[0][field] is None
    finally:
        repo.close()
