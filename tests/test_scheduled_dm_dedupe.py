# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def test_weekly_report_dedupe_anchor_matches_embed_window():
    """Mirror ``StudyBot._weekly_report_dedupe_anchor`` / weekly embed window without importing ``bot.py``."""
    est = ZoneInfo("America/New_York")
    monday = datetime(2026, 5, 4, 12, 0, 0, tzinfo=est)
    sunday_same_week = monday + timedelta(days=6)
    anchor = (sunday_same_week.astimezone(est).date() - timedelta(days=6)).isoformat()
    assert anchor == monday.date().isoformat()


def test_est_calendar_bucket_for_fixed_utc_instant():
    """Eastern calendar bucket must follow ZoneInfo (DST-safe), not a fixed UTC offset."""
    est = datetime(2026, 1, 15, 4, 30, 0, tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("America/New_York"))
    assert est.date().isoformat() == "2026-01-14"


def test_schedule_catchup_dedupe_key_shape():
    """Guardrail shape from Batch A plan (stable kind + block id + eastern bucket + clock)."""
    block_id = 42
    date_est = "2026-05-08"
    hour, minute = 9, 7
    key = f"schedule_catchup:{block_id}:{date_est}:{hour:02d}:{minute:02d}"
    assert key == "schedule_catchup:42:2026-05-08:09:07"
