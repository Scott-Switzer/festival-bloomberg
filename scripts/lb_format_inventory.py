"""P6 — ListenBrainz full-dump format inventory v3.

The 205 GB object is a tar of Parquet shards. Random access via tarfile is
fragile on truncated slices, so this version walks tar headers manually with
zero-copy slicing from the fetched prefix and reads the first real shard with
pyarrow from a BytesIO of exactly that member's bytes.

Usage:
    PYTHONPATH=python .venv/bin/python scripts/lb_format_inventory.py
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from festival_bloomberg.lake.r2 import r2_client  # noqa: E402

BUCKET = "festival-intelligence-raw"
KEY = "bulk/listenbrainz/dump=2593-20260712-000004/listenbrainz-spark-dump-2593-20260712-000004-full.tar"
FETCH = 1300 * (1 << 20)  # 1.3 GiB prefix — enough to fully contain the first ~110 MB data shards after the 3 small metadata members
PREFIX = "listenbrainz-spark-dump-2593-20260712-000004-full"


def fetch_prefix(s3, n_bytes: int) -> bytes:
    buf = io.BytesIO()
    start = 0
    end = n_bytes - 1
    while start <= end:
        part_end = min(start + 64 * (1 << 20) - 1, end)
        resp = s3.get_object(Bucket=BUCKET, Key=KEY, Range=f"bytes={start}-{part_end}")
        buf.write(resp["Body"].read())
        start = part_end + 1
    return buf.getvalue()


def walk_tar(raw: bytes):
    """Yield (name, member_bytes_or_None, next_offset) for members fully inside raw."""
    import struct

    off = 0
    n = len(raw)
    while off + 512 <= n:
        header = raw[off : off + 512]
        if header == b"\0" * 512:
            break
        try:
            name = header[0:100].split(b"\0")[0].decode("utf-8", "replace")
            size_field = header[124:136].split(b"\0")[0]
            size = int(size_field or b"0", 8)
            typeflag = header[156:157]
        except Exception:
            break
        data_start = off + 512
        data_end = data_start + size
        padded_end = data_start + ((size + 511) // 512) * 512
        member_bytes = raw[data_start:data_end] if data_end <= n else None
        yield name, member_bytes, typeflag, data_end
        off = padded_end


def main() -> None:
    s3 = r2_client()
    raw = fetch_prefix(s3, FETCH)
    print(f"fetched {len(raw) / (1 << 20):.1f} MiB from R2\n")

    small_members: dict[str, bytes] = {}
    shards: list[tuple[str, int]] = []
    for name, data, _typeflag, _end in walk_tar(raw):
        base = name.split("/")[-1]
        if data is None:
            shards.append((name, -1))  # partially present or beyond prefix
            continue
        if base in ("SCHEMA_SEQUENCE", "TIMESTAMP", "COPYING") or (
            base.endswith(".parquet") and base[: -len(".parquet")].isdigit()
        ):
            if base.endswith(".parquet"):
                shards.append((name, len(data)))
            else:
                small_members[base] = data
    # Also note the first member we could NOT fully read (tells us shard sizes continue)
    print("=== metadata ===")
    for k in ("SCHEMA_SEQUENCE", "TIMESTAMP", "COPYING"):
        if k in small_members:
            text = small_members[k].decode("utf-8", "replace")
            print(f"{k}: {text.strip()[:500]}")
        else:
            print(f"{k}: NOT IN PREFIX")

    print("\n=== shards seen in prefix ===")
    for name, size in shards[:20]:
        print(f"  {name}: {size:,} bytes" if size >= 0 else f"  {name}: (beyond prefix)")

    # Read the first sizeable shard (including partially-fetched ones: parquet
    # footers live at the END of the file, so we cannot parse truncated shards.
    # Instead read the LAST FULLY-PRESENT sizeable shard.)
    import pyarrow.parquet as pq

    target = next(((n, s) for n, s in reversed(shards) if s > 50 * (1 << 20)), None)
    if target is None:
        print("\nno sizeable shard fully inside prefix; increase FETCH")
        return
    name, size = target
    # re-walk to get exact member bytes
    for n2, data, _t, _e in walk_tar(raw):
        if n2 == name and data is not None:
            print(f"\n=== shard {name}: {len(data) / (1 << 20):.1f} MiB ===")
            pf = pq.ParquetFile(io.BytesIO(data))
            print("schema:")
            print(pf.schema_arrow)
            print(f"\nnum_row_groups: {pf.num_row_groups}, rows: {pf.metadata.num_rows:,}")
            table = pf.read_row_group(0)
            for i in range(min(3, table.num_rows)):
                row = {col: table.column(col)[i].as_py() for col in table.column_names}
                print(f"\nrow {i}: {json.dumps(row, default=str)[:900]}")
            break


if __name__ == "__main__":
    main()
