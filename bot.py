# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

import asyncio
import json
import time
from collections import defaultdict
import sys
import discord
from discord.ext import commands
from discord import app_commands
import os
import logging
import logging.handlers
import random
import traceback
from dotenv import load_dotenv
from pathlib import Path
from env_config import env_ids
from database import Database, _stored_to_est_date
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime, timezone, timedelta
from constants import EST, COLOR_PRIMARY, COLOR_SUCCESS, COLOR_WARNING, COLOR_MUTED, COLOR_GOLD, USER_NAV_FOOTER, LITE_USER_IDS, SB_PING_ROLE_ID
from utils import fmt_long_date_us, fmt_mins, fmt_date_us, utcnow, safe_json_loads

from cogs.tutorial import TutorialNavView, _embed as tutorial_chapter_embed, get_help_overview_preamble
from views.onboarding import OnboardingView

load_dotenv(Path(__file__).with_name(".env"))

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_DIR = os.getenv("LOG_DIR", ".")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.handlers.RotatingFileHandler(
            os.path.join(LOG_DIR, "study_bot.log"),
            maxBytes=2*1024*1024, backupCount=2, encoding="utf-8"
        ),
        logging.StreamHandler()
    ]
)
logging.getLogger("discord.gateway").setLevel(logging.WARNING)
logging.getLogger("discord.http").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
log = logging.getLogger("StudyBot")


def _parse_allowed_guild_ids() -> set[int]:
    """Read the deployment's guild allowlist without a personal default."""
    return set(env_ids("ALLOWED_GUILD_IDS"))


intents = discord.Intents.default()
intents.members = True
intents.message_content = True

if intents.members and intents.message_content:
    log.info("Privileged intents requested. Ensure they are toggled ON in the Discord Developer Portal.")


