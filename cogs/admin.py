# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

import discord
from discord.ext import commands
from discord import app_commands
import logging
import os
import sqlite3
from datetime import datetime, timezone

from constants import EST, COLOR_SUCCESS, COLOR_ERROR, COLOR_WARNING, LITE_USER_IDS, SB_PING_ROLE_ID
from utils import fmt_datetime_us_est, parse_stored


def _display_db_timestamp_us(val: object) -> str:
    """Format a naive UTC-ish DB timestamp for US Eastern display."""
    if val is None:
        return "—"
    s = str(val).strip()
    if not s:
        return "—"
    try:
        dt = parse_stored(s).replace(tzinfo=timezone.utc)
        return fmt_datetime_us_est(dt, with_seconds=True)
    except Exception:
        return s


log = logging.getLogger("StudyBot.Admin")


class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _is_owner(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.bot.owner_id

    admin_group = app_commands.Group(name="admin", description="Owner-only administration commands")

    @admin_group.command(name="health", description="Quick diagnostics (DB, scheduler, guild allowlist)")
    async def health(self, interaction: discord.Interaction):
        if not self._is_owner(interaction):
            await interaction.response.send_message("Owner only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        bot = self.bot
        db_path = getattr(getattr(bot, "db", None), "path", "")

        users_total = sessions_total = ended_total = 0
        db_bytes = 0
        wal_bytes = 0
        shm_bytes = 0
        channels: dict[str, int] = {}
        ping_role_message_id: int = 0
        quick_check: str = ""
        table_counts: dict[str, int] = {}
        try:
            with sqlite3.connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                users_total = int(conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"] or 0)
                sessions_total = int(conn.execute("SELECT COUNT(*) AS c FROM study_sessions").fetchone()["c"] or 0)
                ended_total = int(conn.execute("SELECT COUNT(*) AS c FROM study_sessions WHERE ended_at IS NOT NULL").fetchone()["c"] or 0)
                # Channel config (for the first allowlisted guild, if any)
                allowed = sorted(getattr(bot, "allowed_guild_ids", set()) or [])
                if allowed:
                    gid = int(allowed[0])
                    for key in ("general", "raid", "seasonal", "beacon", "ping_role_message"):
                        row = conn.execute(
                            "SELECT channel_id FROM channel_config WHERE guild_id=? AND channel_type=?",
                            (gid, key),
                        ).fetchone()
                        if row and row["channel_id"]:
                            if key == "ping_role_message":
                                ping_role_message_id = int(row["channel_id"] or 0)
                            else:
                                channels[key] = int(row["channel_id"])

                # DB integrity (fast)
                try:
                    qc = conn.execute("PRAGMA quick_check").fetchone()
                    if qc and len(qc) > 0:
                        quick_check = str(qc[0])
                except Exception:
                    quick_check = ""

                # Growth signals (cheap counts)
                for t in ("study_sessions", "point_transactions", "reminders", "seasonal_history"):
                    try:
                        c = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                        table_counts[t] = int(c or 0)
                    except Exception:
                        pass
        except Exception as e:
            log.warning("Health check DB query failed: %s", e)

        try:
            if db_path and os.path.exists(db_path):
                db_bytes = os.path.getsize(db_path)
            if db_path and os.path.exists(db_path + "-wal"):
                wal_bytes = os.path.getsize(db_path + "-wal")
            if db_path and os.path.exists(db_path + "-shm"):
                shm_bytes = os.path.getsize(db_path + "-shm")
        except Exception:
            pass

        sched = getattr(bot, "scheduler", None)
        sched_running = bool(getattr(sched, "running", False))
        jobs = []
        try:
            if sched:
                jobs = list(sched.get_jobs())
        except Exception:
            jobs = []

        allowed_guilds = sorted(getattr(bot, "allowed_guild_ids", set()) or [])
        lite_ids = sorted(list(LITE_USER_IDS))
        job_ids = {getattr(j, "id", "") for j in jobs}

        def _fmt_bytes(n: int) -> str:
            if n <= 0:
                return "0 B"
            for unit in ("B", "KB", "MB", "GB"):
                if n < 1024 or unit == "GB":
                    return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
                n /= 1024
            return f"{n:.1f} GB"

        # Expected recurring jobs (helps catch scheduling regressions)
        expected = [
            "daily_reset",
            "sched_minutely",
            "checkins",
            "weekly_report",
            "freeze_wipe",
            "raid_cycle",
            "raid_daily_digest",
            "hourly_cleanup",
            "outbox_pump",
            "season_3",
            "season_6",
            "season_9",
            "season_12",
        ]
        missing_jobs = [jid for jid in expected if jid not in job_ids]

        # Next run times for key jobs
        next_runs: dict[str, str] = {}
        try:
            for jid in ("daily_reset", "weekly_report", "raid_cycle", "hourly_cleanup"):
                j = next((x for x in jobs if getattr(x, "id", "") == jid), None)
                if j and getattr(j, "next_run_time", None):
                    next_runs[jid] = str(j.next_run_time)
        except Exception:
            pass

        # Discord connectivity (gateway)
        try:
            latency_ms = int(float(getattr(bot, "latency", 0.0) or 0.0) * 1000)
        except Exception:
            latency_ms = 0

        embed = discord.Embed(title="🩺 Health check", color=COLOR_SUCCESS)
        embed.add_field(name="DB path", value=f"`{db_path}`" if db_path else "_unset_", inline=False)
        embed.add_field(name="Users", value=f"{users_total:,}", inline=True)
        embed.add_field(name="Sessions", value=f"{sessions_total:,}", inline=True)
        embed.add_field(name="Completed", value=f"{ended_total:,}", inline=True)
        embed.add_field(
            name="DB size",
            value=f"{_fmt_bytes(db_bytes)} (WAL: {_fmt_bytes(wal_bytes)}, SHM: {_fmt_bytes(shm_bytes)})",
            inline=False,
        )
        if quick_check:
            embed.add_field(name="SQLite quick_check", value=f"`{quick_check}`", inline=False)

        if table_counts:
            embed.add_field(
                name="Table rows",
                value="\n".join([f"- **{k}**: {v:,}" for k, v in table_counts.items()]),
                inline=False,
            )
        embed.add_field(name="Scheduler", value="🟢 running" if sched_running else "🔴 stopped", inline=True)
        embed.add_field(name="Jobs", value=f"{len(jobs)}", inline=True)
        embed.add_field(name="Gateway latency", value=f"{latency_ms} ms", inline=True)
        core = ["Study", "Stats", "Admin"]
        loaded = [c for c in core if c in getattr(bot, "cogs", {})]
        missing_core = [c for c in core if c not in getattr(bot, "cogs", {})]
        embed.add_field(
            name="Core cogs",
            value=f"✅ {', '.join(loaded)}" + (f"\n⚠️ Missing: {', '.join(missing_core)}" if missing_core else ""),
            inline=False,
        )
        embed.add_field(name="Allowed guild IDs", value=", ".join(str(g) for g in allowed_guilds) or "_none_", inline=False)
        embed.add_field(name="Lite user IDs", value=", ".join(str(u) for u in lite_ids) or "_none_", inline=False)
        embed.add_field(name="Now (EST)", value=f"`{fmt_datetime_us_est(datetime.now(EST))}`", inline=False)

        if channels:
            embed.add_field(
                name="Channels (first allowlisted guild)",
                value="\n".join([f"- **{k}**: `{v}`" for k, v in channels.items()]),
                inline=False,
            )

        # SB Ping reaction role wiring
        g = bot.get_guild(allowed_guilds[0]) if allowed_guilds else None
        role = g.get_role(SB_PING_ROLE_ID) if g else None
        role_line = []
        if role:
            role_line.append(f"Role: ✅ **{role.name}** (`{role.id}`)")
            role_line.append(f"Mentionable: {'✅' if role.mentionable else '❌'}")
            me = g.me if g else None
            if me and me.top_role:
                can_assign = me.top_role.position > role.position
                role_line.append(f"Assignable by bot: {'✅' if can_assign else '❌'}")
        else:
            role_line.append(f"Role: ❌ not found (`{SB_PING_ROLE_ID}`)")
        role_line.append(f"Reaction message id: `{ping_role_message_id}`" if ping_role_message_id else "Reaction message id: _unset_")
        embed.add_field(name="SB Ping wiring", value="\n".join(role_line), inline=False)

        if next_runs:
            embed.add_field(
                name="Next scheduled runs",
                value="\n".join([f"- **{k}**: `{v}`" for k, v in next_runs.items()]),
                inline=False,
            )

        if missing_jobs:
            embed.add_field(
                name="⚠️ Missing scheduled jobs",
                value="\n".join(f"- `{jid}`" for jid in missing_jobs),
                inline=False,
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    @admin_group.command(name="status", description="Combined status: health + usage summary")
    async def status(self, interaction: discord.Interaction):
        if not self._is_owner(interaction):
            await interaction.response.send_message("Owner only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        # Reuse the same DB-level summaries as /admin usage, but in a condensed form.
        db_path = self.bot.db.path
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            users_total = int(conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"] or 0)
            sessions_total = int(conn.execute("SELECT COUNT(*) AS c FROM study_sessions").fetchone()["c"] or 0)
            sessions_ended = int(conn.execute("SELECT COUNT(*) AS c FROM study_sessions WHERE ended_at IS NOT NULL").fetchone()["c"] or 0)
            last_end = conn.execute(
                "SELECT MAX(ended_at) AS last_end FROM study_sessions WHERE ended_at IS NOT NULL"
            ).fetchone()["last_end"]

        # Health embed (call into the same code path by duplicating the key lines lightly)
        sched = getattr(self.bot, "scheduler", None)
        sched_running = bool(getattr(sched, "running", False))
        try:
            latency_ms = int(float(getattr(self.bot, "latency", 0.0) or 0.0) * 1000)
        except Exception:
            latency_ms = 0

        embed = discord.Embed(title="🧭 Admin status", color=COLOR_SUCCESS)
        embed.add_field(name="Scheduler", value="🟢 running" if sched_running else "🔴 stopped", inline=True)
        embed.add_field(name="Gateway latency", value=f"{latency_ms} ms", inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=True)

        embed.add_field(name="Users (rows)", value=str(users_total), inline=True)
        embed.add_field(name="Sessions (rows)", value=str(sessions_total), inline=True)
        embed.add_field(name="Completed sessions", value=str(sessions_ended), inline=True)
        if last_end:
            embed.add_field(
                name="Most recent completed session",
                value=f"`{_display_db_timestamp_us(last_end)}`",
                inline=False,
            )

        core = ["Study", "Stats", "Admin"]
        loaded = [c for c in core if c in getattr(self.bot, "cogs", {})]
        missing_core = [c for c in core if c not in getattr(self.bot, "cogs", {})]
        embed.add_field(
            name="Core cogs",
            value=f"✅ {', '.join(loaded)}" + (f"\n⚠️ Missing: {', '.join(missing_core)}" if missing_core else ""),
            inline=False,
        )

        await interaction.followup.send(embed=embed, ephemeral=True)

    @admin_group.command(name="ping_role_post", description="Post the SB Ping reaction-role message in this channel")
    async def ping_role_post(self, interaction: discord.Interaction):
        if not self._is_owner(interaction):
            await interaction.response.send_message("Owner only.", ephemeral=True)
            return
        if interaction.guild_id is None or not isinstance(interaction.channel, discord.abc.GuildChannel):
            await interaction.response.send_message("Use this in the server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        role = interaction.guild.get_role(SB_PING_ROLE_ID)
        role_name = role.name if role else "SB Ping"
        emoji = "🔔"
        embed = discord.Embed(
            title="🔔 StudyBot pings",
            description=(
                f"React with {emoji} to get the **{role_name}** role.\n"
                f"React again (or remove your reaction) to remove it.\n\n"
                "This role is used for raid/beacon/season announcements."
            ),
            color=COLOR_SUCCESS,
        )
        msg = await interaction.channel.send(embed=embed)
        try:
            await msg.add_reaction(emoji)
        except Exception:
            pass

        # Store message id in channel_config (type key is guild-scoped; value holds message_id).
        (await self.bot.db_worker.run(lambda: self.bot.db.set_channel(interaction.guild_id, "ping_role_message", msg.id)))
        await interaction.followup.send(f"✅ Posted. Saved message id `{msg.id}`.", ephemeral=True)

    @admin_group.command(name="ping_role_clear", description="Clear the stored SB Ping reaction-role message")
    async def ping_role_clear(self, interaction: discord.Interaction):
        if not self._is_owner(interaction):
            await interaction.response.send_message("Owner only.", ephemeral=True)
            return
        if interaction.guild_id is None:
            await interaction.response.send_message("Use this in the server.", ephemeral=True)
            return
        (await self.bot.db_worker.run(lambda: self.bot.db.set_channel(interaction.guild_id, "ping_role_message", 0)))
        await interaction.response.send_message("✅ Cleared stored ping-role message id.", ephemeral=True)

    @admin_group.command(name="usage", description="See whether anyone has used the bot yet")
    async def usage(self, interaction: discord.Interaction):
        if not self._is_owner(interaction):
            await interaction.response.send_message("Owner only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        db_path = self.bot.db.path
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row

            users_total = int(conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"] or 0)
            sessions_total = int(conn.execute("SELECT COUNT(*) AS c FROM study_sessions").fetchone()["c"] or 0)
            sessions_ended = int(conn.execute("SELECT COUNT(*) AS c FROM study_sessions WHERE ended_at IS NOT NULL").fetchone()["c"] or 0)

            users_with_any_session = int(conn.execute(
                "SELECT COUNT(DISTINCT user_id) AS c FROM study_sessions"
            ).fetchone()["c"] or 0)
            users_with_completed_session = int(conn.execute(
                "SELECT COUNT(DISTINCT user_id) AS c FROM study_sessions WHERE ended_at IS NOT NULL"
            ).fetchone()["c"] or 0)
            users_with_5m_completed = int(conn.execute(
                "SELECT COUNT(DISTINCT user_id) AS c FROM study_sessions WHERE ended_at IS NOT NULL AND duration_minutes>=5"
            ).fetchone()["c"] or 0)

            last_end = conn.execute(
                "SELECT MAX(ended_at) AS last_end FROM study_sessions WHERE ended_at IS NOT NULL"
            ).fetchone()["last_end"]

            recent = [dict(r) for r in conn.execute(
                """
                SELECT s.user_id, u.username,
                       MAX(s.ended_at) AS last_end,
                       COALESCE(SUM(CASE WHEN s.ended_at IS NOT NULL THEN s.duration_minutes END), 0) AS total_minutes
                FROM study_sessions s
                LEFT JOIN users u ON u.user_id=s.user_id
                WHERE s.ended_at IS NOT NULL
                GROUP BY s.user_id
                ORDER BY last_end DESC
                LIMIT 8
                """
            ).fetchall()]

        embed = discord.Embed(
            title="📈 Usage report",
            description="Summary of who has data in the database (useful before wiping).",
            color=COLOR_SUCCESS,
        )
        embed.add_field(name="👤 Users (rows)", value=f"{users_total}", inline=True)
        embed.add_field(name="📚 Sessions (rows)", value=f"{sessions_total}", inline=True)
        embed.add_field(name="✅ Completed sessions", value=f"{sessions_ended}", inline=True)

        embed.add_field(name="Users with any session", value=str(users_with_any_session), inline=True)
        embed.add_field(name="Users with completed session", value=str(users_with_completed_session), inline=True)
        embed.add_field(name="Users with ≥5m completed", value=str(users_with_5m_completed), inline=True)

        if last_end:
            embed.add_field(
                name="Most recent completed session",
                value=f"`{_display_db_timestamp_us(last_end)}`",
                inline=False,
            )

        if recent:
            lines = []
            for r in recent:
                name = r.get("username") or f"User {r['user_id']}"
                mins = int(r.get("total_minutes") or 0)
                le = _display_db_timestamp_us(r.get("last_end"))
                lines.append(f"- **{name}** — {mins:,} mins (last: `{le}`)")
            embed.add_field(name="Recent active users", value="\n".join(lines), inline=False)
        else:
            embed.add_field(name="Recent active users", value="_None_", inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)

    @admin_group.command(name="dm_user", description="DM a user by ID (useful for User Install issues)")
    @app_commands.describe(user_id="Target user ID", message="Message to send")
    async def dm_user(self, interaction: discord.Interaction, user_id: str, message: str):
        if not self._is_owner(interaction):
            await interaction.response.send_message("Owner only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        try:
            uid = int(user_id)
        except Exception:
            await interaction.followup.send("Invalid user_id.", ephemeral=True)
            return

        u = await self.bot.get_user_or_fetch(uid)
        if not u:
            await interaction.followup.send("Couldn't fetch that user from Discord.", ephemeral=True)
            return

        try:
            await u.send(message)
            await interaction.followup.send(f"✅ Sent DM to **{u}** (`{uid}`).", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send(
                "Couldn't DM them (Forbidden). They may have DMs closed or blocked the bot.",
                ephemeral=True,
            )
        except discord.HTTPException as e:
            await interaction.followup.send(f"DM failed: `{e}`", ephemeral=True)

    @admin_group.command(name="dm_lite", description="DM the lite user (bare-bones access) to help them connect")
    @app_commands.describe(message="Message to send (optional)")
    async def dm_lite(self, interaction: discord.Interaction, message: str = ""):
        if not self._is_owner(interaction):
            await interaction.response.send_message("Owner only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        lite_ids = list(LITE_USER_IDS)
        if not lite_ids:
            await interaction.followup.send("No lite user configured.", ephemeral=True)
            return
        uid = int(lite_ids[0])

        if not message:
            message = (
                "Hey! This is Study Bot.\n\n"
                "If you just authorized the app but can’t find it in DMs: try clicking the install link again "
                "and use the “Open in Discord” button, or search your DM list for “Study Bot”.\n\n"
                "If Discord still won’t show it, just reply to this message and then type `/help`."
            )

        u = await self.bot.get_user_or_fetch(uid)
        if not u:
            await interaction.followup.send("Couldn't fetch the lite user from Discord.", ephemeral=True)
            return

        try:
            await u.send(message)
            await interaction.followup.send(f"✅ Sent DM to lite user **{u}** (`{uid}`).", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send(
                "Couldn't DM the lite user (Forbidden). They may have DMs closed or blocked the bot.",
                ephemeral=True,
            )
        except discord.HTTPException as e:
            await interaction.followup.send(f"DM failed: `{e}`", ephemeral=True)

    @admin_group.command(name="backup", description="Create a database backup")
    async def backup(self, interaction: discord.Interaction):
        if not self._is_owner(interaction):
            await interaction.response.send_message("Owner only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        timestamp = datetime.now(EST).strftime("%Y%m%d_%H%M%S")
        dest = f"backups/study_bot_{timestamp}.db"
        os.makedirs("backups", exist_ok=True)
        (await self.bot.db_worker.run(lambda: self.bot.db.backup(dest)))
        await interaction.followup.send(f"✅ Backup saved to `{dest}`", ephemeral=True)

    @admin_group.command(name="set_points", description="Set a user's points")
    @app_commands.describe(user="Target user", points="New point value")
    async def set_points(self, interaction: discord.Interaction, user: discord.User, points: int):
        if not self._is_owner(interaction):
            await interaction.response.send_message("Owner only.", ephemeral=True)
            return
        (await self.bot.db_worker.run(lambda: self.bot.db.ensure_user(user.id, user.display_name)))
        (await self.bot.db_worker.run(lambda: self.bot.db.admin_set_points(user.id, points)))
        await interaction.response.send_message(
            f"✅ Set **{user.display_name}**'s points to **{points:,}**.",
            ephemeral=True
        )

    @admin_group.command(name="set_streak", description="Set a user's streak")
    @app_commands.describe(user="Target user", streak="New streak value")
    async def set_streak(self, interaction: discord.Interaction, user: discord.User, streak: int):
        if not self._is_owner(interaction):
            await interaction.response.send_message("Owner only.", ephemeral=True)
            return
        (await self.bot.db_worker.run(lambda: self.bot.db.ensure_user(user.id, user.display_name)))
        (await self.bot.db_worker.run(lambda: self.bot.db.admin_set_streak(user.id, streak)))
        await interaction.response.send_message(
            f"✅ Set **{user.display_name}**'s streak to **{streak}**.",
            ephemeral=True
        )

    @admin_group.command(name="add_coins", description="Add coins to a user")
    @app_commands.describe(user="Target user", amount="Coins to add")
    async def add_coins(self, interaction: discord.Interaction, user: discord.User, amount: int):
        if not self._is_owner(interaction):
            await interaction.response.send_message("Owner only.", ephemeral=True)
            return
        (await self.bot.db_worker.run(lambda: self.bot.db.ensure_user(user.id, user.display_name)))
        (await self.bot.db_worker.run(lambda: self.bot.db.admin_add_coins(user.id, amount)))
        data = (await self.bot.db_worker.run(lambda: self.bot.db.get_user(user.id)))
        await interaction.response.send_message(
            f"✅ Added **{amount}c** to **{user.display_name}**. New balance: **{data['coins']}c**.",
            ephemeral=True
        )

    @admin_group.command(name="spawn_boss", description="Manually spawn a raid boss")
    @app_commands.describe(hp="Boss HP (default: auto-calculated)")
    async def spawn_boss(self, interaction: discord.Interaction, hp: int = 0):
        if not self._is_owner(interaction):
            await interaction.response.send_message("Owner only.", ephemeral=True)
            return

        existing = (await self.bot.db_worker.run(lambda: self.bot.db.get_active_boss()))
        if existing:
            await interaction.response.send_message(
                f"A boss is already active (#{existing['id']}, {existing['hp_remaining']}/{existing['hp']} HP).",
                ephemeral=True
            )
            return

        if hp <= 0:
            hp = 1200
        boss = (await self.bot.db_worker.run(lambda: self.bot.db.spawn_boss(hp)))
        raid_cog = self.bot.cogs.get("Raid")
        if raid_cog:
            await raid_cog._announce_boss(boss)
        await interaction.response.send_message(
            f"✅ Spawned raid boss #{boss['id']} with **{hp:,} HP**.",
            ephemeral=True
        )

    @admin_group.command(name="set_channel", description="Set a channel for bot announcements")
    @app_commands.describe(
        channel_type="Type of channel to set",
        channel="The channel to use"
    )
    @app_commands.choices(channel_type=[
        app_commands.Choice(name="Raid Announcements", value="raid"),
        app_commands.Choice(name="Seasonal Announcements", value="seasonal"),
        app_commands.Choice(name="Beacon Channel", value="beacon"),
        app_commands.Choice(name="General", value="general"),
    ])
    async def set_channel(self, interaction: discord.Interaction,
                          channel_type: str, channel: discord.TextChannel):
        if not self._is_owner(interaction):
            await interaction.response.send_message("Owner only.", ephemeral=True)
            return
        (await self.bot.db_worker.run(lambda: self.bot.db.set_channel(interaction.guild_id, channel_type, channel.id)))
        await interaction.response.send_message(
            f"✅ **{channel_type}** channel set to {channel.mention}.",
            ephemeral=True
        )

    @admin_group.command(name="force_reset", description="Force a daily reset (quests, streaks, etc.)")
    async def force_reset(self, interaction: discord.Interaction):
        if not self._is_owner(interaction):
            await interaction.response.send_message("Owner only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await self.bot._daily_reset()
        await interaction.followup.send("✅ Daily reset forced.", ephemeral=True)

    @admin_group.command(name="add_points", description="Add (or remove) points for a user")
    @app_commands.describe(user="Target user", amount="Points to add (negative to remove)")
    async def add_points_cmd(self, interaction: discord.Interaction, user: discord.User, amount: int):
        if not self._is_owner(interaction):
            await interaction.response.send_message("Owner only.", ephemeral=True)
            return
        (await self.bot.db_worker.run(lambda: self.bot.db.ensure_user(user.id, user.display_name)))
        (await self.bot.db_worker.run(lambda: self.bot.db.add_points(user.id, amount, reason=f"Admin grant ({amount})")))
        data = (await self.bot.db_worker.run(lambda: self.bot.db.get_user(user.id)))
        await interaction.response.send_message(
            f"✅ Added **{amount:,}** points to **{user.display_name}**. Balance: **{data['points']:,}**.",
            ephemeral=True
        )

    @admin_group.command(name="add_xp", description="Add XP to a user (triggers leveling)")
    @app_commands.describe(user="Target user", amount="XP to add")
    async def add_xp_cmd(self, interaction: discord.Interaction, user: discord.User, amount: int):
        if not self._is_owner(interaction):
            await interaction.response.send_message("Owner only.", ephemeral=True)
            return
        (await self.bot.db_worker.run(lambda: self.bot.db.ensure_user(user.id, user.display_name)))
        result = (await self.bot.db_worker.run(lambda: self.bot.db.add_xp(user.id, amount)))
        data = (await self.bot.db_worker.run(lambda: self.bot.db.get_user(user.id)))
        msg = f"✅ Added **{amount:,} XP** to **{user.display_name}**. Level: **{data['level']}**, XP: **{data['xp_current']}/{data.get('total_xp', 0)}**."
        if result.get("level_ups"):
            msg += f"\n🎉 Leveled up to: {', '.join(str(l) for l in result['level_ups'])}"
        if result.get("coins_earned"):
            msg += f"\n🪙 +{result['coins_earned']} coins from level-ups"
        await interaction.response.send_message(msg, ephemeral=True)

    @admin_group.command(name="wipe_db", description="DELETE ALL DATA and reset the database")
    async def wipe_db(self, interaction: discord.Interaction):
        if not self._is_owner(interaction):
            await interaction.response.send_message("Owner only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        view = _DestructiveConfirmView(self.bot, interaction.user.id, "wipe_db")
        embed = discord.Embed(
            title="⚠️ Confirm Database Wipe",
            description="This will **permanently delete ALL data** in every table.\n"
                        "A backup will be saved first, but this cannot be easily undone.\n\n"
                        "Are you sure?",
            color=COLOR_ERROR,
        )
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    async def _do_wipe_db(self, interaction: discord.Interaction):
        db_path = self.bot.db.path
        os.makedirs("backups", exist_ok=True)
        timestamp = datetime.now(EST).strftime("%Y%m%d_%H%M%S")
        (await self.bot.db_worker.run(lambda: self.bot.db.backup(f"backups/pre_wipe_{timestamp}.db")))
        with sqlite3.connect(db_path) as conn:
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()]
            for t in tables:
                conn.execute(f"DELETE FROM [{t}]")
            conn.execute("DELETE FROM sqlite_sequence")
            conn.commit()
        (await self.bot.db_worker.run(lambda: self.bot.db.initialize()))
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="✅ Database Wiped",
                description=f"Backup saved to `backups/pre_wipe_{timestamp}.db`.\n"
                            f"Cleared **{len(tables)}** tables. IDs reset to 1.",
                color=COLOR_SUCCESS,
            ),
            view=None,
        )

    @admin_group.command(name="reset_tasks", description="Delete all tasks and reset task IDs to 1")
    async def reset_tasks(self, interaction: discord.Interaction):
        if not self._is_owner(interaction):
            await interaction.response.send_message("Owner only.", ephemeral=True)
            return
        count = 0
        with sqlite3.connect(self.bot.db.path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]

        view = _DestructiveConfirmView(self.bot, interaction.user.id, "reset_tasks")
        embed = discord.Embed(
            title="⚠️ Confirm Task Reset",
            description=f"This will **permanently delete all {count} tasks** and reset IDs.\n\n"
                        "Are you sure?",
            color=COLOR_WARNING,
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def _do_reset_tasks(self, interaction: discord.Interaction):
        with sqlite3.connect(self.bot.db.path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
            conn.execute("DELETE FROM tasks")
            conn.execute("DELETE FROM sqlite_sequence WHERE name='tasks'")
            conn.commit()
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="✅ Tasks Reset",
                description=f"Deleted **{count}** tasks. Task IDs reset to 1.",
                color=COLOR_SUCCESS,
            ),
            view=None,
        )

    @admin_group.command(name="give_item", description="Give an inventory item to a user")
    @app_commands.describe(user="Target user", item_type="Item type (e.g. potion)", item_key="Item key (e.g. small_potion)")
    async def give_item(self, interaction: discord.Interaction,
                        user: discord.User, item_type: str, item_key: str):
        if not self._is_owner(interaction):
            await interaction.response.send_message("Owner only.", ephemeral=True)
            return
        (await self.bot.db_worker.run(lambda: self.bot.db.ensure_user(user.id, user.display_name)))
        (await self.bot.db_worker.run(lambda: self.bot.db.add_inventory_item(user.id, item_type, item_key)))
        await interaction.response.send_message(
            f"✅ Gave **{item_key}** ({item_type}) to **{user.display_name}**.",
            ephemeral=True
        )


class _DestructiveConfirmView(discord.ui.View):
    def __init__(self, bot, user_id: int, action: str):
        super().__init__(timeout=30)
        self.bot = bot
        self.user_id = user_id
        self.action = action

    @discord.ui.button(label="Yes, I'm sure", style=discord.ButtonStyle.danger, emoji="⚠️")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your action.", ephemeral=True)
            return
        admin_cog = self.bot.cogs.get("Admin")
        if not admin_cog:
            return
        if self.action == "wipe_db":
            await admin_cog._do_wipe_db(interaction)
        elif self.action == "reset_tasks":
            await admin_cog._do_reset_tasks(interaction)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            embed=discord.Embed(description="Cancelled.", color=COLOR_WARNING),
            view=None,
        )
        self.stop()

    async def on_timeout(self):
        pass


async def setup(bot):
    await bot.add_cog(Admin(bot))
