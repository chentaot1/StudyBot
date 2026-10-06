# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

import discord
from discord.ext import commands
from discord import app_commands
import logging
from datetime import datetime, timezone

from utils import fmt_mins, parse_stored
from constants import EST, COLOR_PRIMARY, COLOR_SUCCESS

log = logging.getLogger("StudyBot.Social")


class Social(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="cheer", description="Send encouragement to a fellow studier!")
    @app_commands.checks.cooldown(1, 10.0)
    @app_commands.describe(target="The user to cheer on")
    async def cheer(self, interaction: discord.Interaction, target: discord.User):
        if getattr(self.bot, "is_lite_user", lambda _uid: False)(interaction.user.id):
            await interaction.response.send_message("Lite mode: social/RPG features are disabled for your account.", ephemeral=True)
            return
        uid = interaction.user.id
        if target.id == uid:
            await interaction.response.send_message("You can't cheer yourself! (But we appreciate the energy.)", ephemeral=True)
            return

        self.bot.db.ensure_user(uid, interaction.user.display_name)
        self.bot.db.ensure_user(target.id, target.display_name)

        target_data = self.bot.db.get_user(target.id)
        if target_data and (target_data.get("ghost_mode") or target_data.get("block_cheers")):
            await interaction.response.send_message("That user has blocked cheers.", ephemeral=True)
            return

        ok = self.bot.db.send_cheer(uid, target.id)
        if not ok:
            await interaction.response.send_message(
                "You've already cheered that person **today (EST)**. Try again tomorrow!",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"📣 Cheer sent to {target.display_name}!",
            description="Keep spreading the good vibes!",
            color=COLOR_SUCCESS
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

        quest_cog = self.bot.cogs.get("Quests")
        if quest_cog:
            await quest_cog.track_quest(uid, "cheer_sent")

        badge_cog = self.bot.cogs.get("Badges")
        if badge_cog:
            await badge_cog.check_social_badges(uid)

        if self.bot.db.get_dm_enabled(target.id, "cheer_received"):
            today_est = datetime.now(EST).date().isoformat()
            self.bot.db.enqueue_outbox(
                target_type="user",
                target_id=int(target.id),
                kind="cheer_received",
                settings_key="cheer_received",
                dedupe_key=f"cheer_received:{uid}:{target.id}:{today_est}",
                embed={
                    "title": "📣 You got a Cheer!",
                    "description": f"**{interaction.user.display_name}** is cheering you on! Keep studying!",
                    "color": int(COLOR_SUCCESS),
                },
            )

    @app_commands.command(name="who", description="See who's currently studying")
    @app_commands.checks.cooldown(1, 5.0)
    async def who(self, interaction: discord.Interaction):
        if getattr(self.bot, "is_lite_user", lambda _uid: False)(interaction.user.id):
            await interaction.response.send_message("Lite mode: social/RPG features are disabled for your account.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        sessions = self.bot.db.get_all_active_sessions()
        if not sessions:
            await interaction.followup.send("Nobody is studying right now. Be the first! 📚", ephemeral=True)
            return

        user_ids = [s["user_id"] for s in sessions]
        users_batch = self.bot.db.get_users_batch(user_ids)

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        lines = []
        for s in sessions:
            user_data = users_batch.get(s["user_id"])
            if not user_data or user_data.get("ghost_mode"):
                continue
            if getattr(self.bot, "is_lite_user", lambda _uid: False)(s["user_id"]):
                continue

            name = user_data.get("username") or f"User {s['user_id']}"
            subject = s.get("subject") or "General"
            started = parse_stored(s["started_at"])
            elapsed_mins = int((now - started).total_seconds() / 60)
            paused = "⏸️" if s.get("is_paused") else "📚"
            lines.append(f"{paused} **{name}** — {subject} ({fmt_mins(elapsed_mins)})")

        if not lines:
            await interaction.followup.send("Nobody is studying right now (or everyone's in Ghost Mode).", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"📚 Currently Studying ({len(lines)})",
            description="\n".join(lines[:20]),
            color=COLOR_PRIMARY
        )
        if len(lines) > 20:
            embed.set_footer(text=f"...and {len(lines) - 20} more")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Social(bot))