class StudyBot(commands.Bot):
    # Lite-mode allowlist: these users can use productivity features only (no RPG systems).
    LITE_USER_IDS = LITE_USER_IDS

    # Root slash commands that are considered RPG/server-impacting and therefore disabled in DMs.
    DM_BLOCKED_RPG_ROOT_COMMANDS = {
        # Economy / shop
        "shop", "gacha", "bounty", "beacon",
        "convert", "prestige", "inventory", "use_potion",
        # Raids
        "raid", "raid_leaderboard",
        # Progression surfaces
        "quests", "quest", "badges", "badge",
        "leaderboard", "points",
        # Rewards shop (spends points)
        "reward",
        # Social / server-impacting
        "cheer", "who",
    }

    @staticmethod
    def slash_root_command_name(interaction: discord.Interaction) -> str | None:
        """Top-level slash name (e.g. ``study`` for ``/study start``). ``None`` if unknown."""
        cmd = getattr(interaction, "command", None)
        if cmd is not None:
            rp = getattr(cmd, "root_parent", None)
            if rp is not None:
                return rp.name
            return cmd.name
        data = getattr(interaction, "data", None) or {}
        name = data.get("name")
        return str(name) if name else None

    def __init__(self):
        super().__init__(command_prefix="!", intents=intents, help_command=None)
        db_path = os.getenv("DB_PATH", "study_bot.db")
        os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)
        self.db = Database(db_path)
        self.scheduler = AsyncIOScheduler(timezone="America/New_York")
        self.owner_id: int = int(os.getenv("OWNER_ID", "0"))
        self.allowed_guild_ids: set[int] | None = _parse_allowed_guild_ids()
        if self.allowed_guild_ids:
            log.info("Guild allowlist enabled (%s server ID(s))", len(self.allowed_guild_ids))
        self.state_lock = None
        self.user_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._user_fetch_cache: dict[int, tuple[discord.User, float]] = {}
        # Gateway stuck detection: force close() so _connect_forever can restart (DNS / wedged reconnect).
        self._gateway_had_ready = False
        self._gateway_disconnect_at: float | None = None
        self._gateway_session_started_at = time.monotonic()
        self._gateway_watchdog_task: asyncio.Task | None = None
        self._gateway_last_stuck_log_at = 0.0
        # Reconnect-light heuristic (see on_ready): increment each READY; >1 ⇒ reconnect.
        self._ready_count = 0

    def reset_gateway_watchdog_session(self) -> None:
        """Call before each bot.start(): fresh timeouts for this connection attempt."""
        t = self._gateway_watchdog_task
        if t is not None and not t.done():
            t.cancel()
        self._gateway_watchdog_task = None
        self._gateway_had_ready = False
        self._gateway_disconnect_at = None
        self._gateway_session_started_at = time.monotonic()
        self._gateway_last_stuck_log_at = 0.0

    def _ensure_gateway_watchdog_task(self) -> None:
        t = self._gateway_watchdog_task
        if t is not None and not t.done():
            return
        self._gateway_watchdog_task = asyncio.create_task(
            self._gateway_stuck_watchdog(), name="gateway_stuck_watchdog"
        )

    async def _gateway_stuck_watchdog(self) -> None:
        """Force bot.close() after prolonged outage so _connect_forever can start a fresh session.

        Env:
        - GATEWAY_STUCK_DISCONNECT_SEC (default 900): after READY, disconnected this long → close.
        - GATEWAY_STUCK_INITIAL_CONNECT_SEC (default 1200): never READY this long → close.
        - GATEWAY_STUCK_POLL_SEC (default 30): poll interval.
        - GATEWAY_STUCK_LOG_EVERY_SEC (default 300): throttle WARNING while stuck (0 = off).
        """
        disconnect_sec = float(os.getenv("GATEWAY_STUCK_DISCONNECT_SEC", "900"))
        initial_sec = float(os.getenv("GATEWAY_STUCK_INITIAL_CONNECT_SEC", "1200"))
        interval = max(5.0, float(os.getenv("GATEWAY_STUCK_POLL_SEC", "30")))
        log_every = float(os.getenv("GATEWAY_STUCK_LOG_EVERY_SEC", "300"))
        try:
            while not self.is_closed():
                await asyncio.sleep(interval)
                if self.is_closed():
                    break
                now = time.monotonic()
                if self.is_ready():
                    # Resume paths may not re-fire on_ready; clear stale disconnect markers.
                    self._gateway_disconnect_at = None
                    continue

                if self._gateway_had_ready and self._gateway_disconnect_at is None:
                    # If on_disconnect didn't run, still start the stuck timer once we observe not-ready.
                    self._gateway_disconnect_at = now

                if self._gateway_had_ready and self._gateway_disconnect_at is not None:
                    stale = now - self._gateway_disconnect_at
                    if stale >= disconnect_sec:
                        log.error(
                            "Gateway stuck disconnected for %.0fs (>= %.0fs) — closing client for full reconnect.",
                            stale,
                            disconnect_sec,
                        )
                        try:
                            await self.close()
                        except Exception:
                            log.debug("gateway watchdog close() failed", exc_info=True)
                        return
                    if (
                        log_every > 0
                        and stale >= 60
                        and (now - self._gateway_last_stuck_log_at) >= log_every
                    ):
                        self._gateway_last_stuck_log_at = now
                        log.warning(
                            "Gateway still disconnected (%.0fs elapsed, %.0fs until forced reconnect).",
                            stale,
                            max(0.0, disconnect_sec - stale),
                        )

                if not self._gateway_had_ready:
                    boot_stale = now - self._gateway_session_started_at
                    if boot_stale >= initial_sec:
                        log.error(
                            "Gateway never reached READY after %.0fs (>= %.0fs) — closing client for full reconnect.",
                            boot_stale,
                            initial_sec,
                        )
                        try:
                            await self.close()
                        except Exception:
                            log.debug("gateway watchdog close() failed", exc_info=True)
                        return
                    if (
                        log_every > 0
                        and boot_stale >= 60
                        and (now - self._gateway_last_stuck_log_at) >= log_every
                    ):
                        self._gateway_last_stuck_log_at = now
                        log.warning(
                            "Still waiting for first READY (%.0fs elapsed, %.0fs until forced reconnect).",
                            boot_stale,
                            max(0.0, initial_sec - boot_stale),
                        )
        except asyncio.CancelledError:
            log.debug("Gateway stuck watchdog cancelled")
            raise

    async def on_disconnect(self) -> None:
        if self._gateway_had_ready:
            self._gateway_disconnect_at = time.monotonic()

    def is_lite_user(self, user_id: int) -> bool:
        return int(user_id) in self.LITE_USER_IDS

    def _schedule_block_dms_enabled(self, user_id: int) -> bool:
        """True if `/settings` → Schedule Reminders allows calendar block DMs (same toggle as `/remind` outbox)."""
        try:
            return self.db.get_dm_enabled(int(user_id), "schedule_reminders")
        except Exception:
            log.debug("schedule_reminders toggle read failed (user_id=%s)", user_id, exc_info=True)
            return False

    _member_allowed_guild_cache: dict[int, tuple[bool, float]] = {}
    MEMBER_ALLOWED_GUILD_CACHE_TTL = 120.0

    async def is_member_of_allowed_guild(self, user_id: int, *, allow_fetch: bool = True) -> bool:
        """True if user is a member of any allowlisted guild.

        `allow_fetch=False` ensures this stays fast and cache-only (safe for global interaction checks).
        """
        allowed = self.allowed_guild_ids
        if not allowed:
            return True
        now = time.monotonic()
        hit = self._member_allowed_guild_cache.get(int(user_id))
        if hit and (now - hit[1]) < self.MEMBER_ALLOWED_GUILD_CACHE_TTL:
            return hit[0]
        ok = False
        for gid in allowed:
            guild = self.get_guild(int(gid))
            if guild is None:
                continue
            if guild.get_member(int(user_id)) is not None:
                ok = True
                break
            if not allow_fetch:
                continue
            try:
                await asyncio.wait_for(guild.fetch_member(int(user_id)), timeout=1.0)
            except Exception:
                continue
            else:
                ok = True
                break
        self._member_allowed_guild_cache[int(user_id)] = (ok, now)
        return ok

    USER_FETCH_CACHE_TTL = 300.0
    USER_FETCH_CACHE_MAX = 2000

    async def get_user_or_fetch(self, user_id: int) -> discord.User | None:
        """Prefer member cache; otherwise fetch with a short TTL cache to reduce API spam."""
        u = self.get_user(user_id)
        if u is not None:
            return u
        now = time.monotonic()
        hit = self._user_fetch_cache.get(user_id)
        if hit and (now - hit[1]) < self.USER_FETCH_CACHE_TTL:
            return hit[0]
        try:
            u = await self.fetch_user(user_id)
        except discord.NotFound:
            return None
        except Exception as e:
            log.debug("fetch_user(%s) failed: %s", user_id, e)
            return None
        if len(self._user_fetch_cache) >= self.USER_FETCH_CACHE_MAX:
            for k in list(self._user_fetch_cache.keys())[:400]:
                self._user_fetch_cache.pop(k, None)
        self._user_fetch_cache[user_id] = (u, now)
        return u

    async def setup_hook(self):
        self.state_lock = asyncio.Lock()
        self.db.initialize()

        self.tree.on_error = self._on_tree_error

        # App command install/context policy.
        #
        # We allow DM slash commands (requires user-install), but we still hard-block
        # execution in non-allowlisted guilds via the interaction check below.
        try:
            from discord.app_commands import AppInstallationType, AppCommandContext

            # Allow: guild installs + user installs (needed for DM slash commands).
            self.tree.allowed_installs = AppInstallationType(guild=True, user=True)
            # Allow: guild + DMs.
            self.tree.allowed_contexts = AppCommandContext(guild=True, dm_channel=True, private_channel=True)
        except Exception:
            # Runtime allowlist gate still protects usage if this fails.
            log.debug("Could not set command install/context restrictions", exc_info=True)

        # Global gate: allow productivity in DMs, but block RPG/server-impacting commands.
        async def _tree_interaction_check(interaction: discord.Interaction) -> bool:
            try:
                # Guild allowlist: if configured, block usage in non-allowlisted servers.
                # (DM policy is handled below.)
                if interaction.guild_id is not None and self.allowed_guild_ids:
                    if interaction.guild_id not in self.allowed_guild_ids:
                        if interaction.user.id == self.owner_id or self.is_lite_user(interaction.user.id):
                            # Owner / lite may use the bot outside the home server, but not RPG surfaces.
                            # Slash commands are RPG-gated below; components/modals stay allowed so existing
                            # button flows (e.g. study controls) keep working for owner/lite away from home.
                            if interaction.type in (
                                discord.InteractionType.component,
                                discord.InteractionType.modal_submit,
                            ):
                                return True
                            if interaction.type == discord.InteractionType.ping:
                                return True
                            root = StudyBot.slash_root_command_name(interaction)
                            if root and root in self.DM_BLOCKED_RPG_ROOT_COMMANDS:
                                msg = (
                                    "RPG / economy commands only work in the allowlisted server. "
                                    "Productivity commands (study, stats, etc.) still work here."
                                )
                                try:
                                    if not interaction.response.is_done():
                                        await interaction.response.send_message(msg, ephemeral=True)
                                    else:
                                        await interaction.followup.send(msg, ephemeral=True)
                                except Exception:
                                    pass
                                return False
                            return True
                        msg = "This bot is private and only works in the allowlisted server."
                        try:
                            if not interaction.response.is_done():
                                await interaction.response.send_message(msg, ephemeral=True)
                            else:
                                await interaction.followup.send(msg, ephemeral=True)
                        except Exception:
                            pass
                        return False

                if interaction.guild_id is None:
                    # Private bot protection: only allow DM usage for members of the allowlisted server(s).
                    # This prevents non-member alts/spam from bloating the SQLite DB.
                    if interaction.user.id == self.owner_id:
                        # Owner is always allowed in DMs (avoids cache-miss false negatives).
                        is_member = True
                    elif self.is_lite_user(interaction.user.id):
                        # Lite users are explicitly allowed to use productivity features in DMs,
                        # even if they're not in the allowlisted server.
                        is_member = True
                    else:
                        try:
                            # Cache-only to avoid slow network calls in the interaction check.
                            is_member = await self.is_member_of_allowed_guild(interaction.user.id, allow_fetch=False)
                        except Exception:
                            is_member = False
                        if not is_member:
                            # Fallback: allow a single fast fetch check to avoid cache-miss false negatives after restarts.
                            try:
                                is_member = await self.is_member_of_allowed_guild(interaction.user.id, allow_fetch=True)
                            except Exception:
                                is_member = False
                    if not is_member:
                        msg = "This bot is private. Please use it from the server to continue."
                        try:
                            if not interaction.response.is_done():
                                await interaction.response.send_message(msg, ephemeral=True)
                            else:
                                await interaction.followup.send(msg, ephemeral=True)
                        except Exception:
                            pass
                        return False

                    data = getattr(interaction, "data", None) or {}
                    root_name = self.slash_root_command_name(interaction) or data.get("name")
                    if root_name in self.DM_BLOCKED_RPG_ROOT_COMMANDS:
                        msg = "RPG features are disabled in DMs. Use this command in the server."
                        try:
                            if not interaction.response.is_done():
                                await interaction.response.send_message(msg, ephemeral=True)
                            else:
                                await interaction.followup.send(msg, ephemeral=True)
                        except Exception:
                            pass
                        return False
            except Exception:
                log.exception("Interaction DM gate failed")
                # Safer default: if the DM gate itself breaks, fail closed in DMs (prevents DB bloat via alts/spam).
                # In guilds, fail open to avoid bricking the server UX.
                return interaction.guild_id is not None
            return True

        self.tree.interaction_check = _tree_interaction_check

        cogs = [
            "cogs.reminders",
            "cogs.tasks",
            "cogs.study",
            "cogs.schedule",
            "cogs.rewards",
            "cogs.stats",
            "cogs.temptation",
            "cogs.pomodoro",
            "cogs.projects",
            "cogs.checkin",
            "cogs.export",
            "cogs.economy",
            "cogs.quests",
            "cogs.shop",
            "cogs.raid",
            "cogs.social",
            "cogs.badges",
            "cogs.profile",
            "cogs.tutorial",
            "cogs.admin",
        ]
        for cog in cogs:
            try:
                await self.load_extension(cog)
                log.info(f"Loaded: {cog}")
            except Exception as e:
                log.error(f"Failed to load {cog}: {e}")

        # Startup self-check: fail loudly if core cogs didn't load.
        must = ("Study", "Stats", "Admin")
        missing = [name for name in must if name not in self.cogs]
        if missing:
            log.critical("Core cogs failed to load: %s", ", ".join(missing))
            raise RuntimeError(f"Core cogs failed to load: {', '.join(missing)}")

        dev_guild_raw = os.getenv("DEV_GUILD_ID", "").strip()
        # Sync controls:
        # - SYNC_COMMANDS=true: global sync (enables slash commands in DMs)
        # - SYNC_GUILD_COMMANDS=true: guild sync (fast iteration in one server)
        sync_global = os.environ.get("SYNC_COMMANDS", "false").lower() == "true"
        sync_guild = os.environ.get("SYNC_GUILD_COMMANDS", "false").lower() == "true"

        # Guild sync target: explicit DEV_GUILD_ID, else exactly one allowlisted guild (common "locked" setup).
        guild_id_for_sync: int | None = None
        if dev_guild_raw.isdigit():
            guild_id_for_sync = int(dev_guild_raw)
        elif self.allowed_guild_ids and len(self.allowed_guild_ids) == 1:
            guild_id_for_sync = next(iter(self.allowed_guild_ids))

        if guild_id_for_sync is not None and sync_guild:
            guild = discord.Object(id=guild_id_for_sync)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            log.info("Slash commands synced to guild id=%s (DEV_GUILD_ID or sole ALLOWED_GUILD_IDS).", guild_id_for_sync)
            try:
                cmds = await self.tree.fetch_commands(guild=guild)
                top_names = sorted({c.name for c in cmds})
                log.info(
                    "Slash registry check: count=%s tutorial=%s bundle=%s",
                    len(cmds),
                    "tutorial" in top_names,
                    "bundle" in top_names,
                )
            except Exception as e:
                log.warning("Could not fetch guild commands after sync: %s", e)
            # Optional maintenance to avoid duplicate registrations across guild/global.
            # These are *opt-in* because they can affect DM command availability.
            sync_global_too = os.environ.get("SYNC_GLOBAL_COMMAND_SYNC", "").lower() == "true"
            clear_globals = os.environ.get("CLEAR_GLOBAL_COMMANDS", "").lower() == "true"
            clear_guild = os.environ.get("CLEAR_GUILD_COMMANDS", "").lower() == "true"
            aid = self.application_id
            if sync_global_too:
                try:
                    await self.tree.sync()
                    if aid:
                        if clear_guild:
                            await self.http.bulk_upsert_guild_commands(aid, guild_id_for_sync, [])
                    log.info(
                        "Global slash sync done. (Guild/global maintenance may take up to ~1h in DMs.)",
                        guild_id_for_sync,
                    )
                except Exception as e:
                    log.warning("Global slash sync or guild clear failed: %s", e)
            elif aid:
                try:
                    if clear_globals:
                        await self.http.bulk_upsert_global_commands(aid, [])
                        log.info(
                            "Cleared **global** slash registrations on Discord (guild id=%s keeps its copy).",
                            guild_id_for_sync,
                        )
                except Exception as e:
                    log.warning("Could not clear global slash commands (duplicates may persist): %s", e)
            else:
                log.warning("application_id unset — skipped global slash clear.")
        if sync_global:
            # Optional: wipe globals first to resolve stale/ghost registrations in DMs.
            clear_globals = os.environ.get("CLEAR_GLOBAL_COMMANDS", "").lower() == "true"
            aid = self.application_id
            if clear_globals and aid:
                try:
                    await self.http.bulk_upsert_global_commands(aid, [])
                    log.info("Cleared global slash commands before global sync.")
                except Exception as e:
                    log.warning("Could not clear global commands before sync: %s", e)
            await self.tree.sync()
            log.info("Slash commands synced globally.")
            try:
                cmds = await self.tree.fetch_commands()
                top_names = sorted({c.name for c in cmds})
            except Exception as e:
                log.warning("Could not fetch global commands after sync: %s", e)
        else:
            log.info(
                "Skipped slash sync. To register/update guild commands, set SYNC_GUILD_COMMANDS=true (or SYNC_COMMANDS=true) "
                "for one boot. For slash commands in **DMs**, set SYNC_GLOBAL_COMMAND_SYNC=true on a boot after guild sync "
                "(or use SYNC_COMMANDS=true alone for global-only)."
            )

        self.scheduler.start()
        await self._load_reminders()
        self._schedule_recurring()
        log.info("Scheduler started.")

    # ── END setup_hook ────────────────────────────────────────────────────────

    # ── One-time onboarding nudge ─────────────────────────────────────────────

    async def on_app_command_completion(self, interaction: discord.Interaction, command: app_commands.Command):
        """After the user's first successful slash command, show onboarding buttons once."""
        try:
            uid = interaction.user.id
            self.db.ensure_user(uid, str(interaction.user))

            # Don't nudge inside the nudge itself / obvious entry points.
            cname = getattr(command, "name", "") or ""
            if cname in ("help", "tutorial", "settings"):
                return

            if self.db.get_setting(uid, "onboarding_seen", "0") == "1":
                return

            # Mark first so we never spam even if DM/send fails.
            self.db.set_setting(uid, "onboarding_seen", "1")

            embed = discord.Embed(
                title="👋 Welcome to StudyBot",
                description=(
                    "Two quick setup steps (recommended):\n"
                    "1) Configure what the bot is allowed to DM you.\n"
                    "2) Skim the tutorial overview so you know the core loop.\n\n"
                    "Use the buttons below — this message only appears once."
                ),
                color=COLOR_PRIMARY,
            )
            await interaction.followup.send(embed=embed, view=OnboardingView(self, uid), ephemeral=True)
        except Exception:
            # Never let onboarding break command execution.
            return

    # ── Global error handler ──────────────────────────────────────────────────

    async def _on_tree_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        cooldown_err = error
        if isinstance(error, app_commands.CommandInvokeError):
            inner = getattr(error, "original", None)
            if inner is not None:
                cooldown_err = inner
        if isinstance(cooldown_err, app_commands.CommandOnCooldown):
            ra = float(getattr(cooldown_err, "retry_after", 0) or 0)
            ra_s = f"{ra:.1f}" if ra < 10 and abs(ra - round(ra)) > 0.05 else f"{int(round(ra))}"
            msg = f"This command is on cooldown. Try again in **{ra_s}s**."
        elif isinstance(error, app_commands.MissingPermissions):
            msg = "You don't have permission to use this command."
        elif isinstance(error, app_commands.CheckFailure):
            msg = "You can't use this command right now."
        else:
            msg = "Something went wrong. The error has been logged."
            log.error(f"Unhandled command error: {error}\n{traceback.format_exception(type(error), error, error.__traceback__)}")

        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass

    # ── Reminder loading ──────────────────────────────────────────────────────

    async def _load_reminders(self):
        reminders = self.db.get_all_pending_reminders()
        now = datetime.now(timezone.utc)
        scheduled, late = 0, 0
        for r in reminders:
            if r["fire_at"] > now:
                self._add_reminder_job(r)
                scheduled += 1
            else:
                async def _fire_late(reminder=r):
                    self.db.enqueue_outbox(
                        target_type="user",
                        target_id=int(reminder["user_id"]),
                        kind="reminder_late",
                        settings_key="schedule_reminders",
                        dedupe_key=f"reminder_late:{int(reminder['id'])}:{int(reminder['user_id'])}",
                        embed={
                            "title": "⏰ Late Reminder!",
                            "description": str(reminder["message"]),
                            "color": int(COLOR_WARNING),
                            "footer": "This reminder fired while the bot was offline.",
                        },
                    )
                    self.db.mark_reminder_sent(reminder["id"])
                await asyncio.sleep(0.25)
                asyncio.create_task(_fire_late())
                late += 1
        log.info(f"Loaded {scheduled} reminders, fired {late} overdue.")

    def _add_reminder_job(self, reminder: dict):
        fire_time = reminder["fire_at"]
        if fire_time > utcnow():
            self.scheduler.add_job(
                self._send_reminder,
                trigger="date",
                run_date=fire_time,
                args=[reminder["id"], reminder["user_id"], reminder["message"]],
                id=f"reminder_{reminder['id']}",
                replace_existing=True
            )

    async def _send_reminder(self, reminder_id: int, user_id: int, message: str):
        now_est = datetime.now(EST).strftime("%I:%M %p EST")
        self.db.enqueue_outbox(
            target_type="user",
            target_id=int(user_id),
            kind="reminder",
            settings_key="schedule_reminders",
            dedupe_key=f"reminder:{int(reminder_id)}:{int(user_id)}",
            embed={
                "title": "⏰ Reminder!",
                "description": str(message),
                "color": 0x5865F2,
                "footer": f"StudyBot • {now_est}",
            },
        )
        self.db.mark_reminder_sent(reminder_id)

    # ── Recurring jobs ────────────────────────────────────────────────────────

    def _schedule_recurring(self):
        self.scheduler.add_job(
            self._daily_reset,
            CronTrigger(hour=0, minute=0, timezone=EST),
            id="daily_reset", replace_existing=True
        )
        # Every wall-clock minute (EST) so schedule blocks can use any :00–:59 start time.
        self.scheduler.add_job(
            self._send_schedule_reminders,
            CronTrigger(minute="*", second=0, timezone=EST),
            id="sched_minutely",
            replace_existing=True,
        )
        self.scheduler.add_job(
            self._run_checkins,
            CronTrigger(minute=1, timezone=EST),
            id="checkins", replace_existing=True
        )
        self.scheduler.add_job(
            self._weekly_report,
            CronTrigger(day_of_week="sun", hour=18, minute=0, timezone=EST),
            id="weekly_report", replace_existing=True
        )
        # Friday 11:59 PM — wipe unused streak freezes
        self.scheduler.add_job(
            self._friday_freeze_wipe,
            CronTrigger(day_of_week="fri", hour=23, minute=59, timezone=EST),
            id="freeze_wipe", replace_existing=True
        )
        # Biweekly raid boss check (every Monday at midnight)
        self.scheduler.add_job(
            self._raid_boss_cycle,
            CronTrigger(day_of_week="mon", hour=0, minute=1, timezone=EST),
            id="raid_cycle", replace_existing=True
        )
        # Daily raid HP digest to configured raid/general channels (combine with other server posts here later)
        self.scheduler.add_job(
            self._raid_daily_digest,
            CronTrigger(hour=12, minute=0, timezone=EST),
            id="raid_daily_digest", replace_existing=True
        )
        # Seasonal resets (Mar/Jun/Sep/Dec 21st)
        for month in [3, 6, 9, 12]:
            self.scheduler.add_job(
                self._seasonal_reset,
                CronTrigger(month=month, day=21, hour=0, minute=0, timezone=EST),
                id=f"season_{month}", replace_existing=True
            )
        # Expire overflow coins and effects every hour
        self.scheduler.add_job(
            self._hourly_cleanup,
            CronTrigger(minute=5, timezone=EST),
            id="hourly_cleanup", replace_existing=True
        )

        # Outbox pump: reliable DMs / announcements.
        self.scheduler.add_job(
            self._pump_outbox,
            IntervalTrigger(seconds=15),
            id="outbox_pump",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

    @staticmethod
    def _embed_from_payload(payload: dict) -> discord.Embed:
        emb = discord.Embed(
            title=payload.get("title") or None,
            description=payload.get("description") or None,
            color=int(payload.get("color") or 0x5865F2),
        )
        for f in (payload.get("fields") or [])[:10]:
            try:
                emb.add_field(
                    name=str(f.get("name") or "—")[:256],
                    value=str(f.get("value") or "—")[:1024],
                    inline=bool(f.get("inline", False)),
                )
            except Exception:
                continue
        footer = payload.get("footer")
        if footer:
            emb.set_footer(text=str(footer)[:2048])
        return emb

    @staticmethod
    def _est_calendar_date_iso(*, at: datetime | None = None) -> str:
        """US Eastern calendar date (YYYY-MM-DD) for enqueue-time dedupe buckets."""
        if at is None:
            at = datetime.now(EST)
        elif at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc).astimezone(EST)
        else:
            at = at.astimezone(EST)
        return at.date().isoformat()

    @staticmethod
    def _weekly_report_dedupe_anchor(*, at: datetime | None = None) -> str:
        """Week label aligned with ``build_weekly_report_embed`` (Monday of the 7-day window)."""
        if at is None:
            at = datetime.now(EST)
        elif at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc).astimezone(EST)
        else:
            at = at.astimezone(EST)
        return (at.date() - timedelta(days=6)).isoformat()

    @staticmethod
    def _outbox_embed_payload(embed: discord.Embed, *, sb: dict | None = None) -> str:
        """Serialize a Discord embed for outbox storage (``discord.Embed.from_dict`` round-trip)."""
        payload: dict = {"discord": embed.to_dict()}
        if sb:
            payload["sb"] = sb
        return json.dumps(payload)

    def _embed_from_outbox_row(self, embed_json: str | None) -> tuple[discord.Embed | None, dict | None]:
        """Parse ``embed_json`` from ``enqueue_outbox`` — supports legacy dicts and Discord API embed dicts."""
        if not embed_json:
            return None, None
        raw = safe_json_loads(embed_json, default=None)
        if not isinstance(raw, dict):
            return None, None
        sb_meta = raw.get("sb") if isinstance(raw.get("sb"), dict) else None
        if "discord" in raw and isinstance(raw["discord"], dict):
            try:
                return discord.Embed.from_dict(raw["discord"]), sb_meta
            except Exception:
                return None, sb_meta
        try:
            legacy = dict(raw)
            legacy.pop("sb", None)
            return self._embed_from_payload(legacy), sb_meta
        except Exception:
            return None, sb_meta

    async def _pump_outbox(self):
        """Send pending outbox messages. Best-effort; retries on transient failures."""
        try:
            self.db.requeue_stale_sending_outbox(stale_after_seconds=300)
        except Exception:
            log.debug("Outbox stale requeue failed", exc_info=True)
        try:
            batch = self.db.claim_outbox_batch(limit=25)
        except Exception:
            log.debug("Outbox claim failed", exc_info=True)
            return
        if not batch:
            return

        for m in batch:
            msg_id = int(m["id"])
            target_type = (m.get("target_type") or "").strip().lower()
            target_id = int(m.get("target_id") or 0)
            content = m.get("content")
            embed_json = m.get("embed_json")
            attempts = int(m.get("attempts") or 0)
            settings_key = (m.get("settings_key") or "").strip()
            kind = (m.get("kind") or "").strip()

            embed_obj = None
            sb_meta: dict | None = None
            if embed_json:
                try:
                    embed_obj, sb_meta = self._embed_from_outbox_row(embed_json)
                    if embed_obj is None:
                        self.db.mark_outbox_failed(msg_id, error="bad embed_json")
                        continue
                except Exception as e:
                    self.db.mark_outbox_failed(msg_id, error=f"bad embed_json: {e}")
                    continue

            # Respect DM toggles at send time (covers "toggle off after enqueue").
            if target_type == "user" and settings_key:
                try:
                    if not self.db.get_dm_enabled(target_id, settings_key):
                        self.db.defer_outbox_later(
                            msg_id,
                            reason=f"dm disabled: {settings_key}",
                            delay_seconds=3600,
                        )
                        continue
                except Exception as e:
                    self.db.retry_outbox_later(msg_id, error=f"dm gate check failed: {e}", delay_seconds=60)
                    continue

            view = None
            if kind == "evening_checkin" and sb_meta:
                ci = sb_meta.get("evening_checkin") if isinstance(sb_meta.get("evening_checkin"), dict) else None
                if ci and ci.get("date"):
                    try:
                        view = CheckinView(self, int(ci["user_id"]), str(ci["date"]))
                    except Exception:
                        view = None

            try:
                if target_type == "user":
                    user = await self.get_user_or_fetch(target_id)
                    if not user:
                        self.db.mark_outbox_failed(msg_id, error="user not found")
                        continue
                    sent = await user.send(content=content, embed=embed_obj, view=view)
                    if view is not None:
                        view.message = sent
                elif target_type == "channel":
                    ch = self.get_channel(target_id) or await self.fetch_channel(target_id)
                    await ch.send(content=content, embed=embed_obj)
                else:
                    self.db.mark_outbox_failed(msg_id, error=f"unknown target_type: {target_type}")
                    continue
            except discord.Forbidden:
                self.db.mark_outbox_failed(msg_id, error="forbidden")
                continue
            except discord.NotFound:
                self.db.mark_outbox_failed(msg_id, error="not found")
                continue
            except Exception as e:
                # Exponential-ish backoff, capped.
                delay = min(15 * (2 ** min(attempts, 5)), 600)
                self.db.retry_outbox_later(msg_id, error=str(e), delay_seconds=delay)
                continue

            try:
                self.db.mark_outbox_sent(msg_id)
            except Exception:
                log.debug("Outbox mark_sent failed (id=%s)", msg_id, exc_info=True)
            await asyncio.sleep(0.15)

    # ── Daily reset + morning briefing ────────────────────────────────────────

    async def _daily_reset(self):
        try:
            log.info("Daily reset (midnight EST)...")
            async with self.state_lock:
                self.db.reset_daily_progress()
                self.db.update_streaks()
                self._assign_daily_quests()
            if self.owner_id:
                await self._send_morning_briefing(self.owner_id)
                await self._check_adaptive_goal(self.owner_id)
        except Exception:
            log.exception("Daily reset failed")

    def _assign_daily_quests(self):
        try:
            quest_cog = self.cogs.get("Quests")
            if quest_cog:
                for u in self.db.get_all_users():
                    quest_cog.assign_quests_for_user(u["user_id"])
        except Exception as e:
            log.warning(f"Quest assignment failed: {e}")

    async def _send_morning_briefing(self, user_id: int):
        try:
            user_data = self.db.get_user(user_id)
            if not user_data:
                return
            pending_tasks = self.db.get_user_tasks(user_id, include_done=False)
            due_reviews = self.db.get_due_reviews(user_id)
            today_name = fmt_long_date_us(datetime.now(EST).date())
            today_goal = self.db.get_today_goal(user_id)
            date_est = self._est_calendar_date_iso()

            embed = discord.Embed(title=f"🌅 Good morning! It's {today_name}", color=0x5865F2)
            embed.add_field(name="🔥 Streak",     value=f"{user_data['streak']} days", inline=True)
            embed.add_field(name="💎 Points",      value=str(user_data["points"]),      inline=True)
            goal_display = "🛌 Rest Day" if today_goal == 0 else fmt_mins(today_goal)
            embed.add_field(name="🎯 Today's Goal", value=goal_display, inline=True)

            if user_data.get("coins", 0) > 0:
                embed.add_field(name="🪙 Coins", value=str(user_data["coins"]), inline=True)
            if user_data.get("level", 1) > 1:
                embed.add_field(name="⚔️ Level", value=f"Lv.{user_data['level']}", inline=True)

            if due_reviews:
                lines = [f"🔁 {r['title']} (interval: {r['review_interval']}d)" for r in due_reviews[:3]]
                if len(due_reviews) > 3:
                    lines.append(f"_...and {len(due_reviews)-3} more_")
                embed.add_field(name=f"🔁 {len(due_reviews)} Reviews Due", value="\n".join(lines), inline=False)

            regular = [t for t in pending_tasks if not t["is_review"]]
            if regular:
                top = regular[:4]
                lines = [f"• {t['title']} (+{t['points']} pts)" + (f" [{t['project_name']}]" if t.get("project_name") else "") for t in top]
                if len(regular) > 4:
                    lines.append(f"_...and {len(regular)-4} more_")
                embed.add_field(name=f"📋 {len(regular)} Pending Tasks", value="\n".join(lines), inline=False)

            quests = self.db.get_daily_quests(user_id)
            if quests:
                qlines = []
                for q in quests:
                    status = "✅" if q["completed"] else f"{q['progress']}/{q['target']}"
                    qlines.append(f"T{q['tier']}: **{q['quest_key'].replace('_', ' ').title()}** [{status}]")
                embed.add_field(name="📜 Daily Quests", value="\n".join(qlines), inline=False)

            embed.set_footer(text="Let's have a great study day! 📚")
            self.db.enqueue_outbox(
                target_type="user",
                target_id=int(user_id),
                kind="morning_briefing",
                settings_key="morning_briefing",
                dedupe_key=f"morning_briefing:{int(user_id)}:{date_est}",
                embed_json=self._outbox_embed_payload(embed),
            )
        except Exception as e:
            log.warning(f"Morning briefing failed: {e}")

    async def _check_adaptive_goal(self, user_id: int):
        try:
            user_data = self.db.get_user(user_id)
            if not user_data or not user_data.get("adaptive_goals"):
                return
            today_est = datetime.now(EST).date().isoformat()
            last_suggestion = user_data.get("last_goal_suggestion") or ""
            if last_suggestion:
                from datetime import date
                last_date = date.fromisoformat(last_suggestion)
                if (datetime.now(EST).date() - last_date).days < 7:
                    return

            hits = self.db.get_goal_hit_streak(user_id, days=7)
            hit_count = sum(1 for h in hits if h)
            current = user_data["daily_goal_minutes"]

            if hit_count >= 6 and current < 480:
                suggested = min(current + 15, 480)
                msg = (f"🎯 **Adaptive Goal Suggestion**\n"
                       f"You hit your goal **{hit_count}/7 days** — amazing consistency!\n"
                       f"Consider bumping from **{fmt_mins(current)}** → **{fmt_mins(suggested)}**.\n"
                       f"Use `/goals set {suggested}` to apply, or `/goals set-day` for per-day targets.")
            elif hit_count <= 2 and current > 15:
                suggested = max(current - 15, 15)
                msg = (f"🎯 **Adaptive Goal Suggestion**\n"
                       f"You hit your goal only **{hit_count}/7 days** this week.\n"
                       f"No shame — maybe scale back to **{fmt_mins(suggested)}** to rebuild momentum.\n"
                       f"Use `/goals set {suggested}` to apply.")
            else:
                return

            emb = discord.Embed(description=msg, color=0x57F287)
            self.db.enqueue_outbox(
                target_type="user",
                target_id=int(user_id),
                kind="adaptive_goal_suggestion",
                dedupe_key=f"adaptive_goal_suggestion:{int(user_id)}:{today_est}",
                embed_json=self._outbox_embed_payload(emb),
            )
            self.db.set_last_goal_suggestion(user_id, today_est)
        except Exception as e:
            log.warning(f"Adaptive goal check failed: {e}")

    # ── Evening check-ins ─────────────────────────────────────────────────────

    async def _run_checkins(self):
        try:
            now_est = datetime.now(EST)
            current_hour = now_est.hour
            today = now_est.date().isoformat()

            users = self.db.get_checkin_users_for_hour(current_hour)
            for user_data in users:
                uid = user_data["user_id"]
                if self.db.get_checkin(uid, today):
                    continue
                today_mins = self.db.get_study_minutes_on_date(uid, today)
                if today_mins > 0:
                    self.db.record_checkin(uid, today, studied=True, note="Auto: session recorded")
                    continue
                date_est = self._est_calendar_date_iso()
                embed = discord.Embed(title="📋 Evening Check-in", description="Did you study today?", color=0x5865F2)
                embed.add_field(name="🔥 Streak", value=f"{user_data['streak']} days",        inline=True)
                embed.add_field(name="🎯 Goal",   value=fmt_mins(self.db.get_today_goal(uid)), inline=True)
                payload = self._outbox_embed_payload(
                    embed,
                    sb={
                        "evening_checkin": {
                            "user_id": int(uid),
                            "date": str(today),
                        }
                    },
                )
                try:
                    self.db.enqueue_outbox(
                        target_type="user",
                        target_id=int(uid),
                        kind="evening_checkin",
                        settings_key="evening_checkins",
                        dedupe_key=f"evening_checkin:{int(uid)}:{date_est}",
                        embed_json=payload,
                    )
                except Exception as e:
                    log.warning(f"Check-in enqueue failed for {uid}: {e}")
                await asyncio.sleep(0.05)
        except Exception:
            log.exception("Run checkins job failed")

    # ── Schedule block DMs ────────────────────────────────────────────────────

    async def _send_schedule_reminders(self):
        try:
            now_est = datetime.now(EST)
            day = now_est.strftime("%A").lower()
            hour, minute = now_est.hour, now_est.minute
            blocks = self.db.get_schedule_blocks_at(day, hour, minute)
            for block in blocks:
                uid = int(block["user_id"])
                if not self._schedule_block_dms_enabled(uid):
                    continue
                date_est = now_est.date().isoformat()
                dedupe_key = f"schedule_block:{int(block['id'])}:{date_est}:{hour:02d}:{minute:02d}"
                embed = discord.Embed(
                    title="📚 Study Time!",
                    description=f"Your **{block['subject']}** block is starting now!",
                    color=0x57F287
                )
                embed.add_field(name="Duration", value=f"{block['duration_minutes']} min")
                embed.set_footer(text="Use /study start to begin tracking")
                try:
                    self.db.enqueue_outbox(
                        target_type="user",
                        target_id=uid,
                        kind="schedule_block_start",
                        settings_key="schedule_reminders",
                        dedupe_key=dedupe_key,
                        embed_json=self._outbox_embed_payload(embed),
                    )
                    nudge_time = utcnow() + timedelta(minutes=15)
                    self.scheduler.add_job(
                        self._check_proc_for_block,
                        trigger="date",
                        run_date=nudge_time,
                        args=[int(block["user_id"]), str(block["subject"]), int(block["id"])],
                        id=f"proc_{block['user_id']}_{block['id']}",
                        replace_existing=True
                    )
                except Exception:
                    log.debug("schedule enqueue failed", exc_info=True)
                await asyncio.sleep(0.05)
        except Exception:
            log.exception("Schedule reminders job failed")

    async def _check_proc_for_block(self, user_id: int, subject: str, block_id: int):
        if not self._schedule_block_dms_enabled(user_id):
            return
        if self.db.get_active_session(user_id):
            return
        nudges = [
            f"Hey, your **{subject}** block started 15 minutes ago — still going to study? 👀",
            f"Your **{subject}** session was supposed to start. Even 20 minutes counts! 📚",
            f"Quick reminder: **{subject}** block is 15 min in. Jump in when you're ready! ⏰",
        ]
        embed = discord.Embed(title="👋 Gentle Nudge", description=random.choice(nudges), color=0xFEE75C)
        embed.set_footer(text="Use /study start • /remind add to snooze")
        now_est = datetime.now(EST)
        dedupe_key = (
            f"schedule_nudge:{int(block_id)}:{now_est.date().isoformat()}:"
            f"{now_est.hour:02d}:{now_est.minute:02d}"
        )
        try:
            self.db.enqueue_outbox(
                target_type="user",
                target_id=int(user_id),
                kind="schedule_block_nudge",
                settings_key="schedule_reminders",
                dedupe_key=dedupe_key,
                embed_json=self._outbox_embed_payload(embed),
            )
        except Exception:
            log.debug("schedule nudge enqueue failed", exc_info=True)

    # ── Weekly report ─────────────────────────────────────────────────────────

    def build_weekly_report_embed(self, user_id: int) -> discord.Embed | None:
        user_data = self.db.get_user(user_id)
        if not user_data:
            return None

        weekly_mins = self.db.get_weekly_study_minutes(user_id)
        daily = self.db.get_daily_breakdown(user_id, days=7)
        subjects = self.db.get_subject_stats(user_id, days=7, est_calendar_days=True)
        tags = self.db.get_tag_stats(user_id, days=7, est_calendar_days=True)
        sessions = self.db.get_user_sessions(user_id, limit=50)
        all_tasks = self.db.get_user_tasks(user_id, include_done=True)
        week_start_est = (datetime.now(EST).date() - timedelta(days=6)).isoformat()

        def _task_completed_est_date(t: dict) -> str | None:
            raw = t.get("completed_at")
            if not raw:
                return None
            try:
                return _stored_to_est_date(str(raw))
            except Exception:
                return None

        tasks_done = [
            t
            for t in all_tasks
            if t.get("completed")
            and (d := _task_completed_est_date(t)) is not None
            and d >= week_start_est
        ]
        week_sessions = []
        for s in sessions:
            if not s.get("ended_at"):
                continue
            try:
                if _stored_to_est_date(s["started_at"]) >= week_start_est:
                    week_sessions.append(s)
            except Exception:
                continue
        day_map = {d["date"]: d["minutes"] for d in daily}
        today = datetime.now(EST).date()
        days_list = [(today - timedelta(days=6 - i)) for i in range(7)]
        goal_hits = self.db.get_goal_hit_streak(user_id, days=7)
        days_hit = sum(1 for h in goal_hits if h)
        avg_rating = self.db.get_average_focus_rating(user_id)

        embed = discord.Embed(
            title="📊 Weekly Study Report",
            description=datetime.now(EST).strftime("Week ending %B %d, %Y"),
            color=0x5865F2,
        )
        embed.add_field(name="⏱️ Study Time", value=fmt_mins(weekly_mins), inline=True)
        embed.add_field(name="📚 Sessions", value=str(len(week_sessions)), inline=True)
        embed.add_field(name="✅ Tasks Done", value=str(len(tasks_done)), inline=True)
        embed.add_field(name="🎯 Goal Days", value=f"{days_hit}/7", inline=True)
        embed.add_field(name="🔥 Streak", value=f"{user_data['streak']} days", inline=True)
        if avg_rating:
            embed.add_field(
                name="🎯 Avg Focus",
                value=f"{'⭐' * round(avg_rating)} {avg_rating}/5",
                inline=True,
            )

        max_mins = max((day_map.get(d.isoformat(), 0) for d in days_list), default=1) or 1
        lines = []
        for i, d in enumerate(days_list):
            mins = day_map.get(d.isoformat(), 0)
            blocks = min(int(mins / max_mins * 8), 8)
            bar = "█" * blocks + "░" * (8 - blocks)
            check = "✅" if goal_hits[i] else ("📅" if mins > 0 else "  ")
            lines.append(f"`{d.strftime('%a')} {fmt_date_us(d)}` {check} `{bar}` {fmt_mins(mins)}")
        embed.add_field(name="📅 Day by Day", value="\n".join(lines), inline=False)

        if subjects:
            top = subjects[0]
            embed.add_field(
                name="🏆 Top Subject",
                value=f"**{top['subject']}** — {fmt_mins(top['total_minutes'])}",
                inline=True,
            )

        if tags:
            top_tag = tags[0]
            embed.add_field(
                name="🏷️ Top Tag",
                value=f"**{top_tag['tag']}** — {fmt_mins(top_tag['total_minutes'])}",
                inline=True,
            )

        closes = [
            "Keep showing up. That's all it takes. 💪",
            "Consistency is your superpower. 🔥",
            "Every session adds up. You're building something real.",
            "Another week in the books. You're doing great! 🌟",
        ]
        embed.set_footer(text=f"{random.choice(closes)} • Toggle in /settings")
        return embed

    async def _weekly_report(self):
        try:
            anchor = self._weekly_report_dedupe_anchor()
            for row in self.db.get_all_users():
                uid = int(row["user_id"])
                embed = await asyncio.to_thread(self.build_weekly_report_embed, uid)
                if embed is None:
                    continue
                try:
                    self.db.enqueue_outbox(
                        target_type="user",
                        target_id=uid,
                        kind="weekly_report",
                        settings_key="weekly_report",
                        dedupe_key=f"weekly_report:{uid}:{anchor}",
                        embed_json=self._outbox_embed_payload(embed),
                    )
                except Exception:
                    log.debug("weekly report enqueue failed (user_id=%s)", uid, exc_info=True)
                await asyncio.sleep(0.02)
        except Exception:
            log.exception("Weekly report job failed")

    # ── Freeze wipe (Friday 11:59 PM EST) ─────────────────────────────────────

    async def _friday_freeze_wipe(self):
        try:
            log.info("Friday freeze wipe...")
            self.db.wipe_all_freezes()
        except Exception:
            log.exception("Friday freeze wipe failed")

    # ── Raid boss cycle (every Monday) ────────────────────────────────────────

    async def _raid_boss_cycle(self):
        try:
            raid_cog = self.cogs.get("Raid")
            if raid_cog:
                await raid_cog.handle_boss_cycle()
        except Exception as e:
            log.warning(f"Raid boss cycle error: {e}")

    async def _raid_daily_digest(self):
        try:
            raid_cog = self.cogs.get("Raid")
            if raid_cog:
                await raid_cog.broadcast_daily_progress()
        except Exception as e:
            log.warning(f"Raid daily digest error: {e}")

    # ── Seasonal reset ────────────────────────────────────────────────────────

    async def _seasonal_reset(self):
        try:
            now = datetime.now(EST)
            # Seasons start on the 21st (EST):
            # - spring: Mar 21 → Jun 20
            # - summer: Jun 21 → Sep 20
            # - fall:   Sep 21 → Dec 20
            # - winter: Dec 21 → Mar 20 (key uses the start year, e.g. Jan 2026 -> winter_2025)
            m, d, y = now.month, now.day, now.year
            if (m == 12 and d >= 21) or (m in (1, 2)) or (m == 3 and d < 21):
                season_key = f"winter_{y if (m == 12 and d >= 21) else (y - 1)}"
            elif (m == 3 and d >= 21) or (m in (4, 5)) or (m == 6 and d < 21):
                season_key = f"spring_{y}"
            elif (m == 6 and d >= 21) or (m in (7, 8)) or (m == 9 and d < 21):
                season_key = f"summer_{y}"
            elif (m == 9 and d >= 21) or (m in (10, 11)) or (m == 12 and d < 21):
                season_key = f"fall_{y}"
            else:
                season_key = f"unknown_{y}"
            log.info(f"Seasonal reset: {season_key}")
            self.db.seasonal_reset(season_key)
            # Server announcement (optional): post to configured seasonal/general channel.
            embed = discord.Embed(
                title="🏅 Seasonal Reset",
                description=f"Season has been reset: **{season_key}**.\nYour seasonal minutes and points have been refreshed.",
                color=COLOR_GOLD,
            )

            # Snapshot: previous season top (from seasonal_history written during seasonal_reset)
            try:
                top = self.db.get_seasonal_history_top_minutes(season_key, limit=3)
            except Exception:
                top = []

            if top:
                lines = []
                medals = ["🥇", "🥈", "🥉"]
                for i, row in enumerate(top[:3]):
                    uid = int(row.get("user_id") or 0)
                    u = self.db.get_user(uid) or {}
                    name = u.get("username") or f"User {uid}"
                    mins = int(row.get("total_minutes") or 0)
                    lines.append(f"{medals[i]} **{name}** — {fmt_mins(mins)}")
                embed.add_field(name="🏆 Season leaderboard (Top 3)", value="\n".join(lines), inline=False)
            else:
                embed.add_field(name="🏆 Season leaderboard (Top 3)", value="_No data yet_", inline=False)

            # Quirky stat (rotates by season_key): season cheers / points / efficiency / most improved / breakthrough
            try:
                import hashlib

                choice = int(hashlib.md5(season_key.encode("utf-8")).hexdigest(), 16) % 5
                if choice == 0:
                    row = self.db.get_seasonal_history_top_cheers(season_key) or {}
                    if int(row.get("cheers_sent_at_reset") or 0) > 0:
                        uid = int(row["user_id"])
                        name = (self.db.get_user(uid) or {}).get("username") or f"User {uid}"
                        embed.add_field(
                            name="📣 Season cheerleader",
                            value=f"**{name}** — {int(row['cheers_sent_at_reset']):,} cheers sent",
                            inline=False,
                        )
                    else:
                        embed.add_field(name="📣 Season cheerleader", value="_No cheers yet_", inline=False)
                elif choice == 1:
                    row = self.db.get_seasonal_history_top_points(season_key) or {}
                    if int(row.get("points_at_reset") or 0) > 0:
                        uid = int(row["user_id"])
                        name = (self.db.get_user(uid) or {}).get("username") or f"User {uid}"
                        embed.add_field(
                            name="💠 Season points champ",
                            value=f"**{name}** — {int(row['points_at_reset']):,} points",
                            inline=False,
                        )
                    else:
                        embed.add_field(name="💠 Season points champ", value="_No points yet_", inline=False)
                elif choice == 2:
                    row = self.db.get_seasonal_history_best_efficiency(season_key, min_minutes=60) or {}
                    if int(row.get("total_minutes") or 0) > 0 and int(row.get("points_at_reset") or 0) > 0:
                        uid = int(row["user_id"])
                        name = (self.db.get_user(uid) or {}).get("username") or f"User {uid}"
                        ppm = float(row["ppm"])
                        embed.add_field(
                            name="⚙️ Most efficient",
                            value=f"**{name}** — {ppm:.2f} pts/min (min 60 mins)",
                            inline=False,
                        )
                    else:
                        embed.add_field(name="⚙️ Most efficient", value="_Not enough data yet_", inline=False)
                elif choice == 3:
                    row = self.db.get_seasonal_history_most_improved(season_key) or {}
                    if int(row.get("delta_minutes") or 0) > 0:
                        uid = int(row["user_id"])
                        name = (self.db.get_user(uid) or {}).get("username") or f"User {uid}"
                        embed.add_field(
                            name="📈 Most improved",
                            value=f"**{name}** — +{fmt_mins(int(row['delta_minutes']))} vs last season",
                            inline=False,
                        )
                    else:
                        embed.add_field(name="📈 Most improved", value="_No improvement data yet_", inline=False)
                else:
                    row = self.db.get_seasonal_history_best_efficiency(season_key, min_minutes=180) or {}
                    if int(row.get("total_minutes") or 0) > 0 and int(row.get("points_at_reset") or 0) > 0:
                        uid = int(row["user_id"])
                        name = (self.db.get_user(uid) or {}).get("username") or f"User {uid}"
                        ppm = float(row["ppm"])
                        embed.add_field(
                            name="🚀 Breakthrough session",
                            value=f"**{name}** — {ppm:.2f} pts/min (min 180 mins)",
                            inline=False,
                        )
                    else:
                        embed.add_field(name="🚀 Breakthrough session", value="_Not enough data yet_", inline=False)
            except Exception:
                pass

            for guild in self.guilds:
                channel_id = self.db.get_channel(guild.id, "seasonal")
                if not channel_id:
                    channel_id = self.db.get_channel(guild.id, "general")
                if not channel_id:
                    continue
                ch = self.get_channel(channel_id)
                if not ch:
                    continue
                try:
                    await ch.send(
                        content=(f"<@&{SB_PING_ROLE_ID}>" if SB_PING_ROLE_ID else None),
                        embed=embed,
                        allowed_mentions=discord.AllowedMentions(roles=True),
                    )
                except Exception as e:
                    log.debug("Seasonal announcement send failed (guild=%s, channel=%s): %s", guild.id, channel_id, e)
        except Exception:
            log.exception("Seasonal reset failed")

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        # Reaction role: SB Ping (guild only)
        try:
            if payload.guild_id is None or payload.user_id is None:
                return
            if payload.user_id == getattr(self.user, "id", None):
                return
            if str(payload.emoji) != "🔔":
                return

            msg_id = self.db.get_channel(payload.guild_id, "ping_role_message") or 0
            if not msg_id or int(msg_id) != int(payload.message_id):
                return

            guild = self.get_guild(payload.guild_id)
            if not guild:
                return
            role = guild.get_role(SB_PING_ROLE_ID)
            if not role:
                return
            member = guild.get_member(payload.user_id)
            if not member:
                try:
                    member = await guild.fetch_member(payload.user_id)
                except Exception:
                    return
            if member.bot:
                return
            try:
                await member.add_roles(role, reason="SB Ping reaction role")
            except Exception:
                return
        except Exception:
            return

    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        # Reaction role: SB Ping (guild only)
        try:
            if payload.guild_id is None or payload.user_id is None:
                return
            if str(payload.emoji) != "🔔":
                return

            msg_id = self.db.get_channel(payload.guild_id, "ping_role_message") or 0
            if not msg_id or int(msg_id) != int(payload.message_id):
                return

            guild = self.get_guild(payload.guild_id)
            if not guild:
                return
            role = guild.get_role(SB_PING_ROLE_ID)
            if not role:
                return
            member = guild.get_member(payload.user_id)
            if not member:
                try:
                    member = await guild.fetch_member(payload.user_id)
                except Exception:
                    return
            if member.bot:
                return
            try:
                await member.remove_roles(role, reason="SB Ping reaction role")
            except Exception:
                return
        except Exception:
            return

    # ── Hourly cleanup ────────────────────────────────────────────────────────

    async def _hourly_cleanup(self):
        try:
            self.db.expire_overflow()
            self.db.expire_effects()
            self.db.expire_beacons()
            self.db.expire_bounties_and_refund()
            self.db.cleanup_old_data()
            self.db.cleanup_outbox(keep_sent_days=14, keep_failed_days=30)
        except Exception:
            log.exception("Hourly cleanup failed")

    # ── on_ready ──────────────────────────────────────────────────────────────

    async def _enforce_guild_allowlist(self):
        allowed = self.allowed_guild_ids
        if not allowed:
            return
        for guild in list(self.guilds):
            if guild.id not in allowed:
                log.warning("Leaving unauthorized guild: %r (%s)", guild.name, guild.id)
                try:
                    await guild.leave()
                except discord.HTTPException as e:
                    log.warning("Could not leave guild %s: %s", guild.id, e)

    async def on_guild_join(self, guild: discord.Guild):
        allowed = self.allowed_guild_ids
        if allowed and guild.id not in allowed:
            log.warning("Join rejected (not allowlisted): %r (%s)", guild.name, guild.id)
            try:
                await guild.leave()
            except discord.HTTPException as e:
                log.warning("Could not leave guild %s: %s", guild.id, e)

    async def on_ready(self):
        self._gateway_had_ready = True
        self._gateway_disconnect_at = None
        self._ensure_gateway_watchdog_task()
        self._ready_count += 1
        log.info(f"Online as {self.user} ({self.user.id})")
        await self._enforce_guild_allowlist()
        await self.change_presence(
            activity=discord.Activity(type=discord.ActivityType.watching, name="📚 /help")
        )

        # 1. Zombie Sweeper — only run when *no* active sessions exist.
        # If there are active sessions, users should be able to stop them manually to receive rewards.
        try:
            active = self.db.get_all_active_sessions()
            if active:
                log.info("Skipping zombie sweeper: %s active study session(s) exist.", len(active))
            else:
                zombies = self.db.get_zombie_sessions()
                for z in zombies:
                    uid = int(z.get("user_id") or 0)
                    # No active sessions exist, so we can safely clear truly broken rows.
                    self.db.kill_zombie_session(int(z["id"]))
                    log.info("Killed zombie session %s (user %s)", z["id"], uid)
            stale_lobbies = self.db.get_stale_lobbies()
            for lobby in stale_lobbies:
                self.db.end_group_lobby(lobby["id"])
                log.info(f"Killed stale lobby {lobby['id']}")
        except Exception as e:
            log.warning(f"Zombie sweep error: {e}")

        # 2. Recover active study session live-update tasks (+ inactivity monitor; survives gateway reconnect)
        try:
            study_cog = self.cogs.get("Study")
            for session in self.db.get_all_active_sessions():
                uid = session["user_id"]
                if study_cog and session.get("live_channel_id") and not session.get("is_paused"):
                    study_cog._start_live_task(uid)
                    log.info(f"Recovered live study task for user {uid}")
                if study_cog and (session.get("target_minutes") or 0) >= 10 and not session.get("motivation_sent"):
                    study_cog._ensure_motivation_task(uid)
                if study_cog:
                    study_cog._start_inactivity_monitor(uid)
        except Exception as e:
            log.warning(f"Study session recovery error: {e}")

        # 3. Recover active Pomodoro timers
        try:
            pomo_cog = self.cogs.get("Pomodoro")
            if pomo_cog:
                pomo_cog.recover_group_timers_after_gateway_reconnect()
            for pomo in self.db.get_all_active_pomodoros():
                uid = pomo["user_id"]
                phase = pomo["current_phase"]
                if phase == "work":
                    phase_duration = pomo["work_minutes"]
                elif phase == "long_break":
                    phase_duration = pomo.get("long_break_minutes") or 15
                else:
                    phase_duration = pomo["break_minutes"]

                from utils import parse_stored as _parse_stored
                phase_started = _parse_stored(pomo["phase_started_at"])
                elapsed_secs = int((utcnow().replace(tzinfo=None) - phase_started).total_seconds())
                remaining_secs = max(phase_duration * 60 - elapsed_secs, 0)

                if pomo_cog:
                    if remaining_secs <= 5:
                        log.info(f"Pomodoro for {uid}: phase overdue, transitioning now")
                        asyncio.create_task(pomo_cog._transition_phase(uid, pomo, auto=True))
                    else:
                        log.info(f"Pomodoro for {uid}: resuming with {remaining_secs}s left in {phase}")
                        pomo_cog._schedule_phase_safe(uid, pomo)
        except Exception as e:
            log.warning(f"Pomodoro recovery error: {e}")

        # 4. Schedule catch-up: blocks that started in the last 29 min while offline (heavy fan-out).
        #    Skip on gateway reconnect unless FORCE_STARTUP_RECOVERY=1 (reconnect-light policy).
        try:
            force_startup = os.getenv("FORCE_STARTUP_RECOVERY", "").strip().lower() in ("1", "true", "yes")
            if self._ready_count > 1 and not force_startup:
                log.info("Skipping schedule catch-up (reconnect-light; set FORCE_STARTUP_RECOVERY=1 to force).")
            else:
                now_est = datetime.now(EST)
                for ago in range(1, 30):
                    t = now_est - timedelta(minutes=ago)
                    day = t.strftime("%A").lower()
                    blocks = self.db.get_schedule_blocks_at(day, t.hour, t.minute)
                    for block in blocks:
                        uid = int(block["user_id"])
                        if not self._schedule_block_dms_enabled(uid):
                            continue
                        date_est = t.date().isoformat()
                        dedupe_key = (
                            f"schedule_catchup:{int(block['id'])}:{date_est}:"
                            f"{t.hour:02d}:{t.minute:02d}"
                        )
                        embed = discord.Embed(
                            title="📚 Late Schedule Reminder",
                            description=(
                                f"Your **{block['subject']}** block started {ago} min ago (bot was restarting)."
                            ),
                            color=0xFEE75C,
                        )
                        embed.set_footer(text="Use /study start to begin tracking")
                        try:
                            self.db.enqueue_outbox(
                                target_type="user",
                                target_id=uid,
                                kind="schedule_catchup",
                                settings_key="schedule_reminders",
                                dedupe_key=dedupe_key,
                                embed_json=self._outbox_embed_payload(embed),
                            )
                        except Exception:
                            log.debug("schedule catch-up enqueue failed", exc_info=True)
                        await asyncio.sleep(0.02)
        except Exception as e:
            log.warning(f"Schedule catch-up error: {e}")

        # 5. Startup DM to owner
        if self.owner_id:
            try:
                owner = await self.get_user_or_fetch(self.owner_id)
                if not owner:
                    return
                now_est_str = datetime.now(EST).strftime("%I:%M %p EST")
                active = self.db.get_all_active_sessions()
                pomo_active = self.db.get_all_active_pomodoros()
                boss = self.db.get_active_boss()
                embed = discord.Embed(
                    title="✅ StudyBot is online!",
                    description=f"Ready. It's **{now_est_str}**.",
                    color=0x57F287
                )
                if active:
                    embed.add_field(name="📚 Session Recovered", value="Use `/study status` for a fresh live timer.", inline=False)
                if pomo_active:
                    embed.add_field(name="🍅 Pomodoro Recovered", value="Your Pomodoro timer has been resumed.", inline=False)
                if boss:
                    hp_pct = (boss["hp_remaining"] / boss["hp"] * 100) if boss["hp"] > 0 else 0
                    embed.add_field(name="⚔️ Raid Boss", value=f"HP: {boss['hp_remaining']}/{boss['hp']} ({hp_pct:.0f}%)", inline=False)
                embed.set_footer(text="/help for all commands")
                await owner.send(embed=embed)
            except Exception:
                pass


