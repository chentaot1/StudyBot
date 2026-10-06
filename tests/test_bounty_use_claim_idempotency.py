# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

from __future__ import annotations

import sqlite3
from pathlib import Path

from database import Database


def test_use_bounty_is_idempotent_claim(tmp_path: Path) -> None:
    db_path = tmp_path / "bounty_claim.db"
    db = Database(str(db_path))
    db.initialize()

    owner = 1
    target = 2
    db.ensure_user(owner, "o")
    db.ensure_user(target, "t")

    bounty_id = db.create_bounty(owner, target, cost_coins=5)

    # Ensure "used" is 0 to start (matches schema defaults).
    with sqlite3.connect(db.path) as conn:
        row = conn.execute("SELECT used FROM bounties WHERE id=?", (bounty_id,)).fetchone()
        assert row is not None
        assert int(row[0]) == 0

    # First claim wins.
    assert db.use_bounty(bounty_id) is True
    # Second claim must lose.
    assert db.use_bounty(bounty_id) is False

    with sqlite3.connect(db.path) as conn:
        row2 = conn.execute("SELECT used FROM bounties WHERE id=?", (bounty_id,)).fetchone()
        assert row2 is not None
        assert int(row2[0]) == 1
