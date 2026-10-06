# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

"""Parity tests for smoke_check structural/hypothetical invariants.

This intentionally mirrors a subset of smoke_check.py helpers so pytest failures are easier to read
than running smoke_check.py alone, while still keeping smoke_check.py as the CI source of truth.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import smoke_check as sc


def _must_not_smoke_fail(fn, *args) -> None:
    try:
        fn(*args)
    except SystemExit as e:
        if e.code == 2:
            pytest.fail(f"smoke invariant failed: {fn.__name__} (run python smoke_check.py)")
        raise


def test_bot_py_hypothetical_smoke_bundle() -> None:
    root = Path(__file__).resolve().parent.parent
    bot_path = root / "bot.py"
    src = bot_path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename="bot.py")
    studybot = sc._find_class(tree, "StudyBot")
    assert studybot is not None

    _must_not_smoke_fail(sc._require_schedule_block_dms_respect_toggle, studybot)
    _must_not_smoke_fail(sc._require_tree_interaction_check_denials_are_ephemeral, studybot)
    _must_not_smoke_fail(sc._require_daily_reset_serializes_on_state_lock, studybot)
    _must_not_smoke_fail(sc._require_outbox_pump_singleton_scheduled, studybot)
    _must_not_smoke_fail(sc._require_hourly_cleanup_includes_bounty_expiry, studybot)

    _must_not_smoke_fail(sc._require_discord_token_from_env, src)
    _must_not_smoke_fail(sc._require_no_discord_token_assignment, src)
    _must_not_smoke_fail(sc._require_graceful_shutdown_wal_checkpoint, src)


def test_schedule_gate_helper_detects_correct_get_dm_call() -> None:
    src = """
class StudyBot:
    def _schedule_block_dms_enabled(self, user_id: int) -> bool:
        try:
            return self.db.get_dm_enabled(int(user_id), "schedule_reminders")
        except Exception:
            return False
"""
    tree = ast.parse(src)
    gate = sc._find_class(tree, "StudyBot")
    assert gate is not None
    fn = sc._find_function(gate, "_schedule_block_dms_enabled")
    assert fn is not None
    assert sc._schedule_gate_uses_get_dm_enabled_schedule_reminders(fn)


def test_schedule_gate_helper_rejects_wrong_settings_key() -> None:
    src = """
class StudyBot:
    def _schedule_block_dms_enabled(self, user_id: int) -> bool:
        return self.db.get_dm_enabled(int(user_id), "morning_briefing")
"""
    tree = ast.parse(src)
    gate = sc._find_class(tree, "StudyBot")
    fn = sc._find_function(gate, "_schedule_block_dms_enabled")
    assert fn is not None
    assert not sc._schedule_gate_uses_get_dm_enabled_schedule_reminders(fn)