# ── Check-in button view ──────────────────────────────────────────────────────

class CheckinView(discord.ui.View):
    def __init__(self, bot: StudyBot, user_id: int, date: str):
        now_est = datetime.now(EST)
        midnight_est = now_est.replace(hour=23, minute=59, second=59, microsecond=0)
        secs_until_midnight = max(int((midnight_est - now_est).total_seconds()), 1)
        super().__init__(timeout=secs_until_midnight)
        self.bot = bot
        self.user_id = user_id
        self.date = date
        self.message: discord.Message | None = None

    async def on_timeout(self):
        if self.message:
            try:
                await self.message.edit(
                    embed=discord.Embed(
                        description="⏰ Check-in window closed (midnight EST).",
                        color=0x747F8D
                    ),
                    view=None
                )
            except Exception:
                pass

    @discord.ui.button(label="Yes, I studied!", style=discord.ButtonStyle.success, emoji="✅")
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.bot.db.record_checkin(self.user_id, self.date, studied=True, restore_streak=True)
        user_data = self.bot.db.get_user(self.user_id)
        embed = discord.Embed(title="✅ Logged!", description="Nice work — keep that streak alive! 🔥", color=0x57F287)
        embed.add_field(name="Current Streak", value=f"{user_data['streak']} days")
        await interaction.response.edit_message(embed=embed, view=None)
        self.stop()

    @discord.ui.button(label="No, I didn't", style=discord.ButtonStyle.danger, emoji="❌")
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.bot.db.record_checkin(self.user_id, self.date, studied=False)
        embed = discord.Embed(
            title="❌ Noted",
            description="Tomorrow's a new day. Want to sneak in a quick session?\n"
                        "Even `/study start` for 10 minutes counts! 📚",
            color=0xFEE75C
        )
        embed.set_footer(text="Streak resets at midnight EST if no session is recorded")
        await interaction.response.edit_message(embed=embed, view=None)
        self.stop()


