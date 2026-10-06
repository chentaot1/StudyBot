# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

"""Optional deeper smoke lane (enable with ``SMOKE_WITH_DB_SIM=1``).

Runs lightweight SQLite/outbox invariants without Discord network access.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from database import Database  # noqa: E402


def main() -> None:
    # Keep this subprocess-only and prefer tearing down the temp dir best-effort on Windows.
    d = tempfile.mkdtemp(prefix="studybot_db_sim_")
    path = os.path.join(d, "sim.db")
    try:
        db = Database(path)
        db.initialize()
        mid = db.enqueue_outbox(
            target_type="user",
            target_id=1,
            kind="smoke_sim",
            dedupe_key="smoke_sim:1:a",
            content="hello",
        )
        batch = db.claim_outbox_batch(limit=10)
        ids = [int(m["id"]) for m in batch]
        assert ids == [mid], ids
        db.mark_outbox_sent(mid)
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    main()
