# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timedelta
import logging

from constants import EST, COLOR_PRIMARY, COLOR_SUCCESS, COLOR_WARNING, COLOR_MUTED
from utils import fmt_date_us

log = logging.getLogger("StudyBot.Checkin")


class Checkin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    checkin = app_commands.Group(name="checkin", description="Evening accountability check-in settings")

    @checkin.command(name="settings", description="Configure your evening check-in")
    @app_commands.describe(
        enabled="Turn evening check-ins on or off",
        hour="Hour to receive check-in DM (EST, 0-23). Default 20 = 8pm."
    )
    async def checkin_settings(
        self,
        interaction: discord.Interaction,
        enabled: bool,
        hour: app_commands.Range[int, 0, 23] = 20
    ):
        self.bot.db.ensure_user(interaction.user.id, str(interaction.user))
        self.bot.db.set_checkin_settings(interaction.user.id, enabled, hour)

        # Format hour nicely
        ampm_hour = hour % 12 or 12
        ampm = "AM" if hour < 12 else "PM"
        time_str = f"{ampm_hour}:00 {ampm} EST"

        if enabled:
            embed = discord.Embed(
                title="✅ Evening Check-in Enabled",
                description=f"I'll DM you at **{time_str}** each day to check if you studied.",
                color=COLOR_SUCCESS
            )
            embed.add_field(
                name="How it works",
                value=(
                    "• If you've already studied today, it auto-records ✅ silently.\n"
                    "• If not, I'll ask with ✅/❌ buttons.\n"
                    "• Saying ❌ won't break your streak — only missing a session does."
                ),
                inline=False
            )
        else:
            embed = discord.Embed(
                title="❌ Evening Check-in Disabled",
                description="No more nightly DMs.",
                color=COLOR_MUTED
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @checkin.command(name="history", description="View your check-in history (last 14 days)")
    async def checkin_history(self, interaction: discord.Interaction):
        self.bot.db.ensure_user(interaction.user.id, str(interaction.user))
        entries = self.bot.db.get_checkin_history_with_study(interaction.user.id, days=14)

        embed = discord.Embed(title="📋 Check-in History (14 days)", color=COLOR_PRIMARY)
        lines = []
        studied_count = 0
        for e in entries:
            day = datetime.strptime(e["date"], "%Y-%m-%d")
            day_str = f"{day.strftime('%a')} {fmt_date_us(day.date())}"
            if e["studied"]:
                icon = "✅"
                studied_count += 1
            elif e["is_today"]:
                icon = "📅"
            else:
                icon = "⬜"
            mins_str = f" ({e['minutes']}m)" if e["minutes"] > 0 else ""
            lines.append(f"`{day_str}` {icon}{mins_str}")

        embed.description = "\n".join(lines)
        embed.add_field(name="Studied", value=f"{studied_count}/14 days", inline=True)
        embed.add_field(name="Rate", value=f"{int(studied_count/14*100)}%", inline=True)

        user_data = self.bot.db.get_user(interaction.user.id)
        if user_data and user_data.get("checkin_enabled"):
            hour = user_data.get("checkin_hour", 20)
            ampm_hour = hour % 12 or 12
            ampm = "AM" if hour < 12 else "PM"
            embed.set_footer(text=f"Check-in at {ampm_hour}:00 {ampm} EST • ✅=studied ⬜=no data 📅=today")
        else:
            embed.set_footer(text="Check-ins off. Enable with /checkin settings • ✅=studied ⬜=no data")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @checkin.command(name="now", description="Log today's check-in manually")
    @app_commands.describe(studied="Did you study today?", note="Optional note")
    async def checkin_now(self, interaction: discord.Interaction, studied: bool, note: str = ""):
        self.bot.db.ensure_user(interaction.user.id, str(interaction.user))
        today = datetime.now(EST).date().isoformat()
        self.bot.db.record_checkin(interaction.user.id, today, studied, note, restore_streak=studied)
        user_data = self.bot.db.get_user(interaction.user.id)

        if studied:
            embed = discord.Embed(title="✅ Check-in logged!", description="Marked as studied today.", color=COLOR_SUCCESS)
            embed.add_field(name="🔥 Streak", value=f"{user_data['streak']} days")
        else:
            embed = discord.Embed(title="❌ Check-in logged", description="No study today — tomorrow's a fresh start!", color=COLOR_WARNING)
        if note:
            embed.add_field(name="Note", value=note, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="adaptive", description="Toggle adaptive goal suggestions on or off")
    @app_commands.describe(enabled="On = bot suggests goal adjustments weekly")
    async def adaptive(self, interaction: discord.Interaction, enabled: bool):
        self.bot.db.ensure_user(interaction.user.id, str(interaction.user))
        self.bot.db.set_adaptive_goals(interaction.user.id, enabled)
        status = "✅ enabled" if enabled else "❌ disabled"
        embed = discord.Embed(
            title=f"🎯 Adaptive Goals {status}",
            description=(
                "Each week after daily reset, I'll analyze your goal hit rate and suggest adjustments.\n"
                "• 6+/7 days hit → suggest increasing your goal\n"
                "• 2/7 or fewer → suggest scaling back"
            ) if enabled else "You won't receive adaptive goal suggestions.",
            color=COLOR_SUCCESS if enabled else COLOR_MUTED
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Checkin(bot))