def _help_preamble_fields(*, is_lite: bool) -> list[tuple[str, str]]:
    if is_lite:
        return list(get_help_overview_preamble(is_lite=True)) + [
            (
                "📬 DMs & settings",
                (
                    "• `/settings` — toggle which notifications you receive.\n"
                    "• Reminders and schedules can DM you (when enabled)."
                ),
            ),
        ]
    return list(get_help_overview_preamble(is_lite=False)) + [
        (
            "🎁 Rewards, DMs & badges",
            (
                "• After `/study stop` or `/pomodoro stop`, you get XP/points and may earn **badges** (+points).\n"
                "• **Lucky loot** (points, potions, coins) can roll when a session is **≥60 min recorded** "
                "(1 roll per full hour, max 5). **Later hours in the same session** get slightly better odds on each roll. "
                "Summary appears on the completion embed; details may DM.\n"
                "• `/settings` — turn DMs on/off (weekly report, reminders, etc.)."
            ),
        ),
        (
            "🪙 Economy tips",
            (
                "• `/convert` — points → boss coins (daily limit scales with prestige).\n"
                "• Wallet has a **coin cap**; extra goes to **overflow** — see `/inventory`.\n"
                "• `/bounty` is **pending** until the target runs `/study start` (then 2h 2.0× XP window + a points payout on target session end).\n"
                "• `/shop` `/gacha` `/bounty` `/beacon` — spend coins strategically."
            ),
        ),
        (
            "🧪 Potions",
            (
                "• Using the **same potion type** again **extends** the buff instead of stacking a second timer.\n"
                "• Potions in inventory: `/use_potion` · Active buffs: `/inventory` and `/today`."
            ),
        ),
    ]


