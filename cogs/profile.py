# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

import discord
from discord.ext import commands
from discord import app_commands
import logging
from datetime import datetime
from database import xp_for_level, seasonal_rank_for_minutes
from cogs.badges import BADGE_REGISTRY, BADGE_EMOJIS
from utils import fmt_mins, fmt_hp
from constants import (
    COLOR_PRIMARY,
    COLOR_SUCCESS,
    COLOR_GOLD,
    COLOR_MUTED,
    MAX_LEVEL,
    EST,
    DUO_LEADERBOARD_ORDER,
    DUO_LEADERBOARD_USER_IDS,
)

log = logging.getLogger("StudyBot.Profile")


# Default DM settings (all on by default)
DM_TOGGLES = {
    "raid_announcements":  "Raid Announcements",
    "quest_completion":    "Quest Completion",
    "badge_unlocks":       "Badge Unlocks",
    "inactivity_warnings": "Inactivity Warnings",
    "evening_checkins":    "Evening Check-ins",
    "schedule_reminders":  "Schedule Reminders",
    "morning_briefing":    "Morning Briefing",
    "weekly_report":       "Weekly Report",
    "cheer_received":      "Cheer Received",
    "bounty_activated":    "Bounty Activated",
    "bounty_payout":       "Bounty Payout",
    "group_pomo":          "Group Pomodoro",
    "temptation_bundle":   "Temptation bundle (treat DMs)",
    "pomodoro_phases":     "Solo Pomodoro phase DMs",
    "study_motivation":    "Study halfway motivation",
    "lucky_loot":          "Lucky loot notifications",
}


