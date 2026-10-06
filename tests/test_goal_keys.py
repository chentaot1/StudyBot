# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

"""Locale-independent goal column keys (goal_mon … goal_sun)."""

from datetime import date, timedelta

from utils import goal_override_key_for_date


def test_goal_override_key_monday():
    assert goal_override_key_for_date(date(2025, 1, 6)) == "mon"


def test_goal_override_key_sunday():
    assert goal_override_key_for_date(date(2025, 1, 5)) == "sun"


def test_goal_override_key_full_week():
    # 2025-01-06 is Monday
    d0 = date(2025, 1, 6)
    expected = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
    for i, exp in enumerate(expected):
        assert goal_override_key_for_date(d0 + timedelta(days=i)) == exp