def _help_command_sections(*, is_lite: bool) -> dict[str, str]:
    if is_lite:
        return {
            "📚 Study": (
                "`/study start` `[subject]` `[target]` · `/pause` · `/resume` · `/stop` `[notes]`\n"
                "`/note` · `/extend` · `/status` · `/history`"
            ),
            "🍅 Pomodoro": (
                "`/pomodoro start` · `/pomodoro status` · `/pomodoro skip` · `/pomodoro stop`\n"
                "`/group_pomo start` · `/group_pomo join` · `/group_pomo begin` · `/group_pomo leave` · `/group_pomo status`"
            ),
            "📁 Projects": "`/project add` · `list` · `view` · `done` · `delete`",
            "✅ Tasks": (
                "`/task add` · `list` · `complete` · `complete_many` · `delete` · `history` · `reviews`"
            ),
            "⏰ Reminders": "`/remind add <when> <message>` — e.g. `30m`, `3:30pm`, `6/5/2026 1:00pm` (US Eastern)",
            "📅 Schedule": (
                "`/schedule add` (hour **0–23**, minute **0–59** EST) · `/schedule view` · `/schedule delete`"
            ),
            "🔥 Streaks & goals": (
                "`/streak` · `/goals set` · `set-day` · `view` · `progress`\n"
                "`/checkin` · `/adaptive`"
            ),
            "⚙️ Settings": "`/settings` — Ghost Mode, DM toggles",
            "📊 Stats & export": "`/stats` · `/today` · `/breakdown` · `/export`",
            "🎁 Temptation bundle": "`/bundle set` · `/bundle status` · `/bundle clear` — pair a treat with study/Pomodoro (see `/tutorial`)",
        }
    return {
        "📚 Study": (
            "`/study start` `[subject]` `[target]` · `/pause` · `/resume` · `/stop` `[notes]`\n"
            "`/note` · `/extend` · `/status` · `/history`"
        ),
        "🍅 Pomodoro": (
            "`/pomodoro start` · `/pomodoro status` · `/pomodoro skip` · `/pomodoro stop`\n"
            "`/group_pomo start` · `/group_pomo join` · `/group_pomo begin` · `/group_pomo leave` · `/group_pomo status`"
        ),
        "⚔️ RPG & economy": (
            "`/profile` · `/convert` · `/prestige` · `/shop` · `/gacha`\n"
            "`/bounty` · `/beacon` · `/inventory` · `/use_potion`"
        ),
        "📜 Quests & raids": "`/quests` · `/quest reroll` · `/raid` · `/raid_leaderboard`",
        "🏆 Social & rankings": "`/cheer` · `/who` · `/leaderboard` · `/badges`",
        "📁 Projects": "`/project add` · `list` · `view` · `done` · `delete`",
        "✅ Tasks": (
            "`/task add` · `list` · `complete` · `complete_many` · `delete` · `history` · `reviews`"
        ),
        "⏰ Reminders": "`/remind add <when> <message>` — e.g. `30m`, `3:30pm`, `6/5/2026 1:00pm` (US Eastern)",
        "📅 Schedule": (
            "`/schedule add` (hour **0–23**, minute **0–59** EST) · `/schedule view` · `/schedule delete`"
        ),
        "🔥 Streaks & goals": (
            "`/streak` · `/goals set` · `set-day` · `view` · `progress`\n"
            "`/checkin` · `/adaptive`"
        ),
        "⚙️ Settings": "`/settings` — Ghost Mode, DM toggles",
        "📊 Stats & export": "`/stats` · `/today` · `/breakdown` · `/export`",
        "🎁 Temptation bundle": "`/bundle set` · `/bundle status` · `/bundle clear` — pair a treat with study/Pomodoro (see `/tutorial`)",
    }


