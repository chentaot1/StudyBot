# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

import discord
from discord.ext import commands
from discord import app_commands
import logging
import random
from datetime import datetime
from quest_definitions import QUEST_POOLS, METRIC_TO_QUEST_KEYS, TIER_REWARDS, ALL_COMPLETE_BONUS_PTS, ALL_COMPLETE_BONUS_XP

from constants import EST, COLOR_PRIMARY, COLOR_SUCCESS, COLOR_GOLD, COLOR_ORANGE

log = logging.getLogger("StudyBot.Quests")

QUEST_REROLL_DATE_KEY = "quest_reroll_used_date"


class Quests(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def assign_quests_for_user(self, user_id: int):
        quests = []
        for tier in [1, 2, 3]:
            pool = QUEST_POOLS[tier]
            pick = random.choice(pool)
            quests.append({"tier": tier, "quest_key": pick["key"], "target": pick["target"]})
        self.bot.db.assign_daily_quests(user_id, quests)

    async def track_quest(self, user_id: int, metric: str, amount: int = 1):
        """Called by other cogs when a trackable event happens."""
        quest_keys = METRIC_TO_QUEST_KEYS.get(metric, [])
        if not quest_keys:
            return

        newly_completed = []
        for qk in quest_keys:
            completed = self.bot.db.update_quest_progress(user_id, qk, amount)
            newly_completed.extend(completed)

        for q in newly_completed:
            reward = TIER_REWARDS.get(q["tier"], 0)
            if reward > 0:
                self.bot.db.add_points(user_id, reward, reason=f"Quest: {q['quest_key']}")

            if self.bot.db.get_dm_enabled(user_id, "quest_completion"):
                qdate = str(q.get("date") or datetime.now(EST).date().isoformat())
                qrow_id = int(q.get("id") or 0)
                self.bot.db.enqueue_outbox(
                    target_type="user",
                    target_id=int(user_id),
                    kind="quest_complete",
                    dedupe_key=f"quest_complete:{user_id}:{qdate}:{qrow_id}",
                    settings_key="quest_completion",
                    embed={
                        "title": f"📜 Quest Complete! (Tier {q['tier']})",
                        "description": (
                            f"**{q['quest_key'].replace('_', ' ').title()}** — +{reward} pts!"
                        ),
                        "color": int(COLOR_SUCCESS),
                    },
                )

        if newly_completed and self.bot.db.check_all_quests_complete(user_id):
            self.bot.db.add_points(user_id, ALL_COMPLETE_BONUS_PTS, reason="All daily quests complete")
            self.bot.db.add_xp(user_id, ALL_COMPLETE_BONUS_XP)
            await self.track_quest(user_id, "all_quests_done")

            if self.bot.db.get_dm_enabled(user_id, "quest_completion"):
                bonus_date = datetime.now(EST).date().isoformat()
                self.bot.db.enqueue_outbox(
                    target_type="user",
                    target_id=int(user_id),
                    kind="quest_all_complete",
                    dedupe_key=f"quest_all_complete_bonus:{user_id}:{bonus_date}",
                    settings_key="quest_completion",
                    embed={
                        "title": "🎉 All Quests Complete!",
                        "description": (
                            f"**+{ALL_COMPLETE_BONUS_PTS} pts** and **+{ALL_COMPLETE_BONUS_XP} XP** bonus!"
                        ),
                        "color": int(COLOR_GOLD),
                    },
                )

    @app_commands.command(name="quests", description="View today's daily quests")
    async def quests_cmd(self, interaction: discord.Interaction):
        if getattr(self.bot, "is_lite_user", lambda _uid: False)(interaction.user.id):
            await interaction.response.send_message("Lite mode: RPG features (quests) are disabled for your account.", ephemeral=True)
            return
        uid = interaction.user.id
        self.bot.db.ensure_user(uid, interaction.user.display_name)

        quests = self.bot.db.get_daily_quests(uid)
        if not quests:
            self.assign_quests_for_user(uid)
            quests = self.bot.db.get_daily_quests(uid)

        embed = discord.Embed(title="📜 Daily Quests", color=COLOR_PRIMARY)
        tier_labels = {1: "Easy", 2: "Medium", 3: "Hard"}
        tier_colors = {1: "🟢", 2: "🟡", 3: "🔴"}

        for q in quests:
            pool_entry = None
            for p in QUEST_POOLS.get(q["tier"], []):
                if p["key"] == q["quest_key"]:
                    pool_entry = p
                    break

            name = pool_entry["name"] if pool_entry else q["quest_key"].replace("_", " ").title()
            desc = pool_entry["desc"] if pool_entry else ""
            reward = TIER_REWARDS.get(q["tier"], 0)

            if q["completed"]:
                status = "✅ Complete!"
            else:
                status = f"`{q['progress']}/{q['target']}`"

            embed.add_field(
                name=f"{tier_colors[q['tier']]} T{q['tier']} ({tier_labels[q['tier']]}) — +{reward} pts",
                value=f"**{name}**: {desc}\n{status}",
                inline=False
            )

        all_done = all(q["completed"] for q in quests)
        if all_done:
            embed.set_footer(text=f"🎉 All quests complete! +{ALL_COMPLETE_BONUS_PTS} pts & +{ALL_COMPLETE_BONUS_XP} XP bonus earned!")
        else:
            remaining = sum(1 for q in quests if not q["completed"])
            embed.set_footer(text=f"{remaining} quest(s) remaining • Complete all 3 for a bonus!")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    quest_group = app_commands.Group(name="quest", description="Quest management")

    @quest_group.command(name="reroll", description="Reroll a quest (P2 Tactician perk)")
    @app_commands.describe(tier="Quest tier to reroll (1, 2, or 3)")
    async def reroll(self, interaction: discord.Interaction, tier: app_commands.Range[int, 1, 3]):
        if getattr(self.bot, "is_lite_user", lambda _uid: False)(interaction.user.id):
            await interaction.response.send_message("Lite mode: RPG features (quests) are disabled for your account.", ephemeral=True)
            return
        uid = interaction.user.id
        async with self.bot.user_locks[uid]:
            self.bot.db.ensure_user(uid, interaction.user.display_name)
            perks = self.bot.db.get_prestige_perks(uid)
            if "tactician" not in perks:
                await interaction.response.send_message(
                    "You need the **Tactician** perk (Prestige 2) to reroll quests.",
                    ephemeral=True
                )
                return

            today_est = datetime.now(EST).date().isoformat()
            if self.bot.db.get_setting(uid, QUEST_REROLL_DATE_KEY, "") == today_est:
                await interaction.response.send_message(
                    "You've already used your **Tactician** reroll today (1/day). Try again tomorrow!",
                    ephemeral=True,
                )
                return

            quests = self.bot.db.get_daily_quests(uid)
            current = next((q for q in quests if q["tier"] == tier), None)
            if not current:
                await interaction.response.send_message("No quest found for that tier.", ephemeral=True)
                return
            if current["completed"]:
                await interaction.response.send_message("That quest is already complete!", ephemeral=True)
                return

            pool = QUEST_POOLS[tier]
            candidates = [p for p in pool if p["key"] != current["quest_key"]]
            if not candidates:
                await interaction.response.send_message("No alternative quests available.", ephemeral=True)
                return

            new = random.choice(candidates)
            self.bot.db.reroll_quest(uid, tier, new["key"], new["target"])
            self.bot.db.set_setting(uid, QUEST_REROLL_DATE_KEY, today_est)

            embed = discord.Embed(
                title="🔄 Quest Rerolled!",
                description=f"**T{tier}**: {current['quest_key'].replace('_', ' ').title()} → **{new['name']}**\n{new['desc']}",
                color=COLOR_ORANGE
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Quests(bot))
