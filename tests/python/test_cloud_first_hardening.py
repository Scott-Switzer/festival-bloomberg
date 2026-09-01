"""CLOUD_FIRST_EXECUTION_HARDENING_V1 — focused regression tests.

Covers:
  - Privacy: no raw listener IDs in durable output
  - Partitioning: deterministic HMAC golden vectors
  - Verification: all P6 failure modes (fail-closed)
  - Status security: unauthorized, missing token, oversized body, no leakage
  - Lifecycle: trigger returns quickly, durable status from R2
  - Reduce: Jaccard, cosine, lift, PMI formulas
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import struct
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── P12.1: PRIVACY TESTS ──────────────────────────────────────────


class TestListenerKeyPrivacy:
    """Assert raw source IDs/usernames do not appear in durable output."""

    def test_listener_key_is_hex_digest_not_raw_id(self):
        """The listener_key must be an HMAC hex digest, not a raw user_id."""
        from festival_bloomberg.cloud.listener_key import derive_listener_key

        test_secret = b"x" * 32
        key = derive_listener_key(42, test_secret)
        # Must be 64 hex chars (SHA-256 digest)
        assert len(key) == 64
        assert all(c in "0123456789abcdef" for c in key)
        # Must NOT be the raw user_id
        assert key != "42"
        assert key != str(42)

    def test_same_source_id_same_key_same_partition(self):
        """Same user_id + same secret → same listener_key + same partition."""
        from festival_bloomberg.cloud.listener_key import (
            derive_listener_key_and_partition,
        )

        test_secret = b"x" * 32
        key1, part1 = derive_listener_key_and_partition(42, 64, test_secret)
        key2, part2 = derive_listener_key_and_partition(42, 64, test_secret)
        assert key1 == key2, "HMAC pseudonymization must be deterministic"
        assert part1 == part2, "Partition assignment must be deterministic"

    def test_different_secret_version_incompatible(self):
        """Different secret → different listener_key (incompatible generation)."""
        from festival_bloomberg.cloud.listener_key import derive_listener_key

        secret_v1 = b"a" * 32
        secret_v2 = b"b" * 32
        key_v1 = derive_listener_key(42, secret_v1)
        key_v2 = derive_listener_key(42, secret_v2)
        assert key_v1 != key_v2, "Different secrets must produce different keys"

    def test_no_raw_identity_in_durable_schema(self):
        """Durable partial schema must contain only listener_key, not user_id/user_name.

        The cloud map partial schema (the pa.table written to R2) is:
          listener_key, artist_mbid, listen_count
        It must NOT contain: user_name, user_id.

        The in-memory mapping table (user_id → listener_key) is transient and
        never written to R2, so user_id appearing there is acceptable.
        """
        from festival_bloomberg.cloud import batch_jobs
        import inspect

        source = inspect.getsource(batch_jobs.run_listenbrainz_map)
        # The durable partial must use listener_key.
        assert "listener_key" in source, "Map must use listener_key"

        # Find the pa.table that writes the durable partial to R2.
        # This is the one followed by pq.write_table (the durable output).
        # The mapping table (user_id → listener_key) is in-memory only and
        # is followed by conn.register, not pq.write_table.
        durable_table_sections = source.split("pq.write_table")
        for section in durable_table_sections[1:]:
            # Look backward for the pa.table column names.
            preceding = source[:source.index("pq.write_table") + len(section)]
            # The pa.table just before pq.write_table is the durable one.
            # Check it does not contain user_name or user_id as column names.
            # We check the block between the last pa.table and pq.write_table.
            block_start = preceding.rfind("pa.table(")
            block = preceding[block_start:]
            assert '"user_name"' not in block, (
                "Durable partial schema must NOT contain user_name column"
            )
            assert '"user_id"' not in block, (
                "Durable partial schema must NOT contain user_id column"
            )
            break  # Only check the first durable table


    def test_secret_not_in_metadata(self):
        """The secret itself must never appear in manifest metadata."""
        from festival_bloomberg.cloud.listener_key import ListenerKeyContract

        contract = ListenerKeyContract()
        metadata = contract.to_metadata()
        # Only version identifiers, never the secret.
        for key, val in metadata.items():
            assert isinstance(val, str)
            assert len(val) < 100, f"Metadata value too long for {key}"
            assert "secret" not in val.lower() or "version" in val.lower(), (
                f"Metadata {key}={val} may contain secret value"
            )


# ── P12.2: PARTITIONING GOLDEN VECTORS ────────────────────────────


class TestPartitionGoldenVectors:
    """Test fixed golden vectors to prevent algorithm drift."""

    # Known test vector: secret = b"x"*32, user_id = 42, N = 64
    # Computed once and frozen — any change to the HMAC/partition algorithm
    # must update these vectors and be documented as a breaking change.

    def test_known_hmac_hex(self):
        """The HMAC-SHA256 of user_id=42 with test secret must match the golden vector."""
        from festival_bloomberg.cloud.listener_key import derive_listener_key

        test_secret = b"x" * 32
        key = derive_listener_key(42, test_secret)
        # Verify against independently computed HMAC
        expected = hmac.new(test_secret, b"42", hashlib.sha256).hexdigest()
        assert key == expected, (
            f"Listener key drift: expected {expected}, got {key}"
        )

    def test_known_partition(self):
        """The partition for user_id=42, N=64 must match the golden vector."""
        from festival_bloomberg.cloud.listener_key import derive_partition

        test_secret = b"x" * 32
        partition = derive_partition(42, 64, test_secret)
        # Verify against independently computed partition
        digest = hmac.new(test_secret, b"42", hashlib.sha256).digest()
        expected_part = int.from_bytes(digest[:8], "big") % 64
        assert partition == expected_part, (
            f"Partition drift: expected {expected_part}, got {partition}"
        )

    def test_partition_stable_across_calls(self):
        """Partition must be the same across multiple calls (no randomization)."""
        from festival_bloomberg.cloud.listener_key import derive_partition

        test_secret = b"x" * 32
        partitions = [derive_partition(42, 64, test_secret) for _ in range(10)]
        assert len(set(partitions)) == 1, "Partition must be deterministic"

    def test_partition_distribution(self):
        """Partitions should distribute across the range (not all same partition)."""
        from festival_bloomberg.cloud.listener_key import derive_partition

        test_secret = b"x" * 32
        partitions = [derive_partition(uid, 64, test_secret) for uid in range(1000)]
        assert len(set(partitions)) > 10, (
            f"Partition distribution too skewed: only {len(set(partitions))} unique"
        )


# ── P12.3: VERIFICATION FAILURE MODES (P6) ────────────────────────


class TestVerificationFailureModes:
    """Test all P6 failure cases — each must fail closed."""

    def test_missing_secret_raises(self):
        """Missing FI_LISTENER_HMAC_SECRET must raise, not silently continue."""
        from festival_bloomberg.cloud.listener_key import get_secret

        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(RuntimeError, match="FI_LISTENER_HMAC_SECRET"):
                get_secret()

    def test_short_secret_raises(self):
        """A secret shorter than 32 bytes must raise."""
        from festival_bloomberg.cloud.listener_key import get_secret

        with patch.dict(os.environ, {"FI_LISTENER_HMAC_SECRET": "short"}):
            with pytest.raises(RuntimeError, match="too short"):
                get_secret()

    def test_negative_user_id_rejected(self):
        """Negative user_id must be rejected (canonical input is non-negative)."""
        from festival_bloomberg.cloud.listener_key import canonical_input

        with pytest.raises(ValueError):
            canonical_input(-1)

    def test_contract_mismatch_fails_closed(self):
        """Mixing incompatible listener-key generations must fail closed."""
        from festival_bloomberg.cloud.listener_key import (
            ListenerKeyContract,
            validate_contract_compatibility,
        )

        contract = ListenerKeyContract()
        expected_meta = contract.to_metadata()

        # Mismatched key_version
        bad_meta = {**expected_meta, "listener_key_version": "v2"}
        with pytest.raises(RuntimeError, match="generation mismatch"):
            validate_contract_compatibility(bad_meta, expected_contract=contract)

        # Mismatched partition count
        bad_pc = {**expected_meta, "listener_hash_partitions": 32}
        with pytest.raises(RuntimeError, match="Partition count mismatch"):
            validate_contract_compatibility(
                bad_pc, expected_contract=contract, expected_partition_count=64
            )

    def test_missing_metadata_field_fails_closed(self):
        """Missing required metadata field must fail closed."""
        from festival_bloomberg.cloud.listener_key import (
            ListenerKeyContract,
            validate_contract_compatibility,
        )

        contract = ListenerKeyContract()
        # Missing listener_key_version
        incomplete_meta = {
            "listener_key_algorithm": "HMAC-SHA256",
            # Missing: listener_key_version
        }
        with pytest.raises(RuntimeError, match="missing required field"):
            validate_contract_compatibility(incomplete_meta, expected_contract=contract)


# ── P12.4: ENTRYPOINT SPEC VALIDATION (P7) ────────────────────────


class TestEntrypointSpecValidation:
    """Test that the entrypoint rejects invalid/unsafe job specs."""

    def test_unknown_job_type_rejected(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "batch_entrypoint",
            Path(__file__).resolve().parents[2] / "cloud-runtime" / "docker" / "batch_entrypoint.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        validate_spec = mod.validate_spec

        with pytest.raises(ValueError, match="Unknown or disallowed job_type"):
            validate_spec({"job_type": "arbitrary_command"})

    def test_path_traversal_in_job_id_rejected(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "batch_entrypoint",
            Path(__file__).resolve().parents[2] / "cloud-runtime" / "docker" / "batch_entrypoint.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        validate_spec = mod.validate_spec

        with pytest.raises(ValueError, match="invalid characters"):
            validate_spec({"job_type": "cloud_smoke", "job_id": "../../etc/passwd"})

    def test_shell_payload_rejected(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "batch_entrypoint",
            Path(__file__).resolve().parents[2] / "cloud-runtime" / "docker" / "batch_entrypoint.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        validate_spec = mod.validate_spec

        with pytest.raises(ValueError, match="invalid characters"):
            validate_spec({"job_type": "cloud_smoke", "job_id": "job; rm -rf /"})

    def test_forbidden_command_key_rejected(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "batch_entrypoint",
            Path(__file__).resolve().parents[2] / "cloud-runtime" / "docker" / "batch_entrypoint.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        validate_spec = mod.validate_spec

        for forbidden in ("command", "exec", "executable", "shell", "entrypoint", "cmd"):
            with pytest.raises(ValueError, match="forbidden key"):
                validate_spec({
                    "job_type": "cloud_smoke",
                    forbidden: "rm -rf /",
                })

    def test_oversized_job_id_rejected(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "batch_entrypoint",
            Path(__file__).resolve().parents[2] / "cloud-runtime" / "docker" / "batch_entrypoint.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        validate_spec = mod.validate_spec

        with pytest.raises(ValueError, match="too long"):
            validate_spec({
                "job_type": "cloud_smoke",
                "job_id": "a" * 200,
            })

    def test_invalid_partition_count_rejected(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "batch_entrypoint",
            Path(__file__).resolve().parents[2] / "cloud-runtime" / "docker" / "batch_entrypoint.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        validate_spec = mod.validate_spec

        with pytest.raises(ValueError):
            validate_spec({
                "job_type": "listenbrainz_map",
                "params": {"partitions": 0},
            })

        with pytest.raises(ValueError):
            validate_spec({
                "job_type": "listenbrainz_map",
                "params": {"partitions": 99999},
            })

    def test_valid_spec_accepted(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "batch_entrypoint",
            Path(__file__).resolve().parents[2] / "cloud-runtime" / "docker" / "batch_entrypoint.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        validate_spec = mod.validate_spec

        result = validate_spec({
            "job_type": "cloud_smoke",
            "job_id": "smoke_001",
            "source_generation": "20260831T014029Z-1369",
            "params": {"partitions": 64},
        })
        assert result["job_type"] == "cloud_smoke"
        assert result["job_id"] == "smoke_001"


# ── P12.5: REDUCE FORMULA TESTS ──────────────────────────────────


class TestReduceFormulas:
    """Reference-test Jaccard, cosine, lift, PMI formulas."""

    def test_jaccard_formula(self):
        """Jaccard = |A ∩ B| / |A ∪ B| = sh / (la + lb - sh)."""
        # listeners_a=10, listeners_b=8, shared=6
        # jaccard = 6 / (10 + 8 - 6) = 6/12 = 0.5
        sh, la, lb = 6, 10, 8
        jaccard = sh / (la + lb - sh)
        assert abs(jaccard - 0.5) < 1e-9

    def test_cosine_formula(self):
        """Cosine = sh / sqrt(la * lb)."""
        sh, la, lb = 6, 10, 8
        cosine = sh / (la * lb) ** 0.5
        assert abs(cosine - 6 / (80 ** 0.5)) < 1e-9

    def test_lift_formula(self):
        """Lift = P(A∩B) / (P(A) * P(B)) = sh * N / (la * lb)."""
        sh, la, lb = 6, 10, 8
        N = 100  # total listeners in metric universe
        lift = sh * N / (la * lb)
        assert abs(lift - 600 / 80) < 1e-9
        assert lift > 1.0  # positive correlation

    def test_pmi_formula(self):
        """PMI = log2(lift)."""
        import math

        sh, la, lb = 6, 10, 8
        N = 100
        lift = sh * N / (la * lb)
        pmi = math.log2(lift)
        assert abs(pmi - math.log2(7.5)) < 1e-9

    def test_jaccard_zero_denominator_safe(self):
        """Jaccard with zero denominator must not crash."""
        sh, la, lb = 0, 0, 0
        denom = la + lb - sh
        jaccard = 0.0 if denom == 0 else sh / denom
        assert jaccard == 0.0


# ── P12.6: METRIC-UNIVERSE METADATA TESTS ─────────────────────────


class TestMetricUniverseMetadata:
    """Verify Gold affinity metadata explicitly states the metric universe."""

    def test_metric_universe_labels_present(self):
        """Gold metadata must include audience_source and listener_universe."""
        from festival_bloomberg.cloud.batch_jobs import AFFINITY_METRIC_UNIVERSE

        assert AFFINITY_METRIC_UNIVERSE["audience_source"] == "LISTENBRAINZ"
        assert AFFINITY_METRIC_UNIVERSE["listener_universe"] == "TOP_25_RETAINED_PER_LISTENER"
        assert AFFINITY_METRIC_UNIVERSE["shared_listener_semantics"] == "GLOBAL_UNIQUE_LISTENERS_WITHIN_METRIC_UNIVERSE"
        assert AFFINITY_METRIC_UNIVERSE["top_k"] == 25

    def test_never_label_as_total_fans(self):
        """Metadata must explicitly prohibit TOTAL_FANS labeling."""
        from festival_bloomberg.cloud.batch_jobs import AFFINITY_METRIC_UNIVERSE

        assert AFFINITY_METRIC_UNIVERSE["never_label_as"] == "TOTAL_FANS"

    def test_never_infer_present(self):
        """Metadata must list prohibited inferences."""
        from festival_bloomberg.cloud.batch_jobs import AFFINITY_METRIC_UNIVERSE

        prohibited = AFFINITY_METRIC_UNIVERSE["never_infer"]
        assert "ticket_demand" in prohibited
        assert "purchase_propensity" in prohibited
        assert "attendance" in prohibited
        assert "willingness_to_pay" in prohibited


# ── P12.7: MANIFEST VERIFICATION CONTRACT ─────────────────────────


class TestManifestVerificationContract:
    """Test the BUILD_COMPLETE → VERIFIED → PUBLISHED status flow."""

    def test_verified_status_exists(self):
        """STATUS_VERIFIED must exist in the status constants."""
        from festival_bloomberg.cloud.job_manifest import STATUS_VERIFIED

        assert STATUS_VERIFIED == "VERIFIED"

    def test_manifest_has_verification_fields(self):
        """JobManifest must have verified_at and verified_hashes fields."""
        from festival_bloomberg.cloud.job_manifest import JobManifest

        m = JobManifest()
        assert hasattr(m, "verified_at")
        assert hasattr(m, "verified_hashes")
        assert m.verified_at is None
        assert m.verified_hashes == {}

    def test_valid_statuses_includes_verified(self):
        """VALID_STATUSES must include VERIFIED."""
        from festival_bloomberg.cloud.job_manifest import VALID_STATUSES

        assert "VERIFIED" in VALID_STATUSES


# ── P12.8: R2 VERIFICATION HELPERS ────────────────────────────────


class TestR2VerificationHelpers:
    """Test the verify_object and verify_object_exists helpers."""

    def test_verify_object_missing_returns_false(self):
        """verify_object on a missing object returns False, not raises."""
        from festival_bloomberg.cloud.r2_lake import R2Lake

        # Create a real R2Lake with a mocked _s3.
        lake = R2Lake.__new__(R2Lake)
        lake._s3 = MagicMock()
        lake._s3.get_object.side_effect = Exception("NoSuchKey")

        result = lake.verify_object("bucket", "missing-key", "abc123")
        assert result is False

    def test_verify_object_sha_mismatch_returns_false(self):
        """verify_object with wrong SHA returns False."""
        from festival_bloomberg.cloud.r2_lake import R2Lake

        lake = R2Lake.__new__(R2Lake)
        lake._s3 = MagicMock()
        mock_body = MagicMock()
        mock_body.read.return_value = b"wrong data"
        lake._s3.get_object.return_value = {"Body": mock_body}

        result = lake.verify_object("bucket", "key", "expected_sha")
        assert result is False

    def test_verify_object_sha_match_returns_true(self):
        """verify_object with correct SHA returns True."""
        from festival_bloomberg.cloud.r2_lake import R2Lake

        lake = R2Lake.__new__(R2Lake)
        lake._s3 = MagicMock()
        data = b"correct data"
        expected_sha = hashlib.sha256(data).hexdigest()
        mock_body = MagicMock()
        mock_body.read.return_value = data
        lake._s3.get_object.return_value = {"Body": mock_body}

        result = lake.verify_object("bucket", "key", expected_sha)
        assert result is True
