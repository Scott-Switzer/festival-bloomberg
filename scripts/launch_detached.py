"""Launch a long-running collection pass in a fully detached session.

Usage:
    PYTHONPATH=python .venv/bin/python scripts/launch_detached.py \
        -- python scripts/backfill_wikimedia_1000.py --warehouse /tmp/artist_security_1000.duckdb

The child runs with ``start_new_session=True`` (new process group + session)
so killing the invoking shell does not take it down. stdout/stderr go to
``/tmp/launch_detached_<pid>.log``.
"""

from __future__ import annotations

import argparse
import subprocess
import sys


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", default=None, help="log path (default /tmp/launch_detached_<pid>.log)")
    parser.add_argument("argv", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not args.argv or args.argv[0] != "--":
        print("usage: launch_detached.py -- <cmd> [args...]")
        sys.exit(2)
    cmd = args.argv[1:]
    log = args.log or f"/tmp/launch_detached_{__import__('os').getpid()}.log"
    with open(log, "wb") as out:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    print(f"launched pid={proc.pid} log={log}")


if __name__ == "__main__":
    main()
