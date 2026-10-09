# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

"""Timezone-aware recurring schedules, including DST gaps and repeated hours."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from constants import TZ_STR

DEFAULT_TIMEZONE = TZ_STR
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def valid_timezone(name: str) -> str:
    name = name.strip()
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("Use an IANA timezone such as America/New_York or Europe/London.") from exc
    return name


def occurrence(block: dict, date) -> datetime | None:
    """Choose the first repeated hour; skip local times that do not exist."""
    zone = ZoneInfo(block.get("timezone") or DEFAULT_TIMEZONE)
    local = datetime(date.year, date.month, date.day, block["hour"], block["minute"], tzinfo=zone, fold=0)
    utc = local.astimezone(timezone.utc)
    if utc.astimezone(zone).replace(tzinfo=None) != local.replace(tzinfo=None):
        return None
    return utc


def due_blocks(blocks: list[dict], now: datetime) -> list[dict]:
    now = now.astimezone(timezone.utc).replace(second=0, microsecond=0)
    result = []
    for block in blocks:
        local = now.astimezone(ZoneInfo(block.get("timezone") or DEFAULT_TIMEZONE))
        if DAYS[local.weekday()] not in block["days_of_week"].split(","):
            continue
        start = occurrence(block, local.date())
        if start == now:
            result.append({**block, "starts_at": start})
    return result


def next_block(blocks: list[dict], now: datetime) -> tuple[dict, datetime] | None:
    now = now.astimezone(timezone.utc)
    candidates = []
    for block in blocks:
        local = now.astimezone(ZoneInfo(block.get("timezone") or DEFAULT_TIMEZONE))
        for offset in range(8):
            date = local.date() + timedelta(days=offset)
            if DAYS[date.weekday()] not in block["days_of_week"].split(","):
                continue
            start = occurrence(block, date)
            if start is not None and start >= now:
                candidates.append((block, start))
                break
    return min(candidates, key=lambda value: value[1]) if candidates else None
