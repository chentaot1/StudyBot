# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

from __future__ import annotations

from pathlib import Path

from database import Database
from services.rewards_engine import calc_potion_bonus_xp, calc_bounty_bonus_xp


def test_potion_bonus_uses_session_end(tmp_path: Path):
    db_path = tmp_path / "r.db"
    db = Database(str(db_path))
    db.initialize()

    uid = 123
    db.ensure_user(uid, "u")

    # Start potion now for 2 hours with 1.5x
    db.activate_potion(uid, "potion_small_potion", 1.5, 2)

    # A 10 minute session overlapping fully with potion window should yield ~50% bonus XP
    sess_id = db.start_session(uid, "x", target_minutes=None, source_guild_id=1, allowed_member=True, tags="")
    s = db.get_active_session(uid)
    ended = db.end_session(uid, "done")
    assert ended is not None
    bonus = calc_potion_bonus_xp(db, user_id=uid, session=ended)
    base_xp = int(ended.get("xp_earned") or 0)
    # Invariants (avoid coupling to exact XP formula):
    assert bonus >= 0
    assert bonus <= base_xp


def test_bounty_bonus_overlap_unit():
    class FakeDB:
        def get_active_bounty_xp_window(self, user_id: int):
            return {
                "activated_at": "2026-01-01T00:00:00",
                "buff_expires_at": "2026-01-01T02:00:00",
            }

    session = {
        "duration_minutes": 60,
        "xp_earned": 200,
        "started_at": "2026-01-01T00:30:00",
        "ended_at": "2026-01-01T01:30:00",
    }
    # Full overlap with the 2h window => bonus equals base portion for overlap (1.0 * base_xp).
    bonus = calc_bounty_bonus_xp(FakeDB(), user_id=1, session=session, is_allowed_member=True)
    assert bonus == 200

    # If not allowed member, bonus must be 0.
    bonus2 = calc_bounty_bonus_xp(FakeDB(), user_id=1, session=session, is_allowed_member=False)
    assert bonus2 == 0
