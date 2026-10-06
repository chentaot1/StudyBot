# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
import database as database_module

from database import Database, _utcnow_naive


class _ForceReturningErrorConn:
    """Wrap a SQLite connection so ``UPDATE ... RETURNING`` fails (tests fallback path)."""

    def __init__(self, inner: sqlite3.Connection):
        object.__setattr__(self, "_inner", inner)

    def execute(self, sql, parameters=()):
        s = sql if isinstance(sql, str) else ""
        if "RETURNING" in s.upper():
            raise sqlite3.OperationalError("forced fallback for test")
        return self._inner.execute(sql, parameters)

    def __enter__(self):
        self._inner.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        return self._inner.__exit__(exc_type, exc, tb)

    def __setattr__(self, name, value):
        if name == "_inner":
            object.__setattr__(self, name, value)
        else:
            setattr(self._inner, name, value)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def test_outbox_dedupe_key_idempotent(tmp_path: Path):
    db = Database(str(tmp_path / "o.db"))
    db.initialize()

    a = db.enqueue_outbox(
        target_type="user",
        target_id=123,
        kind="k",
        dedupe_key="x:1",
        content="hi",
        embed={"title": "t", "description": "d", "color": 1},
    )
    b = db.enqueue_outbox(
        target_type="user",
        target_id=123,
        kind="k",
        dedupe_key="x:1",
        content="hi2",
        embed={"title": "t2"},
    )
    assert a == b


def test_outbox_claim_and_mark_sent(tmp_path: Path):
    db = Database(str(tmp_path / "o2.db"))
    db.initialize()

    mid = db.enqueue_outbox(target_type="user", target_id=1, content="x", kind="k")
    batch = db.claim_outbox_batch(limit=10)
    assert [m["id"] for m in batch] == [mid]

    # Claimed messages should not be re-claimed.
    batch2 = db.claim_outbox_batch(limit=10)
    assert batch2 == []

    db.mark_outbox_sent(mid)
    batch3 = db.claim_outbox_batch(limit=10)
    assert batch3 == []


