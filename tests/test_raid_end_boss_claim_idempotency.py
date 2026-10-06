# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

import os
import tempfile
import time
import gc

from database import Database


def test_end_boss_is_claim_idempotent():
    # Use a real sqlite file so WAL/PRAGMAs behave normally.
    # On Windows, sqlite can hold file handles briefly; clean up with retries.
    td = tempfile.mkdtemp()
    db_path = os.path.join(td, "test.db")
    try:
        db = Database(db_path)
        db.initialize()

        boss = db.spawn_boss(hp=100, duration_days=1, season="test")
        assert boss["ended_at"] is None

        first = db.end_boss(int(boss["id"]))
        assert first is not None
        assert first["ended_at"] is not None

        # Second call should not "win the claim".
        second = db.end_boss(int(boss["id"]))
        assert second is None
    finally:
        try:
            del db  # type: ignore[name-defined]
        except Exception:
            pass
        gc.collect()
        # Retry a few times to tolerate transient file locks (WAL).
        for _ in range(10):
            try:
                if os.path.exists(db_path):
                    os.remove(db_path)
                if os.path.exists(db_path + "-wal"):
                    os.remove(db_path + "-wal")
                if os.path.exists(db_path + "-shm"):
                    os.remove(db_path + "-shm")
                break
            except PermissionError:
                time.sleep(0.05)
        for _ in range(10):
            try:
                os.rmdir(td)
                break
            except OSError:
                time.sleep(0.05)
