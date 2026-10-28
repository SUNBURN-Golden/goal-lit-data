#!/usr/bin/env python3
"""pm2 용 진입점: python3 ops/glkg_daemon.py <단계> --interval N --offset N  (= python -m glkg daemon ...)"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from glkg.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["daemon"] + sys.argv[1:]))
