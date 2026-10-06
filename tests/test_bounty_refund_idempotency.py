# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

from __future__ import annotations

from pathlib import Path

from database import Database


def test_expire_bounties_refund_once(tmp_path: Path):
    db_path = tmp_path / "b.db"
    db = Database(str(db_path))
    db.initialize()

    owner = 1
    target = 2
    db.ensure_user(owner, "o")
    db.ensure_user(target, "t")

    # Spend 5 coins by placing a bounty (mimic shop behavior).
    db.add_coins(owner, 5)
    db.add_coins(owner, -5)
    bounty_id = db.create_bounty(owner, target, cost_coins=5)

    # Force it to be expired.
    import sqlite3
    with sqlite3.connect(db.path) as conn:
        conn.execute("UPDATE bounties SET expires_at='2000-01-01T00:00:00' WHERE id=?", (bounty_id,))
        conn.commit()

    before = db.get_user(owner)["coins"]
    refunded = db.expire_bounties_and_refund()
    after = db.get_user(owner)["coins"]
    assert after == before + 5
    assert any(b["id"] == bounty_id for b in refunded)

    # Running again should not refund twice.
    refunded2 = db.expire_bounties_and_refund()
    after2 = db.get_user(owner)["coins"]
    assert after2 == after
    assert all(b["id"] != bounty_id for b in refunded2)
