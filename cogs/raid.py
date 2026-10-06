# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

import asyncio
import discord
from discord.ext import commands
from discord import app_commands
import logging
import random
from datetime import datetime

from utils import fmt_hp, parse_stored, utcnow_naive
from constants import (
    SEED_HP,
    COLOR_PRIMARY,
    COLOR_SUCCESS,
    COLOR_ERROR,
    COLOR_RAID,
    COLOR_GOLD,
)

log = logging.getLogger("StudyBot.Raid")


class Raid(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def handle_boss_cycle(self):
        """Called every Monday at midnight by the scheduler."""
        boss = self.bot.db.get_active_boss()

        if boss:
            ends = parse_stored(boss["ends_at"])
            now = utcnow_naive()
            if now >= ends:
                await self._resolve_boss(boss)
            else:
                return

        last_boss = self._get_last_boss()
        if last_boss:
            next_hp = self.bot.db.calculate_next_boss_hp(last_boss)
        else:
            next_hp = SEED_HP

        self._spawn_new_boss(next_hp)

    def _get_last_boss(self):
        return self.bot.db.get_last_ended_boss()

    def _spawn_new_boss(self, hp: int):
        boss = self.bot.db.spawn_boss(hp)
        log.info(f"Spawned raid boss #{boss['id']} with {hp} HP")
        asyncio.create_task(self._announce_boss(boss))

    async def broadcast_daily_progress(self):
        """Post current boss HP to each guild's raid (or general) channel — scheduled once daily."""
        boss = self.bot.db.get_active_boss()
        if not boss:
            return

        ends = parse_stored(boss["ends_at"])
        now = utcnow_naive()
        remaining = ends - now
        if remaining.total_seconds() <= 0:
            return

        days_left = max(remaining.days, 0)
        hours_left = max(remaining.seconds // 3600, 0)
        embed = discord.Embed(
            title="⚔️ Raid boss — server update",
            color=COLOR_RAID,
            description="Study to deal damage! **1 min studied = 1 damage** (Beacon can boost raid damage).",
        )
        embed.add_field(name="Boss HP", value=fmt_hp(boss["hp_remaining"], boss["hp"]), inline=False)
        embed.add_field(name="⏳ Time left", value=f"{days_left}d {hours_left}h", inline=True)
        embed.set_footer(text="Use /raid for your stats • /raid_leaderboard for rankings")

        for guild in self.bot.guilds:
            channel_id = self.bot.db.get_channel(guild.id, "raid")
            if not channel_id:
                channel_id = self.bot.db.get_channel(guild.id, "general")
            if not channel_id:
                continue
            channel = self.bot.get_channel(channel_id)
            if channel:
                try:
                    from constants import SB_PING_ROLE_ID
                    await channel.send(
                        content=(f"<@&{SB_PING_ROLE_ID}>" if SB_PING_ROLE_ID else None),
                        embed=embed,
                        allowed_mentions=discord.AllowedMentions(roles=True),
                    )
                except Exception as e:
                    log.debug("Raid daily digest send failed (guild=%s, channel=%s): %s", guild.id, channel_id, e)

    async def _announce_boss(self, boss: dict):
        for guild in self.bot.guilds:
            channel_id = self.bot.db.get_channel(guild.id, "raid")
            if not channel_id:
                channel_id = self.bot.db.get_channel(guild.id, "general")
            if not channel_id:
                continue
            channel = self.bot.get_channel(channel_id)
            if not channel:
                continue
            embed = discord.Embed(
                title="⚔️ A New Raid Boss Has Appeared!",
                description=f"**HP:** {boss['hp']:,}\n"
                            f"Deal damage by studying! (1 min = 1 damage)\n\n"
                            f"The boss escapes in **14 days**. Can you defeat it?",
                color=COLOR_RAID
            )
            embed.set_footer(text="Use /raid to check status • Study to deal damage!")
            try:
                from constants import SB_PING_ROLE_ID
                await channel.send(
                    content=(f"<@&{SB_PING_ROLE_ID}>" if SB_PING_ROLE_ID else None),
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions(roles=True),
                )
            except Exception as e:
                log.debug("Raid boss announce failed (guild=%s, channel=%s): %s", guild.id, channel_id, e)

    async def _resolve_boss(self, boss: dict):
        boss = self.bot.db.end_boss(boss["id"])
        if not boss:
            return

        killed = boss["killed"]
        lb = self.bot.db.get_raid_leaderboard(boss["id"], limit=20)
        participants = self.bot.db.get_raid_participants(boss["id"], min_damage=60)

        # Podium rewards
        podium_rewards = [
            (0, 5, 2000, "🥇 1st — Raid champion"),
            (1, 3, 1000, "🥈 2nd Place"),
            (2, 1, 500,  "🥉 3rd Place"),
        ]

        awarded = []
        for idx, coins, xp, label in podium_rewards:
            if idx < len(lb):
                entry = lb[idx]
                uid = entry["user_id"]
                self.bot.db.add_coins(uid, coins)
                self.bot.db.add_xp(uid, xp)
                awarded.append(f"{label}: <@{uid}> ({entry['raw_damage']:,} dmg) — +{coins}c +{xp:,} XP")

        if not killed and lb:
            mvp = lb[0]
            self.bot.db.add_coins(mvp["user_id"], 1)
            awarded.append(f"🏅 MVP (Pity): <@{mvp['user_id']}> — +1c")
            badge_cog = self.bot.cogs.get("Badges")
            if badge_cog:
                await badge_cog.check_valiant_pity_badge(mvp["user_id"])

        top3_ids = {lb[i]["user_id"] for i in range(min(3, len(lb)))}
        participant_uids = {p["user_id"] for p in participants}

        for p in participants:
            uid = p["user_id"]
            scav = "scavenger" in self.bot.db.get_prestige_perks(uid)
            if uid in top3_ids:
                if scav:
                    self._roll_participation(uid, boss, rolls=1)
            else:
                self._roll_participation(uid, boss, rolls=2 if scav else 1)

        # P2 Scavenger: podium + <60 dmg edge case — still get 1 participation roll
        for i in range(min(3, len(lb))):
            uid = lb[i]["user_id"]
            if uid in participant_uids:
                continue
            if "scavenger" not in self.bot.db.get_prestige_perks(uid):
                continue
            self._roll_participation(uid, boss, rolls=1)

        badge_cog = self.bot.cogs.get("Badges")
        if badge_cog:
            for i in range(min(3, len(lb))):
                await badge_cog.check_mvp_podium_finish(lb[i]["user_id"])
            if killed:
                for uid in self.bot.db.get_raid_damage_user_ids(boss["id"]):
                    await badge_cog.check_raid_badges(uid, {"killed": True})

        await self._announce_result(boss, awarded, lb[:5])

    def _roll_participation(self, user_id: int, boss: dict, rolls: int = 1):
        for _ in range(rolls):
            roll = random.randint(1, 100)
            if roll <= 50:
                self.bot.db.add_xp(user_id, 300)
            elif roll <= 85:
                self.bot.db.add_points(user_id, 500, reason="Raid participation")
            else:
                self.bot.db.add_coins(user_id, 1)

    async def _announce_result(self, boss: dict, awarded: list, top5: list):
        killed = boss["killed"]
        title = "⚔️ Raid Boss Defeated!" if killed else "⚔️ Raid Boss Escaped!"
        color = COLOR_SUCCESS if killed else COLOR_ERROR

        embed = discord.Embed(title=title, color=color)
        desc = f"**HP:** {boss['hp_remaining']:,}/{boss['hp']:,}\n"
        if killed:
            desc += "The boss has been **slain**! 🎉"
        else:
            desc += "The boss **escaped**. Better luck next time."
        embed.description = desc

        if awarded:
            embed.add_field(name="🏆 Rewards", value="\n".join(awarded), inline=False)

        if top5:
            lines = []
            for i, entry in enumerate(top5):
                medal = ["🥇", "🥈", "🥉"][i] if i < 3 else f"#{i+1}"
                lines.append(f"{medal} <@{entry['user_id']}> — {entry['raw_damage']:,} dmg")
            embed.add_field(name="📊 Top Damage", value="\n".join(lines), inline=False)

        for guild in self.bot.guilds:
            channel_id = self.bot.db.get_channel(guild.id, "raid")
            if not channel_id:
                channel_id = self.bot.db.get_channel(guild.id, "general")
            if not channel_id:
                continue
            channel = self.bot.get_channel(channel_id)
            if channel:
                try:
                    from constants import SB_PING_ROLE_ID
                    await channel.send(
                        content=(f"<@&{SB_PING_ROLE_ID}>" if SB_PING_ROLE_ID else None),
                        embed=embed,
                        allowed_mentions=discord.AllowedMentions(roles=True),
                    )
                except Exception as e:
                    log.debug("Raid resolution send failed (guild=%s, channel=%s): %s", guild.id, channel_id, e)

    @app_commands.command(
        name="raid",
        description="Boss HP, time left, and your damage — study minutes add damage each session",
    )
    async def raid_cmd(self, interaction: discord.Interaction):
        if getattr(self.bot, "is_lite_user", lambda _uid: False)(interaction.user.id):
            await interaction.response.send_message("Lite mode: RPG features (raids) are disabled for your account.", ephemeral=True)
            return
        uid = interaction.user.id
        await interaction.response.defer(ephemeral=True)
        self.bot.db.ensure_user(uid, interaction.user.display_name)
        boss = self.bot.db.get_active_boss()

        if not boss:
            await interaction.followup.send("No active raid boss right now. Check back Monday!", ephemeral=True)
            return

        ends = parse_stored(boss["ends_at"])
        now = utcnow_naive()
        remaining = ends - now
        days_left = max(remaining.days, 0)
        hours_left = max(remaining.seconds // 3600, 0)

        user_dmg = self.bot.db.get_user_raid_damage(uid, boss["id"])

        embed = discord.Embed(title="⚔️ Raid Boss", color=COLOR_RAID)
        embed.add_field(name="HP", value=fmt_hp(boss["hp_remaining"], boss["hp"]), inline=False)
        embed.add_field(name="⏳ Time Left", value=f"{days_left}d {hours_left}h", inline=True)
        embed.add_field(name="⚔️ Your Damage", value=f"{user_dmg:,}", inline=True)
        embed.set_footer(text="Study to deal damage! 1 min = 1 dmg")

        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="raid_leaderboard",
        description="Top damage dealers for the current boss — podium rewards when the cycle ends",
    )
    async def raid_lb_cmd(self, interaction: discord.Interaction):
        if getattr(self.bot, "is_lite_user", lambda _uid: False)(interaction.user.id):
            await interaction.response.send_message("Lite mode: RPG features (raids) are disabled for your account.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        boss = self.bot.db.get_active_boss()
        if not boss:
            await interaction.followup.send("No active raid boss.", ephemeral=True)
            return

        lb = self.bot.db.get_raid_leaderboard(boss["id"], limit=15)
        if not lb:
            await interaction.followup.send("No damage dealt yet!", ephemeral=True)
            return

        lines = []
        for i, entry in enumerate(lb):
            medal = ["🥇", "🥈", "🥉"][i] if i < 3 else f"`#{i+1}`"
            name = entry.get("username") or f"User {entry['user_id']}"
            lines.append(f"{medal} **{name}** — {entry['raw_damage']:,} dmg")

        embed = discord.Embed(title="⚔️ Raid Leaderboard", color=COLOR_RAID)
        embed.description = "\n".join(lines)
        embed.add_field(
            name="Boss HP",
            value=fmt_hp(boss["hp_remaining"], boss["hp"]),
            inline=False
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Raid(bot))