def _build_help_embed(topic_index: int, *, is_lite: bool) -> discord.Embed:
    """0 = overview (preamble + every command group). 1+ = single section."""
    preamble = _help_preamble_fields(is_lite=is_lite)
    sections = _help_command_sections(is_lite=is_lite)
    footer = (
        f"Weekly report: opt in via /settings (Sun 6pm EST) • Morning briefing midnight EST • "
        f"Raids ~2 weeks • {USER_NAV_FOOTER}"
    )
    embed = discord.Embed(
        title="📚 StudyBot — Help",
        description="All times are **EST**.",
        color=0x5865F2,
    )
    if topic_index == 0:
        for name, val in preamble:
            embed.add_field(name=name, value=val, inline=False)
        embed.add_field(
            name="📖 Command topics",
            value="Use the **menu below** to focus on one category — every slash command still works.",
            inline=False,
        )
        for name, val in sections.items():
            embed.add_field(name=name, value=val, inline=False)
    else:
        keys = list(sections.keys())
        if 1 <= topic_index <= len(keys):
            key = keys[topic_index - 1]
            embed.add_field(name=key, value=sections[key], inline=False)
        else:
            embed.description = "Unknown topic — pick another from the menu."
    embed.set_footer(text=footer)
    return embed


class HelpTopicSelect(discord.ui.Select):
    def __init__(self, *, is_lite: bool):
        sections = _help_command_sections(is_lite=is_lite)
        opts = [
            discord.SelectOption(
                label="📖 Full tutorial (deep dive)",
                value="0",
                description="Same as /tutorial — chapters & long explanations",
            )
        ]
        for i, label in enumerate(sections.keys(), start=1):
            opts.append(discord.SelectOption(label=label[:100], value=str(i)))
        super().__init__(placeholder="Jump to a topic…", min_values=1, max_values=1, options=opts[:25], row=0)

    async def callback(self, interaction: discord.Interaction):
        is_lite = getattr(self.view, "is_lite", False)
        idx = int(self.values[0])
        if idx == 0:
            embed = tutorial_chapter_embed(0, is_lite=is_lite)
            await interaction.response.edit_message(embed=embed, view=TutorialNavView(is_lite=is_lite))
            return
        embed = _build_help_embed(idx, is_lite=is_lite)
        await interaction.response.edit_message(embed=embed, view=self.view)


