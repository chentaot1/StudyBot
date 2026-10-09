# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

import discord
from discord.ext import commands
from discord import app_commands
import logging
import random
from datetime import datetime

from constants import (
    EST, POTION_CATALOG, GACHA_TIERS, COSMETIC_COSTS,
    COLOR_ORANGE, COLOR_SUCCESS, COLOR_ERROR, COLOR_GOLD,
)

log = logging.getLogger("StudyBot.Shop")


class Shop(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def notify_economy_badges(self, user_id: int):
        cog = self.bot.cogs.get("Badges")
        if cog:
            await cog.check_economy_badges(user_id)

    async def _get_potion_params(self, user_id: int, potion_key: str) -> dict:
        """Apply prestige modifiers to a potion."""
        base = POTION_CATALOG[potion_key].copy()
        perks = (await self.bot.db_worker.run(lambda: self.bot.db.get_prestige_perks(user_id)))
        if "time_lord" in perks:
            base["hours"] *= 2
        if "master_alchemist" in perks:
            base["mult"] = 2.0
        return base

    @app_commands.command(name="shop", description="Browse the StudyBot shop")
    async def shop_cmd(self, interaction: discord.Interaction):
        if getattr(self.bot, "is_lite_user", lambda _uid: False)(interaction.user.id):
            await interaction.response.send_message("Lite mode: RPG/economy features are disabled for your account.", ephemeral=True)
            return
        uid = interaction.user.id
        (await self.bot.db_worker.run(lambda: self.bot.db.ensure_user(uid, interaction.user.display_name)))
        user = (await self.bot.db_worker.run(lambda: self.bot.db.get_user(uid)))
        perks = (await self.bot.db_worker.run(lambda: self.bot.db.get_prestige_perks(uid)))

        embed = discord.Embed(title="🛒 StudyBot Shop", color=0xE67E22)
        embed.description = f"💎 **{user['points']:,}** pts  •  🪙 **{user['coins']}** coins"

        potion_lines = []
        for key, p in POTION_CATALOG.items():
            mod = (await self._get_potion_params(uid, key))
            cost = f"{p['cost_pts']:,} pts" if p['cost_pts'] else f"{p['cost_coins']}c"
            potion_lines.append(f"**{mod['name']}** — {cost} — {mod['mult']}x XP / {mod['hours']}h")
        potion_lines.append(
            "_Same potion again **extends** your current timer (one active buff per potion type)._"
        )
        embed.add_field(name="🧪 Potions", value="\n".join(potion_lines), inline=False)

        es_cost = 2 if "resilient" in perks else 3
        embed.add_field(name="🛡️ Emergency Save", value=f"**{es_cost}c** — +1 Streak Freeze (Mon-Fri, bank must be 0)", inline=False)

        gacha_lines = []
        for tier, g in GACHA_TIERS.items():
            extra = " (5% Jackpot: 50,000 pts!)" if tier == "gold" else ""
            gacha_lines.append(f"**{tier.title()}** — {g['cost']}c{extra}")
        gacha_lines.append("*Use `/gacha <tier>` to pull*")
        embed.add_field(name="🎰 Gacha", value="\n".join(gacha_lines), inline=False)

        embed.add_field(
            name="📢 Social",
            value="**Study Bounty** — 5c — Place a pending bounty (activates when they `/study start`)\n"
                  "└ 2h **2.0x XP** window for both + a **points payout** on the target's session end\n"
                  "└ Use `/bounty @user`\n"
                  "**The Beacon** — 10c — 1.5x raid dmg + 200 pts/hr for 2h\n"
                  "└ Use `/beacon`",
            inline=False
        )

        embed.add_field(
            name="🎨 Cosmetics",
            value="**Leaderboard Icon** — 10c — custom thumbnail on **`/profile`** (your Discord avatar)\n"
                  "**Suffix Title** — 15c\n**Role Color** — 25c",
            inline=False
        )

        view = ShopView(self, uid)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="gacha", description="Try your luck with the gacha!")
    @app_commands.checks.cooldown(1, 5.0)
    @app_commands.describe(tier="bronze (1c), silver (5c), or gold (15c)")
    @app_commands.choices(tier=[
        app_commands.Choice(name="Bronze (1c)", value="bronze"),
        app_commands.Choice(name="Silver (5c)", value="silver"),
        app_commands.Choice(name="Gold (15c)", value="gold"),
    ])
    async def gacha_cmd(self, interaction: discord.Interaction, tier: str):
        if getattr(self.bot, "is_lite_user", lambda _uid: False)(interaction.user.id):
            await interaction.response.send_message("Lite mode: RPG/economy features are disabled for your account.", ephemeral=True)
            return
        uid = interaction.user.id
        await interaction.response.defer(ephemeral=True)
        async with self.bot.user_locks[uid]:
            (await self.bot.db_worker.run(lambda: self.bot.db.ensure_user(uid, interaction.user.display_name)))
            g = GACHA_TIERS[tier]
            if not (await self.bot.db_worker.run(lambda: self.bot.db.remove_coins(uid, g["cost"]))):
                await interaction.followup.send(
                    f"Not enough coins! You need **{g['cost']}c** for a {tier} pull.",
                    ephemeral=True
                )
                return

            roll = random.randint(1, 100)
            is_jackpot = False
            if roll <= g["loss_pct"]:
                result = random.randint(*g["loss_range"])
                outcome = "loss"
            elif g["jackpot_pct"] > 0 and roll > 100 - g["jackpot_pct"]:
                result = g["jackpot"]
                outcome = "jackpot"
                is_jackpot = True
            else:
                result = random.randint(*g["profit_range"])
                outcome = "profit"

            (await self.bot.db_worker.run(lambda: self.bot.db.add_points(uid, result, reason=f"Gacha {tier}", track_earned=False)))
            (await self.bot.db_worker.run(lambda: self.bot.db.record_gacha(uid, tier, g["cost"], result, is_jackpot)))

            badge_cog = self.bot.cogs.get("Badges")
            if badge_cog:
                await badge_cog.check_deg_gambler_badge(uid)
                await badge_cog.check_economy_badges(uid)

            if outcome == "jackpot":
                embed = discord.Embed(title="🎰💰 JACKPOT! 💰🎰", color=0xF1C40F)
                embed.description = f"You hit the **Gold Jackpot**!\n\n🎉 **+{result:,} points!**"
            elif outcome == "profit":
                embed = discord.Embed(title="🎰 Nice Pull!", color=0x57F287)
                embed.description = f"**{tier.title()} Gacha** — You won **+{result:,} pts**!"
            else:
                embed = discord.Embed(title="🎰 Better Luck Next Time", color=0xE74C3C)
                embed.description = f"**{tier.title()} Gacha** — You got **+{result:,} pts** (cost: {g['cost']}c)"

            user = (await self.bot.db_worker.run(lambda: self.bot.db.get_user(uid)))
            embed.add_field(name="Balance", value=f"💎 {user['points']:,} pts  •  🪙 {user['coins']}c", inline=False)
            await interaction.followup.send(embed=embed, ephemeral=True)

            await self.notify_economy_badges(uid)

    @app_commands.command(name="bounty", description="Send a Study Bounty to a friend (5c)")
    @app_commands.checks.cooldown(1, 10.0)
    @app_commands.describe(target="The user to send the bounty to")
    async def bounty_cmd(self, interaction: discord.Interaction, target: discord.User):
        if getattr(self.bot, "is_lite_user", lambda _uid: False)(interaction.user.id):
            await interaction.response.send_message("Lite mode: RPG/economy features are disabled for your account.", ephemeral=True)
            return
        if getattr(self.bot, "is_lite_user", lambda _uid: False)(target.id):
            await interaction.response.send_message("You can’t place a bounty on that account.", ephemeral=True)
            return
        uid = interaction.user.id
        await interaction.response.defer(ephemeral=True)
        async with self.bot.user_locks[uid]:
            (await self.bot.db_worker.run(lambda: self.bot.db.ensure_user(uid, interaction.user.display_name)))
            if target.id == uid:
                await interaction.followup.send("You can't bounty yourself!", ephemeral=True)
                return
            target_data = (await self.bot.db_worker.run(lambda: self.bot.db.get_user(target.id)))
            if target_data and target_data.get("ghost_mode"):
                await interaction.followup.send("That user has Ghost Mode enabled.", ephemeral=True)
                return
            (await self.bot.db_worker.run(lambda: self.bot.db.ensure_user(target.id, target.display_name)))
            # Target can only have one active bounty at a time (pending or active buff window)
            if (await self.bot.db_worker.run(lambda: self.bot.db.get_active_bounty(target.id))):
                await interaction.followup.send("That user already has an active bounty. No coins were spent.", ephemeral=True)
                return
            if not (await self.bot.db_worker.run(lambda: self.bot.db.remove_coins(uid, 5))):
                await interaction.followup.send("Not enough coins (5c required).", ephemeral=True)
                return
            bounty_id = (await self.bot.db_worker.run(lambda: self.bot.db.create_bounty(uid, target.id, cost_coins=5)))

            embed = discord.Embed(
                title="📢 Study Bounty Sent!",
                description=(
                    f"A bounty is now **pending** on {target.mention}.\n"
                    f"It activates when they run **`/study start`**.\n"
                    f"If they don’t start within **12 hours**, your **5c** is refunded automatically."
                ),
                color=0xE67E22
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

            badge_cog = self.bot.cogs.get("Badges")
            if badge_cog:
                await badge_cog.check_sponsor_badge(uid)
                await badge_cog.check_economy_badges(uid)

            if (await self.bot.db_worker.run(lambda: self.bot.db.get_dm_enabled(target.id, "bounty_activated"))):
                (await self.bot.db_worker.run(lambda: self.bot.db.enqueue_outbox(
                    target_type="user",
                    target_id=target.id,
                    kind="bounty_pending",
                    dedupe_key=f"bounty_pending:{bounty_id}:{target.id}",
                    settings_key="bounty_activated",
                    embed={
                        "title": "📢 You've Received a Study Bounty!",
                        "description": (
                            f"**{interaction.user.display_name}** placed a **pending bounty** on you.\n"
                            f"It activates when you run **`/study start`** (2 hour 2.0× XP window)."
                        ),
                        "color": 0xE67E22,
                    },
                )))

    @app_commands.command(name="beacon", description="Activate The Beacon (10c) — 1.5x raid dmg + 200 pts/hr for 2h")
    @app_commands.checks.cooldown(1, 10.0)
    async def beacon_cmd(self, interaction: discord.Interaction):
        if getattr(self.bot, "is_lite_user", lambda _uid: False)(interaction.user.id):
            await interaction.response.send_message("Lite mode: RPG/economy features are disabled for your account.", ephemeral=True)
            return
        uid = interaction.user.id
        await interaction.response.defer(ephemeral=True)
        async with self.bot.user_locks[uid]:
            (await self.bot.db_worker.run(lambda: self.bot.db.ensure_user(uid, interaction.user.display_name)))
            perks = (await self.bot.db_worker.run(lambda: self.bot.db.get_prestige_perks(uid)))
            duration = 4 if "time_lord" in perks else 2

            if not (await self.bot.db_worker.run(lambda: self.bot.db.remove_coins(uid, 10))):
                await interaction.followup.send("Not enough coins (10c required).", ephemeral=True)
                return

            channel_id = interaction.channel_id if interaction.channel else 0
            (await self.bot.db_worker.run(lambda: self.bot.db.create_beacon(uid, channel_id, duration)))

            embed = discord.Embed(
                title="🔥 The Beacon is Lit!",
                description=f"**1.5x raid damage** and **+200 pts/hr** for **{duration} hours**!",
                color=0xF1C40F
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

        # Announcement to configured beacon channel (server-only)
        try:
            if interaction.guild_id:
                announce_ch_id = (await self.bot.db_worker.run(lambda: self.bot.db.get_channel(interaction.guild_id, "beacon")))
                if announce_ch_id:
                    ch = self.bot.get_channel(announce_ch_id)
                    if ch:
                        ann = discord.Embed(
                            title="🔥 The Beacon is Lit!",
                            description=(
                                f"**{interaction.user.display_name}** activated **The Beacon**.\n"
                                f"**1.5x raid damage** and **+200 pts/hr** for **{duration} hours**."
                            ),
                            color=0xF1C40F,
                        )
                        from constants import SB_PING_ROLE_ID
                        await ch.send(
                            content=(f"<@&{SB_PING_ROLE_ID}>" if SB_PING_ROLE_ID else None),
                            embed=ann,
                            allowed_mentions=discord.AllowedMentions(roles=True),
                        )
        except Exception:
            pass

            badge_cog = self.bot.cogs.get("Badges")
            if badge_cog:
                await badge_cog.check_beacon_badge(uid)
                await badge_cog.check_economy_badges(uid)


class ShopView(discord.ui.View):
    def __init__(self, shop: "Shop", user_id: int):
        super().__init__(timeout=120)
        self.shop = shop
        self.bot = shop.bot
        self.user_id = user_id

    @discord.ui.select(
        placeholder="Choose an item to buy...",
        options=[
            discord.SelectOption(label="Small Potion (800 pts)", value="small_potion", emoji="🧪"),
            discord.SelectOption(label="Large Potion (1,500 pts)", value="large_potion", emoji="🧪"),
            discord.SelectOption(label="Mega Potion (4c)", value="mega_potion", emoji="🧪"),
            discord.SelectOption(label="Emergency Save (3c)", value="emergency_save", emoji="🛡️"),
            discord.SelectOption(label="Leaderboard Icon (10c)", value="lb_icon", emoji="🎨"),
            discord.SelectOption(label="Suffix Title (15c)", value="suffix_title", emoji="🏷️"),
            discord.SelectOption(label="Role Color (25c)", value="role_color", emoji="🎨"),
        ]
    )
    async def select_item(self, interaction: discord.Interaction, select: discord.ui.Select):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your shop.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        uid = self.user_id
        choice = select.values[0]

        async with self.bot.user_locks[uid]:
            user = (await self.bot.db_worker.run(lambda: self.bot.db.get_user(uid)))
            perks = (await self.bot.db_worker.run(lambda: self.bot.db.get_prestige_perks(uid)))
            shop_cog = self.shop

            if choice in POTION_CATALOG:
                cat = POTION_CATALOG[choice]
                params = (await shop_cog._get_potion_params(uid, choice)) if shop_cog else cat

                if cat["cost_pts"] > 0:
                    if not (await self.bot.db_worker.run(lambda: self.bot.db.deduct_points(uid, cat["cost_pts"]))):
                        await interaction.followup.send("Not enough points!", ephemeral=True)
                        return
                elif cat["cost_coins"] > 0:
                    if not (await self.bot.db_worker.run(lambda: self.bot.db.remove_coins(uid, cat["cost_coins"]))):
                        await interaction.followup.send("Not enough coins!", ephemeral=True)
                        return

                _, extended = (await self.bot.db_worker.run(lambda: self.bot.db.activate_potion(uid, f"potion_{choice}", params["mult"], params["hours"])))
                verb = "Time extended!" if extended else "Activated!"
                embed = discord.Embed(
                    title=f"🧪 {params['name']} — {verb}",
                    description=f"**{params['mult']}x XP** — **+{params['hours']}h** added to this buff.",
                    color=0x57F287
                )
                await interaction.edit_original_response(embed=embed, view=None)
                await shop_cog.notify_economy_badges(uid)
                u2 = (await self.bot.db_worker.run(lambda: self.bot.db.get_user(uid)))
                if u2 and int(u2.get("coins") or 0) == 0:
                    badge_cog = self.bot.cogs.get("Badges")
                    if badge_cog:
                        await badge_cog.check_calculated_risk_badge(uid)

            elif choice == "emergency_save":
                now = datetime.now(EST)
                if now.weekday() >= 5:
                    await interaction.followup.send("Emergency Save can only be used Mon-Fri.", ephemeral=True)
                    return
                freezes = (await self.bot.db_worker.run(lambda: self.bot.db.get_freezes(uid)))
                if freezes["count"] > 0:
                    await interaction.followup.send("Your freeze bank must be 0 to buy this.", ephemeral=True)
                    return
                cost = 2 if "resilient" in perks else 3
                if not (await self.bot.db_worker.run(lambda: self.bot.db.remove_coins(uid, cost))):
                    await interaction.followup.send(f"Not enough coins ({cost}c required).", ephemeral=True)
                    return
                (await self.bot.db_worker.run(lambda: self.bot.db.add_freeze(uid)))
                embed = discord.Embed(
                    title="🛡️ Emergency Save Purchased!",
                    description="**+1 Streak Freeze** added to your bank.",
                    color=0x57F287
                )
                await interaction.edit_original_response(embed=embed, view=None)
                await shop_cog.notify_economy_badges(uid)
                u2 = (await self.bot.db_worker.run(lambda: self.bot.db.get_user(uid)))
                if u2 and int(u2.get("coins") or 0) == 0:
                    badge_cog = self.bot.cogs.get("Badges")
                    if badge_cog:
                        await badge_cog.check_calculated_risk_badge(uid)

            elif choice in COSMETIC_COSTS:
                cost = COSMETIC_COSTS[choice]
                if not (await self.bot.db_worker.run(lambda: self.bot.db.remove_coins(uid, cost))):
                    await interaction.followup.send(f"Not enough coins ({cost}c required).", ephemeral=True)
                    return
                (await self.bot.db_worker.run(lambda: self.bot.db.add_inventory_item(uid, "cosmetic", choice)))
                desc = f"**{choice.replace('_', ' ').title()}** added to your inventory!"
                if choice == "lb_icon":
                    desc += "\n\nUse **`/profile`** — your Discord avatar shows as the card thumbnail."
                embed = discord.Embed(
                    title="🎨 Cosmetic Purchased!",
                    description=desc,
                    color=0x57F287
                )
                await interaction.edit_original_response(embed=embed, view=None)
                await shop_cog.notify_economy_badges(uid)
                u2 = (await self.bot.db_worker.run(lambda: self.bot.db.get_user(uid)))
                if u2 and int(u2.get("coins") or 0) == 0:
                    badge_cog = self.bot.cogs.get("Badges")
                    if badge_cog:
                        await badge_cog.check_calculated_risk_badge(uid)


async def setup(bot):
    await bot.add_cog(Shop(bot))
