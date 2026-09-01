"""Listener key contract — deterministic HMAC-SHA256 pseudonymization.

Production ListenBrainz schema:
    user_id: int64 not null  (pseudonymous numeric ID from the dump)

Canonical input:
    ASCII decimal representation of the int64 user_id, no whitespace, no sign
    ambiguity.  For user_id = 42, canonical_input = b"42".

Derivation:
    digest = HMAC-SHA256(FI_LISTENER_HMAC_SECRET, canonical_input)
    listener_key = hex(digest)                  # 64 hex chars
    partition = int.from_bytes(digest[0:8], "big") % partition_count

Metadata (recorded in manifests/checkpoints, never the secret itself):
    listener_key_algorithm = HMAC-SHA256
    listener_key_version = v1
    listener_key_input = listenbrainz_user_id_decimal_v1
    listener_key_secret_version = 2026-09-v1   # identifier only
    partition_algorithm = hmac_sha256_prefix_u64_be_mod_v1

Requirements:
    - secret only in Cloudflare secret binding (FI_LISTENER_HMAC_SECRET)
    - secret never in Git, R2, manifests, checkpoints, stdout, stderr,
      status API, or exception payloads
    - same source user_id + same secret/version → same listener_key + partition
    - partition result does not depend on Python process hash randomization
    - key rotation requires a new generation; v1 and v2 partials must never
      be silently combined
"""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass

# ── Contract metadata (public, not secret) ─────────────────────────

LISTENER_KEY_ALGORITHM = "HMAC-SHA256"
LISTENER_KEY_VERSION = "v1"
LISTENER_KEY_INPUT = "listenbrainz_user_id_decimal_v1"
LISTENER_KEY_SECRET_VERSION = "2026-09-v1"
PARTITION_ALGORITHM = "hmac_sha256_prefix_u64_be_mod_v1"

# Minimum secret length (256 bits = 32 bytes for HMAC-SHA256 key).
MIN_SECRET_BYTES = 32


@dataclass(frozen=True)
class ListenerKeyContract:
    """Immutable description of the pseudonymization contract for this run."""

    algorithm: str = LISTENER_KEY_ALGORITHM
    key_version: str = LISTENER_KEY_VERSION
    key_input: str = LISTENER_KEY_INPUT
    secret_version: str = LISTENER_KEY_SECRET_VERSION
    partition_algorithm: str = PARTITION_ALGORITHM

    def to_metadata(self) -> dict:
        """Return a metadata dict suitable for manifests/checkpoints.

        Never includes the secret itself — only version identifiers.
        """
        return {
            "listener_key_algorithm": self.algorithm,
            "listener_key_version": self.key_version,
            "listener_key_input": self.key_input,
            "listener_key_secret_version": self.secret_version,
            "partition_algorithm": self.partition_algorithm,
        }


def get_secret() -> bytes:
    """Read the HMAC secret from the Cloudflare secret binding.

    Raises RuntimeError if the secret is missing or too short.
    Never logs or returns the secret in error messages.
    """
    raw = os.environ.get("FI_LISTENER_HMAC_SECRET", "")
    if not raw:
        raise RuntimeError(
            "FI_LISTENER_HMAC_SECRET is not set. "
            "This secret is required for listener pseudonymization."
        )
    secret = raw.encode("utf-8")
    if len(secret) < MIN_SECRET_BYTES:
        raise RuntimeError(
            f"FI_LISTENER_HMAC_SECRET is too short: must be at least "
            f"{MIN_SECRET_BYTES} bytes."
        )
    return secret


def canonical_input(user_id: int) -> bytes:
    """Canonical input for HMAC: ASCII decimal of the int64 user_id.

    >>> canonical_input(42)
    b'42'
    >>> canonical_input(0)
    b'0'
    >>> canonical_input(-1)
    Traceback (most recent call last):
        ...
    ValueError: user_id must be non-negative
    """
    if not isinstance(user_id, int) or isinstance(user_id, bool):
        raise TypeError(f"user_id must be int, got {type(user_id).__name__}")
    if user_id < 0:
        raise ValueError("user_id must be non-negative")
    return str(user_id).encode("ascii")


