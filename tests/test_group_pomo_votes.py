# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

from __future__ import annotations

from pathlib import Path

from database import Database


def test_group_pomo_votes_count_only_active_members(tmp_path: Path):
    db_path = tmp_path / "t.db"
    db = Database(str(db_path))
    db.initialize()

    lobby = db.create_group_lobby(host_id=1, subject="x")
    lobby_id = lobby["id"]

    # Join 2 more members (3 total)
    db.join_group_lobby(db.get_group_lobby_by_id(lobby_id)["code"], 2)
    db.join_group_lobby(db.get_group_lobby_by_id(lobby_id)["code"], 3)

    db.vote_start_group_lobby(lobby_id, 1)
    db.vote_start_group_lobby(lobby_id, 2)
    assert db.get_group_lobby_vote_count(lobby_id) == 2

    # Member 2 leaves (becomes inactive); vote count should drop.
    db.leave_group_lobby(2)
    assert db.get_group_lobby_vote_count(lobby_id) == 1


def test_begin_group_lobby_clears_votes(tmp_path: Path):
    db_path = tmp_path / "t2.db"
    db = Database(str(db_path))
    db.initialize()

    lobby = db.create_group_lobby(host_id=10, subject="x")
    lobby_id = lobby["id"]
    code = db.get_group_lobby_by_id(lobby_id)["code"]
    db.join_group_lobby(code, 11)
    db.join_group_lobby(code, 12)

    db.vote_start_group_lobby(lobby_id, 10)
    db.vote_start_group_lobby(lobby_id, 11)
    assert db.get_group_lobby_vote_count(lobby_id) == 2

    ok = db.begin_group_lobby(lobby_id)
    assert ok is True
    assert db.get_group_lobby_vote_count(lobby_id) == 0

    # Idempotency: second begin should fail (already active) and not change votes.
    ok2 = db.begin_group_lobby(lobby_id)
    assert ok2 is False
    assert db.get_group_lobby_vote_count(lobby_id) == 0
