# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

from pathlib import Path

from database import Database


def test_start_session_creates_first_segment(tmp_path: Path):
    db = Database(str(tmp_path / "seg1.db"))
    db.initialize()
    uid = 123
    sid = db.start_session(uid, subject="Math", tags="algebra")
    assert sid > 0

    import sqlite3
    with sqlite3.connect(db.path) as conn:
        conn.row_factory = sqlite3.Row
        seg = conn.execute(
            "SELECT * FROM study_session_segments WHERE session_id=? ORDER BY id ASC LIMIT 1",
            (sid,),
        ).fetchone()
        assert seg is not None
        assert seg["user_id"] == uid
        assert (seg["subject"] or "") == "Math"
        assert (seg["tags"] or "") == "algebra"
        assert seg["ended_at"] is None


def test_switch_creates_new_segment_and_closes_old(tmp_path: Path):
    db = Database(str(tmp_path / "seg2.db"))
    db.initialize()
    uid = 123
    sid = db.start_session(uid, subject="A", tags="x")
    assert db.switch_active_session_segment(uid, subject="B", tags="y") is True

    import sqlite3
    with sqlite3.connect(db.path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT subject, tags, ended_at FROM study_session_segments WHERE session_id=? ORDER BY id ASC",
            (sid,),
        ).fetchall()
        assert len(rows) == 2
        assert rows[0]["subject"] == "A"
        assert rows[0]["tags"] == "x"
        assert rows[0]["ended_at"] is not None
        assert rows[1]["subject"] == "B"
        assert rows[1]["tags"] == "y"
        assert rows[1]["ended_at"] is None