def derive_listener_key(user_id: int, secret: bytes | None = None) -> str:
    """Derive the deterministic listener_key (hex digest) from a user_id.

    The secret is read from the environment if not provided.
    Returns a 64-character hex string.
    """
    if secret is None:
        secret = get_secret()
    msg = canonical_input(user_id)
    digest = hmac.new(secret, msg, hashlib.sha256).digest()
    return digest.hex()


def derive_partition(
    user_id: int,
    partition_count: int,
    secret: bytes | None = None,
) -> int:
    """Derive the deterministic partition index for a listener.

    Uses the first 8 bytes of the HMAC digest as a big-endian uint64,
    then modulo partition_count.

    This is independent of Python's built-in hash() and DuckDB's hash()
    function — it is a cryptographic digest that is stable across
    processes, restarts, and DuckDB versions.
    """
    if partition_count <= 0:
        raise ValueError("partition_count must be positive")
    if secret is None:
        secret = get_secret()
    msg = canonical_input(user_id)
    digest = hmac.new(secret, msg, hashlib.sha256).digest()
    # Big-endian uint64 from first 8 bytes — stable across platforms.
    return int.from_bytes(digest[:8], "big") % partition_count


def derive_listener_key_and_partition(
    user_id: int,
    partition_count: int,
    secret: bytes | None = None,
) -> tuple[str, int]:
    """Derive both listener_key and partition in one HMAC call.

    Returns (listener_key_hex, partition_index).
    """
    if partition_count <= 0:
        raise ValueError("partition_count must be positive")
    if secret is None:
        secret = get_secret()
    msg = canonical_input(user_id)
    digest = hmac.new(secret, msg, hashlib.sha256).digest()
    listener_key = digest.hex()
    partition = int.from_bytes(digest[:8], "big") % partition_count
    return listener_key, partition


# ── Batch/vectorized helpers (P2: performance-safe pseudonymization) ──

def derive_listener_keys_batch(
    user_ids: list[int],
    secret: bytes | None = None,
) -> list[str]:
    """Derive listener_keys for a batch of user_ids.

    For the full corpus, this should be called per distinct user_id set
    (not per listen). The caller is responsible for deduplication before
    calling this function — typically:

        distinct_ids = set(all_user_ids_in_batch)
        key_map = dict(zip(distinct_ids, derive_listener_keys_batch(list(distinct_ids))))

    Then join/map the key back to the full listen set.
    """
    if secret is None:
        secret = get_secret()
    return [derive_listener_key(uid, secret) for uid in user_ids]


def derive_partitions_batch(
    user_ids: list[int],
    partition_count: int,
    secret: bytes | None = None,
) -> list[int]:
    """Derive partitions for a batch of user_ids."""
    if partition_count <= 0:
        raise ValueError("partition_count must be positive")
    if secret is None:
        secret = get_secret()
    return [derive_partition(uid, partition_count, secret) for uid in user_ids]


def validate_contract_compatibility(
    manifest_metadata: dict,
    expected_contract: ListenerKeyContract | None = None,
    expected_partition_count: int | None = None,
) -> None:
    """Fail closed if input partials mix incompatible listener-key generations.

    Raises RuntimeError if:
    - listener_key_version differs
    - listener_key_secret_version differs
    - partition_algorithm differs
    - partition_count differs (when expected_partition_count is provided)
    """
    if expected_contract is None:
        expected_contract = ListenerKeyContract()

    expected = expected_contract.to_metadata()

    for field in (
        "listener_key_algorithm",
        "listener_key_version",
        "listener_key_input",
        "listener_key_secret_version",
        "partition_algorithm",
    ):
        actual = manifest_metadata.get(field)
        if actual is None:
            raise RuntimeError(
                f"Reducer input missing required field '{field}' — "
                "cannot verify listener-key generation compatibility."
            )
        if actual != expected[field]:
            raise RuntimeError(
                f"Listener-key generation mismatch on '{field}': "
                f"expected '{expected[field]}', got '{actual}'. "
                "Never silently combine incompatible generations."
            )

    if expected_partition_count is not None:
        actual_pc = manifest_metadata.get("listener_hash_partitions")
        if actual_pc is None:
            raise RuntimeError(
                "Reducer input missing 'listener_hash_partitions' — "
                "cannot verify partition count compatibility."
            )
        if int(actual_pc) != int(expected_partition_count):
            raise RuntimeError(
                f"Partition count mismatch: expected {expected_partition_count}, "
                f"got {actual_pc}. Never combine partials with different "
                "partition geometries."
            )
