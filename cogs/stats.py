# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timedelta, timezone
import logging

from constants import EST, COLOR_PRIMARY, USER_NAV_FOOTER
from utils import fmt_mins, fmt_date_us, make_bar, streak_tier, parse_stored, goal_override_key_for_date

log = logging.getLogger("StudyBot.Stats")


class _DaysModal(discord.ui.Modal, title="Custom tag range"):
    days = discord.ui.TextInput(
        label="Days (e.g. 7, 30, 120)",
        placeholder="30",
        max_length=6,
        required=True,
    )

    def __init__(self, cog: "Stats", user_id: int):
        super().__init__()
        self.cog = cog
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This view is for the requesting user only.", ephemeral=True)
            return
        raw = (self.days.value or "").strip()
        try:
            n = int(raw)
        except Exception:
            n = 30
        n = max(1, min(n, 3650))
        await self.cog._render_tags_breakdown(interaction, user_id=self.user_id, range_key="days", days=n, edit=True)


class TagBreakdownView(discord.ui.View):
    def __init__(self, cog: "Stats", user_id: int, *, range_key: str, days: int | None):
        super().__init__(timeout=180)
        self.cog = cog
        self.user_id = user_id
        self.range_key = range_key
        self.days = days

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This view is for the requesting user only.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="7d", style=discord.ButtonStyle.secondary, row=0)
    async def b7(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog._render_tags_breakdown(interaction, user_id=self.user_id, range_key="7d", days=7, edit=True)

    @discord.ui.button(label="30d", style=discord.ButtonStyle.secondary, row=0)
    async def b30(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog._render_tags_breakdown(interaction, user_id=self.user_id, range_key="30d", days=30, edit=True)

    @discord.ui.button(label="Season", style=discord.ButtonStyle.secondary, row=0)
    async def bseason(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog._render_tags_breakdown(interaction, user_id=self.user_id, range_key="season", days=None, edit=True)

    @discord.ui.button(label="All", style=discord.ButtonStyle.secondary, row=0)
    async def ball(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog._render_tags_breakdown(interaction, user_id=self.user_id, range_key="all", days=None, edit=True)

    @discord.ui.button(label="Custom", style=discord.ButtonStyle.primary, row=0)
    async def bcustom(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(_DaysModal(self.cog, self.user_id))


class Stats(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _season_start_est(self, now_est: datetime) -> datetime:
        """Season boundaries use the 21st (EST). Returns season start datetime in EST."""
        y = now_est.year
        m = now_est.month
        d = now_est.day
        if (m == 12 and d >= 21):
            return now_est.replace(month=12, day=21, hour=0, minute=0, second=0, microsecond=0)
        if m in (1, 2) or (m == 3 and d < 21):
            return now_est.replace(year=y - 1, month=12, day=21, hour=0, minute=0, second=0, microsecond=0)
        if (m == 3 and d >= 21) or m in (4, 5) or (m == 6 and d < 21):
            return now_est.replace(month=3, day=21, hour=0, minute=0, second=0, microsecond=0)
        if (m == 6 and d >= 21) or m in (7, 8) or (m == 9 and d < 21):
            return now_est.replace(month=6, day=21, hour=0, minute=0, second=0, microsecond=0)
        if (m == 9 and d >= 21) or m in (10, 11) or (m == 12 and d < 21):
            return now_est.replace(month=9, day=21, hour=0, minute=0, second=0, microsecond=0)
        return now_est.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)

    async def _render_tags_breakdown(
        self,
        interaction: discord.Interaction,
        *,
        user_id: int,
        range_key: str,
        days: int | None,
        edit: bool,
    ):
        self.bot.db.ensure_user(user_id, str(interaction.user))
        now_est = datetime.now(EST)

        label = "30 Days"
        eff_days = days
        if range_key == "7d":
            label = "7 Days"
            eff_days = 7
        elif range_key == "30d":
            label = "30 Days"
            eff_days = 30
        elif range_key == "season":
            start = self._season_start_est(now_est).date()
            eff_days = (now_est.date() - start).days + 1
            label = "Season"
        elif range_key == "all":
            eff_days = None
            label = "All Time"
        elif range_key == "days" and days:
            label = f"{days} Days"
            eff_days = days

        tags = self.bot.db.get_tag_stats(user_id, days=eff_days)
        if not tags:
            embed = discord.Embed(title=f"🏷️ Tag Breakdown ({label})", color=0x5865F2)
            embed.description = "_No tagged sessions in this range._"
            view = TagBreakdownView(self, user_id, range_key=range_key, days=eff_days)
            if edit:
                await interaction.response.edit_message(embed=embed, view=view)
            else:
                await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            return

        total = sum(t["total_minutes"] for t in tags)
        embed = discord.Embed(title=f"🏷️ Tag Breakdown ({label})", color=0x5865F2)
        max_mins = tags[0]["total_minutes"] or 1
        medals = ["🥇", "🥈", "🥉"]
        for i, t in enumerate(tags[:24]):
            medal = medals[i] if i < 3 else "  "
            bar = make_bar(t["total_minutes"], max_mins, 12)
            pct = int(t["total_minutes"] / total * 100) if total else 0
            embed.add_field(
                name=f"{medal} {t['tag']}",
                value=f"`{bar}` {pct}%\n⏱️ {fmt_mins(t['total_minutes'])}  📚 {t['session_count']} sessions  ⭐ {t['total_xp']} XP",
                inline=False,
            )
        if len(tags) > 24:
            embed.add_field(name="...", value=f"_+{len(tags)-24} more tags not shown_", inline=False)
        embed.set_footer(text=f"Total tagged time: {fmt_mins(total)} across {len(tags)} tags • {USER_NAV_FOOTER}")

        view = TagBreakdownView(self, user_id, range_key=range_key, days=eff_days)
        if edit:
            await interaction.response.edit_message(embed=embed, view=view)
        else:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(
        name="stats",
        description="Full dashboard: XP, streak, goals, tasks, season rank, weekly minutes",
    )
    async def stats(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        self.bot.db.ensure_user(interaction.user.id, str(interaction.user))

        # LAZY EVALUATION: Check adaptive goal logic every time stats are viewed!
        await self.bot._check_adaptive_goal(interaction.user.id)
        is_lite = getattr(self.bot, "is_lite_user", lambda _uid: False)(interaction.user.id)

        user = self.bot.db.get_user(interaction.user.id)
        total_mins = self.bot.db.get_total_study_minutes(interaction.user.id)
        weekly_mins = self.bot.db.get_weekly_study_minutes(interaction.user.id)
        sessions = self.bot.db.get_user_sessions(interaction.user.id, limit=100)
        all_tasks = self.bot.db.get_user_tasks(interaction.user.id, include_done=True)
        done_tasks = [t for t in all_tasks if t["completed"]]
        pending_tasks = [t for t in all_tasks if not t["completed"]]
        rewards = self.bot.db.get_rewards(interaction.user.id)
        redemptions = self.bot.db.get_redemption_history(interaction.user.id, limit=100)
        avg_rating = self.bot.db.get_average_focus_rating(interaction.user.id)
        projects = self.bot.db.get_projects(interaction.user.id)

        from database import xp_for_level, MAX_LEVEL, seasonal_rank_for_minutes
        rpg_level = user.get("level", 1)
        xp_into = user.get("xp_current", 0)
        xp_needed = xp_for_level(rpg_level) if rpg_level < MAX_LEVEL else 0
        level_pct = int(xp_into / xp_needed * 100) if xp_needed else 100
        level_bar = make_bar(xp_into, xp_needed, 16)
        prestige = user.get("prestige", 0)

        s = user["streak"]
        tier = streak_tier(s)

        ended = [s for s in sessions if s.get("duration_minutes")]
        best = max(ended, key=lambda s: s["duration_minutes"], default=None)
        avg_session = total_mins // len(ended) if ended else 0

        week_ago_est = (datetime.now(EST).date() - timedelta(days=6)).isoformat()
        def _to_est_date(ts: str) -> str | None:
            try:
                return datetime.fromisoformat(ts.split("+")[0].split("Z")[0])\
                    .replace(tzinfo=timezone.utc).astimezone(EST).date().isoformat()
            except Exception:
                return None
        study_days_week = len(set(
            d for s in sessions
            if (d := _to_est_date(s["started_at"])) and d >= week_ago_est
        ))

        embed = discord.Embed(title="📊 Your Study Dashboard", color=0x5865F2, timestamp=datetime.now(timezone.utc))
        embed.set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar.url)

        if not is_lite:
            prestige_tag = f"[P{prestige}] " if prestige else ""
            if rpg_level < MAX_LEVEL:
                embed.add_field(
                    name=f"⚔️ {prestige_tag}Level {rpg_level}",
                    value=f"`{level_bar}` {level_pct}%\n{xp_into:,}/{xp_needed:,} XP → Level {rpg_level+1}",
                    inline=False
                )
            else:
                embed.add_field(
                    name=f"⚔️ {prestige_tag}Level {MAX_LEVEL} (MAX)",
                    value="Banked: " + str(user.get("banked_xp", 0)) + " XP",
                    inline=False,
                )

        embed.add_field(name="💎 Points", value=f"{user['points']:,}", inline=True)
        if not is_lite:
            embed.add_field(name="🪙 Coins", value=str(user.get("coins", 0)), inline=True)
        embed.add_field(name="🔥 Streak", value=f"{s}d ({tier})", inline=True)

        embed.add_field(name="📅 This Week", value=fmt_mins(weekly_mins), inline=True)
        embed.add_field(name="⏱️ All Time", value=fmt_mins(total_mins), inline=True)
        embed.add_field(name="📆 Study Days (7d)", value=f"{study_days_week}/7", inline=True)

        embed.add_field(name="📚 Sessions", value=str(len(ended)), inline=True)
        embed.add_field(name="🏅 Best Session", value=fmt_mins(best["duration_minutes"]) if best else "—", inline=True)
        embed.add_field(name="📈 Avg Session", value=fmt_mins(avg_session), inline=True)

        if avg_rating:
            stars = "⭐" * round(avg_rating)
            embed.add_field(name="🎯 Avg Focus", value=f"{stars} {avg_rating}/5", inline=True)

        embed.add_field(
            name="✅ Tasks",
            value=f"{len(done_tasks)} done · {len(pending_tasks)} pending",
            inline=True
        )
        embed.add_field(name="📁 Projects", value=str(len(projects)), inline=True)

        pts_spent = sum(r["cost"] for r in redemptions)
        embed.add_field(name="🏪 Rewards", value=f"{len(rewards)} in shop · {len(redemptions)} redeemed · {pts_spent} pts spent", inline=False)

        goal = self.bot.db.get_today_goal(interaction.user.id)
        # DERIVED STATE TRUTH: Pull the real today's minutes
        today_iso = datetime.now(EST).date().isoformat()
        today_mins = self.bot.db.get_study_minutes_on_date(interaction.user.id, today_iso)

        if goal > 0:
            pct = min(int(today_mins / goal * 100), 100)
            bar = make_bar(today_mins, goal, 16)
            embed.add_field(name="🎯 Today's Goal", value=f"`{bar}` {pct}%  {fmt_mins(today_mins)}/{fmt_mins(goal)}", inline=False)

        seasonal_mins = user.get("seasonal_minutes", 0)
        if seasonal_mins > 0:
            rank = seasonal_rank_for_minutes(seasonal_mins)
            embed.add_field(name="🏅 Season", value=f"{rank} ({fmt_mins(seasonal_mins)})", inline=True)

        now_est = datetime.now(EST).strftime("%I:%M %p EST")
        embed.set_footer(text=f"StudyBot • {now_est} • /breakdown for deeper analysis")
        await interaction.followup.send(embed=embed, ephemeral=True)

        quest_cog = self.bot.cogs.get("Quests")
        if quest_cog:
            await quest_cog.track_quest(interaction.user.id, "stats_viewed")

    @app_commands.command(
        name="today",
        description="Daily snapshot: streak, goal, potions, raid — same info as /stats in one screen",
    )
    async def today_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        uid = interaction.user.id
        self.bot.db.ensure_user(uid, str(interaction.user))
        await self.bot._check_adaptive_goal(uid)
        is_lite = getattr(self.bot, "is_lite_user", lambda _uid: False)(uid)

        user = self.bot.db.get_user(uid)
        if not user:
            await interaction.followup.send("Could not load your profile.", ephemeral=True)
            return

        today_iso = datetime.now(EST).date().isoformat()
        today_mins = self.bot.db.get_study_minutes_on_date(uid, today_iso)
        goal = self.bot.db.get_today_goal(uid)

        embed = discord.Embed(title="📌 Today", color=COLOR_PRIMARY)
        embed.set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar.url)

        s = user["streak"]
        embed.add_field(name="🔥 Streak", value=f"{s}d ({streak_tier(s)})", inline=True)
        embed.add_field(name="💎 Points", value=f"{user['points']:,}", inline=True)
        if not is_lite:
            embed.add_field(name="🪙 Coins", value=str(user.get("coins", 0)), inline=True)

        if goal > 0:
            pct = min(int(today_mins / goal * 100), 100)
            bar = make_bar(today_mins, goal, 14)
            embed.add_field(
                name="🎯 Today's goal",
                value=f"`{bar}` {pct}% · {fmt_mins(today_mins)}/{fmt_mins(goal)}",
                inline=False,
            )
        else:
            embed.add_field(name="🎯 Today's goal", value="Not set — use `/goals set`", inline=False)

        potions = self.bot.db.get_active_potions(uid)
        if potions:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            lines = []
            for p in potions:
                expires = parse_stored(p["expires_at"])
                remaining = max((expires - now).total_seconds() / 60, 0)
                lines.append(f"**{p['effect_type']}** {p['multiplier']}x · {fmt_mins(int(remaining))} left")
            embed.add_field(name="🧪 Potions", value="\n".join(lines[:8]), inline=False)
        else:
            embed.add_field(name="🧪 Potions", value="_None active_", inline=False)

        overflow = self.bot.db.get_overflow(uid)
        if overflow and not is_lite:
            total_ov = sum(o["amount"] for o in overflow)
            embed.add_field(
                name="📬 Coin overflow",
                value=f"**{total_ov}** pending — open `/inventory`",
                inline=False,
            )

        session = self.bot.db.get_active_session(uid)
        if session:
            from cogs.study import get_elapsed_and_paused

            active_secs, _ = get_elapsed_and_paused(session)
            subj = session.get("subject") or "General"
            paused = " (paused)" if session.get("is_paused") else ""
            embed.add_field(
                name="📚 Active session",
                value=f"**{subj}**{paused} · {fmt_mins(active_secs // 60)} active",
                inline=False,
            )

        if not is_lite:
            boss = self.bot.db.get_active_boss()
            if boss and boss.get("hp_remaining", 0) > 0:
                hp = boss["hp"]
                rem = boss["hp_remaining"]
                embed.add_field(
                    name="⚔️ Raid boss",
                    value=f"**{rem:,}** / {hp:,} HP left — `/raid`",
                    inline=False,
                )
            else:
                embed.add_field(name="⚔️ Raid boss", value="_No active boss_", inline=False)

        sched_blocks = self.bot.db.get_user_schedule(uid)
        if sched_blocks and not self.bot.db.get_dm_enabled(uid, "schedule_reminders"):
            embed.add_field(
                name="📅 Schedule DMs",
                value="You have `/schedule` blocks but **Schedule Reminders** are off — turn them on in `/settings` to get start-time DMs.",
                inline=False,
            )

        embed.set_footer(text=USER_NAV_FOOTER)
        await interaction.followup.send(embed=embed, ephemeral=True)

    breakdown = app_commands.Group(
        name="breakdown",
        description="Deep dives: time by subject, daily patterns — use /breakdown subjects",
    )

    @breakdown.command(name="subjects", description="Time per subject (last 30 days)")
    async def breakdown_subjects(self, interaction: discord.Interaction):
        self.bot.db.ensure_user(interaction.user.id, str(interaction.user))
        subjects = self.bot.db.get_subject_stats(interaction.user.id, days=30)
        if not subjects:
            await interaction.response.send_message("No sessions in the last 30 days.", ephemeral=True)
            return
        total = sum(s["total_minutes"] for s in subjects)
        embed = discord.Embed(title="📚 Subject Breakdown (30 Days)", color=0x5865F2)
        max_mins = subjects[0]["total_minutes"] or 1
        medals = ["🥇","🥈","🥉"]

        # CHANGED: 25 to 24 to prevent Discord Embed 26-field crash
        for i, s in enumerate(subjects[:24]):
            medal = medals[i] if i < 3 else "  "
            bar = make_bar(s["total_minutes"], max_mins, 12)
            pct = int(s["total_minutes"] / total * 100) if total else 0
            rating_str = ""
            if s["avg_rating"]:
                stars = "⭐" * round(s["avg_rating"])
                rating_str = f" · Focus: {stars}"
            embed.add_field(
                name=f"{medal} {s['subject']}",
                value=f"`{bar}` {pct}%\n⏱️ {fmt_mins(s['total_minutes'])}  📚 {s['session_count']} sessions  ⭐ {s['total_xp']} XP{rating_str}",
                inline=False
            )

        # CHANGED: 25 to 24
        if len(subjects) > 24:
            embed.add_field(name="...", value=f"_+{len(subjects)-24} more subjects not shown_", inline=False)

        embed.set_footer(text=f"Total: {fmt_mins(total)} across {len(subjects)} subjects")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @breakdown.command(name="tags", description="Time per tag (pick a range with buttons or options)")
    @app_commands.describe(
        range="Quick range preset (ignored if days is provided)",
        days="Custom range in days (1..3650)",
    )
    @app_commands.choices(range=[
        app_commands.Choice(name="7 days", value="7d"),
        app_commands.Choice(name="30 days", value="30d"),
        app_commands.Choice(name="This season", value="season"),
        app_commands.Choice(name="All time", value="all"),
    ])
    async def breakdown_tags(
        self,
        interaction: discord.Interaction,
        range: str = "30d",
        days: app_commands.Range[int, 1, 3650] | None = None,
    ):
        rk = "days" if days is not None else range
        await self._render_tags_breakdown(
            interaction,
            user_id=interaction.user.id,
            range_key=rk,
            days=int(days) if days is not None else None,
            edit=False,
        )

    @breakdown.command(name="week", description="Day-by-day study activity this week")
    async def breakdown_week(self, interaction: discord.Interaction):
        self.bot.db.ensure_user(interaction.user.id, str(interaction.user))
        daily = self.bot.db.get_daily_breakdown(interaction.user.id, days=7)
        user_data = self.bot.db.get_user(interaction.user.id)
        default_goal = user_data["daily_goal_minutes"] if user_data else 60
        day_map = {d["date"]: d["minutes"] for d in daily}
        today = datetime.now(EST).date()
        days = [(today - timedelta(days=6-i)) for i in range(7)]
        total = sum(day_map.get(d.isoformat(), 0) for d in days)
        max_mins = max((day_map.get(d.isoformat(), 0) for d in days), default=1) or 1
        goal_hits = self.bot.db.get_goal_hit_streak(interaction.user.id, days=7)
        lines = []
        days_hit = 0
        for i, d in enumerate(days):
            mins = day_map.get(d.isoformat(), 0)
            day_key = goal_override_key_for_date(d)
            override = user_data.get(f"goal_{day_key}") if user_data else None
            day_goal = override if override is not None else default_goal
            hit = goal_hits[i] if i < len(goal_hits) else False
            if hit:
                days_hit += 1
            if day_goal == 0:
                goal_marker = "🛌"
            elif hit:
                goal_marker = "✅"
            elif mins > 0:
                goal_marker = "🎯"
            else:
                goal_marker = "  "
            bar = make_bar(mins, max(max_mins, day_goal if day_goal > 0 else max_mins), 14)
            today_marker = " ← today" if d == today else ""
            lines.append(f"`{d.strftime('%a')} {fmt_date_us(d)}` {goal_marker} `{bar}` {fmt_mins(mins)}{today_marker}")
        embed = discord.Embed(title="📅 This Week", color=0x5865F2)
        embed.description = "\n".join(lines)
        embed.add_field(name="Total", value=fmt_mins(total), inline=True)
        embed.add_field(name="Daily Avg", value=fmt_mins(total // 7), inline=True)
        embed.add_field(name="Goal Days", value=f"{days_hit}/7", inline=True)
        embed.set_footer(text=f"✅=goal hit 🎯=partial 🛌=rest day")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @breakdown.command(name="focus", description="Peak focus hours, quality trends, best time to study")
    async def breakdown_focus(self, interaction: discord.Interaction):
        self.bot.db.ensure_user(interaction.user.id, str(interaction.user))
        hourly = self.bot.db.get_hourly_stats(interaction.user.id, days=30)

        if not hourly:
            await interaction.response.send_message("Not enough data yet — study more sessions first!", ephemeral=True)
            return

        embed = discord.Embed(
            title="🧠 Focus Analytics (Last 30 Days)",
            description="Your study patterns by time of day (EST)",
            color=0x5865F2
        )

        max_count = max((h["session_count"] for h in hourly), default=1) or 1
        max_avg = max((h["avg_minutes"] for h in hourly), default=1) or 1
        best_by_count = max(hourly, key=lambda h: h["session_count"])
        best_by_avg = max(hourly, key=lambda h: h["avg_minutes"])
        best_by_rating = max((h for h in hourly if h["avg_rating"] > 0), key=lambda h: h["avg_rating"], default=None)

        def fmt_hour(h: int) -> str:
            ampm = "AM" if h < 12 else "PM"
            disp = h % 12 or 12
            return f"{disp}:00 {ampm}"

        lines = []
        for h in hourly:
            bar = make_bar(h["session_count"], max_count, 10)
            rating_str = f" ⭐{h['avg_rating']:.1f}" if h["avg_rating"] else ""
            lines.append(f"`{fmt_hour(h['est_hour']):8s}` `{bar}` {h['session_count']} sessions · {fmt_mins(int(h['avg_minutes']))} avg{rating_str}")

        am_lines = [l for l, h in zip(lines, hourly) if h["est_hour"] < 12]
        pm_lines = [l for l, h in zip(lines, hourly) if h["est_hour"] >= 12]

        if am_lines:
            embed.add_field(name="🌅 Morning (AM)", value="\n".join(am_lines), inline=False)
        if pm_lines:
            embed.add_field(name="🌆 Afternoon/Evening (PM)", value="\n".join(pm_lines), inline=False)

        insights = [
            f"📊 **Most active**: {fmt_hour(best_by_count['est_hour'])} ({best_by_count['session_count']} sessions)",
            f"⏱️ **Longest sessions**: {fmt_hour(best_by_avg['est_hour'])} (avg {fmt_mins(int(best_by_avg['avg_minutes']))})",
        ]
        if best_by_rating:
            insights.append(f"🎯 **Best focus**: {fmt_hour(best_by_rating['est_hour'])} (avg {best_by_rating['avg_rating']:.1f}⭐)")

        if best_by_rating and best_by_rating["avg_rating"] >= 4:
            insights.append(f"\n✨ **Tip**: Your peak focus window appears to be around **{fmt_hour(best_by_rating['est_hour'])}** — try scheduling your hardest work then.")
        elif best_by_avg:
            insights.append(f"\n✨ **Tip**: You study longest around **{fmt_hour(best_by_avg['est_hour'])}** — consider blocking this time for deep work.")

        embed.add_field(name="🔍 Insights", value="\n".join(insights), inline=False)
        embed.set_footer(text="Rate your sessions with the stars after /study stop to improve this data")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Stats(bot))