class Profile(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="profile", description="View your (or someone's) profile")
    @app_commands.describe(user="User to view (leave blank for yourself)")
    async def profile_cmd(self, interaction: discord.Interaction, user: discord.User = None):
        target = user or interaction.user
        uid = target.id
        is_lite = getattr(self.bot, "is_lite_user", lambda _uid: False)(uid)
        (await self.bot.db_worker.run(lambda: self.bot.db.ensure_user(uid, target.display_name)))
        data = (await self.bot.db_worker.run(lambda: self.bot.db.get_user(uid)))
        if not data:
            await interaction.response.send_message("User not found.", ephemeral=True)
            return

        prestige = data.get("prestige", 0)
        level = data.get("level", 1)
        xp_current = data.get("xp_current", 0)
        xp_needed = xp_for_level(level) if level < MAX_LEVEL else 0
        coins = data.get("coins", 0)
        streak = data.get("streak", 0)
        total_mins = (await self.bot.db_worker.run(lambda: self.bot.db.get_total_study_minutes(uid)))
        seasonal_mins = data.get("seasonal_minutes", 0)
        seasonal_rank = seasonal_rank_for_minutes(seasonal_mins)

        title_parts = [target.display_name]
        if data.get("suffix_title"):
            title_parts.append(data["suffix_title"])
        if prestige > 0 and not is_lite:
            title_parts.insert(0, f"[P{prestige}]")

        embed = discord.Embed(
            title=" ".join(title_parts),
            color=int(data["role_color_hex"], 16) if data.get("role_color_hex") else COLOR_PRIMARY
        )
        inv = (await self.bot.db_worker.run(lambda: self.bot.db.get_inventory(uid)))
        has_lb_icon = any(
            it.get("item_type") == "cosmetic" and it.get("item_key") == "lb_icon"
            for it in inv
        )
        thumb = None
        if has_lb_icon:
            thumb = str(target.display_avatar.replace(size=256).url)
        elif data.get("leaderboard_icon"):
            thumb = data["leaderboard_icon"]
        if thumb:
            embed.set_thumbnail(url=thumb)

        if not is_lite:
            # XP Bar
            if level < MAX_LEVEL and xp_needed > 0:
                pct = xp_current / xp_needed
                bar_len = 15
                filled = int(pct * bar_len)
                bar = "█" * filled + "░" * (bar_len - filled)
                xp_line = f"Lv.**{level}** `{bar}` {xp_current:,}/{xp_needed:,} XP"
            else:
                xp_line = f"Lv.**{MAX_LEVEL}** (MAX)" + (f" | Banked: {data.get('banked_xp', 0):,} XP" if data.get('banked_xp') else "")
            embed.add_field(name="⚔️ Level", value=xp_line, inline=False)

        # Freezes
        freezes = (await self.bot.db_worker.run(lambda: self.bot.db.get_freezes(uid)))
        freeze_display = f"{'🧊' * freezes['count']}{'⬜' * (2 - freezes['count'])}" if freezes["count"] > 0 else "None"

        embed.add_field(name="🔥 Streak", value=f"**{streak}** days", inline=True)
        embed.add_field(name="🧊 Freezes", value=freeze_display, inline=True)
        if not is_lite:
            embed.add_field(name="🪙 Coins", value=str(coins), inline=True)
        embed.add_field(name="💎 Points", value=f"{data['points']:,}", inline=True)
        embed.add_field(name="⏱️ Total Time", value=fmt_mins(total_mins), inline=True)
        embed.add_field(name="🏅 Season", value=f"{seasonal_rank} ({fmt_mins(seasonal_mins)})", inline=True)

        # Featured badges
        featured = (await self.bot.db_worker.run(lambda: self.bot.db.get_featured_badges(uid)))
        if featured:
            badge_names = []
            for bk in featured:
                info = BADGE_REGISTRY.get(bk, {})
                cat = info.get("cat", "")
                emoji = BADGE_EMOJIS.get(cat, "🏅")
                badge_names.append(f"{emoji} {info.get('name', bk)}")
            embed.add_field(name="🏅 Badges", value=" • ".join(badge_names), inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="duo", description="Duo leaderboard (just you and your buddy)")
    async def duo_cmd(self, interaction: discord.Interaction):
        """A tiny leaderboard that works in Lite Mode (productivity-only metrics)."""
        if interaction.user.id not in DUO_LEADERBOARD_USER_IDS:
            await interaction.response.send_message("This leaderboard isn't available for your account.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        ids = list(DUO_LEADERBOARD_ORDER)
        users: dict[int, dict] = {}

        # Resolve display names where possible (safe for DMs).
        for uid in ids:
            u = await getattr(self.bot, "get_user_or_fetch", lambda _uid: None)(uid)
            display = u.display_name if u else f"User {uid}"
            (await self.bot.db_worker.run(lambda: self.bot.db.ensure_user(uid, display)))
            row = (await self.bot.db_worker.run(lambda: self.bot.db.get_user(uid))) or {"user_id": uid, "username": display}
            if not row.get("username"):
                row["username"] = display
            users[uid] = row

        today_iso = datetime.now(EST).date().isoformat()

        def _stat(uid: int) -> dict:
            return {
                "today_mins": int(self.bot.db.get_study_minutes_on_date(uid, today_iso) or 0),
                "weekly_mins": int(self.bot.db.get_weekly_study_minutes(uid) or 0),
                "total_mins": int(self.bot.db.get_total_study_minutes(uid) or 0),
                "streak": int((users[uid].get("streak") or 0)),
                "seasonal_mins": int((users[uid].get("seasonal_minutes") or 0)),
            }

        stats = await self.bot.db_worker.run(lambda: {uid: _stat(uid) for uid in ids})

        def _winner_label(key: str, higher_is_better: bool = True) -> int | None:
            a, b = ids[0], ids[1]
            va, vb = stats[a][key], stats[b][key]
            if va == vb:
                return None
            if higher_is_better:
                return a if va > vb else b
            return a if va < vb else b

        def _line(uid: int, key: str, fmt) -> str:
            name = users[uid].get("username") or f"User {uid}"
            val = fmt(stats[uid][key])
            return f"**{name}** — {val}"

        embed = discord.Embed(title="🤝 Duo Leaderboard", color=COLOR_GOLD)
        embed.description = "A small snapshot comparing just two users."

        w_today = _winner_label("today_mins")
        embed.add_field(
            name="📌 Today (minutes)",
            value="\n".join(
                [("🏆 " if w_today == uid else "") + _line(uid, "today_mins", lambda x: fmt_mins(x)) for uid in ids]
            ),
            inline=False,
        )

        w_week = _winner_label("weekly_mins")
        embed.add_field(
            name="📅 Last 7 days (minutes)",
            value="\n".join(
                [("🏆 " if w_week == uid else "") + _line(uid, "weekly_mins", lambda x: fmt_mins(x)) for uid in ids]
            ),
            inline=False,
        )

        w_total = _winner_label("total_mins")
        embed.add_field(
            name="⏱️ All time (minutes)",
            value="\n".join(
                [("🏆 " if w_total == uid else "") + _line(uid, "total_mins", lambda x: fmt_mins(x)) for uid in ids]
            ),
            inline=False,
        )

        w_streak = _winner_label("streak")
        embed.add_field(
            name="🔥 Streak (days)",
            value="\n".join(
                [("🏆 " if w_streak == uid else "") + _line(uid, "streak", lambda x: f"{x}d") for uid in ids]
            ),
            inline=False,
        )

        w_season = _winner_label("seasonal_mins")
        embed.add_field(
            name="🏅 Season (minutes)",
            value="\n".join(
                [("🏆 " if w_season == uid else "") + _line(uid, "seasonal_mins", lambda x: fmt_mins(x)) for uid in ids]
            ),
            inline=False,
        )

        embed.set_footer(text="Tip: use /today and /stats for details")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="leaderboard", description="View server leaderboards")
    @app_commands.describe(category="Which leaderboard to view")
    @app_commands.choices(category=[
        app_commands.Choice(name="XP", value="xp"),
        app_commands.Choice(name="Study Minutes", value="minutes"),
        app_commands.Choice(name="Streak", value="streak"),
        app_commands.Choice(name="Seasonal Rank", value="seasonal"),
        app_commands.Choice(name="Raid Damage", value="raid"),
    ])
    async def leaderboard_cmd(self, interaction: discord.Interaction, category: str = "xp"):
        if getattr(self.bot, "is_lite_user", lambda _uid: False)(interaction.user.id):
            await interaction.response.send_message("Lite mode: RPG features (leaderboards) are disabled for your account.", ephemeral=True)
            return
        # Raid leaderboard uses the active boss context rather than lifetime totals.
        if category == "raid":
            boss = (await self.bot.db_worker.run(lambda: self.bot.db.get_active_boss()))
            if not boss:
                await interaction.response.send_message("No active raid boss.", ephemeral=True)
                return
            lb = (await self.bot.db_worker.run(lambda: self.bot.db.get_raid_leaderboard(boss["id"], limit=15)))
            if not lb:
                await interaction.response.send_message("No damage dealt yet!", ephemeral=True)
                return

            lines = []
            for i, entry in enumerate(lb):
                medal = ["🥇", "🥈", "🥉"][i] if i < 3 else f"`#{i+1}`"
                name = entry.get("username") or f"User {entry.get('user_id', '?')}"
                lines.append(f"{medal} **{name}** — {entry.get('raw_damage', 0):,} dmg")

            embed = discord.Embed(title="⚔️ Raid Leaderboard", color=COLOR_GOLD)
            embed.description = "\n".join(lines)
            embed.add_field(name="Boss HP", value=fmt_hp(boss["hp_remaining"], boss["hp"]), inline=False)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        lb = (await self.bot.db_worker.run(lambda: self.bot.db.get_leaderboard(category, limit=15)))
        if not lb:
            await interaction.response.send_message("No data for this leaderboard yet.", ephemeral=True)
            return

        titles = {
            "xp": "🏆 XP Leaderboard",
            "minutes": "⏱️ Study Minutes Leaderboard",
            "streak": "🔥 Streak Leaderboard",
            "seasonal": "🏅 Seasonal Leaderboard",
            "raid": "⚔️ Raid Damage Leaderboard",
        }

        lines = []
        for i, entry in enumerate(lb):
            medal = ["🥇", "🥈", "🥉"][i] if i < 3 else f"`#{i+1}`"
            name = entry.get("username") or f"User {entry.get('user_id', '?')}"

            if category == "xp":
                val = f"{entry.get('total_xp', 0):,} XP"
            elif category == "minutes":
                val = fmt_mins(entry.get("total_minutes", 0))
            elif category == "streak":
                val = f"{entry.get('streak', 0)} days"
            elif category == "seasonal":
                mins = entry.get("seasonal_minutes", 0)
                rank = seasonal_rank_for_minutes(mins)
                val = f"{rank} ({fmt_mins(mins)})"
            elif category == "raid":
                val = f"{entry.get('raw_damage', 0):,} dmg"
            else:
                val = "?"

            lines.append(f"{medal} **{name}** — {val}")

        embed = discord.Embed(title=titles.get(category, "Leaderboard"), color=COLOR_GOLD)
        embed.description = "\n".join(lines)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="settings", description="Configure your StudyBot settings")
    async def settings_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        uid = interaction.user.id
        (await self.bot.db_worker.run(lambda: self.bot.db.ensure_user(uid, interaction.user.display_name)))
        user = (await self.bot.db_worker.run(lambda: self.bot.db.get_user(uid)))

        embed = discord.Embed(title="⚙️ Settings", color=COLOR_PRIMARY)

        ghost = "🟢 ON" if user.get("ghost_mode") else "🔴 OFF"
        cheers = "🔴 Blocked" if user.get("block_cheers") else "🟢 Allowed"
        embed.add_field(name="👻 Ghost Mode", value=ghost, inline=True)
        embed.add_field(name="📣 Cheers", value=cheers, inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=True)

        dm_lines = []
        for key, label in DM_TOGGLES.items():
            enabled = (await self.bot.db_worker.run(lambda: self.bot.db.get_dm_enabled(uid, key)))
            status = "✅" if enabled else "❌"
            dm_lines.append(f"{status} {label}")
        embed.add_field(name="📬 DM Notifications", value="\n".join(dm_lines), inline=False)

        from views.forms import PreferencesView
        from services.scheduling import DEFAULT_TIMEZONE
        zone = await self.bot.db_worker.run(self.bot.db.get_setting, uid, "timezone", DEFAULT_TIMEZONE)
        embed.add_field(name="Timezone", value=zone, inline=False)
        view = PreferencesView(self.bot, uid)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


