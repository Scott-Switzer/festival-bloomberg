"""Canonical local configuration loading for development.

Precedence (documented):

    PROCESS ENV  >  LOCAL `.env`  >  NOT_CONFIGURED

- Process environment always wins: ``load_local_env`` never overwrites an
  already-set variable.
- The canonical repo ``.env`` is loaded for local development only. It is
  never read in CI unless explicitly provided (CI has no ``.env``), and
  ``FESTIVAL_BLOOMBERG_SKIP_ENV_FILE=1`` disables loading entirely.
- No value is ever logged or printed; only a count of variables loaded.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

#: Absolute path of the canonical repository `.env`.
REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env"

#: Skip env-file loading (tests, hermetic CI, explicit opt-out).
SKIP_ENV_VAR = "FESTIVAL_BLOOMBERG_SKIP_ENV_FILE"

_ENV_LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def load_local_env(path: str | os.PathLike | None = None) -> int:
    """Load ``NAME=VALUE`` lines from ``path`` (default repo ``.env``).

    Process environment wins: a variable already present in ``os.environ`` is
    never overwritten. Returns the number of variables loaded. Never prints or
    logs values.
    """
    if os.environ.get(SKIP_ENV_VAR) == "1":
        return 0

    target = Path(path) if path is not None else ENV_FILE
    if not target.is_file():
        return 0

    loaded = 0
    with open(target, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            match = _ENV_LINE.match(line)
            if not match:
                continue
            name, value = match.group(1), match.group(2)
            if not name or name in os.environ:
                continue
            os.environ[name] = value
            loaded += 1
    return loaded
