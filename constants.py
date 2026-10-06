# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

"""Centralized constants for StudyBot RPG. Single source of truth for balance tuning."""

import os
from env_config import env_ids, env_id
from zoneinfo import ZoneInfo

# ── Timezone (single source of truth) ─────────────────────────────────────────
TZ_STR = os.getenv("TIMEZONE", "America/New_York")
EST = ZoneInfo(TZ_STR)

# ── XP Engine ─────────────────────────────────────────────────────────────────
XP_TIERS = [
    (25,  2),   # 0-25m: 2 XP/min
    (60,  3),   # 26-60m: 3 XP/min
    (120, 4),   # 61-120m: 4 XP/min
    (9999, 5),  # 121m+: 5 XP/min
]
FLAT_BONUSES = [
    (25,  25,  "Pomodoro"),    # 25m+: +25 XP
    (60,  50,  "Deep Work"),   # 60m+: +50 XP
    (120, 100, "Marathon"),    # 120m+: +100 XP
]

# ── Leveling ──────────────────────────────────────────────────────────────────
BASE_XP = 500
XP_GROWTH = 1.03
MAX_LEVEL = 50
COIN_EVERY_N_LEVELS = 5

# ── Economy ───────────────────────────────────────────────────────────────────
DEFAULT_COIN_CAP = 50
P4_COIN_CAP = 100
OVERFLOW_DAYS = 7
DEFAULT_CONVERT_COST = 2000
P1_CONVERT_COST = 1800
DAILY_STUDY_CAP_MINUTES = 480
BADGE_UNLOCK_POINTS = 200  # Study points granted each time a new badge tier is earned

# Shown on study/economy embeds so users know where to look next
USER_NAV_FOOTER = "Tip: `/today` · `/profile` · `/help` · `/source`"

# ── Lite mode ────────────────────────────────────────────────────────────────
# Explicit allowlist: productivity-only access (no RPG systems).
LITE_USER_IDS = set(env_ids("LITE_USER_IDS"))

# Private `/duo` leaderboard — configured user IDs (order = display order in embed).
DUO_LEADERBOARD_ORDER = env_ids("DUO_LEADERBOARD_USER_IDS")
DUO_LEADERBOARD_USER_IDS = frozenset(DUO_LEADERBOARD_ORDER)

# ── SB Ping role ─────────────────────────────────────────────────────────────
# Reaction-role ping group for announcements.
SB_PING_ROLE_ID = env_id("SB_PING_ROLE_ID")

# ── Seasonal Ranks ────────────────────────────────────────────────────────────
SEASONAL_RANKS = [
    (500,   "Fighter"),
    (2000,  "Warrior"),
    (5000,  "Champion"),
    (8000,  "Godslayer"),
]

# ── Streak Tiers ──────────────────────────────────────────────────────────────
STREAK_TIERS = [
    (30, "🏆 Legendary"),
    (14, "💎 Diamond"),
    (7,  "🔥 On Fire"),
    (3,  "⚡ Building"),
    (1,  "🌱 Starting"),
    (0,  "😴 Inactive"),
]

# ── Embed Colors ──────────────────────────────────────────────────────────────
COLOR_PRIMARY = 0x5865F2     # blurple — default/info
COLOR_SUCCESS = 0x57F287     # green — success/positive
COLOR_WARNING = 0xFEE75C     # yellow — warning/caution
COLOR_ERROR = 0xE74C3C       # red — error/danger
COLOR_GOLD = 0xF1C40F        # gold — economy/prestige
COLOR_ORANGE = 0xE67E22      # orange — shop/bounty
COLOR_MUTED = 0x747F8D       # grey — disabled/cancelled
COLOR_AQUA = 0x3498DB        # aqua — inventory
COLOR_RAID = 0xED4245        # raid boss red

# ── Pomodoro ──────────────────────────────────────────────────────────────────
WORK_COLOR = COLOR_RAID
BREAK_COLOR = COLOR_SUCCESS
LONG_BREAK_COLOR = 0x4FC3F7

# ── Task Priority ─────────────────────────────────────────────────────────────
PRI_EMOJI = {"high": "🔴", "medium": "🟡", "low": "🟢"}
PRI_COLOR = {"high": COLOR_RAID, "medium": COLOR_WARNING, "low": COLOR_SUCCESS}
SRS_INTERVALS = [1, 2, 4, 8, 16, 32, 60]

# ── Raid ──────────────────────────────────────────────────────────────────────
SEED_HP = 1200

# ── Lucky Loot ────────────────────────────────────────────────────────────────
LUCKY_LOOT_TABLE = [
    (0.80,  "common",    "pts",          (50, 200)),
    (0.15,  "uncommon",  "small_potion", None),
    (0.049, "rare",      "large_potion", None),
    (0.001, "legendary", "boss_coin",    None),
]
# One weighted loot pull per full hour of session (max 5); see roll_lucky_loot in cogs/study.py.
LUCKY_LOOT_MIN_MINUTES = 60

# ── Shop ──────────────────────────────────────────────────────────────────────
POTION_CATALOG = {
    "small_potion":  {"name": "Small Potion",  "cost_pts": 800,  "cost_coins": 0, "mult": 1.5, "hours": 2,  "desc": "1.5x XP for 2 hours"},
    "large_potion":  {"name": "Large Potion",  "cost_pts": 1500, "cost_coins": 0, "mult": 1.5, "hours": 4,  "desc": "1.5x XP for 4 hours"},
    "mega_potion":   {"name": "Mega Potion",   "cost_pts": 0,    "cost_coins": 4, "mult": 1.5, "hours": 24, "desc": "1.5x XP for 24 hours"},
}

GACHA_TIERS = {
    "bronze": {"cost": 1,  "loss_pct": 85, "loss_range": (300, 800),    "profit_pct": 15, "profit_range": (1200, 2500),  "jackpot_pct": 0,  "jackpot": 0},
    "silver": {"cost": 5,  "loss_pct": 90, "loss_range": (2000, 4500),  "profit_pct": 10, "profit_range": (6000, 10000), "jackpot_pct": 0,  "jackpot": 0},
    "gold":   {"cost": 15, "loss_pct": 90, "loss_range": (8000, 14000), "profit_pct": 5,  "profit_range": (20000, 40000),"jackpot_pct": 5,  "jackpot": 50000},
}

COSMETIC_COSTS = {"lb_icon": 10, "suffix_title": 15, "role_color": 25}

# ── Motivational Quotes ──────────────────────────────────────────────────────
MOTIVATIONAL_QUOTES = [
    "You're doing great — every minute counts. 💪",
    "Consistency beats perfection every time. Keep going!",
    "The person you'll be tomorrow is built by what you do today. 📚",
    "You're already ahead of the you who didn't start.",
    "Progress, not perfection. You're crushing it. 🔥",
    "Every expert was once a beginner. You're building something real.",
    "Flow state incoming. Just a bit longer. 🎯",
    "You chose to show up. That's already the hardest part.",
]

STAR_LABELS = {1: "😴 Distracted", 2: "😐 So-so", 3: "🙂 Decent", 4: "😊 Focused", 5: "🔥 In the zone"}
