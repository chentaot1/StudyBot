# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

"""Shared utility functions for StudyBot RPG. Imported by bot.py and all cogs."""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from constants import EST

_log_si = logging.getLogger("StudyBot.single_instance")


def fmt_mins(m: int) -> str:
    if m <= 0:
        return "0m"
    if m < 60:
        return f"{m}m"
    return f"{m // 60}h {m % 60}m" if m % 60 else f"{m // 60}h"


def fmt_secs(s: int) -> str:
    m, sec = divmod(max(s, 0), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m}m {sec}s"
    if m:
        return f"{m}m {sec}s"
    return f"{sec}s"


def make_bar(value: int | float, max_val: int | float, width: int = 12) -> str:
    if max_val == 0:
        return "░" * width
    pct = min(value / max_val, 1.0)
    filled = int(pct * width)
    return "█" * filled + "░" * (width - filled)


def fmt_hp(hp: int, total: int) -> str:
    pct = (hp / total * 100) if total > 0 else 0
    bar_len = 20
    filled = int(pct / 100 * bar_len)
    bar = "█" * filled + "░" * (bar_len - filled)
    return f"`{bar}` {hp:,}/{total:,} ({pct:.1f}%)"


def fmt_date_us(d: date) -> str:
    """US-style calendar date (MM/DD/YYYY)."""
    return d.strftime("%m/%d/%Y")


def fmt_long_date_us(d: date) -> str:
    """US long form: Monday, June 2, 2026."""
    return f"{d.strftime('%A')}, {d.strftime('%B')} {d.day}, {d.year}"


def fmt_date_us_from_iso(iso_date: str) -> str:
    """Convert stored YYYY-MM-DD to MM/DD/YYYY for display."""
    if not iso_date:
        return ""
    raw = (iso_date or "").strip()[:10]
    if not raw:
        return ""
    try:
        return fmt_date_us(date.fromisoformat(raw))
    except ValueError:
        return raw


def fmt_datetime_us_est(dt: datetime, *, with_seconds: bool = False) -> str:
    """Format an instant in US style (Eastern local time)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    est = dt.astimezone(EST)
    h = int(est.strftime("%I"))
    if with_seconds:
        return f"{fmt_date_us(est.date())} {h}:{est.strftime('%M:%S %p')} EST"
    return f"{fmt_date_us(est.date())} {h}:{est.strftime('%M %p')} EST"


def fmt_weekday_datetime_us_est(dt: datetime) -> str:
    """Weekday + US date + 12h time in Eastern (session history, etc.)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    est = dt.astimezone(EST)
    h = int(est.strftime("%I"))
    return f"{est.strftime('%a')} {fmt_date_us(est.date())} {h}{est.strftime(':%M %p')}"


def goal_override_key_for_date(d: date) -> str:
    """DB column suffix ``goal_mon`` … ``goal_sun`` for a calendar date (locale-independent)."""
    keys = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
    return keys[d.weekday()]


def parse_stored(ts: str) -> datetime:
    """Parse a naive-UTC DB timestamp string, stripping any tz suffix."""
    return datetime.fromisoformat(ts.split("+")[0].split("Z")[0])


def stored_to_est_date(ts: str) -> str:
    """Convert a UTC-stored timestamp to an EST date string (YYYY-MM-DD)."""
    return parse_stored(ts).replace(tzinfo=timezone.utc).astimezone(EST).date().isoformat()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def streak_tier(streak: int) -> str:
    from constants import STREAK_TIERS
    for threshold, label in STREAK_TIERS:
        if streak >= threshold:
            return label
    return "😴 Inactive"


def parse_due_date(raw: str) -> str | None:
    """Parse US-style or ISO dates into YYYY-MM-DD for storage."""
    raw = raw.strip()
    today_est = datetime.now(EST).date()

    for fmt in ("%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d"):
        try:
            d = datetime.strptime(raw, fmt).date()
            return d.strftime("%Y-%m-%d")
        except ValueError:
            pass

    parts = raw.split("/")
    if len(parts) == 2:
        try:
            month, day = int(parts[0]), int(parts[1])
        except ValueError:
            return None
        for year_offset in range(2):
            year = today_est.year + year_offset
            try:
                d_date = datetime(year, month, day).date()
                if d_date >= today_est:
                    return d_date.strftime("%Y-%m-%d")
            except ValueError:
                continue

    return None


def safe_json_loads(text: str | None, *, default=None):
    """Parse JSON from persisted text; never raises (corruption-tolerant)."""
    if text is None:
        return default
    s = str(text).strip()
    if not s:
        return default
    try:
        return json.loads(s)
    except Exception:
        return default


def next_srs_interval(current: int) -> int:
    from constants import SRS_INTERVALS
    for i in SRS_INTERVALS:
        if i > current:
            return i
    return 60


def acquire_single_instance_lock() -> None:
    """
    Best-effort local single-instance guard (plan A1).
    Opt out with STUDYBOT_ALLOW_MULTI=1. Does not coordinate across hosts.
    """
    if os.environ.get("STUDYBOT_ALLOW_MULTI", "").strip().lower() in ("1", "true", "yes", "on"):
        _log_si.warning("STUDYBOT_ALLOW_MULTI set — single-instance lock bypassed.")
        return

    base = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or str(Path.home())
    lock_path = Path(base) / "StudyBot" / "bot.instance.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            existing_txt = ""
            existing_pid: Optional[int] = None
            try:
                existing_txt = lock_path.read_text(encoding="utf-8")[:2000]
                existing = json.loads(existing_txt) if existing_txt else {}
                if isinstance(existing, dict) and "pid" in existing:
                    existing_pid = int(existing["pid"])
            except Exception:
                existing_pid = None

            alive = False
            if existing_pid:
                try:
                    os.kill(existing_pid, 0)
                    alive = True
                except Exception:
                    alive = False

            if alive:
                raise RuntimeError(
                    f"Another StudyBot instance appears to be running (pid={existing_pid}). "
                    f"Lock file: {lock_path}"
                )

            try:
                lock_path.unlink(missing_ok=True)  # type: ignore[call-arg]
            except TypeError:
                try:
                    if lock_path.exists():
                        lock_path.unlink()
                except Exception:
                    pass
            continue
        except OSError as e:
            _log_si.warning("Could not create single-instance lock (%s); continuing without guard.", e)
            return

    try:
        info = {
            "pid": int(os.getpid()),
            "cwd": os.getcwd(),
            "argv": list(getattr(sys, "argv", [])),
            "ts_ms": int(time.time() * 1000),
        }
        os.write(fd, json.dumps(info, sort_keys=True).encode("utf-8"))
        _log_si.info("Single-instance lock acquired: %s", lock_path)
    finally:
        try:
            os.close(fd)
        except Exception:
            pass
