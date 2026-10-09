# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

import os
import sqlite3
import json
import math
import random
import string
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from services.scheduling import DEFAULT_TIMEZONE

from constants import (
    EST, BASE_XP, XP_GROWTH, MAX_LEVEL, COIN_EVERY_N_LEVELS,
    DEFAULT_COIN_CAP, P4_COIN_CAP, OVERFLOW_DAYS,
    DEFAULT_CONVERT_COST, P1_CONVERT_COST, SEASONAL_RANKS,
    BADGE_UNLOCK_POINTS,
    LITE_USER_IDS,
)
from utils import next_srs_interval, goal_override_key_for_date, safe_json_loads

log = logging.getLogger("StudyBot.DB")


def xp_for_level(level: int) -> int:
    return int(BASE_XP * (XP_GROWTH ** (level - 1)))


def level_from_total_xp(total_xp: int) -> tuple[int, int]:
    """Returns (level, xp_into_current_level)."""
    remaining = total_xp
    for lvl in range(1, MAX_LEVEL + 1):
        needed = xp_for_level(lvl)
        if remaining < needed:
            return lvl, remaining
        remaining -= needed
    return MAX_LEVEL, remaining


def seasonal_rank_for_minutes(minutes: int) -> str:
    rank = ""
    for threshold, name in SEASONAL_RANKS:
        if minutes >= threshold:
            rank = name
    if rank == "Godslayer" and minutes >= 8000:
        extra = (minutes - 8000) // 2000
        if extra > 0:
            numerals = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"]
            rank += f" {numerals[min(extra, len(numerals) - 1)]}"
    return rank or "Unranked"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_stored(ts: str) -> datetime:
    ts = ts.split("+")[0].split("Z")[0]
    return datetime.fromisoformat(ts)


def _stored_to_est_date(ts: str) -> str:
    return _parse_stored(ts).replace(tzinfo=timezone.utc).astimezone(EST).date().isoformat()


