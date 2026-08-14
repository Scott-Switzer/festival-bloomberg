"""Entrypoint so ``python -m festival_bloomberg.cli`` works."""

from __future__ import annotations

import sys

from .main import main

if __name__ == "__main__":
    sys.exit(main())
