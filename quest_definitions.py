# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

"""Quest pool definitions and metric-to-quest mapping for the Daily Quest system."""

QUEST_POOLS = {
    1: [
        {"key": "warm_up",     "name": "Warm Up",     "target": 5,  "desc": "Study for 5 minutes"},
        {"key": "initiator",   "name": "Initiator",   "target": 20, "desc": "Study for 20 minutes"},
        {"key": "encourager",  "name": "Encourager",  "target": 1,  "desc": "Send 1 Cheer"},
        {"key": "librarian",   "name": "Librarian",   "target": 1,  "desc": "Add 1 task"},
        {"key": "checklist",   "name": "Checklist",   "target": 1,  "desc": "Complete 1 task"},
        {"key": "status_update","name": "Status Update","target": 1, "desc": "Add 1 session note"},
        {"key": "punctual",    "name": "Punctual",    "target": 1,  "desc": "Start within 30m of schedule"},
        {"key": "the_planner", "name": "The Planner", "target": 3,  "desc": "Add 3 tasks"},
        {"key": "revisit",     "name": "Revisit",     "target": 1,  "desc": "Open your stats"},
        {"key": "on_target",   "name": "On Target",   "target": 1,  "desc": "Set a session target"},
        {"key": "hydrated",    "name": "Hydrated",    "target": 1,  "desc": "Pause and resume once"},
    ],
    2: [
        {"key": "steady_work", "name": "Steady Work", "target": 60, "desc": "Study for 60 minutes"},
        {"key": "the_habit",   "name": "The Habit",   "target": 3,  "desc": "Complete 3 SRS reviews"},
        {"key": "productive",  "name": "Productive",  "target": 3,  "desc": "Complete 3 tasks"},
        {"key": "intervals",   "name": "Intervals",   "target": 1,  "desc": "Complete a 25m Pomo + break"},
        {"key": "double_shift","name": "Double Shift", "target": 2, "desc": "Complete 2 separate sessions"},
        {"key": "long_haul",   "name": "Long Haul",   "target": 45, "desc": "Single session >= 45m"},
        {"key": "deep_focus",  "name": "Deep Focus",  "target": 45, "desc": "45m without pausing"},
        {"key": "streak_guard","name": "Streak Guard", "target": 1, "desc": "Hit your daily goal"},
        {"key": "clean_slate", "name": "Clean Slate",  "target": 5, "desc": "Complete 5 tasks total"},
        {"key": "pomodoro_pro","name": "Pomodoro Pro", "target": 2, "desc": "Complete 2 full Pomo cycles"},
        {"key": "the_reviewer","name": "The Reviewer", "target": 1, "desc": "Complete all due SRS tasks"},
        {"key": "the_finisher","name": "The Finisher", "target": 1, "desc": "Finish all tasks due today"},
    ],
    3: [
        {"key": "the_grinder",     "name": "The Grinder",     "target": 120,"desc": "Study for 120 minutes"},
        {"key": "squad_up",        "name": "Squad Up",        "target": 1,  "desc": "4-cycle Group Pomo (>=20m work)"},
        {"key": "the_scholar",     "name": "The Scholar",     "target": 8,  "desc": "Complete 8 SRS reviews"},
        {"key": "academic_sweep",  "name": "Academic Sweep",  "target": 5,  "desc": "Complete 5 tasks today"},
        {"key": "iron_will",       "name": "Iron Will",       "target": 90, "desc": "90m without pausing"},
        {"key": "the_completionist","name":"The Completionist","target": 1,  "desc": "Hit goal + all 3 quests"},
        {"key": "srs_master",      "name": "SRS Master",      "target": 1,  "desc": "Review interval day 16+"},
        {"key": "project_push",    "name": "Project Push",    "target": 3,  "desc": "3 tasks in same project"},
        {"key": "the_architect",   "name": "The Architect",   "target": 1,  "desc": "5+ active tasks across 3+ projects"},
        {"key": "century",         "name": "Century",         "target": 100,"desc": "100 minutes today"},
        {"key": "the_traditionalist","name":"The Traditionalist","target":1, "desc": "4-cycle solo Pomo"},
    ],
}

# Maps study-engine metrics to the quest keys they can increment.
# The quest tracker uses this to know which quests to update when an event fires.
METRIC_TO_QUEST_KEYS = {
    "study_minutes":     ["warm_up", "initiator", "steady_work", "the_grinder", "century"],
    "session_complete":  ["double_shift"],
    "cheer_sent":        ["encourager"],
    "task_added":        ["librarian", "the_planner"],
    "task_completed":    ["checklist", "productive", "clean_slate", "academic_sweep"],
    "session_note":      ["status_update"],
    "punctual_start":    ["punctual"],
    "stats_viewed":      ["revisit"],
    "target_set":        ["on_target"],
    "pause_resume":      ["hydrated"],
    "srs_review":        ["the_habit", "the_scholar"],
    "pomo_cycle":        ["intervals", "pomodoro_pro", "the_traditionalist"],
    "long_session":      ["long_haul"],
    "deep_focus":        ["deep_focus", "iron_will"],
    "streak_guard":      ["streak_guard"],
    "all_srs_done":      ["the_reviewer"],
    "all_due_done":      ["the_finisher"],
    "group_pomo_4cycle": ["squad_up"],
    "all_quests_done":   ["the_completionist"],
    "srs_master":        ["srs_master"],
    "project_push":      ["project_push"],
    "the_architect":     ["the_architect"],
}

TIER_REWARDS = {
    1: 25,
    2: 75,
    3: 150,
}

ALL_COMPLETE_BONUS_PTS = 250
ALL_COMPLETE_BONUS_XP = 200