class HelpNavView(discord.ui.View):
    def __init__(self, *, is_lite: bool):
        super().__init__(timeout=600)
        self.is_lite = is_lite
        self.add_item(HelpTopicSelect(is_lite=is_lite))


bot = StudyBot()


@bot.tree.command(name="source", description="Get the source code and license for this deployment")
async def source_command(interaction: discord.Interaction):
    source_url = os.getenv("BOT_SOURCE_URL", "").strip() or "https://github.com/chentaot1/StudyBot"
    await interaction.response.send_message(
        "StudyBot is licensed under the GNU Affero General Public License, version 3.\n"
        f"Source code for this deployment: {source_url}",
        ephemeral=True,
    )


async def _connect_forever() -> None:
    """Keep the gateway session alive; on fatal disconnect errors, backoff and retry."""
    if not bot.allowed_guild_ids:
        log.error("Set ALLOWED_GUILD_IDS to the server IDs for this deployment.")
        raise SystemExit(1)
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        log.error("DISCORD_TOKEN not set!")
        raise SystemExit(1)

    backoff_min = float(os.getenv("RECONNECT_BACKOFF_MIN_SEC", "5"))
    backoff_max = float(os.getenv("RECONNECT_BACKOFF_MAX_SEC", "300"))
    backoff = backoff_min
    crash_on_repeat = os.getenv("RECONNECT_CRASH_ON_REPEAT", "true").strip().lower() in ("1", "true", "yes", "y")
    repeat_window_sec = float(os.getenv("RECONNECT_REPEAT_WINDOW_SEC", "120"))
    repeat_limit = int(os.getenv("RECONNECT_REPEAT_LIMIT", "5"))
    last_sig: tuple[str, str] | None = None
    last_sig_t = 0.0
    repeat_count = 0

    while True:
        try:
            bot.reset_gateway_watchdog_session()
            bot._ensure_gateway_watchdog_task()
            log.info("Python executable: %s", sys.executable)
            log.info("Python version: %s", sys.version.replace("\n", " "))
            log.info("Connecting to Discord gateway (reconnect=True)…")
            await bot.start(token, reconnect=True)
            # Normal return: client was closed cleanly (e.g. await bot.close()).
            log.info("Gateway session ended normally.")
            break
        except discord.LoginFailure:
            log.critical("DISCORD_TOKEN rejected — fix credentials in .env.")
            raise SystemExit(1)
        except KeyboardInterrupt:
            log.info("Interrupt received — shutting down…")
            await _graceful_shutdown(bot)
            try:
                if not bot.is_closed():
                    await bot.close()
            except Exception:
                pass
            break
        except Exception as e:
            # If the same exception keeps happening quickly, treat it as a deterministic bug and crash
            # so the supervisor (NSSM/watchdog) can surface it instead of looping silently forever.
            sig = (type(e).__name__, str(e)[:200])
            now = time.monotonic()
            if last_sig == sig and (now - last_sig_t) <= repeat_window_sec:
                repeat_count += 1
            else:
                repeat_count = 1
            last_sig, last_sig_t = sig, now

            log.exception(
                "Disconnected or fatal error (%s): %s — retrying in %.1fs",
                type(e).__name__,
                e,
                backoff,
            )
            if crash_on_repeat and repeat_count >= repeat_limit:
                log.critical(
                    "Same error repeated %s times within %.0fs — crashing to avoid infinite retry loop.",
                    repeat_count,
                    repeat_window_sec,
                )
                raise
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, backoff_max)
            try:
                if not bot.is_closed():
                    await bot.close()
            except Exception:
                log.debug("bot.close() during reconnect backoff failed", exc_info=True)


