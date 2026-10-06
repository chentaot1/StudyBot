# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging

log = logging.getLogger("StudyBot.RewardsEngine")


def _parse_iso_naive(s: str) -> datetime:
    """Parse an ISO timestamp to a naive UTC datetime.

    We store timestamps as naive ISO strings. Some callers/tests may pass timezone-aware ISO strings.
    This helper normalizes both into naive UTC.
    """
    s = (s or "").strip()
    if not s:
        raise ValueError("empty datetime string")
    # Accept trailing Z.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


@dataclass(frozen=True)
class XpBonusBreakdown:
    potion_bonus_xp: int = 0
    bounty_bonus_xp: int = 0

    @property
    def total_bonus_xp(self) -> int:
        return int(self.potion_bonus_xp) + int(self.bounty_bonus_xp)


def calc_potion_bonus_xp(db, *, user_id: int, session: dict) -> int:
    """Return potion bonus XP for this session based on overlap weighting.

    `db` is the StudyBot Database instance (duck-typed).
    """
    minutes = int(session.get("duration_minutes") or 0)
    if minutes <= 0:
        return 0
    started_at = session.get("started_at")
    ended_at = session.get("ended_at")
    if not started_at or not ended_at:
        return 0
    overlap = db.get_potion_overlap_multiplier(user_id, started_at, ended_at, minutes)
    return int(overlap.get("bonus_xp") or 0)


def calc_bounty_bonus_xp(db, *, user_id: int, session: dict, is_allowed_member: bool) -> int:
    """Return bounty XP bonus for this session (overlap with bounty window).

    This function mirrors the logic in the Study rewards pipeline:
    - Only applies if `is_allowed_member` is True.
    - Only the "extra portion" is added (doubling the overlapping minutes).
    """
    if not is_allowed_member:
        return 0

    minutes = int(session.get("duration_minutes") or 0)
    if minutes <= 0:
        return 0

    started_at = session.get("started_at")
    ended_at = session.get("ended_at")
    if not started_at or not ended_at:
        return 0

    bounty_xp = db.get_active_bounty_xp_window(user_id)
    if not bounty_xp:
        return 0

    try:
        started = _parse_iso_naive(started_at)
        ended = _parse_iso_naive(ended_at)
        act = _parse_iso_naive(bounty_xp.get("activated_at") or "")
        buff_end = _parse_iso_naive(bounty_xp.get("buff_expires_at") or "")
        overlap_start = max(started, act)
        overlap_end = min(ended, buff_end)
        overlap_mins = max(int((overlap_end - overlap_start).total_seconds() / 60), 0)
        if overlap_mins <= 0:
            return 0
        base_xp = int(session.get("xp_earned") or 0)
        return int(base_xp * (overlap_mins / minutes))
    except Exception:
        log.debug("Bounty XP parse/calc failed (uid=%s)", user_id, exc_info=True)
        return 0


def calc_xp_bonus_breakdown(db, *, user_id: int, session: dict, is_allowed_member: bool) -> XpBonusBreakdown:
    return XpBonusBreakdown(
        potion_bonus_xp=calc_potion_bonus_xp(db, user_id=user_id, session=session),
        bounty_bonus_xp=calc_bounty_bonus_xp(db, user_id=user_id, session=session, is_allowed_member=is_allowed_member),
    )
