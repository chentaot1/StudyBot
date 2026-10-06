# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

"""US date display helpers and due-date parsing."""
from datetime import date, datetime, timezone

from constants import EST
from utils import fmt_date_us, fmt_date_us_from_iso, fmt_datetime_us_est, fmt_long_date_us, parse_due_date


def test_fmt_date_us():
    assert fmt_date_us(date(2026, 6, 5)) == "06/05/2026"


def test_fmt_date_us_from_iso():
    assert fmt_date_us_from_iso("2026-06-05") == "06/05/2026"
    assert fmt_date_us_from_iso("") == ""


def test_parse_due_date_accepts_us_dash():
    assert parse_due_date("06-05-2026") == "2026-06-05"


def test_fmt_long_date_us():
    assert "2026" in fmt_long_date_us(date(2026, 6, 5))
    assert "June" in fmt_long_date_us(date(2026, 6, 5))


def test_fmt_datetime_us_est():
    dt = datetime(2026, 6, 5, 13, 7, 9, tzinfo=EST)
    s = fmt_datetime_us_est(dt, with_seconds=True)
    assert "06/05/2026" in s
    assert "1:07:09 PM" in s

    dt_utc = datetime(2026, 6, 5, 17, 0, 0, tzinfo=timezone.utc)
    s2 = fmt_datetime_us_est(dt_utc)
    assert "06/05/2026" in s2
    assert "PM" in s2 or "AM" in s2