@bot.tree.command(name="help", description="Show all StudyBot commands")
async def help_cmd(interaction: discord.Interaction):
    is_lite = getattr(bot, "is_lite_user", lambda _uid: False)(interaction.user.id)
    embed = _build_help_embed(0, is_lite=is_lite)
    await interaction.response.send_message(embed=embed, view=HelpNavView(is_lite=is_lite), ephemeral=True)


async def _graceful_shutdown(bot_instance):
    """Save state and clean up before exit (important for Termux/Android)."""
    log.info("Graceful shutdown initiated...")
    try:
        zombie = bot_instance.db.get_all_active_sessions()
        for s in zombie:
            bot_instance.db.end_session(s["user_id"])
            log.info(f"  Saved active session for user {s['user_id']}")

        active_pomos = bot_instance.db.get_all_active_pomodoros()
        for p in active_pomos:
            bot_instance.db.end_pomodoro(p["user_id"])
            log.info(f"  Ended active pomodoro for user {p['user_id']}")
    except Exception as e:
        log.warning(f"Session save during shutdown: {e}")

    try:
        import sqlite3
        conn = sqlite3.connect(bot_instance.db.path)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
        log.info("SQLite WAL checkpointed.")
    except Exception as e:
        log.warning(f"WAL checkpoint: {e}")

    if bot_instance.scheduler.running:
        bot_instance.scheduler.shutdown(wait=False)

    log.info("Shutdown complete.")


if __name__ == "__main__":
    try:
        from utils import acquire_single_instance_lock

        acquire_single_instance_lock()
    except RuntimeError as e:
        log.critical("%s", e)
        raise SystemExit(1)
    try:
        asyncio.run(_connect_forever())
    except KeyboardInterrupt:
        # asyncio.run may surface this if not caught inside _connect_forever
        log.info("Shutdown requested.")
