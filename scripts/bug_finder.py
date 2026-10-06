# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

"""StudyBot bug-finder lanes (expand over time).

Lanes should stay bounded enough for local developer runs and CI follow-ups.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys


def main() -> int:
    ap = argparse.ArgumentParser(description="StudyBot bug finder runner")
    ap.add_argument("--lane", default="outbox", help="Which lane to run (default: outbox)")
    args = ap.parse_args()

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    if args.lane == "outbox":
        cmd = [sys.executable, "-m", "pytest", "-q", os.path.join(root, "tests", "test_outbox.py")]
        return subprocess.call(cmd, cwd=root)

    print(f"Unknown lane: {args.lane}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