class Database:
    def __init__(self, path: str):
        self.path = path

    def _conn(self) -> sqlite3.Connection:
        # Wait up to N seconds when DB is locked (writer contention / WAL checkpoints).
        timeout_sec = float(os.getenv("SQLITE_CONNECT_TIMEOUT_SEC", "30"))
        conn = sqlite3.connect(self.path, timeout=timeout_sec)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        busy_ms = int(os.getenv("SQLITE_BUSY_TIMEOUT_MS", "5000"))
        if busy_ms > 0:
            conn.execute(f"PRAGMA busy_timeout={busy_ms}")
        return conn

    def initialize(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id             INTEGER PRIMARY KEY,
                    username            TEXT,
                    points              INTEGER DEFAULT 0,
                    total_xp            INTEGER DEFAULT 0,
                    streak              INTEGER DEFAULT 0,
                    longest_streak      INTEGER DEFAULT 0,
                    last_study_date     TEXT,
                    daily_goal_minutes  INTEGER DEFAULT 60,
                    daily_minutes_today INTEGER DEFAULT 0,
                    goal_mon            INTEGER,
                    goal_tue            INTEGER,
                    goal_wed            INTEGER,
                    goal_thu            INTEGER,
                    goal_fri            INTEGER,
                    goal_sat            INTEGER,
                    goal_sun            INTEGER,
                    adaptive_goals      INTEGER DEFAULT 1,
                    last_goal_suggestion TEXT,
                    checkin_enabled     INTEGER DEFAULT 0,
                    checkin_hour        INTEGER DEFAULT 20,
                    coins               INTEGER DEFAULT 0,
                    level               INTEGER DEFAULT 1,
                    xp_current          INTEGER DEFAULT 0,
                    prestige            INTEGER DEFAULT 0,
                    banked_xp           INTEGER DEFAULT 0,
                    total_converted_pts INTEGER DEFAULT 0,
                    convert_count_today INTEGER DEFAULT 0,
                    last_convert_date   TEXT,
                    seasonal_minutes    INTEGER DEFAULT 0,
                    seasonal_cheers_sent INTEGER DEFAULT 0,
                    ghost_mode          INTEGER DEFAULT 0,
                    block_cheers        INTEGER DEFAULT 0,
                    featured_badges     TEXT DEFAULT '[]',
                    leaderboard_icon    TEXT,
                    suffix_title        TEXT,
                    role_color_hex      TEXT,
                    weekend_bonus_date  TEXT,
                    total_points_earned INTEGER DEFAULT 0,
                    total_points_spent  INTEGER DEFAULT 0,
                    cheers_sent_total   INTEGER DEFAULT 0,
                    has_gold_jackpot    INTEGER DEFAULT 0,
                    created_at          TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS reminders (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id    INTEGER NOT NULL,
                    message    TEXT NOT NULL,
                    fire_at    TEXT NOT NULL,
                    sent       INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS schedule_blocks (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id          INTEGER NOT NULL,
                    subject          TEXT NOT NULL,
                    days_of_week     TEXT NOT NULL,
                    hour             INTEGER NOT NULL,
                    minute           INTEGER NOT NULL,
                    duration_minutes INTEGER DEFAULT 60,
                    created_at       TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS projects (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id     INTEGER NOT NULL,
                    name        TEXT NOT NULL,
                    description TEXT,
                    due_date    TEXT,
                    completed   INTEGER DEFAULT 0,
                    created_at  TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS tasks (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id             INTEGER NOT NULL,
                    user_task_num       INTEGER DEFAULT 0,
                    project_id          INTEGER,
                    title               TEXT NOT NULL,
                    description         TEXT,
                    points              INTEGER DEFAULT 10,
                    priority            TEXT DEFAULT 'medium',
                    due_date            TEXT,
                    completed           INTEGER DEFAULT 0,
                    completed_at        TEXT,
                    is_review           INTEGER DEFAULT 0,
                    review_interval     INTEGER DEFAULT 1,
                    review_number       INTEGER DEFAULT 1,
                    next_review_date    TEXT,
                    created_at          TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS study_sessions (
                    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id               INTEGER NOT NULL,
                    subject               TEXT,
                    tags                  TEXT,
                    started_at            TEXT NOT NULL,
                    ended_at              TEXT,
                    duration_minutes      INTEGER,
                    xp_earned             INTEGER DEFAULT 0,
                    points_earned         INTEGER DEFAULT 0,
                    is_paused             INTEGER DEFAULT 0,
                    paused_at             TEXT,
                    total_paused_seconds  INTEGER DEFAULT 0,
                    target_minutes        INTEGER,
                    notes                 TEXT,
                    focus_rating          INTEGER,
                    live_channel_id       INTEGER,
                    live_message_id       INTEGER,
                    motivation_sent       INTEGER DEFAULT 0,
                    is_group              INTEGER DEFAULT 0,
                    group_lobby_id        INTEGER,
                    source_guild_id       INTEGER,
                    allowed_member        INTEGER
                );

                CREATE TABLE IF NOT EXISTS study_session_segments (
                    id                         INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id                  INTEGER NOT NULL,
                    user_id                     INTEGER NOT NULL,
                    subject                     TEXT,
                    tags                        TEXT,
                    started_at                  TEXT NOT NULL,
                    ended_at                    TEXT,
                    duration_minutes            INTEGER DEFAULT 0,
                    paused_offset_seconds_start INTEGER DEFAULT 0,
                    FOREIGN KEY (session_id) REFERENCES study_sessions(id)
                );

                CREATE TABLE IF NOT EXISTS pomodoro_sessions (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id           INTEGER NOT NULL,
                    work_minutes      INTEGER DEFAULT 25,
                    break_minutes     INTEGER DEFAULT 5,
                    long_break_minutes INTEGER DEFAULT 15,
                    max_cycles         INTEGER DEFAULT 0,
                    current_phase     TEXT DEFAULT 'work',
                    phase_number      INTEGER DEFAULT 1,
                    total_cycles      INTEGER DEFAULT 0,
                    phase_started_at  TEXT NOT NULL,
                    active            INTEGER DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS checkins (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id    INTEGER NOT NULL,
                    date       TEXT NOT NULL,
                    studied    INTEGER DEFAULT 0,
                    note       TEXT,
                    UNIQUE(user_id, date)
                );

                CREATE TABLE IF NOT EXISTS rewards (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id     INTEGER NOT NULL,
                    name        TEXT NOT NULL,
                    description TEXT,
                    cost        INTEGER NOT NULL,
                    created_at  TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS redemptions (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id     INTEGER NOT NULL,
                    reward_id   INTEGER NOT NULL,
                    reward_name TEXT NOT NULL,
                    cost        INTEGER NOT NULL,
                    redeemed_at TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS point_transactions (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id    INTEGER NOT NULL,
                    delta      INTEGER NOT NULL,
                    reason     TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                );

                -- ── RPG SYSTEM TABLES ──────────────────────────────────────

                CREATE TABLE IF NOT EXISTS streak_freezes (
                    user_id     INTEGER PRIMARY KEY,
                    count       INTEGER DEFAULT 0,
                    earned_sat  INTEGER DEFAULT 0,
                    earned_sun  INTEGER DEFAULT 0,
                    week_start  TEXT
                );

                CREATE TABLE IF NOT EXISTS daily_quests (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id    INTEGER NOT NULL,
                    date       TEXT NOT NULL,
                    tier       INTEGER NOT NULL,
                    quest_key  TEXT NOT NULL,
                    progress   INTEGER DEFAULT 0,
                    target     INTEGER NOT NULL,
                    completed  INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS raid_bosses (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    hp           INTEGER NOT NULL,
                    hp_remaining INTEGER NOT NULL,
                    started_at   TEXT NOT NULL,
                    ends_at      TEXT NOT NULL,
                    ended_at     TEXT,
                    killed       INTEGER DEFAULT 0,
                    season       TEXT
                );

                CREATE TABLE IF NOT EXISTS raid_damage (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id    INTEGER NOT NULL,
                    boss_id    INTEGER NOT NULL,
                    raw_damage INTEGER DEFAULT 0,
                    hp_taken   INTEGER DEFAULT 0,
                    FOREIGN KEY (boss_id) REFERENCES raid_bosses(id)
                );

                CREATE TABLE IF NOT EXISTS badges (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id   INTEGER NOT NULL,
                    badge_key TEXT NOT NULL,
                    tier      INTEGER DEFAULT 1,
                    earned_at TEXT DEFAULT (datetime('now')),
                    UNIQUE(user_id, badge_key, tier)
                );

                CREATE TABLE IF NOT EXISTS badge_progress (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id       INTEGER NOT NULL,
                    badge_key     TEXT NOT NULL,
                    current_value REAL DEFAULT 0,
                    UNIQUE(user_id, badge_key)
                );

                CREATE TABLE IF NOT EXISTS cheers (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    sender_id   INTEGER NOT NULL,
                    receiver_id INTEGER NOT NULL,
                    created_at  TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS bounties (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_id     INTEGER NOT NULL,
                    target_id    INTEGER NOT NULL,
                    multiplier   REAL DEFAULT 2.0,
                    activated    INTEGER DEFAULT 0,
                    used         INTEGER DEFAULT 0,
                    created_at   TEXT DEFAULT (datetime('now')),
                    expires_at   TEXT,
                    activated_at TEXT,
                    buff_expires_at TEXT,
                    cost_coins   INTEGER DEFAULT 5,
                    refunded     INTEGER DEFAULT 0,
                    refunded_at  TEXT
                );

                CREATE TABLE IF NOT EXISTS beacons (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id      INTEGER NOT NULL,
                    channel_id   INTEGER,
                    activated_at TEXT DEFAULT (datetime('now')),
                    expires_at   TEXT NOT NULL,
                    active       INTEGER DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS group_pomo_lobbies (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    code            TEXT NOT NULL UNIQUE,
                    host_id         INTEGER NOT NULL,
                    subject         TEXT DEFAULT '',
                    work_mins       INTEGER DEFAULT 25,
                    break_mins      INTEGER DEFAULT 5,
                    long_break_mins INTEGER DEFAULT 15,
                    cycles          INTEGER DEFAULT 4,
                    current_phase   TEXT DEFAULT 'waiting',
                    current_cycle   INTEGER DEFAULT 0,
                    phase_started_at TEXT,
                    state           TEXT DEFAULT 'waiting',
                    source_guild_id INTEGER,
                    announce_channel_id INTEGER,
                    announce_message_id INTEGER,
                    created_at      TEXT DEFAULT (datetime('now')),
                    started_at      TEXT
                );

                CREATE TABLE IF NOT EXISTS group_pomo_members (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    lobby_id  INTEGER NOT NULL,
                    user_id   INTEGER NOT NULL,
                    joined_at TEXT DEFAULT (datetime('now')),
                    active    INTEGER DEFAULT 1,
                    FOREIGN KEY (lobby_id) REFERENCES group_pomo_lobbies(id),
                    UNIQUE(lobby_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS group_pomo_votes (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    lobby_id  INTEGER NOT NULL,
                    user_id   INTEGER NOT NULL,
                    created_at TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY (lobby_id) REFERENCES group_pomo_lobbies(id),
                    UNIQUE(lobby_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS active_effects (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id       INTEGER NOT NULL,
                    effect_type   TEXT NOT NULL,
                    multiplier    REAL DEFAULT 1.0,
                    data          TEXT,
                    activated_at  TEXT DEFAULT (datetime('now')),
                    expires_at    TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS gacha_history (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id     INTEGER NOT NULL,
                    tier        TEXT NOT NULL,
                    cost_coins  INTEGER NOT NULL,
                    result_pts  INTEGER NOT NULL,
                    is_jackpot  INTEGER DEFAULT 0,
                    created_at  TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS coin_overflow (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id    INTEGER NOT NULL,
                    amount     INTEGER NOT NULL,
                    created_at TEXT DEFAULT (datetime('now')),
                    expires_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS seasonal_history (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id         INTEGER NOT NULL,
                    season_key      TEXT NOT NULL,
                    rank            TEXT,
                    total_minutes   INTEGER DEFAULT 0,
                    points_at_reset INTEGER DEFAULT 0,
                    cheers_sent_at_reset INTEGER DEFAULT 0,
                    UNIQUE(user_id, season_key)
                );

                CREATE TABLE IF NOT EXISTS user_settings (
                    id      INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    key     TEXT NOT NULL,
                    value   TEXT,
                    UNIQUE(user_id, key)
                );

                CREATE TABLE IF NOT EXISTS inventory (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id     INTEGER NOT NULL,
                    item_type   TEXT NOT NULL,
                    item_key    TEXT NOT NULL,
                    quantity    INTEGER DEFAULT 1,
                    acquired_at TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS channel_config (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id     INTEGER NOT NULL,
                    channel_type TEXT NOT NULL,
                    channel_id   INTEGER NOT NULL,
                    UNIQUE(guild_id, channel_type)
                );

                CREATE TABLE IF NOT EXISTS outbox_messages (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_type TEXT NOT NULL, -- 'user' or 'channel'
                    target_id   INTEGER NOT NULL,
                    content     TEXT,
                    embed_json  TEXT,
                    kind        TEXT,
                    dedupe_key  TEXT,
                    settings_key TEXT, -- if set, re-check dm_<settings_key> at send time for user targets
                    status      TEXT DEFAULT 'pending', -- pending, sending, sent, failed
                    attempts    INTEGER DEFAULT 0,
                    last_error  TEXT,
                    not_before  TEXT,
                    sending_since TEXT,
                    created_at  TEXT DEFAULT (datetime('now')),
                    sent_at     TEXT
                );
            """)

            # Lightweight rollups (safe pruning): points spent, cheers sent, jackpot flag.
            for ddl in [
                "ALTER TABLE users ADD COLUMN total_points_spent INTEGER DEFAULT 0",
                "ALTER TABLE users ADD COLUMN cheers_sent_total INTEGER DEFAULT 0",
                "ALTER TABLE users ADD COLUMN has_gold_jackpot INTEGER DEFAULT 0",
            ]:
                try:
                    conn.execute(ddl)
                except sqlite3.OperationalError:
                    pass

            # Lightweight provenance: where a study session started (DM vs guild). Optional for legacy rows.
            try:
                conn.execute("ALTER TABLE study_sessions ADD COLUMN source_guild_id INTEGER")
            except sqlite3.OperationalError:
                pass

            migrations = [
                ("schedule_blocks", "days_of_week",         "TEXT"),
                ("pomodoro_sessions", "long_break_minutes", "INTEGER DEFAULT 15"),
                ("study_sessions", "is_paused",            "INTEGER DEFAULT 0"),
                ("study_sessions", "paused_at",            "TEXT"),
                ("study_sessions", "total_paused_seconds", "INTEGER DEFAULT 0"),
                ("study_sessions", "target_minutes",       "INTEGER"),
                ("study_sessions", "notes",                "TEXT"),
                ("study_sessions", "focus_rating",         "INTEGER"),
                ("study_sessions", "live_channel_id",      "INTEGER"),
                ("study_sessions", "live_message_id",      "INTEGER"),
                ("study_sessions", "live_kind", "TEXT DEFAULT 'ephemeral'"),
                ("schedule_blocks", "timezone", "TEXT DEFAULT '" + DEFAULT_TIMEZONE.replace("'", "''") + "'"),
                ("tasks", "source_message_url", "TEXT"),
                ("study_sessions", "motivation_sent",     "INTEGER DEFAULT 0"),
                ("study_sessions", "points_earned",        "INTEGER DEFAULT 0"),
                ("study_sessions", "is_group",             "INTEGER DEFAULT 0"),
                ("study_sessions", "group_lobby_id",       "INTEGER"),
                ("study_sessions", "tags",                "TEXT"),
                ("study_sessions", "allowed_member",      "INTEGER"),
                ("group_pomo_lobbies", "source_guild_id", "INTEGER"),
                ("group_pomo_lobbies", "announce_channel_id", "INTEGER"),
                ("group_pomo_lobbies", "announce_message_id", "INTEGER"),
                ("group_pomo_votes", "created_at", "TEXT DEFAULT (datetime('now'))"),
                ("tasks", "project_id",          "INTEGER"),
                ("tasks", "is_review",           "INTEGER DEFAULT 0"),
                ("tasks", "review_interval",     "INTEGER DEFAULT 1"),
                ("tasks", "review_number",       "INTEGER DEFAULT 1"),
                ("tasks", "next_review_date",    "TEXT"),
                ("users", "goal_mon",            "INTEGER"),
                ("users", "goal_tue",            "INTEGER"),
                ("users", "goal_wed",            "INTEGER"),
                ("users", "goal_thu",            "INTEGER"),
                ("users", "goal_fri",            "INTEGER"),
                ("users", "goal_sat",            "INTEGER"),
                ("users", "goal_sun",            "INTEGER"),
                ("users", "adaptive_goals",      "INTEGER DEFAULT 1"),
                ("users", "last_goal_suggestion","TEXT"),
                ("users", "checkin_enabled",     "INTEGER DEFAULT 0"),
                ("users", "checkin_hour",        "INTEGER DEFAULT 20"),
                ("pomodoro_sessions", "max_cycles", "INTEGER DEFAULT 0"),
                ("users", "coins",               "INTEGER DEFAULT 0"),
                ("users", "level",               "INTEGER DEFAULT 1"),
                ("users", "xp_current",          "INTEGER DEFAULT 0"),
                ("users", "prestige",            "INTEGER DEFAULT 0"),
                ("users", "banked_xp",           "INTEGER DEFAULT 0"),
                ("users", "total_converted_pts", "INTEGER DEFAULT 0"),
                ("users", "convert_count_today", "INTEGER DEFAULT 0"),
                ("users", "last_convert_date",   "TEXT"),
                ("users", "seasonal_minutes",    "INTEGER DEFAULT 0"),
                ("users", "seasonal_cheers_sent", "INTEGER DEFAULT 0"),
                ("users", "ghost_mode",          "INTEGER DEFAULT 0"),
                ("users", "block_cheers",        "INTEGER DEFAULT 0"),
                ("users", "featured_badges",     "TEXT DEFAULT '[]'"),
                ("users", "leaderboard_icon",    "TEXT"),
                ("users", "suffix_title",        "TEXT"),
                ("users", "role_color_hex",      "TEXT"),
                ("users", "weekend_bonus_date",  "TEXT"),
                ("users", "total_points_earned", "INTEGER DEFAULT 0"),
                ("users", "total_coins_spent", "INTEGER DEFAULT 0"),
                ("streak_freezes", "freeze_dates", "TEXT DEFAULT '[]'"),
                ("tasks", "user_task_num", "INTEGER DEFAULT 0"),
                ("bounties", "activated_at", "TEXT"),
                ("bounties", "buff_expires_at", "TEXT"),
                ("bounties", "cost_coins", "INTEGER DEFAULT 5"),
                ("bounties", "refunded", "INTEGER DEFAULT 0"),
                ("bounties", "refunded_at", "TEXT"),
                ("seasonal_history", "cheers_sent_at_reset", "INTEGER DEFAULT 0"),
                ("outbox_messages", "sending_since", "TEXT"),
                ("outbox_messages", "settings_key", "TEXT"),
                ("study_session_segments", "paused_offset_seconds_start", "INTEGER DEFAULT 0"),
            ]
            for table, col, col_def in migrations:
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_def}")
                except Exception:
                    pass

            # Segment indexes (best-effort).
            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_segments_user_started ON study_session_segments(user_id, started_at)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_segments_session_open ON study_session_segments(session_id, ended_at)")
            except Exception:
                pass

            try:
                conn.execute("""
                    UPDATE schedule_blocks
                    SET days_of_week = day_of_week
                    WHERE days_of_week IS NULL AND day_of_week IS NOT NULL
                """)
            except Exception:
                pass

        with self._conn() as conn:
            conn.executescript("""
                CREATE INDEX IF NOT EXISTS idx_sessions_user ON study_sessions(user_id);
                CREATE INDEX IF NOT EXISTS idx_sessions_ended ON study_sessions(ended_at);
                CREATE INDEX IF NOT EXISTS idx_sessions_active ON study_sessions(user_id, ended_at);
                CREATE INDEX IF NOT EXISTS idx_tasks_user ON tasks(user_id, completed);
                CREATE INDEX IF NOT EXISTS idx_tasks_review ON tasks(user_id, is_review, completed);
                CREATE INDEX IF NOT EXISTS idx_quests_user_date ON daily_quests(user_id, date);
                CREATE INDEX IF NOT EXISTS idx_reminders_pending ON reminders(user_id, sent, fire_at);
                CREATE INDEX IF NOT EXISTS idx_raid_dmg_boss ON raid_damage(boss_id);
                CREATE INDEX IF NOT EXISTS idx_bounties_target ON bounties(target_id, used);
                CREATE INDEX IF NOT EXISTS idx_beacons_active ON beacons(active, expires_at);
                CREATE INDEX IF NOT EXISTS idx_point_tx_user ON point_transactions(user_id);
                CREATE INDEX IF NOT EXISTS idx_gacha_user ON gacha_history(user_id);
                CREATE INDEX IF NOT EXISTS idx_outbox_pending ON outbox_messages(status, not_before, id);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_outbox_dedupe ON outbox_messages(dedupe_key) WHERE dedupe_key IS NOT NULL;
            """)

    # ── Simple user field setters (avoid raw _conn() in cogs) ─────────────────

    def set_ghost_mode(self, user_id: int, enabled: bool):
        with self._conn() as conn:
            conn.execute(
                "UPDATE users SET ghost_mode=? WHERE user_id=?",
                (1 if enabled else 0, user_id),
            )

    def set_block_cheers(self, user_id: int, enabled: bool):
        with self._conn() as conn:
            conn.execute(
                "UPDATE users SET block_cheers=? WHERE user_id=?",
                (1 if enabled else 0, user_id),
            )

    def set_weekend_bonus_date(self, user_id: int, date_iso: str):
        with self._conn() as conn:
            conn.execute(
                "UPDATE users SET weekend_bonus_date=? WHERE user_id=?",
                (date_iso, user_id),
            )

    # ── OUTBOX (reliable DMs/posts) ───────────────────────────────────────────

    def enqueue_outbox(
        self,
        *,
        target_type: str,
        target_id: int,
        content: str | None = None,
        embed: dict | None = None,
        embed_json: str | None = None,
        kind: str | None = None,
        dedupe_key: str | None = None,
        settings_key: str | None = None,
        not_before_iso: str | None = None,
    ) -> int:
        """Enqueue a message for reliable sending.

        If `dedupe_key` is provided, this is idempotent (same key inserts once).
        `embed` is a plain dict; the bot reconstructs a discord.Embed at send time.
        """
        if embed is not None and embed_json is not None:
            raise ValueError("Pass embed or embed_json, not both")
        if embed_json is not None and not isinstance(json.loads(embed_json), dict):
            raise ValueError("Outbox embed must be a JSON object")
        embed_json = embed_json if embed_json is not None else (json.dumps(embed) if embed is not None else None)
        with self._conn() as conn:
            if dedupe_key:
                # Idempotent insert.
                conn.execute(
                    """INSERT OR IGNORE INTO outbox_messages
                       (target_type, target_id, content, embed_json, kind, dedupe_key, settings_key, not_before)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (target_type, int(target_id), content, embed_json, kind, dedupe_key, settings_key, not_before_iso),
                )
                row = conn.execute(
                    "SELECT id FROM outbox_messages WHERE dedupe_key=?",
                    (dedupe_key,),
                ).fetchone()
                return int(row["id"]) if row else 0

            cur = conn.execute(
                """INSERT INTO outbox_messages
                   (target_type, target_id, content, embed_json, kind, dedupe_key, settings_key, not_before)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (target_type, int(target_id), content, embed_json, kind, None, settings_key, not_before_iso),
            )
            return int(cur.lastrowid)

    @staticmethod
    def _claim_outbox_batch_fallback(
        conn: sqlite3.Connection, now_iso: str, lim: int
    ) -> tuple[list[sqlite3.Row], bool]:
        """SELECT then UPDATE with ``status='pending'`` guard.

        Returns ``(rows, ok)``. If another writer races and ``rowcount != len(ids)``,
        rolls back and returns ``([], False)`` so callers never commit a partial claim.
        """
        sel = conn.execute(
            """SELECT * FROM outbox_messages
               WHERE status='pending' AND (not_before IS NULL OR not_before<=?)
               ORDER BY id ASC LIMIT ?""",
            (now_iso, lim),
        ).fetchall()
        if not sel:
            return [], True
        ids = [int(r["id"]) for r in sel]
        placeholders = ",".join("?" * len(ids))
        upd = conn.execute(
            f"""UPDATE outbox_messages SET status='sending', sending_since=?
                WHERE id IN ({placeholders}) AND status='pending'""",
            (now_iso, *ids),
        )
        if upd.rowcount != len(ids):
            conn.rollback()
            return [], False
        rows = conn.execute(
            f"SELECT * FROM outbox_messages WHERE id IN ({placeholders})",
            ids,
        ).fetchall()
        return rows, True

    def claim_outbox_batch(self, *, limit: int = 25) -> list[dict]:
        """Atomically claim pending messages for sending (multi-pump safe).

        Uses ``UPDATE ... RETURNING`` inside ``BEGIN IMMEDIATE`` when supported;
        otherwise falls back to a guarded SELECT+UPDATE in one transaction.
        If a batch cannot be claimed atomically (partial ``rowcount``), returns
        ``[]`` and leaves rows ``pending`` — never delivers an inconsistent batch.
        """
        now_iso = _utcnow_naive().isoformat()
        lim = max(1, int(limit))
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows: list[sqlite3.Row]
            try:
                cur = conn.execute(
                    """UPDATE outbox_messages SET status='sending', sending_since=?
                       WHERE id IN (
                         SELECT id FROM (
                           SELECT id FROM outbox_messages
                           WHERE status='pending'
                             AND (not_before IS NULL OR not_before<=?)
                           ORDER BY id ASC
                           LIMIT ?
                         ) AS claim_candidates
                       ) AND status='pending'
                       RETURNING *""",
                    (now_iso, now_iso, lim),
                )
                rows = cur.fetchall()
            except sqlite3.OperationalError:
                conn.rollback()
                conn.execute("BEGIN IMMEDIATE")
                rows, ok = Database._claim_outbox_batch_fallback(conn, now_iso, lim)
                if not ok:
                    return []
            conn.commit()
            return [dict(r) for r in rows]
        except BaseException:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def mark_outbox_sent(self, msg_id: int) -> None:
        now_iso = _utcnow_naive().isoformat()
        with self._conn() as conn:
            conn.execute(
                "UPDATE outbox_messages SET status='sent', sent_at=?, sending_since=NULL WHERE id=?",
                (now_iso, int(msg_id)),
            )

    def mark_outbox_failed(self, msg_id: int, *, error: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE outbox_messages SET status='failed', last_error=?, sending_since=NULL WHERE id=?",
                (str(error)[:500], int(msg_id)),
            )

    def retry_outbox_later(self, msg_id: int, *, error: str, delay_seconds: int) -> None:
        now = _utcnow_naive()
        not_before = (now + timedelta(seconds=int(delay_seconds))).isoformat()
        with self._conn() as conn:
            conn.execute(
                """UPDATE outbox_messages
                   SET status='pending', attempts=attempts+1, last_error=?, not_before=?, sending_since=NULL
                   WHERE id=?""",
                (str(error)[:500], not_before, int(msg_id)),
            )

    def defer_outbox_later(self, msg_id: int, *, reason: str, delay_seconds: int) -> None:
        """Requeue later without incrementing attempts (policy deferral, not a send failure)."""
        now = _utcnow_naive()
        not_before = (now + timedelta(seconds=int(delay_seconds))).isoformat()
        with self._conn() as conn:
            conn.execute(
                """UPDATE outbox_messages
                   SET status='pending', last_error=?, not_before=?, sending_since=NULL
                   WHERE id=?""",
                (str(reason)[:500], not_before, int(msg_id)),
            )

    def requeue_stale_sending_outbox(self, *, stale_after_seconds: int = 300) -> int:
        """Requeue messages stuck in 'sending' (e.g., crash mid-send). Returns count re-queued."""
        cutoff = (_utcnow_naive() - timedelta(seconds=int(stale_after_seconds))).isoformat()
        with self._conn() as conn:
            res = conn.execute(
                """UPDATE outbox_messages
                   SET status='pending', sending_since=NULL
                   WHERE status='sending' AND sending_since IS NOT NULL AND sending_since<=?""",
                (cutoff,),
            )
            return int(res.rowcount or 0)

    def cleanup_outbox(self, *, keep_sent_days: int = 14, keep_failed_days: int = 30) -> int:
        """Delete old sent/failed outbox rows to keep DB small. Returns deleted row count."""
        now = _utcnow_naive()
        sent_cutoff = (now - timedelta(days=int(keep_sent_days))).isoformat()
        fail_cutoff = (now - timedelta(days=int(keep_failed_days))).isoformat()
        with self._conn() as conn:
            a = conn.execute(
                "DELETE FROM outbox_messages WHERE status='sent' AND sent_at IS NOT NULL AND sent_at<=?",
                (sent_cutoff,),
            ).rowcount or 0
            b = conn.execute(
                "DELETE FROM outbox_messages WHERE status='failed' AND created_at IS NOT NULL AND created_at<=?",
                (fail_cutoff,),
            ).rowcount or 0
            return int(a + b)

    # ── RAID helpers ──────────────────────────────────────────────────────────

    def get_last_ended_boss(self) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM raid_bosses WHERE ended_at IS NOT NULL ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None

    # ── USER ──────────────────────────────────────────────────────────────────

    def ensure_user(self, user_id: int, username: str = "") -> dict:
        with self._conn() as conn:
            conn.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?,?)", (user_id, username))
            if username:
                conn.execute("UPDATE users SET username=? WHERE user_id=?", (username, user_id))
            return dict(conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone())

    def get_user(self, user_id: int) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
            return dict(row) if row else None

    def get_all_users(self) -> list[dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM users").fetchall()]

    def get_users_batch(self, user_ids: list[int]) -> dict[int, dict]:
        if not user_ids:
            return {}
        placeholders = ",".join("?" for _ in user_ids)
        with self._conn() as conn:
            rows = conn.execute(f"SELECT * FROM users WHERE user_id IN ({placeholders})", user_ids).fetchall()
            return {r["user_id"]: dict(r) for r in rows}

    def add_points(self, user_id: int, delta: int, reason: str = "", track_earned: bool = True):
        with self._conn() as conn:
            if track_earned and delta > 0:
                conn.execute(
                    "UPDATE users SET points=points+?, total_points_earned=total_points_earned+? WHERE user_id=?",
                    (delta, delta, user_id)
                )
            elif delta < 0:
                conn.execute(
                    "UPDATE users SET points=points+?, total_points_spent=total_points_spent+? WHERE user_id=?",
                    (delta, -delta, user_id)
                )
            else:
                conn.execute("UPDATE users SET points=points+? WHERE user_id=?", (delta, user_id))
            conn.execute(
                "INSERT INTO point_transactions (user_id, delta, reason) VALUES (?,?,?)",
                (user_id, delta, reason)
            )

    def deduct_points(self, user_id: int, amount: int) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE users SET points=points-? WHERE user_id=? AND points>=?",
                (amount, user_id, amount)
            )
            if cur.rowcount == 0:
                return False
            conn.execute(
                "INSERT INTO point_transactions (user_id, delta, reason) VALUES (?,?,?)",
                (user_id, -amount, "Reward redeemed")
            )
        return True

    def set_daily_goal(self, user_id: int, minutes: int):
        with self._conn() as conn:
            conn.execute("UPDATE users SET daily_goal_minutes=? WHERE user_id=?", (minutes, user_id))

    def set_day_goal(self, user_id: int, day: str, minutes: Optional[int]):
        col = f"goal_{day}"
        with self._conn() as conn:
            conn.execute(f"UPDATE users SET {col}=? WHERE user_id=?", (minutes, user_id))

    def get_today_goal(self, user_id: int) -> int:
        day_key = goal_override_key_for_date(datetime.now(EST).date())
        user = self.get_user(user_id)
        if not user:
            return 60
        override = user.get(f"goal_{day_key}")
        return override if override is not None else user["daily_goal_minutes"]

    def set_checkin_settings(self, user_id: int, enabled: bool, hour: int):
        with self._conn() as conn:
            conn.execute(
                "UPDATE users SET checkin_enabled=?, checkin_hour=? WHERE user_id=?",
                (int(enabled), hour, user_id)
            )

    def set_adaptive_goals(self, user_id: int, enabled: bool):
        with self._conn() as conn:
            conn.execute("UPDATE users SET adaptive_goals=? WHERE user_id=?", (int(enabled), user_id))

    def set_last_goal_suggestion(self, user_id: int, date_str: str):
        with self._conn() as conn:
            conn.execute("UPDATE users SET last_goal_suggestion=? WHERE user_id=?", (date_str, user_id))

    def add_daily_minutes(self, user_id: int, minutes: int):
        with self._conn() as conn:
            conn.execute(
                "UPDATE users SET daily_minutes_today=daily_minutes_today+? WHERE user_id=?",
                (minutes, user_id)
            )

    def get_daily_minutes_today(self, user_id: int) -> int:
        user = self.get_user(user_id)
        return user["daily_minutes_today"] if user else 0

    def reset_daily_progress(self):
        with self._conn() as conn:
            conn.execute("UPDATE users SET daily_minutes_today=0, convert_count_today=0")
            if datetime.now(EST).weekday() == 0:
                conn.execute("UPDATE streak_freezes SET earned_sat=0, earned_sun=0")

    # ── ECONOMY (Coins, Levels, Prestige) ─────────────────────────────────────

    def get_prestige(self, user_id: int) -> int:
        user = self.get_user(user_id)
        return user["prestige"] if user else 0

    def get_coin_cap(self, user_id: int) -> int:
        return P4_COIN_CAP if self.get_prestige(user_id) >= 4 else DEFAULT_COIN_CAP

    def get_convert_cost(self, user_id: int) -> int:
        return P1_CONVERT_COST if self.get_prestige(user_id) >= 1 else DEFAULT_CONVERT_COST

    def get_convert_limit(self, user_id: int) -> int:
        p = self.get_prestige(user_id)
        if p >= 4:
            return 999999
        if p >= 3:
            return 3
        return 1

    def get_prestige_perks(self, user_id: int) -> set:
        p = self.get_prestige(user_id)
        perks = set()
        if p >= 1:
            perks.update(["efficient_mint", "resilient"])
        if p >= 2:
            perks.update(["scavenger", "tactician"])
        if p >= 3:
            perks.update(["industrialist", "time_lord"])
        if p >= 4:
            perks.update(["the_vault", "the_benefactor"])
        if p >= 5:
            perks.update(["master_alchemist", "grandmaster_aura"])
        return perks

    def add_xp(self, user_id: int, amount: int) -> dict:
        """Add XP and handle level-ups. Returns dict with level_ups and coins_earned."""
        user = self.get_user(user_id)
        if not user:
            return {"level_ups": [], "coins_earned": 0}

        old_level = user["level"]
        xp = user["xp_current"] + amount
        level = old_level
        coins_earned = 0
        level_ups = []
        banked = user["banked_xp"]

        while level < MAX_LEVEL:
            needed = xp_for_level(level)
            if xp >= needed:
                xp -= needed
                level += 1
                level_ups.append(level)
                if level % COIN_EVERY_N_LEVELS == 0:
                    coins_earned += 1
            else:
                break

        if level >= MAX_LEVEL:
            banked += xp
            max_bank = xp_for_level(MAX_LEVEL) * (MAX_LEVEL + 1) if user["prestige"] < 5 else 999999999
            banked = min(banked, max_bank)
            xp = 0
            level = MAX_LEVEL

        with self._conn() as conn:
            conn.execute(
                """UPDATE users SET level=?, xp_current=?, total_xp=total_xp+?, banked_xp=?
                   WHERE user_id=?""",
                (level, xp, amount, banked, user_id)
            )
            if coins_earned > 0:
                self._add_coins_internal(conn, user_id, coins_earned)

        return {"level_ups": level_ups, "coins_earned": coins_earned, "new_level": level}

    def _add_coins_internal(self, conn, user_id: int, amount: int) -> dict:
        """Apply coin grant inside an open connection. Returns {direct, overflow} to wallet vs overflow pool."""
        row = conn.execute("SELECT coins, prestige FROM users WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            return {"direct": 0, "overflow": amount}
        current = row["coins"]
        cap = P4_COIN_CAP if row["prestige"] >= 4 else DEFAULT_COIN_CAP
        space = cap - current
        direct = min(amount, max(space, 0))
        overflow = amount - direct
        if direct > 0:
            conn.execute("UPDATE users SET coins=coins+? WHERE user_id=?", (direct, user_id))
        if overflow > 0:
            expires = (_utcnow_naive() + timedelta(days=OVERFLOW_DAYS)).isoformat()
            conn.execute(
                "INSERT INTO coin_overflow (user_id, amount, expires_at) VALUES (?,?,?)",
                (user_id, overflow, expires)
            )
        return {"direct": direct, "overflow": overflow}

    def add_coins(self, user_id: int, amount: int) -> dict:
        """Add coins, respecting cap. Excess goes to overflow. Returns {direct, overflow}."""
        with self._conn() as conn:
            row = conn.execute("SELECT coins, prestige FROM users WHERE user_id=?", (user_id,)).fetchone()
            if not row:
                return {"direct": 0, "overflow": 0}
            cap = P4_COIN_CAP if row["prestige"] >= 4 else DEFAULT_COIN_CAP
            space = cap - row["coins"]
            direct = min(amount, max(space, 0))
            over = amount - direct
            if direct > 0:
                conn.execute("UPDATE users SET coins=coins+? WHERE user_id=?", (direct, user_id))
            if over > 0:
                expires = (_utcnow_naive() + timedelta(days=OVERFLOW_DAYS)).isoformat()
                conn.execute(
                    "INSERT INTO coin_overflow (user_id, amount, expires_at) VALUES (?,?,?)",
                    (user_id, over, expires)
                )
        return {"direct": direct, "overflow": over}

    def remove_coins(self, user_id: int, amount: int) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE users SET coins=coins-?, total_coins_spent=COALESCE(total_coins_spent,0)+? "
                "WHERE user_id=? AND coins>=?",
                (amount, amount, user_id, amount),
            )
            return cur.rowcount > 0

    def get_overflow(self, user_id: int) -> list[dict]:
        now = _utcnow_naive().isoformat()
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM coin_overflow WHERE user_id=? AND expires_at>?",
                (user_id, now)
            ).fetchall()]

    def expire_overflow(self):
        now = _utcnow_naive().isoformat()
        with self._conn() as conn:
            conn.execute("DELETE FROM coin_overflow WHERE expires_at<=?", (now,))

    def convert_points(self, user_id: int) -> Optional[dict]:
        user = self.get_user(user_id)
        if not user:
            return None
        cost = self.get_convert_cost(user_id)
        limit = self.get_convert_limit(user_id)
        today = datetime.now(EST).date().isoformat()

        if user.get("last_convert_date") == today and user.get("convert_count_today", 0) >= limit:
            return {"error": "daily_limit"}
        if user["points"] < cost:
            return {"error": "insufficient_points"}

        last = user.get("last_convert_date")
        yesterday = (datetime.now(EST).date() - timedelta(days=1)).isoformat()
        prev_streak = int(self.get_badge_progress(user_id, "the_broker_streak"))
        if last == today:
            new_broker_streak = prev_streak
        elif last == yesterday:
            new_broker_streak = prev_streak + 1 if prev_streak > 0 else 2
        else:
            new_broker_streak = 1

        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE users SET points=points-?, total_converted_pts=total_converted_pts+?, "
                "convert_count_today=CASE WHEN last_convert_date=? THEN convert_count_today+1 ELSE 1 END, "
                "last_convert_date=? WHERE user_id=? AND points>=?",
                (cost, cost, today, today, user_id, cost)
            )
            if cur.rowcount == 0:
                return {"error": "insufficient_points"}
            conn.execute(
                "INSERT INTO point_transactions (user_id, delta, reason) VALUES (?,?,?)",
                (user_id, -cost, "Point conversion")
            )
            split = self._add_coins_internal(conn, user_id, 1)

        if last != today:
            self.set_badge_progress(user_id, "the_broker_streak", float(new_broker_streak))

        u = self.get_user(user_id)
        return {
            "cost": cost,
            "coins_after": u["coins"] if u else 0,
            "coin_direct": split["direct"],
            "coin_overflow": split["overflow"],
        }

    def prestige_up(self, user_id: int) -> Optional[dict]:
        user = self.get_user(user_id)
        if not user or user["level"] < MAX_LEVEL or user["prestige"] >= 5:
            return None
        new_prestige = user["prestige"] + 1
        with self._conn() as conn:
            conn.execute(
                "UPDATE users SET prestige=?, level=1, xp_current=0, banked_xp=0 WHERE user_id=?",
                (new_prestige, user_id)
            )
        return {"new_prestige": new_prestige, "perks": self.get_prestige_perks(user_id)}

    # ── STREAK LOGIC ──────────────────────────────────────────────────────────

    def recalculate_streak(self, user_id: int) -> int:
        today = datetime.now(EST).date()
        user = self.get_user(user_id)
        if not user:
            return 0

        history = self.get_checkin_history_with_study(user_id, days=400)
        history_map = {h["date"]: h for h in history}

        streak = 0
        current = today

        for _ in range(400):
            curr_str = current.isoformat()
            weekday = current.weekday()
            is_weekend = weekday >= 5

            day_data = history_map.get(curr_str)
            studied = day_data["studied"] if day_data else False

            day_key = goal_override_key_for_date(current)
            override = user.get(f"goal_{day_key}")
            is_rest_day = (override is not None and override == 0)

            if current == today and not studied and not is_rest_day:
                current -= timedelta(days=1)
                continue

            if studied:
                streak += 1
            elif is_weekend or is_rest_day:
                pass
            else:
                freeze_used = self._check_freeze_for_date(user_id, current)
                if freeze_used:
                    pass
                else:
                    break

            current -= timedelta(days=1)

        with self._conn() as conn:
            conn.execute(
                "UPDATE users SET streak = ?, longest_streak = MAX(longest_streak, ?) WHERE user_id = ?",
                (streak, streak, user_id)
            )
        return streak

    def _check_freeze_for_date(self, user_id: int, date) -> bool:
        """Check if a freeze covers this missed date. Uses a tracking field (freeze_dates)
        so that recalculate_streak is idempotent — repeated calls don't re-consume."""
        date_str = date.isoformat() if hasattr(date, 'isoformat') else str(date)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT freeze_dates FROM streak_freezes WHERE user_id=?", (user_id,)
            ).fetchone()
            if not row:
                return False
            used_dates = json.loads(row["freeze_dates"] or "[]")
            if date_str in used_dates:
                return True
            current_count = conn.execute(
                "SELECT count FROM streak_freezes WHERE user_id=?", (user_id,)
            ).fetchone()["count"]
            if current_count <= 0:
                return False
            used_dates.append(date_str)
            conn.execute(
                "UPDATE streak_freezes SET count=count-1, freeze_dates=? WHERE user_id=? AND count>0",
                (json.dumps(used_dates), user_id)
            )
        return True

    def update_streaks(self):
        users = self.get_all_users()
        for u in users:
            active = self.get_active_session(u["user_id"])
            if active:
                continue
            self.recalculate_streak(u["user_id"])

    # ── STREAK FREEZES ────────────────────────────────────────────────────────

    def get_freezes(self, user_id: int) -> dict:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM streak_freezes WHERE user_id=?", (user_id,)).fetchone()
            if row:
                return dict(row)
            conn.execute(
                "INSERT INTO streak_freezes (user_id, count, earned_sat, earned_sun, freeze_dates) VALUES (?,0,0,0,'[]')",
                (user_id,)
            )
            return {"user_id": user_id, "count": 0, "earned_sat": 0, "earned_sun": 0, "week_start": None, "freeze_dates": "[]"}

    def earn_weekend_freeze(self, user_id: int, day: str) -> bool:
        """day is 'sat' or 'sun'. Returns True if freeze was earned."""
        f = self.get_freezes(user_id)
        col = f"earned_{day}"
        if f.get(col, 0):
            return False
        if f["count"] >= 2:
            return False
        with self._conn() as conn:
            conn.execute(
                f"UPDATE streak_freezes SET count=count+1, {col}=1 WHERE user_id=?",
                (user_id,)
            )
        return True

    def consume_freeze(self, user_id: int) -> bool:
        f = self.get_freezes(user_id)
        if f["count"] <= 0:
            return False
        with self._conn() as conn:
            conn.execute(
                "UPDATE streak_freezes SET count=count-1 WHERE user_id=? AND count>0",
                (user_id,)
            )
        return True

    def add_freeze(self, user_id: int) -> bool:
        """Add a freeze (from Emergency Save). Returns False if already at max 2."""
        f = self.get_freezes(user_id)
        if f["count"] >= 2:
            return False
        with self._conn() as conn:
            conn.execute(
                "UPDATE streak_freezes SET count=count+1 WHERE user_id=?",
                (user_id,)
            )
        return True

    def wipe_all_freezes(self):
        with self._conn() as conn:
            conn.execute("UPDATE streak_freezes SET count=0, earned_sat=0, earned_sun=0, freeze_dates='[]'")

    # ── CHECK-INS ─────────────────────────────────────────────────────────────

    def record_checkin(self, user_id: int, date: str, studied: bool, note: str = "", restore_streak: bool = False):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO checkins (user_id, date, studied, note) VALUES (?,?,?,?)",
                (user_id, date, int(studied), note)
            )
        if restore_streak and studied:
            self.recalculate_streak(user_id)

    def get_checkin(self, user_id: int, date: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM checkins WHERE user_id=? AND date=?", (user_id, date)).fetchone()
            return dict(row) if row else None

    def get_checkin_history(self, user_id: int, days: int = 14) -> list[dict]:
        cutoff = (datetime.now(EST).date() - timedelta(days=days)).isoformat()
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM checkins WHERE user_id=? AND date>=? ORDER BY date DESC",
                (user_id, cutoff)
            ).fetchall()]

    def get_checkin_history_with_study(self, user_id: int, days: int = 14) -> list[dict]:
        cutoff_est = (datetime.now(EST).date() - timedelta(days=days)).isoformat()
        today_est = datetime.now(EST).date().isoformat()
        with self._conn() as conn:
            checkins = {
                r["date"]: dict(r) for r in conn.execute(
                    "SELECT * FROM checkins WHERE user_id=? AND date>=?",
                    (user_id, cutoff_est)
                ).fetchall()
            }
            session_rows = conn.execute(
                "SELECT started_at, duration_minutes FROM study_sessions WHERE user_id=? AND ended_at IS NOT NULL",
                (user_id,)
            ).fetchall()
        session_minutes: dict[str, int] = {}
        for r in session_rows:
            try:
                est_date = _stored_to_est_date(r["started_at"])
                if est_date >= cutoff_est:
                    session_minutes[est_date] = session_minutes.get(est_date, 0) + (r["duration_minutes"] or 0)
            except Exception:
                pass
        result = []
        for i in range(days):
            day = (datetime.now(EST).date() - timedelta(days=days - 1 - i))
            iso = day.isoformat()
            mins = session_minutes.get(iso, 0)
            ci = checkins.get(iso)
            result.append({
                "date":     iso,
                "studied":  (bool(ci["studied"]) if ci else False) or mins > 0,
                "minutes":  mins,
                "note":     ci["note"] if ci else "",
                "is_today": iso == today_est,
            })
        return result

    def get_checkin_users_for_hour(self, hour: int) -> list[dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM users WHERE checkin_enabled=1 AND checkin_hour=?", (hour,)
            ).fetchall()]

    def get_goal_hit_streak(self, user_id: int, days: int = 7) -> list[bool]:
        results = []
        user = self.get_user(user_id)
        if not user:
            return [False] * days
        for i in range(days - 1, -1, -1):
            day = (datetime.now(EST).date() - timedelta(days=i))
            day_key = goal_override_key_for_date(day)
            override = user.get(f"goal_{day_key}")
            if override == 0:
                results.append(True)
                continue
            goal = override if override is not None else user["daily_goal_minutes"]
            mins = self.get_study_minutes_on_date(user_id, day.isoformat())
            results.append(mins >= goal)
        return results

    def get_study_minutes_on_date(self, user_id: int, est_date_str: str) -> int:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT started_at, duration_minutes FROM study_sessions WHERE user_id=? AND ended_at IS NOT NULL",
                (user_id,)
            ).fetchall()
        total = 0
        for r in rows:
            try:
                if _stored_to_est_date(r["started_at"]) == est_date_str:
                    total += r["duration_minutes"] or 0
            except Exception:
                pass
        return total

    def get_point_history(self, user_id: int, limit: int = 10) -> list[dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM point_transactions WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit)
            ).fetchall()]

    # ── REMINDERS ─────────────────────────────────────────────────────────────

    def add_reminder(self, user_id: int, message: str, fire_at: datetime) -> int:
        if fire_at.tzinfo is not None:
            fire_at = fire_at.astimezone(timezone.utc).replace(tzinfo=None)
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO reminders (user_id, message, fire_at) VALUES (?,?,?)",
                (user_id, message, fire_at.isoformat())
            )
            return cur.lastrowid

    def get_user_reminders(self, user_id: int) -> list[dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM reminders WHERE user_id=? AND sent=0 ORDER BY fire_at ASC", (user_id,)
            ).fetchall()]

    def get_all_pending_reminders(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM reminders WHERE sent=0").fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["fire_at"] = _parse_stored(d["fire_at"]).replace(tzinfo=timezone.utc)
                result.append(d)
            return result

    def delete_reminder(self, reminder_id: int, user_id: int) -> bool:
        with self._conn() as conn:
            return conn.execute(
                "DELETE FROM reminders WHERE id=? AND user_id=?", (reminder_id, user_id)
            ).rowcount > 0

    def mark_reminder_sent(self, reminder_id: int):
        with self._conn() as conn:
            conn.execute("UPDATE reminders SET sent=1 WHERE id=?", (reminder_id,))

    # ── SCHEDULE ──────────────────────────────────────────────────────────────

    DAYS_ORDER = {
        "monday":0,"tuesday":1,"wednesday":2,"thursday":3,
        "friday":4,"saturday":5,"sunday":6
    }
    ALL_DAYS = list(DAYS_ORDER.keys())

    def add_schedule_block(self, user_id: int, subject: str, days_of_week: str,
                           hour: int, minute: int, duration: int, *, timezone_name: str = DEFAULT_TIMEZONE) -> int:
        from services.scheduling import valid_timezone
        timezone_name = valid_timezone(timezone_name)
        with self._conn() as conn:
            if conn.execute("SELECT COUNT(*) FROM schedule_blocks WHERE user_id=?", (user_id,)).fetchone()[0] >= 25:
                raise ValueError("Max 25 schedule blocks reached.")
            cur = conn.execute(
                """INSERT INTO schedule_blocks
                   (user_id, subject, days_of_week, hour, minute, duration_minutes, timezone)
                   VALUES (?,?,?,?,?,?,?)""",
                (user_id, subject, days_of_week.lower(), hour, minute, duration, timezone_name)
            )
            return cur.lastrowid

    def get_user_schedule(self, user_id: int) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM schedule_blocks WHERE user_id=?", (user_id,)
            ).fetchall()
            result = [dict(r) for r in rows]
            def sort_key(b):
                days = [self.DAYS_ORDER.get(d.strip(), 7) for d in (b["days_of_week"] or "").split(",")]
                return (min(days) if days else 7, b["hour"], b["minute"])
            result.sort(key=sort_key)
            return result

    def delete_schedule_block(self, block_id: int, user_id: int) -> bool:
        with self._conn() as conn:
            return conn.execute(
                "DELETE FROM schedule_blocks WHERE id=? AND user_id=?", (block_id, user_id)
            ).rowcount > 0

    def get_schedule_blocks_at(self, day: str, hour: int, minute: int) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM schedule_blocks WHERE hour=? AND minute=?",
                (hour, minute)
            ).fetchall()
            results = []
            for r in rows:
                block = dict(r)
                days = [d.strip() for d in (block.get("days_of_week") or "").split(",")]
                if day.lower() in days:
                    results.append(block)
            return results

    def get_all_schedule_blocks(self) -> list[dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM schedule_blocks").fetchall()]

    # ── PROJECTS ──────────────────────────────────────────────────────────────

    def add_project(self, user_id: int, name: str, description: str, due_date: Optional[str]) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO projects (user_id, name, description, due_date) VALUES (?,?,?,?)",
                (user_id, name, description, due_date)
            )
            return cur.lastrowid

    def get_projects(self, user_id: int, include_done: bool = False) -> list[dict]:
        with self._conn() as conn:
            q = "SELECT * FROM projects WHERE user_id=?"
            if not include_done:
                q += " AND completed=0"
            q += " ORDER BY due_date ASC, created_at ASC"
            return [dict(r) for r in conn.execute(q, (user_id,)).fetchall()]

    def get_project(self, project_id: int, user_id: int) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM projects WHERE id=? AND user_id=?", (project_id, user_id)).fetchone()
            return dict(row) if row else None

    def complete_project(self, project_id: int, user_id: int) -> bool:
        with self._conn() as conn:
            return conn.execute(
                "UPDATE projects SET completed=1 WHERE id=? AND user_id=?", (project_id, user_id)
            ).rowcount > 0

    def delete_project(self, project_id: int, user_id: int) -> bool:
        with self._conn() as conn:
            return conn.execute("DELETE FROM projects WHERE id=? AND user_id=?", (project_id, user_id)).rowcount > 0

    def get_project_task_stats(self, project_id: int) -> dict:
        with self._conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM tasks WHERE project_id=?", (project_id,)).fetchone()[0]
            done = conn.execute("SELECT COUNT(*) FROM tasks WHERE project_id=? AND completed=1", (project_id,)).fetchone()[0]
            pts = conn.execute("SELECT COALESCE(SUM(points),0) FROM tasks WHERE project_id=? AND completed=0", (project_id,)).fetchone()[0]
            return {"total": total, "done": done, "remaining": total - done, "pts_remaining": pts}

    # ── TASKS ─────────────────────────────────────────────────────────────────

    def _next_user_task_num_pending(self, conn, user_id: int) -> int:
        """Next display # for new pending tasks. Uses only incomplete rows so when your list is empty, the next add is #1."""
        max_num = conn.execute(
            "SELECT COALESCE(MAX(user_task_num), 0) FROM tasks WHERE user_id=? AND completed=0",
            (user_id,),
        ).fetchone()[0]
        return max_num + 1

    def add_task(self, user_id: int, title: str, description: str,
                 points: int, priority: str, due_date: Optional[str],
                 project_id: Optional[int] = None,
                 is_review: bool = False, review_interval: int = 1, *, source_message_url: str | None = None) -> int:
        next_review = due_date if is_review else None
        with self._conn() as conn:
            next_num = self._next_user_task_num_pending(conn, user_id)
            conn.execute(
                """INSERT INTO tasks
                   (user_id, user_task_num, project_id, title, description, points, priority, due_date,
                    is_review, review_interval, next_review_date, source_message_url)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (user_id, next_num, project_id, title, description, points, priority, due_date,
                 int(is_review), review_interval, next_review, source_message_url)
            )
            return next_num

    def resolve_task_num(self, user_id: int, user_task_num: int) -> Optional[int]:
        """Map user-facing # → row id. Prefers pending tasks so numbers can restart at #1 while history keeps old rows."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id FROM tasks WHERE user_id=? AND user_task_num=? AND completed=0",
                (user_id, user_task_num),
            ).fetchone()
            if row:
                return row["id"]
            row = conn.execute(
                "SELECT id FROM tasks WHERE user_id=? AND user_task_num=? AND completed=1",
                (user_id, user_task_num),
            ).fetchone()
            return row["id"] if row else None

    def get_user_tasks(self, user_id: int, include_done: bool = False,
                       project_id: Optional[int] = None) -> list[dict]:
        with self._conn() as conn:
            q = """SELECT t.*, p.name as project_name
                   FROM tasks t LEFT JOIN projects p ON t.project_id=p.id
                   WHERE t.user_id=?"""
            params: list = [user_id]
            if not include_done:
                q += " AND t.completed=0"
            if project_id is not None:
                q += " AND t.project_id=?"
                params.append(project_id)
            q += " ORDER BY CASE t.priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, t.due_date ASC, t.created_at"
            return [dict(r) for r in conn.execute(q, params).fetchall()]

    def get_due_reviews(self, user_id: int) -> list[dict]:
        today = datetime.now(EST).date().isoformat()
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT t.*, p.name as project_name FROM tasks t
                   LEFT JOIN projects p ON t.project_id=p.id
                   WHERE t.user_id=? AND t.is_review=1 AND t.completed=0
                   AND (t.next_review_date IS NULL OR t.next_review_date<=?)
                   ORDER BY t.next_review_date ASC""",
                (user_id, today)
            ).fetchall()
            return [dict(r) for r in rows]

    def complete_task(self, task_id: int, user_id: int) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                """SELECT t.*, p.name as project_name FROM tasks t
                   LEFT JOIN projects p ON t.project_id=p.id
                   WHERE t.id=? AND t.user_id=? AND t.completed=0""",
                (task_id, user_id)
            ).fetchone()
            if not row:
                return None
            task = dict(row)
            conn.execute(
                "UPDATE tasks SET completed=1, completed_at=? WHERE id=?",
                (_utcnow_naive().isoformat(), task_id)
            )
            return task

    def complete_task_with_review(self, task_id: int, user_id: int) -> Optional[dict]:
        now_iso = _utcnow_naive().isoformat()
        with self._conn() as conn:
            row = conn.execute(
                """SELECT t.*, p.name as project_name FROM tasks t
                   LEFT JOIN projects p ON t.project_id=p.id
                   WHERE t.id=? AND t.user_id=? AND t.completed=0""",
                (task_id, user_id)
            ).fetchone()
            if not row:
                return None

            task = dict(row)
            conn.execute(
                "UPDATE tasks SET completed=1, completed_at=? WHERE id=?",
                (now_iso, task_id)
            )

            if task.get("is_review"):
                review_num = task.get("review_number") or 1
                base_pts = min(10 * review_num, 50)
                base_xp = min(20 * review_num, 100)

                today_est = datetime.now(EST).date()
                srs_start_utc = datetime.combine(today_est, datetime.min.time(), tzinfo=EST).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                srs_end_utc = datetime.combine(today_est + timedelta(days=1), datetime.min.time(), tzinfo=EST).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                today_srs_count = conn.execute(
                    """SELECT COUNT(*) FROM tasks WHERE user_id=? AND is_review=1
                       AND completed=1 AND completed_at>=? AND completed_at<?""",
                    (user_id, srs_start_utc, srs_end_utc)
                ).fetchone()[0]

                pts_award = base_pts if today_srs_count <= 10 else 10
                xp_award = base_xp if today_srs_count <= 10 else 0
            else:
                pts_award = task["points"]
                xp_award = 0

            conn.execute(
                "UPDATE users SET points=points+?, total_points_earned=total_points_earned+? WHERE user_id=?",
                (pts_award, pts_award, user_id)
            )
            reason_prefix = "SRS Review" if task.get("is_review") else "Task Complete"
            conn.execute(
                "INSERT INTO point_transactions (user_id, delta, reason) VALUES (?,?,?)",
                (user_id, pts_award, f"{reason_prefix}: {task['title']}")
            )

            result = {
                "task": task,
                "pts_earned": pts_award,
                "xp_earned": xp_award,
                "review_number": task.get("review_number") or 1,
                "next_review_id": None,
                "next_review_interval": None,
                "next_review_date": None,
            }

            if task.get("is_review"):
                old_interval = task.get("review_interval") or 1
                new_interval = next_srs_interval(old_interval)
                next_date = (datetime.now(EST).date() + timedelta(days=new_interval)).strftime("%Y-%m-%d")
                next_task_num = self._next_user_task_num_pending(conn, user_id)
                cur = conn.execute(
                    """INSERT INTO tasks (
                           user_id, user_task_num, project_id, title, description, points, priority, due_date,
                           completed, is_review, review_interval, review_number, next_review_date
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        user_id,
                        next_task_num,
                        task.get("project_id"),
                        task["title"],
                        task.get("description") or "",
                        task["points"],
                        task["priority"],
                        next_date,
                        0,
                        1,
                        new_interval,
                        review_num + 1,
                        next_date,
                    )
                )
                result.update({
                    "next_review_id": cur.lastrowid,
                    "next_review_interval": new_interval,
                    "next_review_date": next_date,
                })

                # SRS Sunday: +1 coin once per ISO week when completing a review on Sunday with ≥5 SRS reviews that week (Mon–Sun EST).
                est_today = datetime.now(EST).date()
                if est_today.weekday() == 6:
                    monday = est_today - timedelta(days=est_today.weekday())
                    next_monday = monday + timedelta(days=7)
                    w_start = datetime.combine(monday, datetime.min.time(), tzinfo=EST).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                    w_end = datetime.combine(next_monday, datetime.min.time(), tzinfo=EST).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                    week_srs = conn.execute(
                        """SELECT COUNT(*) FROM tasks WHERE user_id=? AND is_review=1
                           AND completed=1 AND completed_at>=? AND completed_at<?""",
                        (user_id, w_start, w_end),
                    ).fetchone()[0]
                    iso = est_today.isocalendar()
                    week_key = f"{iso[0]}-W{iso[1]:02d}"
                    row = conn.execute(
                        "SELECT value FROM user_settings WHERE user_id=? AND key=?",
                        (user_id, "srs_sunday_week_bonus"),
                    ).fetchone()
                    granted = row["value"] if row else ""
                    if week_srs >= 5 and granted != week_key:
                        self._add_coins_internal(conn, user_id, 1)
                        conn.execute(
                            """INSERT INTO user_settings (user_id, key, value)
                               VALUES (?,?,?) ON CONFLICT(user_id, key) DO UPDATE SET value=?""",
                            (user_id, "srs_sunday_week_bonus", week_key, week_key),
                        )

            result["_xp_award"] = xp_award
            result["_user_id"] = user_id

        if result.get("_xp_award", 0) > 0:
            xp_result = self.add_xp(result["_user_id"], result["_xp_award"])
            result["level_ups"] = xp_result.get("level_ups", [])
            result["coins_from_levels"] = xp_result.get("coins_earned", 0)
        user_data = self.get_user(user_id)
        result["user_points"] = user_data["points"] if user_data else 0
        result["user_total_xp"] = user_data["total_xp"] if user_data else 0
        return result

    def undo_task_complete(self, task_id: int, user_id: int) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM tasks WHERE id=? AND user_id=? AND completed=1",
                (task_id, user_id)
            ).fetchone()
            if not row:
                return None
            conn.execute("UPDATE tasks SET completed=0, completed_at=NULL WHERE id=?", (task_id,))
            return dict(row)

    def delete_task(self, task_id: int, user_id: int) -> bool:
        with self._conn() as conn:
            return conn.execute("DELETE FROM tasks WHERE id=? AND user_id=?", (task_id, user_id)).rowcount > 0

    def get_tasks_completed_today(self, user_id: int) -> int:
        today = datetime.now(EST).date()
        start_utc = datetime.combine(today, datetime.min.time(), tzinfo=EST).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        end_utc = datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=EST).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        with self._conn() as conn:
            return conn.execute(
                """SELECT COUNT(*) FROM tasks WHERE user_id=? AND completed=1
                   AND is_review=0 AND completed_at>=? AND completed_at<?""",
                (user_id, start_utc, end_utc)
            ).fetchone()[0]

    def get_srs_reviews_today(self, user_id: int) -> int:
        today = datetime.now(EST).date()
        start_utc = datetime.combine(today, datetime.min.time(), tzinfo=EST).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        end_utc = datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=EST).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        with self._conn() as conn:
            return conn.execute(
                """SELECT COUNT(*) FROM tasks WHERE user_id=? AND completed=1
                   AND is_review=1 AND completed_at>=? AND completed_at<?""",
                (user_id, start_utc, end_utc)
            ).fetchone()[0]

    # ── STUDY SESSIONS ────────────────────────────────────────────────────────

    @staticmethod
    def normalize_tags(raw: str) -> str:
        """Normalize a user-provided tag list into a canonical comma-separated string.

        Example inputs:
        - "anki, math,  exam prep" -> "anki,math,exam prep"
        - "#anki #math" -> "anki,math"
        """
        raw = (raw or "").strip()
        if not raw:
            return ""
        # Split on commas first; also accept whitespace-separated tags (e.g. "#anki #math").
        parts: list[str] = []
        for chunk in raw.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            parts.extend([p for p in chunk.split() if p.strip()])

        cleaned: list[str] = []
        seen: set[str] = set()
        for p in parts:
            p = p.strip()
            if not p:
                continue
            if p.startswith("#"):
                p = p[1:]
            p = p.strip().lower()
            if not p:
                continue
            if len(p) > 32:
                p = p[:32]
            if p in seen:
                continue
            seen.add(p)
            cleaned.append(p)
        return ",".join(cleaned)

    def start_session(self, user_id: int, subject: str = "",
                      target_minutes: Optional[int] = None,
                      is_group: bool = False, group_lobby_id: Optional[int] = None,
                      source_guild_id: Optional[int] = None,
                      allowed_member: Optional[bool] = None,
                      tags: str = "") -> int:
        now = _utcnow_naive().isoformat()
        norm_tags = self.normalize_tags(tags)
        allowed_member_i = None if allowed_member is None else int(bool(allowed_member))
        with self._conn() as conn:
            conn.execute(
                "UPDATE study_sessions SET ended_at=?, duration_minutes=0 WHERE user_id=? AND ended_at IS NULL",
                (now, user_id)
            )
            cur = conn.execute(
                """INSERT INTO study_sessions
                   (user_id, subject, tags, started_at, target_minutes, is_group, group_lobby_id, source_guild_id, allowed_member)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (user_id, subject, norm_tags, now, target_minutes, int(is_group), group_lobby_id, source_guild_id, allowed_member_i)
            )
            session_id = int(cur.lastrowid)
            # Create first segment for this session.
            conn.execute(
                """INSERT INTO study_session_segments
                   (session_id, user_id, subject, tags, started_at, paused_offset_seconds_start)
                   VALUES (?,?,?,?,?,?)""",
                (session_id, int(user_id), subject, norm_tags, now, 0),
            )
            return session_id

    def _close_open_segment(self, conn: sqlite3.Connection, *, session: dict, ended_iso: str) -> None:
        """Close the current open segment for this session, computing minutes excluding paused time."""
        seg = conn.execute(
            """SELECT * FROM study_session_segments
               WHERE session_id=? AND ended_at IS NULL
               ORDER BY id DESC LIMIT 1""",
            (int(session["id"]),),
        ).fetchone()
        if not seg:
            return
        try:
            seg_started = _parse_stored(seg["started_at"])
        except Exception:
            return
        ended_dt = _parse_stored(ended_iso)
        total_paused = int(session.get("total_paused_seconds") or 0)
        paused_offset = int(seg["paused_offset_seconds_start"] or 0)
        paused_delta = max(total_paused - paused_offset, 0)
        seg_secs = max(int((ended_dt - seg_started).total_seconds()) - paused_delta, 0)
        seg_mins = int(seg_secs // 60)
        conn.execute(
            "UPDATE study_session_segments SET ended_at=?, duration_minutes=? WHERE id=?",
            (ended_iso, seg_mins, int(seg["id"])),
        )

    def switch_active_session_segment(self, user_id: int, *, subject: str, tags: str = "") -> bool:
        """Switch the active session to a new subject/tags segment (ends previous segment)."""
        session = self.get_active_session(user_id)
        if not session:
            return False
        if session.get("is_paused"):
            return False
        now = _utcnow_naive().isoformat()
        norm_tags = self.normalize_tags(tags)
        with self._conn() as conn:
            # Re-read with current pause total for accurate segment minutes.
            row = conn.execute("SELECT * FROM study_sessions WHERE id=?", (int(session["id"]),)).fetchone()
            if not row:
                return False
            session = dict(row)
            self._close_open_segment(conn, session=session, ended_iso=now)
            conn.execute(
                "UPDATE study_sessions SET subject=?, tags=? WHERE id=?",
                (subject, norm_tags, int(session["id"])),
            )
            conn.execute(
                """INSERT INTO study_session_segments
                   (session_id, user_id, subject, tags, started_at, paused_offset_seconds_start)
                   VALUES (?,?,?,?,?,?)""",
                (int(session["id"]), int(user_id), subject, norm_tags, now, int(session.get("total_paused_seconds") or 0)),
            )
        return True

    def get_active_session(self, user_id: int) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM study_sessions WHERE user_id=? AND ended_at IS NULL", (user_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_all_active_sessions(self) -> list[dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM study_sessions WHERE ended_at IS NULL"
            ).fetchall()]

    def set_session_live_message(self, session_id: int, channel_id: int, message_id: int, *, kind: str = "ephemeral"):
        with self._conn() as conn:
            conn.execute(
                "UPDATE study_sessions SET live_channel_id=?, live_message_id=?, live_kind=? WHERE id=?",
                (channel_id, message_id, kind, session_id)
            )

    def set_motivation_sent(self, session_id: int, sent: bool = True):
        with self._conn() as conn:
            conn.execute(
                "UPDATE study_sessions SET motivation_sent=? WHERE id=?",
                (int(sent), session_id)
            )

    def set_session_rating(self, session_id: int, rating: int):
        with self._conn() as conn:
            conn.execute("UPDATE study_sessions SET focus_rating=? WHERE id=?", (rating, session_id))

    def pause_session(self, user_id: int, *, expected_session_id: int | None = None) -> bool:
        session = self.get_active_session(user_id)
        if not session or session["is_paused"] or (expected_session_id is not None and session["id"] != expected_session_id):
            return False
        with self._conn() as conn:
            conn.execute(
                "UPDATE study_sessions SET is_paused=1, paused_at=? WHERE id=?",
                (_utcnow_naive().isoformat(), session["id"])
            )
        return True

    def resume_session(self, user_id: int, *, expected_session_id: int | None = None) -> Optional[int]:
        session = self.get_active_session(user_id)
        if not session or not session["is_paused"] or (expected_session_id is not None and session["id"] != expected_session_id):
            return None
        paused_at = _parse_stored(session["paused_at"])
        pause_secs = int((_utcnow_naive() - paused_at).total_seconds())
        new_total = (session["total_paused_seconds"] or 0) + pause_secs
        with self._conn() as conn:
            conn.execute(
                "UPDATE study_sessions SET is_paused=0, paused_at=NULL, total_paused_seconds=? WHERE id=?",
                (new_total, session["id"])
            )
        return pause_secs

    def add_session_note(self, user_id: int, note: str, *, expected_session_id: int | None = None) -> bool:
        session = self.get_active_session(user_id)
        if not session or (expected_session_id is not None and session["id"] != expected_session_id):
            return False
        existing = session.get("notes") or ""
        new_notes = (existing + "\n• " + note) if existing else "• " + note
        with self._conn() as conn:
            conn.execute("UPDATE study_sessions SET notes=? WHERE id=?", (new_notes, session["id"]))
        return True

    def extend_session_target(self, user_id: int, extra_minutes: int) -> Optional[int]:
        session = self.get_active_session(user_id)
        if not session:
            return None
        new_target = (session.get("target_minutes") or 0) + extra_minutes
        with self._conn() as conn:
            conn.execute("UPDATE study_sessions SET target_minutes=? WHERE id=?", (new_target, session["id"]))
        return new_target

    def end_session(self, user_id: int, notes: str = "") -> Optional[dict]:
        session = self.get_active_session(user_id)
        if not session:
            return None
        started = _parse_stored(session["started_at"])
        ended = _utcnow_naive()

        total_paused = session.get("total_paused_seconds") or 0
        if session["is_paused"] and session.get("paused_at"):
            paused_at = _parse_stored(session["paused_at"])
            total_paused += int((ended - paused_at).total_seconds())

        total_secs = int((ended - started).total_seconds())
        active_secs = max(total_secs - total_paused, 0)
        minutes = min(active_secs // 60, 480)

        xp = self._calc_xp(minutes)
        points = minutes if minutes >= 5 else 0

        existing_notes = session.get("notes") or ""
        if notes:
            final_notes = (existing_notes + "\n• " + notes) if existing_notes else "• " + notes
        else:
            final_notes = existing_notes

        with self._conn() as conn:
            # Close the open segment first (uses current total_paused_seconds).
            self._close_open_segment(conn, session=session, ended_iso=ended.isoformat())
            conn.execute(
                """UPDATE study_sessions
                   SET ended_at=?, duration_minutes=?, xp_earned=?, points_earned=?,
                       is_paused=0, paused_at=NULL, notes=?
                   WHERE id=?""",
                (ended.isoformat(), minutes, xp, points, final_notes or None, session["id"])
            )

        session.update({
            "ended_at": ended.isoformat(),
            "duration_minutes": minutes,
            "xp_earned": xp,
            "points_earned": points,
            "notes": final_notes,
            "total_paused_seconds": total_paused,
        })
        self.recalculate_streak(user_id)
        return session

    def _calc_xp(self, minutes: int) -> int:
        if minutes < 5:
            return 0
        xp = min(minutes, 25) * 2
        if minutes > 25:
            xp += min(minutes - 25, 35) * 3
        if minutes > 60:
            xp += min(minutes - 60, 60) * 4
        if minutes > 120:
            xp += (minutes - 120) * 5
        # Flat bonuses (stack: Pomodoro + Deep Work + Marathon)
        if minutes >= 25:
            xp += 25
        if minutes >= 60:
            xp += 50
        if minutes >= 120:
            xp += 100
        return xp

    def get_user_sessions(self, user_id: int, limit: int = 10) -> list[dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                """SELECT * FROM study_sessions WHERE user_id=? AND ended_at IS NOT NULL
                   ORDER BY started_at DESC LIMIT ?""",
                (user_id, limit)
            ).fetchall()]

    def get_all_sessions(self, user_id: int) -> list[dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM study_sessions WHERE user_id=? AND ended_at IS NOT NULL ORDER BY started_at ASC",
                (user_id,)
            ).fetchall()]

    def get_total_study_minutes(self, user_id: int) -> int:
        with self._conn() as conn:
            return conn.execute(
                "SELECT COALESCE(SUM(duration_minutes),0) FROM study_sessions WHERE user_id=? AND ended_at IS NOT NULL",
                (user_id,)
            ).fetchone()[0]

    def get_weekly_study_minutes(self, user_id: int) -> int:
        """Rolling last 7 **calendar days** in EST ending today (same strip as day-by-day charts)."""
        week_start = (datetime.now(EST).date() - timedelta(days=6)).isoformat()
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT started_at, duration_minutes FROM study_sessions WHERE user_id=? AND ended_at IS NOT NULL",
                (user_id,)
            ).fetchall()
        total = 0
        for r in rows:
            try:
                if _stored_to_est_date(r["started_at"]) >= week_start:
                    total += r["duration_minutes"] or 0
            except Exception:
                pass
        return total

    def get_subject_stats(self, user_id: int, days: int = 30, *, est_calendar_days: bool = False) -> list[dict]:
        """Subject aggregates for the last `days` sessions (ended).

        If ``est_calendar_days`` is True, filter by **EST calendar date** of session start
        (inclusive window ending today), matching ``get_daily_breakdown`` / weekly charts.
        Otherwise use a rolling UTC wall-clock window (legacy behavior).
        """
        # Prefer segment data if available (more accurate when using /study switch).
        try:
            with self._conn() as conn:
                seg_count = conn.execute(
                    "SELECT COUNT(1) FROM study_session_segments WHERE user_id=? AND ended_at IS NOT NULL",
                    (int(user_id),),
                ).fetchone()[0]
        except Exception:
            seg_count = 0

        if seg_count and est_calendar_days:
            est_cutoff = (datetime.now(EST).date() - timedelta(days=max(0, days - 1))).isoformat()
            # Loose UTC lower bound so we do not scan entire session history.
            rough_cutoff = (_utcnow_naive() - timedelta(days=int(days) + 2)).isoformat()
            with self._conn() as conn:
                rows = conn.execute(
                    """SELECT COALESCE(subject,'General') AS subject, started_at, duration_minutes
                       FROM study_session_segments
                       WHERE user_id=? AND ended_at IS NOT NULL AND started_at>=?""",
                    (int(user_id), rough_cutoff),
                ).fetchall()
            from collections import defaultdict

            sums: dict[str, dict] = defaultdict(
                lambda: {"session_count": 0, "total_minutes": 0}
            )
            for r in rows:
                try:
                    if _stored_to_est_date(r["started_at"]) < est_cutoff:
                        continue
                except Exception:
                    continue
                subj = r["subject"] or "General"
                b = sums[subj]
                b["session_count"] += 1
                b["total_minutes"] += int(r["duration_minutes"] or 0)
            out: list[dict] = []
            for subj, b in sums.items():
                out.append(
                    {
                        "subject": subj,
                        "session_count": b["session_count"],
                        "total_minutes": b["total_minutes"],
                        "total_xp": 0,
                        "avg_rating": 0.0,
                    }
                )
            out.sort(key=lambda x: (-int(x["total_minutes"]), x["subject"]))
            return out

        if seg_count and not est_calendar_days:
            cutoff = (_utcnow_naive() - timedelta(days=days)).isoformat()
            with self._conn() as conn:
                rows = conn.execute(
                    """SELECT COALESCE(subject,'General') AS subject,
                              COUNT(*) as session_count,
                              COALESCE(SUM(duration_minutes),0) as total_minutes
                       FROM study_session_segments
                       WHERE user_id=? AND ended_at IS NOT NULL AND started_at>=?
                       GROUP BY COALESCE(subject,'General')
                       ORDER BY total_minutes DESC""",
                    (int(user_id), cutoff),
                ).fetchall()
            return [
                {
                    "subject": r["subject"],
                    "session_count": int(r["session_count"] or 0),
                    "total_minutes": int(r["total_minutes"] or 0),
                    "total_xp": 0,
                    "avg_rating": 0.0,
                }
                for r in rows
            ]

        cutoff = (_utcnow_naive() - timedelta(days=days)).isoformat()
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                """SELECT COALESCE(subject,'General') as subject,
                          COUNT(*) as session_count,
                          COALESCE(SUM(duration_minutes),0) as total_minutes,
                          COALESCE(SUM(xp_earned),0) as total_xp,
                          COALESCE(AVG(CASE WHEN focus_rating IS NOT NULL THEN focus_rating END),0) as avg_rating
                   FROM study_sessions
                   WHERE user_id=? AND ended_at IS NOT NULL AND started_at>=?
                   GROUP BY COALESCE(subject,'General')
                   ORDER BY total_minutes DESC""",
                (user_id, cutoff)
            ).fetchall()]

    def get_tag_stats(self, user_id: int, days: int | None = 30, *, est_calendar_days: bool = False) -> list[dict]:
        """Aggregate completed study time by tag from the last N days.

        Stored tags are a canonical comma-separated string, e.g. "anki,math".
        See ``get_subject_stats`` for ``est_calendar_days`` semantics.
        """
        est_cutoff: str | None = None
        if est_calendar_days and days is not None:
            est_cutoff = (datetime.now(EST).date() - timedelta(days=max(0, int(days) - 1))).isoformat()

        cutoff = None
        if not est_calendar_days and days is not None:
            cutoff = (_utcnow_naive() - timedelta(days=int(days))).isoformat()

        # Prefer segments if available.
        try:
            with self._conn() as conn:
                seg_count = conn.execute(
                    "SELECT COUNT(1) FROM study_session_segments WHERE user_id=? AND ended_at IS NOT NULL",
                    (int(user_id),),
                ).fetchone()[0]
        except Exception:
            seg_count = 0

        with self._conn() as conn:
            if cutoff is None and est_cutoff is None:
                if seg_count:
                    rows = conn.execute(
                        """SELECT tags, started_at, duration_minutes, 0 as xp_earned, 0 as points_earned
                           FROM study_session_segments
                           WHERE user_id=? AND ended_at IS NOT NULL""",
                        (int(user_id),),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """SELECT tags, started_at, duration_minutes, xp_earned, points_earned
                           FROM study_sessions
                           WHERE user_id=? AND ended_at IS NOT NULL""",
                        (user_id,),
                    ).fetchall()
            elif est_cutoff is not None:
                rough_cutoff = (_utcnow_naive() - timedelta(days=int(days) + 2)).isoformat()
                if seg_count:
                    rows = conn.execute(
                        """SELECT tags, started_at, duration_minutes, 0 as xp_earned, 0 as points_earned
                           FROM study_session_segments
                           WHERE user_id=? AND ended_at IS NOT NULL AND started_at>=?""",
                        (int(user_id), rough_cutoff),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """SELECT tags, started_at, duration_minutes, xp_earned, points_earned
                           FROM study_sessions
                           WHERE user_id=? AND ended_at IS NOT NULL AND started_at>=?""",
                        (user_id, rough_cutoff),
                    ).fetchall()
            else:
                if seg_count:
                    rows = conn.execute(
                        """SELECT tags, started_at, duration_minutes, 0 as xp_earned, 0 as points_earned
                           FROM study_session_segments
                           WHERE user_id=? AND ended_at IS NOT NULL AND started_at>=?""",
                        (int(user_id), cutoff),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """SELECT tags, started_at, duration_minutes, xp_earned, points_earned
                           FROM study_sessions
                           WHERE user_id=? AND ended_at IS NOT NULL AND started_at>=?""",
                        (user_id, cutoff),
                    ).fetchall()

        buckets: dict[str, dict] = {}
        for r in rows:
            if est_cutoff is not None:
                try:
                    if _stored_to_est_date(r["started_at"]) < est_cutoff:
                        continue
                except Exception:
                    continue
            mins = int(r["duration_minutes"] or 0)
            if mins <= 0:
                continue
            tags = (r["tags"] or "").strip()
            if not tags:
                continue
            xp = int(r["xp_earned"] or 0)
            pts = int(r["points_earned"] or 0)
            for tag in [t.strip() for t in tags.split(",") if t.strip()]:
                b = buckets.get(tag)
                if not b:
                    b = {"tag": tag, "session_count": 0, "total_minutes": 0, "total_xp": 0, "total_points": 0}
                    buckets[tag] = b
                b["session_count"] += 1
                b["total_minutes"] += mins
                b["total_xp"] += xp
                b["total_points"] += pts

        return sorted(buckets.values(), key=lambda x: (-int(x["total_minutes"]), x["tag"]))

    def get_daily_breakdown(self, user_id: int, days: int = 7) -> list[dict]:
        """Bucket minutes by EST calendar date for the last ``days`` days **ending today** (inclusive).

        First day in the window is ``today_est - (days - 1)``, so ``days=7`` matches Mon–Sun strips
        that end on ``today`` in the weekly report.
        """
        cutoff = (datetime.now(EST).date() - timedelta(days=max(0, days - 1))).isoformat()
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT started_at, duration_minutes FROM study_sessions WHERE user_id=? AND ended_at IS NOT NULL",
                (user_id,)
            ).fetchall()
        buckets: dict[str, int] = {}
        for r in rows:
            try:
                est_date = _stored_to_est_date(r["started_at"])
                if est_date >= cutoff:
                    buckets[est_date] = buckets.get(est_date, 0) + (r["duration_minutes"] or 0)
            except Exception:
                pass
        return [{"date": d, "minutes": m} for d, m in sorted(buckets.items())]

    def get_hourly_stats(self, user_id: int, days: int = 30) -> list[dict]:
        cutoff = (_utcnow_naive() - timedelta(days=days)).isoformat()
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT started_at, duration_minutes, focus_rating
                   FROM study_sessions
                   WHERE user_id=? AND ended_at IS NOT NULL AND started_at>=?""",
                (user_id, cutoff)
            ).fetchall()

        from collections import defaultdict
        buckets: dict[int, list] = defaultdict(list)
        for row in rows:
            try:
                dt_naive = _parse_stored(row["started_at"])
                dt_utc = dt_naive.replace(tzinfo=timezone.utc)
                dt_est = dt_utc.astimezone(EST)
                h = dt_est.hour
                buckets[h].append((row["duration_minutes"] or 0, row["focus_rating"]))
            except Exception:
                continue

        result = []
        for h in sorted(buckets.keys()):
            entries = buckets[h]
            durations = [e[0] for e in entries]
            ratings = [e[1] for e in entries if e[1] is not None]
            result.append({
                "est_hour": h,
                "session_count": len(entries),
                "avg_minutes": sum(durations) / len(durations) if durations else 0,
                "total_minutes": sum(durations),
                "avg_rating": round(sum(ratings) / len(ratings), 1) if ratings else 0,
            })
        return result

    def get_average_focus_rating(self, user_id: int) -> float:
        with self._conn() as conn:
            result = conn.execute(
                "SELECT AVG(focus_rating) FROM study_sessions WHERE user_id=? AND focus_rating IS NOT NULL",
                (user_id,)
            ).fetchone()[0]
            return round(result, 1) if result else 0.0

    def get_sessions_today(self, user_id: int) -> list[dict]:
        today = datetime.now(EST).date().isoformat()
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM study_sessions WHERE user_id=? AND ended_at IS NOT NULL",
                (user_id,)
            ).fetchall()
        return [dict(r) for r in rows if _stored_to_est_date(r["started_at"]) == today]

    # ── POMODORO ──────────────────────────────────────────────────────────────

    def start_pomodoro(self, user_id: int, work_mins: int, break_mins: int,
                       long_break_mins: int = 15, max_cycles: int = 0) -> int:
        now = _utcnow_naive().isoformat()
        with self._conn() as conn:
            conn.execute("UPDATE pomodoro_sessions SET active=0 WHERE user_id=? AND active=1", (user_id,))
            cur = conn.execute(
                """INSERT INTO pomodoro_sessions
                   (user_id, work_minutes, break_minutes, long_break_minutes,
                    max_cycles, current_phase, phase_started_at)
                   VALUES (?,?,?,?,?,'work',?)""",
                (user_id, work_mins, break_mins, long_break_mins, max_cycles, now)
            )
            return cur.lastrowid

    def get_active_pomodoro(self, user_id: int) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM pomodoro_sessions WHERE user_id=? AND active=1", (user_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_all_active_pomodoros(self) -> list[dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM pomodoro_sessions WHERE active=1"
            ).fetchall()]

    def advance_pomodoro(self, user_id: int, next_phase: str) -> Optional[dict]:
        pomo = self.get_active_pomodoro(user_id)
        if not pomo:
            return None
        new_cycle = pomo["total_cycles"] + (1 if pomo["current_phase"] == "work" else 0)
        with self._conn() as conn:
            conn.execute(
                """UPDATE pomodoro_sessions
                   SET current_phase=?, phase_number=?, total_cycles=?, phase_started_at=?
                   WHERE id=?""",
                (next_phase, pomo["phase_number"]+1, new_cycle,
                 _utcnow_naive().isoformat(), pomo["id"])
            )
        return self.get_active_pomodoro(user_id)

    def end_pomodoro(self, user_id: int) -> Optional[dict]:
        pomo = self.get_active_pomodoro(user_id)
        if not pomo:
            return None
        with self._conn() as conn:
            conn.execute("UPDATE pomodoro_sessions SET active=0 WHERE id=?", (pomo["id"],))
        return pomo

    # ── GROUP POMODORO ────────────────────────────────────────────────────────

    def _generate_lobby_code(self) -> str:
        with self._conn() as conn:
            for _ in range(100):
                code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
                existing = conn.execute(
                    "SELECT id FROM group_pomo_lobbies WHERE code=? AND state!='completed'",
                    (code,)
                ).fetchone()
                if not existing:
                    return code
        return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))

    def create_group_lobby(self, host_id: int, work: int = 25, break_: int = 5,
                           long_break: int = 15, cycles: int = 4, subject: str = "") -> dict:
        code = self._generate_lobby_code()
        now = _utcnow_naive().isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO group_pomo_lobbies
                   (code, host_id, subject, work_mins, break_mins, long_break_mins, cycles, created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (code, host_id, subject, work, break_, long_break, cycles, now)
            )
            lobby_id = cur.lastrowid
            conn.execute(
                "INSERT INTO group_pomo_members (lobby_id, user_id) VALUES (?,?)",
                (lobby_id, host_id)
            )
        return {"id": lobby_id, "code": code}

    def join_group_lobby(self, code: str, user_id: int) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM group_pomo_lobbies WHERE code=? AND state='waiting'",
                (code.upper(),)
            ).fetchone()
            if not row:
                return None
            lobby = dict(row)
            member_count = conn.execute(
                "SELECT COUNT(*) FROM group_pomo_members WHERE lobby_id=? AND active=1",
                (lobby["id"],)
            ).fetchone()[0]
            if member_count >= 10:
                return {"error": "full"}
            try:
                conn.execute(
                    "INSERT INTO group_pomo_members (lobby_id, user_id) VALUES (?,?)",
                    (lobby["id"], user_id)
                )
            except sqlite3.IntegrityError:
                conn.execute(
                    "UPDATE group_pomo_members SET active=1 WHERE lobby_id=? AND user_id=?",
                    (lobby["id"], user_id)
                )
            return lobby

    def get_group_lobby_by_code(self, code: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM group_pomo_lobbies WHERE code=? AND state!='completed'",
                (code.upper(),)
            ).fetchone()
            return dict(row) if row else None

    def get_group_lobby_by_id(self, lobby_id: int) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM group_pomo_lobbies WHERE id=?", (lobby_id,)).fetchone()
            return dict(row) if row else None

    def get_user_group_lobby(self, user_id: int) -> Optional[dict]:
        with self._conn() as conn:
            member = conn.execute(
                """SELECT gpm.lobby_id FROM group_pomo_members gpm
                   JOIN group_pomo_lobbies gpl ON gpm.lobby_id=gpl.id
                   WHERE gpm.user_id=? AND gpm.active=1 AND gpl.state!='completed'""",
                (user_id,)
            ).fetchone()
            if not member:
                return None
            return self.get_group_lobby_by_id(member["lobby_id"])

    def get_group_members(self, lobby_id: int) -> list[dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM group_pomo_members WHERE lobby_id=? AND active=1 ORDER BY joined_at",
                (lobby_id,)
            ).fetchall()]

    def vote_start_group_lobby(self, lobby_id: int, user_id: int) -> None:
        with self._conn() as conn:
            try:
                conn.execute(
                    "INSERT INTO group_pomo_votes (lobby_id, user_id) VALUES (?,?)",
                    (lobby_id, user_id),
                )
            except sqlite3.IntegrityError:
                pass

    def get_group_lobby_vote_count(self, lobby_id: int) -> int:
        with self._conn() as conn:
            return int(conn.execute(
                """SELECT COUNT(*) FROM group_pomo_votes v
                   JOIN group_pomo_members m
                     ON v.lobby_id=m.lobby_id AND v.user_id=m.user_id
                   WHERE v.lobby_id=? AND m.active=1""",
                (lobby_id,),
            ).fetchone()[0] or 0)

    def has_voted_group_lobby(self, lobby_id: int, user_id: int) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM group_pomo_votes WHERE lobby_id=? AND user_id=?",
                (lobby_id, user_id),
            ).fetchone()
            return bool(row)

    def clear_group_lobby_votes(self, lobby_id: int) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM group_pomo_votes WHERE lobby_id=?", (lobby_id,))

    def begin_group_lobby(self, lobby_id: int) -> bool:
        now = _utcnow_naive().isoformat()
        with self._conn() as conn:
            ok = conn.execute(
                """UPDATE group_pomo_lobbies SET state='active', started_at=?,
                   current_phase='work', current_cycle=1, phase_started_at=?
                   WHERE id=? AND state='waiting'""",
                (now, now, lobby_id)
            ).rowcount > 0
            if ok:
                conn.execute("DELETE FROM group_pomo_votes WHERE lobby_id=?", (lobby_id,))
            return ok

    def set_group_lobby_announce(self, lobby_id: int, *, source_guild_id: int | None, channel_id: int | None, message_id: int | None):
        with self._conn() as conn:
            conn.execute(
                "UPDATE group_pomo_lobbies SET source_guild_id=?, announce_channel_id=?, announce_message_id=? WHERE id=?",
                (source_guild_id, channel_id, message_id, lobby_id),
            )

    def leave_group_lobby(self, user_id: int) -> Optional[dict]:
        lobby = self.get_user_group_lobby(user_id)
        if not lobby:
            return None
        with self._conn() as conn:
            conn.execute(
                "UPDATE group_pomo_members SET active=0 WHERE lobby_id=? AND user_id=?",
                (lobby["id"], user_id)
            )
            remaining = conn.execute(
                "SELECT COUNT(*) FROM group_pomo_members WHERE lobby_id=? AND active=1",
                (lobby["id"],)
            ).fetchone()[0]
            if remaining == 0:
                conn.execute(
                    "UPDATE group_pomo_lobbies SET state='completed' WHERE id=?",
                    (lobby["id"],)
                )
            elif lobby["host_id"] == user_id:
                next_host = conn.execute(
                    "SELECT user_id FROM group_pomo_members WHERE lobby_id=? AND active=1 ORDER BY joined_at LIMIT 1",
                    (lobby["id"],)
                ).fetchone()
                if next_host:
                    conn.execute(
                        "UPDATE group_pomo_lobbies SET host_id=? WHERE id=?",
                        (next_host["user_id"], lobby["id"])
                    )
        return lobby

    def advance_group_phase(self, lobby_id: int, next_phase: str, next_cycle: int) -> bool:
        now = _utcnow_naive().isoformat()
        with self._conn() as conn:
            return conn.execute(
                """UPDATE group_pomo_lobbies SET current_phase=?, current_cycle=?,
                   phase_started_at=? WHERE id=?""",
                (next_phase, next_cycle, now, lobby_id)
            ).rowcount > 0

    def end_group_lobby(self, lobby_id: int):
        with self._conn() as conn:
            conn.execute("UPDATE group_pomo_lobbies SET state='completed' WHERE id=?", (lobby_id,))
            conn.execute("UPDATE group_pomo_members SET active=0 WHERE lobby_id=?", (lobby_id,))

    def get_stale_lobbies(self, max_age_hours: int = 12) -> list[dict]:
        cutoff = (_utcnow_naive() - timedelta(hours=max_age_hours)).isoformat()
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM group_pomo_lobbies WHERE state!='completed' AND created_at<?",
                (cutoff,)
            ).fetchall()]

    def get_waiting_group_lobbies(self) -> list[dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM group_pomo_lobbies WHERE state='waiting'",
            ).fetchall()]

    def get_active_group_lobbies(self) -> list[dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM group_pomo_lobbies WHERE state='active'",
            ).fetchall()]

    # ── REWARDS ───────────────────────────────────────────────────────────────

    def add_reward(self, user_id: int, name: str, description: str, cost: int) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO rewards (user_id,name,description,cost) VALUES (?,?,?,?)",
                (user_id, name, description, cost)
            )
            return cur.lastrowid

    def get_rewards(self, user_id: int) -> list[dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM rewards WHERE user_id=? ORDER BY cost ASC", (user_id,)
            ).fetchall()]

    def get_reward(self, reward_id: int, user_id: int) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM rewards WHERE id=? AND user_id=?", (reward_id, user_id)).fetchone()
            return dict(row) if row else None

    def delete_reward(self, reward_id: int, user_id: int) -> bool:
        with self._conn() as conn:
            return conn.execute("DELETE FROM rewards WHERE id=? AND user_id=?", (reward_id, user_id)).rowcount > 0

    def redeem_reward(self, user_id: int, reward_id: int) -> bool:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM rewards WHERE id=? AND user_id=?", (reward_id, user_id)).fetchone()
            if not row:
                return False
            reward = dict(row)
            cur = conn.execute(
                "UPDATE users SET points=points-? WHERE user_id=? AND points>=?",
                (reward["cost"], user_id, reward["cost"])
            )
            if cur.rowcount == 0:
                return False
            conn.execute(
                "INSERT INTO point_transactions (user_id, delta, reason) VALUES (?,?,?)",
                (user_id, -reward["cost"], "Reward redeemed")
            )
            conn.execute(
                "INSERT INTO redemptions (user_id,reward_id,reward_name,cost) VALUES (?,?,?,?)",
                (user_id, reward_id, reward["name"], reward["cost"])
            )
        return True

    def get_redemption_history(self, user_id: int, limit: int = 100) -> list[dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM redemptions WHERE user_id=? ORDER BY redeemed_at DESC LIMIT ?",
                (user_id, limit)
            ).fetchall()]

    def get_all_redemptions(self, user_id: int) -> list[dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM redemptions WHERE user_id=? ORDER BY redeemed_at ASC", (user_id,)
            ).fetchall()]

    # ── DAILY QUESTS ──────────────────────────────────────────────────────────

    def get_daily_quests(self, user_id: int, date: Optional[str] = None) -> list[dict]:
        date = date or datetime.now(EST).date().isoformat()
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM daily_quests WHERE user_id=? AND date=? ORDER BY tier",
                (user_id, date)
            ).fetchall()]

    def assign_daily_quests(self, user_id: int, quests: list[dict]) -> list[dict]:
        """quests = [{"tier": 1, "quest_key": "warm_up", "target": 5}, ...]"""
        date = datetime.now(EST).date().isoformat()
        existing = self.get_daily_quests(user_id, date)
        if existing:
            return existing
        with self._conn() as conn:
            for q in quests:
                conn.execute(
                    "INSERT INTO daily_quests (user_id, date, tier, quest_key, target) VALUES (?,?,?,?,?)",
                    (user_id, date, q["tier"], q["quest_key"], q["target"])
                )
        return self.get_daily_quests(user_id, date)

    def update_quest_progress(self, user_id: int, quest_key: str, amount: int = 1) -> list[dict]:
        """Increment progress on matching quests. Returns list of newly completed quests."""
        date = datetime.now(EST).date().isoformat()
        completed = []
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM daily_quests WHERE user_id=? AND date=? AND quest_key=? AND completed=0",
                (user_id, date, quest_key)
            ).fetchall()
            for row in rows:
                q = dict(row)
                new_progress = min(q["progress"] + amount, q["target"])
                conn.execute(
                    "UPDATE daily_quests SET progress=? WHERE id=?",
                    (new_progress, q["id"])
                )
                if new_progress >= q["target"]:
                    conn.execute("UPDATE daily_quests SET completed=1 WHERE id=?", (q["id"],))
                    q["progress"] = new_progress
                    q["completed"] = 1
                    completed.append(q)
        return completed

    def update_quest_progress_by_metric(self, user_id: int, metric: str, amount: int = 1) -> list[dict]:
        """Update quests that track a given metric. The quest cog's track_quest method
        handles metric-to-key mapping; this is a fallback that does direct key lookup."""
        return self.update_quest_progress(user_id, metric, amount)

    def check_all_quests_complete(self, user_id: int, date: Optional[str] = None) -> bool:
        date = date or datetime.now(EST).date().isoformat()
        quests = self.get_daily_quests(user_id, date)
        return len(quests) == 3 and all(q["completed"] for q in quests)

    def reroll_quest(self, user_id: int, tier: int, new_key: str, new_target: int) -> bool:
        date = datetime.now(EST).date().isoformat()
        with self._conn() as conn:
            return conn.execute(
                """UPDATE daily_quests SET quest_key=?, target=?, progress=0, completed=0
                   WHERE user_id=? AND date=? AND tier=?""",
                (new_key, new_target, user_id, date, tier)
            ).rowcount > 0

    # ── RAID BOSS ─────────────────────────────────────────────────────────────

    def get_active_boss(self) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM raid_bosses WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None

    def spawn_boss(self, hp: int, duration_days: int = 14, season: str = "") -> dict:
        now = _utcnow_naive()
        ends = now + timedelta(days=duration_days)
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO raid_bosses (hp, hp_remaining, started_at, ends_at, season)
                   VALUES (?,?,?,?,?)""",
                (hp, hp, now.isoformat(), ends.isoformat(), season)
            )
            result = dict(conn.execute("SELECT * FROM raid_bosses WHERE id=?", (cur.lastrowid,)).fetchone())
        return result

    def add_raid_damage(self, user_id: int, boss_id: int, raw_damage: int) -> dict:
        if int(user_id) in LITE_USER_IDS:
            return {"hp_taken": 0, "new_remaining": None, "killed": False}
        with self._conn() as conn:
            boss = conn.execute("SELECT * FROM raid_bosses WHERE id=?", (boss_id,)).fetchone()
            if not boss:
                return {"hp_taken": 0}
            hp_can_take = max(boss["hp_remaining"], 0)
            hp_taken = min(raw_damage, hp_can_take)

            existing = conn.execute(
                "SELECT * FROM raid_damage WHERE user_id=? AND boss_id=?",
                (user_id, boss_id)
            ).fetchone()

            if existing:
                conn.execute(
                    "UPDATE raid_damage SET raw_damage=raw_damage+?, hp_taken=hp_taken+? WHERE id=?",
                    (raw_damage, hp_taken, existing["id"])
                )
            else:
                conn.execute(
                    "INSERT INTO raid_damage (user_id, boss_id, raw_damage, hp_taken) VALUES (?,?,?,?)",
                    (user_id, boss_id, raw_damage, hp_taken)
                )

            new_remaining = max(boss["hp_remaining"] - hp_taken, 0)
            conn.execute(
                "UPDATE raid_bosses SET hp_remaining=? WHERE id=?",
                (new_remaining, boss_id)
            )

            killed = new_remaining <= 0
            if killed:
                conn.execute(
                    "UPDATE raid_bosses SET killed=1, ended_at=? WHERE id=? AND ended_at IS NULL",
                    (_utcnow_naive().isoformat(), boss_id)
                )

        return {"hp_taken": hp_taken, "new_remaining": new_remaining, "killed": killed}

    def get_raid_leaderboard(self, boss_id: int, limit: int = 20) -> list[dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                """SELECT rd.*, u.username FROM raid_damage rd
                   JOIN users u ON rd.user_id=u.user_id
                   WHERE rd.boss_id=? AND rd.user_id NOT IN ({})
                   ORDER BY rd.raw_damage DESC LIMIT ?""".format(
                    ",".join("?" for _ in LITE_USER_IDS) or "0"
                ),
                (boss_id, *list(LITE_USER_IDS), limit)
            ).fetchall()]

    def get_user_raid_damage(self, user_id: int, boss_id: int) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT raw_damage FROM raid_damage WHERE user_id=? AND boss_id=?",
                (user_id, boss_id)
            ).fetchone()
            return row["raw_damage"] if row else 0

    def get_user_total_raid_damage(self, user_id: int) -> int:
        """Sum of raw_damage across all bosses (lifetime)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(raw_damage), 0) AS t FROM raid_damage WHERE user_id=?",
                (user_id,),
            ).fetchone()
            return int(row["t"]) if row else 0

    def get_raid_damage_user_ids(self, boss_id: int) -> list[int]:
        """Distinct users who dealt any raw damage to this boss."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT user_id FROM raid_damage WHERE boss_id=? AND raw_damage>0 AND user_id NOT IN ({})".format(
                    ",".join("?" for _ in LITE_USER_IDS) or "0"
                ),
                (boss_id, *list(LITE_USER_IDS)),
            ).fetchall()
            return [int(r["user_id"]) for r in rows]

    def get_raid_participants(self, boss_id: int, min_damage: int = 0) -> list[dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                """SELECT rd.*, u.username FROM raid_damage rd
                   JOIN users u ON rd.user_id=u.user_id
                   WHERE rd.boss_id=? AND rd.raw_damage>=? AND rd.user_id NOT IN ({})
                   ORDER BY rd.raw_damage DESC""".format(
                    ",".join("?" for _ in LITE_USER_IDS) or "0"
                ),
                (boss_id, min_damage, *list(LITE_USER_IDS))
            ).fetchall()]

    def end_boss(self, boss_id: int) -> Optional[dict]:
        with self._conn() as conn:
            boss = conn.execute("SELECT * FROM raid_bosses WHERE id=?", (boss_id,)).fetchone()
            if not boss:
                return None
            boss = dict(boss)
            if boss["ended_at"]:
                # Important: callers use end_boss() as a "claim" for reward resolution.
                # Returning None here prevents double-award if the weekly job runs twice.
                return None
            killed = boss["hp_remaining"] <= 0
            ended_at = _utcnow_naive().isoformat()
            cur = conn.execute(
                "UPDATE raid_bosses SET killed=?, ended_at=? WHERE id=? AND ended_at IS NULL",
                (int(killed), ended_at, boss_id)
            )
            if cur.rowcount <= 0:
                return None
            row2 = conn.execute("SELECT * FROM raid_bosses WHERE id=?", (boss_id,)).fetchone()
            return dict(row2) if row2 else None

    def calculate_next_boss_hp(self, boss: dict) -> int:
        hp_taken = boss["hp"] - boss["hp_remaining"]
        base = int(hp_taken * 1.25)
        modifier = 1.025 if boss["killed"] else 0.975
        return max(int(base * modifier), 500)

    # ── BADGES ────────────────────────────────────────────────────────────────

    def get_badges(self, user_id: int) -> list[dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM badges WHERE user_id=? ORDER BY earned_at",
                (user_id,)
            ).fetchall()]

    def award_badge(self, user_id: int, badge_key: str, tier: int = 1) -> bool:
        """Returns True if this was a new award."""
        with self._conn() as conn:
            try:
                conn.execute(
                    "INSERT INTO badges (user_id, badge_key, tier) VALUES (?,?,?)",
                    (user_id, badge_key, tier)
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def has_badge(self, user_id: int, badge_key: str, tier: int = 1) -> bool:
        with self._conn() as conn:
            return conn.execute(
                "SELECT 1 FROM badges WHERE user_id=? AND badge_key=? AND tier=?",
                (user_id, badge_key, tier)
            ).fetchone() is not None

    def get_badge_progress(self, user_id: int, badge_key: str) -> float:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT current_value FROM badge_progress WHERE user_id=? AND badge_key=?",
                (user_id, badge_key)
            ).fetchone()
            return row["current_value"] if row else 0

    def set_badge_progress(self, user_id: int, badge_key: str, value: float):
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO badge_progress (user_id, badge_key, current_value)
                   VALUES (?,?,?) ON CONFLICT(user_id, badge_key) DO UPDATE SET current_value=?""",
                (user_id, badge_key, value, value)
            )

    def increment_badge_progress(self, user_id: int, badge_key: str, amount: float = 1) -> float:
        current = self.get_badge_progress(user_id, badge_key)
        new_val = current + amount
        self.set_badge_progress(user_id, badge_key, new_val)
        return new_val

    def set_featured_badges(self, user_id: int, badge_keys: list[str]):
        with self._conn() as conn:
            conn.execute(
                "UPDATE users SET featured_badges=? WHERE user_id=?",
                (json.dumps(badge_keys[:3]), user_id)
            )

    def get_featured_badges(self, user_id: int) -> list[str]:
        user = self.get_user(user_id)
        if not user:
            return []
        raw = safe_json_loads(user.get("featured_badges"), default=[])
        if not isinstance(raw, list):
            return []
        out: list[str] = []
        for x in raw:
            try:
                out.append(str(x))
            except Exception:
                continue
        return out[:3]

    # ── SOCIAL (Cheers, Bounties, Beacons) ────────────────────────────────────

    def send_cheer(self, sender_id: int, receiver_id: int) -> bool:
        # Enforce: 1 cheer per sender→receiver per EST day
        today_est = datetime.now(EST).date().isoformat()
        pair_key = f"cheer_pair_{sender_id}_{receiver_id}_date"
        if self.get_setting(sender_id, pair_key, "") == today_est:
            return False

        with self._conn() as conn:
            conn.execute(
                "INSERT INTO cheers (sender_id, receiver_id) VALUES (?,?)",
                (sender_id, receiver_id)
            )
            conn.execute(
                "UPDATE users SET cheers_sent_total=cheers_sent_total+1, seasonal_cheers_sent=seasonal_cheers_sent+1 WHERE user_id=?",
                (sender_id,),
            )
        self.set_setting(sender_id, pair_key, today_est)

        # Receiver reward: +25 points per cheer, max +75 per EST day
        pts_date_key = "cheer_pts_date"
        pts_earned_key = "cheer_pts_earned"
        if self.get_setting(receiver_id, pts_date_key, "") != today_est:
            self.set_setting(receiver_id, pts_date_key, today_est)
            self.set_setting(receiver_id, pts_earned_key, "0")
        try:
            earned_today = int(self.get_setting(receiver_id, pts_earned_key, "0") or 0)
        except Exception:
            earned_today = 0
        grant = max(0, min(25, 75 - earned_today))
        if grant > 0:
            self.add_points(receiver_id, grant, reason="Cheer received")
            self.set_setting(receiver_id, pts_earned_key, str(earned_today + grant))
        else:
            grant = 0
        return True

    def get_cheers_sent(self, user_id: int) -> int:
        user = self.get_user(user_id)
        if user is None:
            return 0
        return int(user.get("cheers_sent_total") or 0)

    def get_cheers_received(self, user_id: int) -> int:
        with self._conn() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM cheers WHERE receiver_id=?", (user_id,)
            ).fetchone()[0]

    def create_bounty(self, owner_id: int, target_id: int, *, cost_coins: int = 5) -> int:
        # Pending bounty expires if not activated within 12 hours
        expires = (_utcnow_naive() + timedelta(hours=12)).isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO bounties (owner_id, target_id, expires_at, cost_coins) VALUES (?,?,?,?)",
                (owner_id, target_id, expires, cost_coins)
            )
            return cur.lastrowid

    def get_active_bounty(self, target_id: int) -> Optional[dict]:
        now = _utcnow_naive().isoformat()
        with self._conn() as conn:
            row = conn.execute(
                """SELECT * FROM bounties
                   WHERE target_id=? AND used=0 AND refunded=0 AND (
                     (activated=0 AND expires_at>?)
                     OR (activated=1 AND buff_expires_at>?)
                   )
                   ORDER BY created_at DESC LIMIT 1""",
                (target_id, now, now)
            ).fetchone()
            return dict(row) if row else None

    def activate_pending_bounty_for_target(self, target_id: int) -> Optional[dict]:
        """Activate the newest pending bounty for a target (if any). Returns bounty row dict if activated."""
        now = _utcnow_naive()
        now_iso = now.isoformat()
        buff_expires = (now + timedelta(hours=2)).isoformat()
        pending = None
        with self._conn() as conn:
            row = conn.execute(
                """SELECT * FROM bounties
                   WHERE target_id=? AND activated=0 AND used=0 AND refunded=0 AND expires_at>?
                   ORDER BY created_at DESC LIMIT 1""",
                (target_id, now_iso),
            ).fetchone()
            if not row:
                return None
            pending = dict(row)
            conn.execute(
                "UPDATE bounties SET activated=1, activated_at=?, buff_expires_at=? WHERE id=?",
                (now_iso, buff_expires, pending["id"]),
            )
        pending["activated"] = 1
        pending["activated_at"] = now_iso
        pending["buff_expires_at"] = buff_expires
        return pending

    def get_active_bounty_xp_window(self, user_id: int) -> Optional[dict]:
        """Return an active bounty that provides XP buff to this user (as owner or target)."""
        now_iso = _utcnow_naive().isoformat()
        with self._conn() as conn:
            row = conn.execute(
                """SELECT * FROM bounties
                   WHERE activated=1 AND used=0 AND refunded=0 AND buff_expires_at>?
                     AND (owner_id=? OR target_id=?)
                   ORDER BY activated_at DESC LIMIT 1""",
                (now_iso, user_id, user_id),
            ).fetchone()
            return dict(row) if row else None

    def expire_bounties_and_refund(self) -> list[dict]:
        """Refund un-activated bounties whose pending window expired."""
        now_iso = _utcnow_naive().isoformat()
        refunded: list[dict] = []
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM bounties
                   WHERE activated=0 AND used=0 AND refunded=0 AND expires_at<=?""",
                (now_iso,),
            ).fetchall()
            for r in rows:
                b = dict(r)
                amt = int(b.get("cost_coins") or 5)
                # Claim the refund row first (idempotent under concurrent schedulers).
                cur = conn.execute(
                    """UPDATE bounties
                       SET refunded=1, refunded_at=?
                       WHERE id=?
                         AND activated=0 AND used=0 AND refunded=0 AND expires_at<=?""",
                    (now_iso, b["id"], now_iso),
                )
                if cur.rowcount <= 0:
                    continue
                if amt > 0:
                    self._add_coins_internal(conn, b["owner_id"], amt)
                b["refund_coins"] = amt
                refunded.append(b)
        return refunded

    def use_bounty(self, bounty_id: int) -> bool:
        """Mark a bounty as used once (idempotent claim). Returns True only for the winner."""
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE bounties SET used=1 WHERE id=? AND used=0",
                (bounty_id,),
            )
            return cur.rowcount > 0

    def create_beacon(self, user_id: int, channel_id: int, duration_hours: int = 2) -> int:
        now = _utcnow_naive()
        expires = (now + timedelta(hours=duration_hours)).isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO beacons (user_id, channel_id, expires_at) VALUES (?,?,?)",
                (user_id, channel_id, expires)
            )
            return cur.lastrowid

    def get_active_beacon(self, channel_id: int) -> Optional[dict]:
        now = _utcnow_naive().isoformat()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM beacons WHERE channel_id=? AND active=1 AND expires_at>?",
                (channel_id, now)
            ).fetchone()
            return dict(row) if row else None

    def get_active_beacons(self) -> list[dict]:
        now = _utcnow_naive().isoformat()
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM beacons WHERE active=1 AND expires_at>?", (now,)
            ).fetchall()]

    def expire_beacons(self):
        now = _utcnow_naive().isoformat()
        with self._conn() as conn:
            conn.execute("UPDATE beacons SET active=0 WHERE expires_at<=? AND active=1", (now,))

    def cleanup_old_data(self, days: int = 30):
        cutoff = (_utcnow_naive() - timedelta(days=days)).isoformat()
        with self._conn() as conn:
            conn.execute("DELETE FROM daily_quests WHERE date < ?", (cutoff[:10],))
            conn.execute("DELETE FROM bounties WHERE used=1 AND created_at < ?", (cutoff,))
            # Safety: never delete expired bounties that were not refunded (coins would be lost).
            conn.execute("DELETE FROM bounties WHERE expires_at < ? AND refunded=1", (cutoff,))
            conn.execute("DELETE FROM beacons WHERE active=0 AND expires_at < ?", (cutoff,))
            conn.execute("DELETE FROM reminders WHERE sent=1 AND fire_at < ?", (cutoff,))
            # Keep history tables for small-bot deployments (≤10 users): point_transactions, gacha_history, cheers.
            # Keep raid history forever (raid_damage + raid_bosses).

    # ── ACTIVE EFFECTS (Potions) ──────────────────────────────────────────────

    def activate_potion(self, user_id: int, effect_type: str, multiplier: float,
                        duration_hours: float) -> tuple[int, bool]:
        """Start or extend one active row per effect_type: rebuy adds duration. Returns (effect_row_id, extended)."""
        now = _utcnow_naive()
        now_s = now.isoformat()
        add_h = timedelta(hours=duration_hours)
        with self._conn() as conn:
            row = conn.execute(
                """SELECT id, expires_at, multiplier FROM active_effects
                   WHERE user_id=? AND effect_type=? AND expires_at>?""",
                (user_id, effect_type, now_s),
            ).fetchone()
            if row:
                cur_exp = _parse_stored(row["expires_at"])
                base = max(cur_exp, now)
                new_exp = (base + add_h).isoformat()
                new_mult = max(float(row["multiplier"]), float(multiplier))
                conn.execute(
                    "UPDATE active_effects SET expires_at=?, multiplier=? WHERE id=?",
                    (new_exp, new_mult, row["id"]),
                )
                return (row["id"], True)
            expires = (now + add_h).isoformat()
            cur = conn.execute(
                """INSERT INTO active_effects (user_id, effect_type, multiplier, activated_at, expires_at)
                   VALUES (?,?,?,?,?)""",
                (user_id, effect_type, multiplier, now_s, expires),
            )
            return (cur.lastrowid, False)

    def get_active_potions(self, user_id: int) -> list[dict]:
        now = _utcnow_naive().isoformat()
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                """SELECT * FROM active_effects WHERE user_id=? AND effect_type LIKE 'potion%'
                   AND expires_at>?""",
                (user_id, now)
            ).fetchall()]

    def get_potion_overlap_multiplier(self, user_id: int, session_start_str: str,
                                       session_end_str: str, session_minutes: int) -> dict:
        """Calculate effective XP multiplier based on potion overlap with session.
        Returns {"multiplier": float, "bonus_xp": int}."""
        sess_start = _parse_stored(session_start_str)
        sess_end = _parse_stored(session_end_str)
        total_session_secs = (sess_end - sess_start).total_seconds()
        if total_session_secs <= 0:
            return {"multiplier": 1.0, "bonus_xp": 0}

        with self._conn() as conn:
            potions = conn.execute(
                """SELECT * FROM active_effects WHERE user_id=? AND effect_type LIKE 'potion%'""",
                (user_id,)
            ).fetchall()

        if not potions:
            return {"multiplier": 1.0, "bonus_xp": 0}

        weighted_mult = 0.0
        for p in potions:
            p = dict(p)
            pot_start = _parse_stored(p["activated_at"])
            pot_end = _parse_stored(p["expires_at"])
            overlap_start = max(sess_start, pot_start)
            overlap_end = min(sess_end, pot_end)
            overlap_secs = max((overlap_end - overlap_start).total_seconds(), 0)
            if overlap_secs > 0:
                fraction = overlap_secs / total_session_secs
                weighted_mult += (p["multiplier"] - 1.0) * fraction

        effective = min(1.0 + weighted_mult, 2.0)
        bonus_xp = 0
        if effective > 1.0:
            base_xp = self._calc_xp(session_minutes)
            bonus_xp = int(base_xp * (effective - 1.0))

        return {"multiplier": round(effective, 3), "bonus_xp": bonus_xp}

    def expire_effects(self):
        now = _utcnow_naive().isoformat()
        with self._conn() as conn:
            conn.execute("DELETE FROM active_effects WHERE expires_at<=?", (now,))

    # ── GACHA ─────────────────────────────────────────────────────────────────

    def record_gacha(self, user_id: int, tier: str, cost: int, result: int, is_jackpot: bool = False):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO gacha_history (user_id, tier, cost_coins, result_pts, is_jackpot) VALUES (?,?,?,?,?)",
                (user_id, tier, cost, result, int(is_jackpot))
            )
            if is_jackpot:
                conn.execute("UPDATE users SET has_gold_jackpot=1 WHERE user_id=?", (user_id,))

    def get_gacha_history(self, user_id: int, limit: int = 20) -> list[dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM gacha_history WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit)
            ).fetchall()]

    def get_total_gacha_winnings(self, user_id: int) -> int:
        with self._conn() as conn:
            return conn.execute(
                "SELECT COALESCE(SUM(result_pts),0) FROM gacha_history WHERE user_id=?",
                (user_id,)
            ).fetchone()[0]

    def has_gold_jackpot(self, user_id: int) -> bool:
        user = self.get_user(user_id)
        if user is None:
            return False
        return bool(int(user.get("has_gold_jackpot") or 0))

    # ── INVENTORY ─────────────────────────────────────────────────────────────

    def add_inventory_item(self, user_id: int, item_type: str, item_key: str, quantity: int = 1):
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT * FROM inventory WHERE user_id=? AND item_type=? AND item_key=?",
                (user_id, item_type, item_key)
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE inventory SET quantity=quantity+? WHERE id=?",
                    (quantity, existing["id"])
                )
            else:
                conn.execute(
                    "INSERT INTO inventory (user_id, item_type, item_key, quantity) VALUES (?,?,?,?)",
                    (user_id, item_type, item_key, quantity)
                )

    def get_inventory(self, user_id: int) -> list[dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM inventory WHERE user_id=? AND quantity>0 ORDER BY item_type, item_key",
                (user_id,)
            ).fetchall()]

    def use_inventory_item(self, user_id: int, item_type: str, item_key: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE inventory SET quantity=quantity-1 WHERE user_id=? AND item_type=? AND item_key=? AND quantity>0",
                (user_id, item_type, item_key)
            )
            return cur.rowcount > 0

    # ── SEASONAL ──────────────────────────────────────────────────────────────

    @staticmethod
    def prev_season_key(season_key: str) -> str:
        try:
            name, year_s = season_key.split("_", 1)
            year = int(year_s)
            order = ["spring", "summer", "fall", "winter"]
            if name not in order:
                return ""
            i = order.index(name)
            if i == 0:
                return f"winter_{year - 1}"
            return f"{order[i - 1]}_{year}"
        except Exception:
            return ""

    def add_seasonal_minutes(self, user_id: int, minutes: int):
        with self._conn() as conn:
            conn.execute(
                "UPDATE users SET seasonal_minutes=seasonal_minutes+? WHERE user_id=?",
                (minutes, user_id)
            )

    def get_seasonal_rank(self, user_id: int) -> str:
        user = self.get_user(user_id)
        if not user:
            return "Unranked"
        return seasonal_rank_for_minutes(user.get("seasonal_minutes", 0))

    def get_seasonal_history_top_minutes(self, season_key: str, limit: int = 3) -> list[dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                """SELECT user_id, total_minutes
                   FROM seasonal_history
                   WHERE season_key=?
                   ORDER BY total_minutes DESC
                   LIMIT ?""",
                (season_key, limit),
            ).fetchall()]

    def get_seasonal_history_top_cheers(self, season_key: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                """SELECT user_id, cheers_sent_at_reset
                   FROM seasonal_history
                   WHERE season_key=?
                   ORDER BY cheers_sent_at_reset DESC
                   LIMIT 1""",
                (season_key,),
            ).fetchone()
            return dict(row) if row else None

    def get_seasonal_history_top_points(self, season_key: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                """SELECT user_id, points_at_reset
                   FROM seasonal_history
                   WHERE season_key=?
                   ORDER BY points_at_reset DESC
                   LIMIT 1""",
                (season_key,),
            ).fetchone()
            return dict(row) if row else None

    def get_seasonal_history_best_efficiency(self, season_key: str, *, min_minutes: int) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                """SELECT user_id, total_minutes, points_at_reset,
                          (1.0*points_at_reset / total_minutes) AS ppm
                   FROM seasonal_history
                   WHERE season_key=? AND total_minutes>=? AND total_minutes>0
                   ORDER BY ppm DESC
                   LIMIT 1""",
                (season_key, int(min_minutes)),
            ).fetchone()
            return dict(row) if row else None

    def get_seasonal_history_most_improved(self, season_key: str) -> dict | None:
        prev_key = self.prev_season_key(season_key)
        if not prev_key:
            return None
        with self._conn() as conn:
            row = conn.execute(
                """SELECT cur.user_id,
                          cur.total_minutes AS cur_minutes,
                          COALESCE(prev.total_minutes, 0) AS prev_minutes,
                          (cur.total_minutes - COALESCE(prev.total_minutes, 0)) AS delta_minutes
                   FROM seasonal_history cur
                   LEFT JOIN seasonal_history prev
                     ON prev.user_id=cur.user_id AND prev.season_key=?
                   WHERE cur.season_key=?
                   ORDER BY delta_minutes DESC
                   LIMIT 1""",
                (prev_key, season_key),
            ).fetchone()
            return dict(row) if row else None

    def seasonal_reset(self, season_key: str):
        with self._conn() as conn:
            users = [dict(r) for r in conn.execute("SELECT * FROM users").fetchall()]
            for u in users:
                conn.execute(
                    """INSERT INTO seasonal_history (user_id, season_key, rank, total_minutes, points_at_reset, cheers_sent_at_reset)
                       VALUES (?,?,?,?,?,?) ON CONFLICT(user_id, season_key) DO NOTHING""",
                    (u["user_id"], season_key, seasonal_rank_for_minutes(u.get("seasonal_minutes", 0)),
                     u.get("seasonal_minutes", 0), u.get("points", 0), u.get("seasonal_cheers_sent", 0))
                )
            conn.execute(
                "INSERT INTO point_transactions (user_id, delta, reason) "
                "SELECT user_id, -points, ? FROM users WHERE points>0",
                (f"Seasonal reset: {season_key}",)
            )
            conn.execute("UPDATE users SET points=0, seasonal_minutes=0, seasonal_cheers_sent=0")

        # Perfect Year: 4 consecutive seasons at Godslayer (≥8000 seasonal mins)
        for u in users:
            uid = u["user_id"]
            if int(uid) in LITE_USER_IDS:
                continue
            mins = u.get("seasonal_minutes", 0) or 0
            if mins >= 8000 and seasonal_rank_for_minutes(mins) == "Godslayer":
                cur = int(self.get_badge_progress(uid, "perfect_year_streak")) + 1
                self.set_badge_progress(uid, "perfect_year_streak", float(cur))
                if cur >= 4 and self.award_badge(uid, "perfect_year", 1):
                    self.add_points(uid, BADGE_UNLOCK_POINTS, reason="Badge: Perfect Year", track_earned=True)
            else:
                self.set_badge_progress(uid, "perfect_year_streak", 0.0)

    def get_season_leaderboard(self, limit: int = 20) -> list[dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                """SELECT user_id, username, seasonal_minutes FROM users
                   WHERE seasonal_minutes>0 AND user_id NOT IN ({})
                   ORDER BY seasonal_minutes DESC LIMIT ?""".format(
                    ",".join("?" for _ in LITE_USER_IDS) or "0"
                ),
                (*list(LITE_USER_IDS), limit)
            ).fetchall()]

    # ── USER SETTINGS ─────────────────────────────────────────────────────────

    def get_setting(self, user_id: int, key: str, default: str = "") -> str:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM user_settings WHERE user_id=? AND key=?",
                (user_id, key)
            ).fetchone()
            return row["value"] if row else default

    def set_setting(self, user_id: int, key: str, value: str):
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO user_settings (user_id, key, value)
                   VALUES (?,?,?) ON CONFLICT(user_id, key) DO UPDATE SET value=?""",
                (user_id, key, value, value)
            )

    def get_all_settings(self, user_id: int) -> dict:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT key, value FROM user_settings WHERE user_id=?", (user_id,)
            ).fetchall()
            return {r["key"]: r["value"] for r in rows}

    def save_preferences(self, user_id: int, *, ghost: bool, block_cheers: bool, settings: dict[str, str]):
        """Save explicit values together; untouched categories stay unchanged."""
        from services.scheduling import valid_timezone
        if "timezone" in settings:
            valid_timezone(settings["timezone"])
        with self._conn() as conn:
            conn.execute("UPDATE users SET ghost_mode=?, block_cheers=? WHERE user_id=?", (int(ghost), int(block_cheers), user_id))
            conn.executemany(
                "INSERT INTO user_settings (user_id, key, value) VALUES (?,?,?) ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value",
                [(user_id, key, value) for key, value in settings.items()],
            )

    def set_task_source(self, user_id: int, task_number: int, url: str):
        with self._conn() as conn:
            conn.execute("UPDATE tasks SET source_message_url=? WHERE user_id=? AND user_task_num=? AND completed=0", (url, user_id, task_number))

    def get_dm_enabled(self, user_id: int, dm_type: str) -> bool:
        val = self.get_setting(user_id, f"dm_{dm_type}", "1")
        return val == "1"

    # ── CHANNEL CONFIG ────────────────────────────────────────────────────────

    def set_channel(self, guild_id: int, channel_type: str, channel_id: int):
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO channel_config (guild_id, channel_type, channel_id)
                   VALUES (?,?,?) ON CONFLICT(guild_id, channel_type) DO UPDATE SET channel_id=?""",
                (guild_id, channel_type, channel_id, channel_id)
            )

    def get_channel(self, guild_id: int, channel_type: str) -> Optional[int]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT channel_id FROM channel_config WHERE guild_id=? AND channel_type=?",
                (guild_id, channel_type)
            ).fetchone()
            return row["channel_id"] if row else None

    # ── LEADERBOARDS ──────────────────────────────────────────────────────────

    def get_leaderboard(self, metric: str, limit: int = 20) -> list[dict]:
        with self._conn() as conn:
            if metric == "xp":
                return [dict(r) for r in conn.execute(
                    "SELECT user_id, username, total_xp FROM users WHERE user_id NOT IN ({}) ORDER BY total_xp DESC LIMIT ?".format(
                        ",".join("?" for _ in LITE_USER_IDS) or "0"
                    ),
                    (*list(LITE_USER_IDS), limit)
                ).fetchall()]
            elif metric == "minutes":
                rows = conn.execute(
                    """SELECT user_id, username,
                       (SELECT COALESCE(SUM(duration_minutes),0) FROM study_sessions
                        WHERE study_sessions.user_id=users.user_id AND ended_at IS NOT NULL) as total_minutes
                       FROM users WHERE user_id NOT IN ({})
                       ORDER BY total_minutes DESC LIMIT ?""".format(
                        ",".join("?" for _ in LITE_USER_IDS) or "0"
                    ),
                    (*list(LITE_USER_IDS), limit)
                ).fetchall()
                return [dict(r) for r in rows]
            elif metric == "streak":
                return [dict(r) for r in conn.execute(
                    "SELECT user_id, username, streak FROM users WHERE user_id NOT IN ({}) ORDER BY streak DESC LIMIT ?".format(
                        ",".join("?" for _ in LITE_USER_IDS) or "0"
                    ),
                    (*list(LITE_USER_IDS), limit)
                ).fetchall()]
            elif metric == "seasonal":
                return [dict(r) for r in conn.execute(
                    "SELECT user_id, username, seasonal_minutes FROM users WHERE user_id NOT IN ({}) ORDER BY seasonal_minutes DESC LIMIT ?".format(
                        ",".join("?" for _ in LITE_USER_IDS) or "0"
                    ),
                    (*list(LITE_USER_IDS), limit)
                ).fetchall()]
            elif metric == "raid":
                boss = self.get_active_boss()
                if not boss:
                    return []
                return self.get_raid_leaderboard(boss["id"], limit)
            return []

    # ── ADMIN HELPERS ─────────────────────────────────────────────────────────

    def admin_set_points(self, user_id: int, points: int):
        with self._conn() as conn:
            conn.execute("UPDATE users SET points=? WHERE user_id=?", (points, user_id))

    def admin_set_streak(self, user_id: int, streak: int):
        with self._conn() as conn:
            conn.execute("UPDATE users SET streak=? WHERE user_id=?", (streak, user_id))

    def admin_add_coins(self, user_id: int, amount: int):
        with self._conn() as conn:
            conn.execute("UPDATE users SET coins=coins+? WHERE user_id=?", (amount, user_id))

    def get_zombie_sessions(self, max_age_hours: int = 12) -> list[dict]:
        cutoff = (_utcnow_naive() - timedelta(hours=max_age_hours)).isoformat()
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM study_sessions WHERE ended_at IS NULL AND started_at<? AND (xp_earned=0 OR xp_earned IS NULL)",
                (cutoff,)
            ).fetchall()]

    def kill_zombie_session(self, session_id: int):
        with self._conn() as conn:
            conn.execute(
                "UPDATE study_sessions SET ended_at=?, duration_minutes=0, xp_earned=0, points_earned=0 WHERE id=?",
                (_utcnow_naive().isoformat(), session_id)
            )

    def backup(self, dest_path: str):
        import shutil
        src = sqlite3.connect(self.path)
        dst = sqlite3.connect(dest_path)
        src.backup(dst)
        dst.close()
        src.close()
