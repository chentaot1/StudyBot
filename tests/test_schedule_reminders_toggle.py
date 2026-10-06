# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

"""Contract tests for Schedule Reminders (`schedule_reminders` / `dm_schedule_reminders`)."""

from __future__ import annotations

from pathlib import Path

from database import Database


def test_schedule_reminders_defaults_on(tmp_path: Path):
    db = Database(str(tmp_path / "s.db"))
    db.initialize()
    db.ensure_user(42, "tester")
    assert db.get_dm_enabled(42, "schedule_reminders") is True


def test_schedule_reminders_respects_user_setting_off(tmp_path: Path):
    db = Database(str(tmp_path / "s2.db"))
    db.initialize()
    db.ensure_user(7, "u")
    db.set_setting(7, "dm_schedule_reminders", "0")
    assert db.get_dm_enabled(7, "schedule_reminders") is False


def test_schedule_reminders_user_can_re_enable(tmp_path: Path):
    db = Database(str(tmp_path / "s3.db"))
    db.initialize()
    db.ensure_user(99, "u")
    db.set_setting(99, "dm_schedule_reminders", "0")
    assert db.get_dm_enabled(99, "schedule_reminders") is False
    db.set_setting(99, "dm_schedule_reminders", "1")
    assert db.get_dm_enabled(99, "schedule_reminders") is True


def test_schedule_reminders_non_one_string_treated_off(tmp_path: Path):
    """Only explicit ``\"1\"`` enables; any other stored value is off (matches get_dm_enabled)."""
    db = Database(str(tmp_path / "s4.db"))
    db.initialize()
    db.ensure_user(1, "u")
    for raw in ("0", "", "yes", "2"):
        db.set_setting(1, "dm_schedule_reminders", raw)
        assert db.get_dm_enabled(1, "schedule_reminders") is (raw == "1")