def test_outbox_retry_sets_pending_and_increments_attempts(tmp_path: Path):
    db = Database(str(tmp_path / "o3.db"))
    db.initialize()
    mid = db.enqueue_outbox(target_type="user", target_id=1, content="x", kind="k")
    _ = db.claim_outbox_batch(limit=10)

    db.retry_outbox_later(mid, error="boom", delay_seconds=5)
    batch2 = db.claim_outbox_batch(limit=10)
    assert batch2 == []

    # Ensure attempts incremented and not_before is set in the future.
    import sqlite3
    with sqlite3.connect(db.path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM outbox_messages WHERE id=?", (mid,)).fetchone()
        assert int(row["attempts"]) == 1
        assert row["not_before"] is not None


def test_outbox_stores_settings_key(tmp_path: Path):
    db = Database(str(tmp_path / "o4.db"))
    db.initialize()
    mid = db.enqueue_outbox(target_type="user", target_id=1, content="x", kind="k", settings_key="bounty_payout")
    import sqlite3
    with sqlite3.connect(db.path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT settings_key FROM outbox_messages WHERE id=?", (mid,)).fetchone()
        assert row["settings_key"] == "bounty_payout"


def test_outbox_defer_does_not_increment_attempts(tmp_path: Path):
    db = Database(str(tmp_path / "o5.db"))
    db.initialize()
    mid = db.enqueue_outbox(target_type="user", target_id=1, content="x", kind="k", settings_key="schedule_reminders")
    _ = db.claim_outbox_batch(limit=10)
    db.defer_outbox_later(mid, reason="dm disabled: schedule_reminders", delay_seconds=3600)

    import sqlite3
    with sqlite3.connect(db.path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT status, attempts, not_before FROM outbox_messages WHERE id=?", (mid,)).fetchone()
        assert row["status"] == "pending"
        assert int(row["attempts"]) == 0
        assert row["not_before"] is not None


def test_outbox_requeue_stale_sending(tmp_path: Path):
    db = Database(str(tmp_path / "o6.db"))
    db.initialize()
    mid = db.enqueue_outbox(target_type="user", target_id=1, content="x", kind="k")
    _ = db.claim_outbox_batch(limit=10)

    import sqlite3
    with sqlite3.connect(db.path) as conn:
        conn.row_factory = sqlite3.Row
        # Force it to be stale.
        conn.execute(
            "UPDATE outbox_messages SET sending_since='2000-01-01T00:00:00' WHERE id=?",
            (mid,),
        )
        conn.commit()

    n = db.requeue_stale_sending_outbox(stale_after_seconds=1)
    assert n == 1

    batch = db.claim_outbox_batch(limit=10)
    assert [m["id"] for m in batch] == [mid]


def test_outbox_claim_two_workers_disjoint(tmp_path: Path):
    """Concurrent pumps must not claim the same row (real SQLite contention)."""
    path = str(tmp_path / "conc.db")
    db = Database(path)
    db.initialize()
    ids = [
        db.enqueue_outbox(target_type="user", target_id=i, content="x", kind="k")
        for i in range(8)
    ]
    barrier = threading.Barrier(2)
    claimed_batches: list[list[dict]] = []

    def worker():
        d = Database(path)
        barrier.wait()
        claimed_batches.append(d.claim_outbox_batch(limit=25))

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    t2.start()
    t1.join(timeout=60)
    t2.join(timeout=60)
    assert not t1.is_alive() and not t2.is_alive()

    flat = [int(m["id"]) for b in claimed_batches for m in b]
    assert len(flat) == len(set(flat)), "same id claimed twice"
    assert sorted(flat) == sorted(ids)


def _patch_connect_force_returning_error(monkeypatch):
    """Python 3.14+ makes ``Connection.execute`` immutable; wrap connections instead."""
    real_connect = database_module.sqlite3.connect

    def connect_wrap(*args, **kwargs):
        return _ForceReturningErrorConn(real_connect(*args, **kwargs))

    monkeypatch.setattr(database_module.sqlite3, "connect", connect_wrap)


def test_outbox_claim_forced_fallback_path(tmp_path: Path, monkeypatch):
    """If RETURNING errors, fallback claim still works."""
    _patch_connect_force_returning_error(monkeypatch)
    db = Database(str(tmp_path / "fb.db"))
    db.initialize()
    mid = db.enqueue_outbox(target_type="user", target_id=1, content="x", kind="k")

    batch = db.claim_outbox_batch(limit=10)
    assert [m["id"] for m in batch] == [mid]


def test_outbox_claim_pending_guard_in_update_sql():
    """Regression: claim UPDATE must filter ``status='pending'``."""
    from pathlib import Path as P
    import database as dbmod

    src = P(dbmod.__file__).read_text(encoding="utf-8")
    assert "AND status='pending'" in src
    assert "UPDATE outbox_messages SET status='sending'" in src


def test_outbox_fallback_aborts_when_pending_changes_mid_transaction(tmp_path: Path):
    """If the candidate set shrinks before guarded UPDATE, ``rowcount`` must reflect it (abort partial claim)."""
    path = str(tmp_path / "race.db")
    db = Database(path)
    db.initialize()
    id1 = db.enqueue_outbox(target_type="user", target_id=1, content="a", kind="k")
    id2 = db.enqueue_outbox(target_type="user", target_id=2, content="b", kind="k")
    id3 = db.enqueue_outbox(target_type="user", target_id=3, content="c", kind="k")

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=8000")
    now_iso = _utcnow_naive().isoformat()
    sel = conn.execute(
        """SELECT id FROM outbox_messages
           WHERE status='pending' AND (not_before IS NULL OR not_before<=?)
           ORDER BY id ASC LIMIT ?""",
        (now_iso, 10),
    ).fetchall()
    ids = [int(r["id"]) for r in sel]
    assert ids == [id1, id2, id3]

    conn2 = sqlite3.connect(path)
    conn2.execute("PRAGMA busy_timeout=8000")
    conn2.execute(
        "UPDATE outbox_messages SET status='failed', last_error=? WHERE id=?",
        ("stolen mid-claim", id2),
    )
    conn2.commit()
    conn2.close()

    ph = ",".join("?" * len(ids))
    upd = conn.execute(
        f"""UPDATE outbox_messages SET status='sending', sending_since=?
            WHERE id IN ({ph}) AND status='pending'""",
        (now_iso, *ids),
    )
    assert int(upd.rowcount or 0) == 2
    conn.close()

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("BEGIN IMMEDIATE")
    rows, ok = Database._claim_outbox_batch_fallback(conn, _utcnow_naive().isoformat(), 10)
    conn.commit()
    conn.close()
    assert ok
    assert {int(r["id"]) for r in rows} == {id1, id3}


def test_outbox_claim_returns_empty_when_fallback_signals_abort(tmp_path: Path, monkeypatch):
    """Partial-batch failure must not commit claims or leave orphan ``sending`` rows."""
    _patch_connect_force_returning_error(monkeypatch)
    monkeypatch.setattr(
        Database,
        "_claim_outbox_batch_fallback",
        staticmethod(lambda conn, now_iso, lim: ([], False)),
    )

    db = Database(str(tmp_path / "abort.db"))
    db.initialize()
    db.enqueue_outbox(target_type="user", target_id=1, content="x", kind="k")

    assert db.claim_outbox_batch(limit=10) == []

    with sqlite3.connect(db.path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT status FROM outbox_messages LIMIT 1").fetchone()
        assert row["status"] == "pending"