class SettingsView(discord.ui.View):
    def __init__(self, bot, user_id: int):
        super().__init__(timeout=120)
        self.bot = bot
        self.user_id = user_id

    @discord.ui.button(label="Toggle Ghost Mode", style=discord.ButtonStyle.secondary, emoji="👻")
    async def toggle_ghost(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return
        user = (await self.bot.db_worker.run(lambda: self.bot.db.get_user(self.user_id)))
        new_val = 0 if user.get("ghost_mode") else 1
        (await self.bot.db_worker.run(lambda: self.bot.db.set_ghost_mode(self.user_id, bool(new_val))))
        status = "enabled" if new_val else "disabled"
        await interaction.response.send_message(f"👻 Ghost Mode **{status}**.", ephemeral=True)

    @discord.ui.button(label="Toggle Block Cheers", style=discord.ButtonStyle.secondary, emoji="📣")
    async def toggle_cheers(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return
        user = (await self.bot.db_worker.run(lambda: self.bot.db.get_user(self.user_id)))
        new_val = 0 if user.get("block_cheers") else 1
        (await self.bot.db_worker.run(lambda: self.bot.db.set_block_cheers(self.user_id, bool(new_val))))
        status = "blocked" if new_val else "allowed"
        await interaction.response.send_message(f"📣 Cheers now **{status}**.", ephemeral=True)

    @discord.ui.select(
        placeholder="Toggle a DM notification...",
        options=[discord.SelectOption(label=label, value=key) for key, label in DM_TOGGLES.items()]
    )
    async def toggle_dm(self, interaction: discord.Interaction, select: discord.ui.Select):
        if interaction.user.id != self.user_id:
            return
        key = select.values[0]
        current = (await self.bot.db_worker.run(lambda: self.bot.db.get_dm_enabled(self.user_id, key)))
        new_val = "0" if current else "1"
        (await self.bot.db_worker.run(lambda: self.bot.db.set_setting(self.user_id, f"dm_{key}", new_val)))
        label = DM_TOGGLES.get(key, key)
        status = "enabled" if new_val == "1" else "disabled"
        await interaction.response.send_message(f"📬 **{label}** notifications **{status}**.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Profile(bot))
