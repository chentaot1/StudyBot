# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

"""Temptation bundling — pair a specific treat with study (settings + copy for live card).

Kept as a top-level module (not utils.*) because `utils.py` shadows a `utils/` package on import path.
"""

from __future__ import annotations

from typing import Any


RULE_POMO_WORK = "pomo_work_complete"
RULE_POMO_BREAK = "pomo_break_only"
RULE_STUDY_MIN = "study_minutes"

ALL_RULES = (RULE_POMO_WORK, RULE_POMO_BREAK, RULE_STUDY_MIN)


def bundle_config(db: Any, user_id: int) -> dict | None:
    """Return bundle dict or None if disabled / incomplete."""
    if db.get_setting(user_id, "temptation_bundle_enabled", "0") != "1":
        return None
    label = (db.get_setting(user_id, "temptation_bundle_label", "") or "").strip()
    if not label:
        return None
    rule = db.get_setting(user_id, "temptation_bundle_rule", RULE_POMO_WORK)
    if rule not in ALL_RULES:
        rule = RULE_POMO_WORK
    try:
        study_mins = int(db.get_setting(user_id, "temptation_bundle_study_minutes", "25"))
    except ValueError:
        study_mins = 25
    study_mins = max(5, min(480, study_mins))
    url = (db.get_setting(user_id, "temptation_bundle_url", "") or "").strip()
    return {"label": label[:500], "url": url[:500] if url else "", "rule": rule, "study_mins": study_mins}


def bundle_live_line_for_study_session(db: Any, user_id: int, elapsed_active_mins: int) -> str | None:
    """One line for /study live embed (no Discord imports)."""
    c = bundle_config(db, user_id)
    if not c:
        return None
    lab = c["label"][:200]
    rule = c["rule"]
    if rule == RULE_STUDY_MIN:
        n = c["study_mins"]
        left = max(0, n - int(elapsed_active_mins))
        if left > 0:
            return f"🎁 **Treat bundle:** {lab} — DM when you stop after **≥{n}** min focused (**{left}** min to go)."
        return f"🎁 **Treat bundle:** {lab} — you'll get a DM at **/study stop** if this session stays **≥{n}** min."
    if rule == RULE_POMO_WORK:
        return f"🎁 **Treat bundle:** {lab} — DMs **after each Pomodoro work block** (`/pomodoro`)."
    if rule == RULE_POMO_BREAK:
        return f"🎁 **Treat bundle:** {lab} — DMs when a **Pomodoro break** starts (`/pomodoro`)."
    return None


def rule_display(rule: str) -> str:
    return {
        RULE_POMO_WORK: "After each Pomodoro work block",
        RULE_POMO_BREAK: "When a Pomodoro break starts",
        RULE_STUDY_MIN: "When /study stop with enough focus minutes",
    }.get(rule, rule)
