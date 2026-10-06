# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

from __future__ import annotations

import sqlite3
from pathlib import Path

from database import Database


def test_cleanup_old_data_does_not_delete_unrefunded_expired_bounties(tmp_path: Path) -> None:
    db_path = tmp_path / "cleanup_bounties.db"
    db = Database(str(db_path))
    db.initialize()

    owner = 1
    target = 2
    db.ensure_user(owner, "o")
    db.ensure_user(target, "t")
    bounty_id = db.create_bounty(owner, target, cost_coins=5)

    # Make it look very old and expired but NOT refunded.
    with sqlite3.connect(db.path) as conn:
        conn.execute(
            "UPDATE bounties SET created_at='2000-01-01T00:00:00', expires_at='2000-01-01T00:00:00', refunded=0 WHERE id=?",
            (bounty_id,),
        )
        conn.commit()

    db.cleanup_old_data(days=30)

    with sqlite3.connect(db.path) as conn:
        row = conn.execute("SELECT id FROM bounties WHERE id=?", (bounty_id,)).fetchone()
        assert row is not None


def test_cleanup_old_data_deletes_refunded_expired_bounties(tmp_path: Path) -> None:
    db_path = tmp_path / "cleanup_bounties_refunded.db"
    db = Database(str(db_path))
    db.initialize()

    owner = 1
    target = 2
    db.ensure_user(owner, "o")
    db.ensure_user(target, "t")
    bounty_id = db.create_bounty(owner, target, cost_coins=5)

    # Mark as old, expired, and refunded.
    with sqlite3.connect(db.path) as conn:
        conn.execute(
            "UPDATE bounties SET created_at='2000-01-01T00:00:00', expires_at='2000-01-01T00:00:00', refunded=1 WHERE id=?",
            (bounty_id,),
        )
        conn.commit()

    db.cleanup_old_data(days=30)

    with sqlite3.connect(db.path) as conn:
        row = conn.execute("SELECT id FROM bounties WHERE id=?", (bounty_id,)).fetchone()
        assert row is None
