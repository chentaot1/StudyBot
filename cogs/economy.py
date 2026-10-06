# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

import discord
from discord.ext import commands
from discord import app_commands
import logging
from datetime import datetime, timezone

from database import xp_for_level
from utils import fmt_mins, parse_stored
from constants import (
    MAX_LEVEL,
    COLOR_GOLD,
    COLOR_SUCCESS,
    COLOR_ORANGE,
    COLOR_ERROR,
    COLOR_MUTED,
    COLOR_AQUA,
    USER_NAV_FOOTER,
    POTION_CATALOG,
)

log = logging.getLogger("StudyBot.Economy")


class Economy(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _potion_params_for_user(self, user_id: int, potion_key: str) -> dict:
        """Match Shop potion rules (Time Lord / Master Alchemist)."""
        base = POTION_CATALOG[potion_key].copy()
        perks = self.bot.db.get_prestige_perks(user_id)
        if "time_lord" in perks:
            base["hours"] *= 2
        if "master_alchemist" in perks:
            base["mult"] = 2.0
        return base

    @app_commands.command(
        name="use_potion",
        description="Drink a stored potion (shop, Lucky Loot, etc.) — same buff as buying from /shop",
    )
    @app_commands.describe(which="Potion from your inventory (autocomplete shows what you own)")
    async def use_potion(self, interaction: discord.Interaction, which: str):
        if getattr(self.bot, "is_lite_user", lambda _uid: False)(interaction.user.id):
            await interaction.response.send_message("Lite mode: RPG/economy features are disabled for your account.", ephemeral=True)
            return
        uid = interaction.user.id
        key = which
        await interaction.response.defer(ephemeral=True)
        async with self.bot.user_locks[uid]:
            self.bot.db.ensure_user(uid, interaction.user.display_name)
            if key not in POTION_CATALOG:
                await interaction.followup.send("Unknown potion type.", ephemeral=True)
                return
            if not self.bot.db.use_inventory_item(uid, "potion", key):
                await interaction.followup.send(
                    f"You don't have any **{POTION_CATALOG[key]['name']}** in your inventory. "
                    f"Open `/inventory` to see what you own.",
                    ephemeral=True,
                )
                return
            params = self._potion_params_for_user(uid, key)
            _, extended = self.bot.db.activate_potion(uid, f"potion_{key}", params["mult"], params["hours"])
            verb = "Time extended!" if extended else "Bottoms up!"
            embed = discord.Embed(
                title=f"🧪 {params['name']} — {verb}",
                description=f"**{params['mult']}x XP** — **{params['hours']}h** on the clock.\n"
                            f"_{'Stacked with your current buff.' if extended else 'Shows under `/inventory` and applies to your next study sessions.'}_",
                color=COLOR_SUCCESS,
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

    @use_potion.autocomplete("which")
    async def use_potion_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        uid = interaction.user.id
        self.bot.db.ensure_user(uid, str(interaction.user))
        inv = self.bot.db.get_inventory(uid)
        choices: list[app_commands.Choice[str]] = []
        cur = (current or "").lower()
        for row in inv:
            if row.get("item_type") != "potion" or int(row.get("quantity") or 0) <= 0:
                continue
            key = row.get("item_key") or ""
            if key not in POTION_CATALOG:
                continue
            name = POTION_CATALOG[key]["name"]
            label = f"{name} ×{row['quantity']}"
            if cur in label.lower() or cur in key or not cur:
                choices.append(app_commands.Choice(name=label[:100], value=key))
        return choices[:25]

    @app_commands.command(name="convert", description="Convert Study Points into Boss Coins (2,000 pts = 1 coin)")
    @app_commands.checks.cooldown(1, 5.0)
    @app_commands.describe(amount="Number of conversions to perform (default 1)")
    async def convert(self, interaction: discord.Interaction, amount: int = 1):
        if getattr(self.bot, "is_lite_user", lambda _uid: False)(interaction.user.id):
            await interaction.response.send_message("Lite mode: RPG/economy features are disabled for your account.", ephemeral=True)
            return
        uid = interaction.user.id
        await interaction.response.defer(ephemeral=True)
        async with self.bot.user_locks[uid]:
            self.bot.db.ensure_user(uid, interaction.user.display_name)
            amount = max(1, min(amount, 50))
            results = []
            for _ in range(amount):
                result = self.bot.db.convert_points(uid)
                if not result:
                    break
                if "error" in result:
                    results.append(result)
                    break
                results.append(result)

            if not results:
                await interaction.followup.send("Something went wrong.", ephemeral=True)
                return

            successful = [r for r in results if "error" not in r]
            last = results[-1]

            if "error" in last:
                if successful:
                    total_cost = sum(r["cost"] for r in successful)
                    user = self.bot.db.get_user(uid)
                    to_wallet = sum(r.get("coin_direct", 0) for r in successful)
                    to_overflow = sum(r.get("coin_overflow", 0) for r in successful)
                    if last["error"] == "daily_limit":
                        limit = self.bot.db.get_convert_limit(uid)
                        desc = (
                            f"**{len(successful)}** conversion(s) completed, then you hit the daily limit "
                            f"({limit}/day). Your points and coins for those conversions are already applied."
                        )
                    else:
                        cost = self.bot.db.get_convert_cost(uid)
                        desc = (
                            f"**{len(successful)}** conversion(s) completed; not enough points for another "
                            f"(**{cost:,}** pts each). Your earlier conversions are already applied."
                        )
                    embed = discord.Embed(title="🪙 Points Converted (partial)", description=desc, color=COLOR_ORANGE)
                    embed.add_field(name="Conversions", value=str(len(successful)), inline=True)
                    embed.add_field(name="Cost", value=f"{total_cost:,} pts", inline=True)
                    embed.add_field(name="Wallet", value=f"🪙 {user['coins']}", inline=True)
                    embed.add_field(name="Points now", value=f"💎 {user['points']:,}", inline=True)
                    if to_overflow > 0:
                        embed.add_field(
                            name="Overflow",
                            value=f"**+{to_overflow}** coin(s) went to **overflow** (wallet at cap). See `/inventory` → **Coin Overflow**.",
                            inline=False,
                        )
                    elif to_wallet > 0:
                        embed.add_field(
                            name="Coins gained",
                            value=f"**+{to_wallet}** to wallet this batch.",
                            inline=False,
                        )
                    embed.set_footer(text=USER_NAV_FOOTER)
                    await interaction.followup.send(embed=embed, ephemeral=True)
                    badge_cog = self.bot.cogs.get("Badges")
                    if badge_cog:
                        await badge_cog.check_economy_badges(uid)
                    return

                if last["error"] == "daily_limit":
                    limit = self.bot.db.get_convert_limit(uid)
                    await interaction.followup.send(
                        f"You've hit your daily conversion limit ({limit}/day). Come back tomorrow!",
                        ephemeral=True
                    )
                elif last["error"] == "insufficient_points":
                    cost = self.bot.db.get_convert_cost(uid)
                    await interaction.followup.send(
                        f"Not enough points. You need **{cost:,}** pts per conversion.",
                        ephemeral=True
                    )
                return

            total_cost = sum(r["cost"] for r in successful)
            user = self.bot.db.get_user(uid)
            to_wallet = sum(r.get("coin_direct", 0) for r in successful)
            to_overflow = sum(r.get("coin_overflow", 0) for r in successful)
            embed = discord.Embed(title="🪙 Points Converted!", color=COLOR_GOLD)
            embed.add_field(name="Conversions", value=str(len(successful)), inline=True)
            embed.add_field(name="Cost", value=f"{total_cost:,} pts", inline=True)
            embed.add_field(name="Coins (wallet)", value=f"🪙 {user['coins']}", inline=True)
            embed.add_field(name="Points Remaining", value=f"💎 {user['points']:,}", inline=True)
            if to_overflow > 0:
                embed.add_field(
                    name="Overflow",
                    value=f"**+{to_overflow}** coin(s) went to **overflow** (wallet at cap). See `/inventory` → **Coin Overflow**.",
                    inline=False,
                )
            foot_bits = []
            if to_wallet > 0:
                foot_bits.append(f"+{to_wallet} coin(s) to wallet this batch")
            foot_bits.append(USER_NAV_FOOTER)
            embed.set_footer(text=" • ".join(foot_bits))
            await interaction.followup.send(embed=embed, ephemeral=True)
            badge_cog = self.bot.cogs.get("Badges")
            if badge_cog:
                await badge_cog.check_economy_badges(uid)

    @app_commands.command(name="prestige", description="Prestige up! Reset to Level 1 and unlock new perks")
    async def prestige(self, interaction: discord.Interaction):
        if getattr(self.bot, "is_lite_user", lambda _uid: False)(interaction.user.id):
            await interaction.response.send_message("Lite mode: RPG/economy features are disabled for your account.", ephemeral=True)
            return
        uid = interaction.user.id
        async with self.bot.user_locks[uid]:
            self.bot.db.ensure_user(uid, interaction.user.display_name)
            user = self.bot.db.get_user(uid)
            if user["level"] < MAX_LEVEL:
                await interaction.response.send_message(
                    f"You must reach **Level {MAX_LEVEL}** before you can prestige. "
                    f"(Currently Level {user['level']})",
                    ephemeral=True
                )
                return
            if user["prestige"] >= 5:
                await interaction.response.send_message(
                    "You're already at **Prestige 5** — the maximum tier!", ephemeral=True
                )
                return

            view = PrestigeConfirmView(self.bot, uid)
            embed = discord.Embed(
                title="⚔️ Ready to Prestige?",
                description=(
                    f"You're **Level {MAX_LEVEL}** at **P{user['prestige']}**.\n\n"
                    f"Prestiging will:\n"
                    f"• Reset you to **Level 1**\n"
                    f"• Clear your banked XP\n"
                    f"• Unlock **Prestige {user['prestige'] + 1}** perks\n\n"
                    f"Your points, coins, streak, and badges are **kept**."
                ),
                color=COLOR_ORANGE
            )
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="inventory", description="View your item inventory")
    async def inventory(self, interaction: discord.Interaction):
        if getattr(self.bot, "is_lite_user", lambda _uid: False)(interaction.user.id):
            await interaction.response.send_message("Lite mode: RPG/economy features are disabled for your account.", ephemeral=True)
            return
        uid = interaction.user.id
        self.bot.db.ensure_user(uid, interaction.user.display_name)
        items = self.bot.db.get_inventory(uid)
        potions = self.bot.db.get_active_potions(uid)
        overflow = self.bot.db.get_overflow(uid)

        embed = discord.Embed(title="🎒 Inventory", color=COLOR_AQUA)

        if items:
            lines = []
            for item in items:
                lines.append(f"**{item['item_key'].replace('_', ' ').title()}** x{item['quantity']} ({item['item_type']})")
            embed.add_field(name="📦 Items", value="\n".join(lines[:15]), inline=False)
        else:
            embed.add_field(name="📦 Items", value="_Empty_", inline=False)

        inv_has_potion = any(i["item_type"] == "potion" and i.get("quantity", 0) > 0 for i in items)
        if inv_has_potion:
            embed.add_field(
                name="🧪 Using potions",
                value="Drink one with **`/use_potion`** (consumes 1 from inventory and starts the buff).",
                inline=False,
            )

        if potions:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            lines = []
            for p in potions:
                expires = parse_stored(p["expires_at"])
                remaining = max((expires - now).total_seconds() / 60, 0)
                lines.append(f"**{p['effect_type']}** — {p['multiplier']}x XP — {fmt_mins(int(remaining))} left")
            embed.add_field(name="🧪 Active Potions", value="\n".join(lines), inline=False)

        if overflow:
            total = sum(o["amount"] for o in overflow)
            embed.add_field(
                name="📬 Coin Overflow",
                value=f"**{total}** coins pending (claim space by spending coins!)",
                inline=False
            )

        embed.set_footer(text=USER_NAV_FOOTER)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class PrestigeConfirmView(discord.ui.View):
    def __init__(self, bot, user_id: int):
        super().__init__(timeout=60)
        self.bot = bot
        self.user_id = user_id

    @discord.ui.button(label="Prestige Up!", style=discord.ButtonStyle.danger, emoji="⚔️")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't yours.", ephemeral=True)
            return
        async with self.bot.user_locks[self.user_id]:
            result = self.bot.db.prestige_up(self.user_id)
            if not result:
                await interaction.response.edit_message(
                    embed=discord.Embed(description="Prestige failed. Are you Level 50?", color=COLOR_ERROR),
                    view=None
                )
                return

            perk_names = {
                1: ("**Efficient Mint** (1,800 pt conversions)", "**Resilient** (Emergency Save costs 2c)"),
                2: ("**Scavenger** (+1 participation roll if top 3, +2 if not)", "**Tactician** (1 free quest reroll/day)"),
                3: ("**Industrialist** (3 conversions/day)", "**Time Lord** (Doubled potion/beacon durations)"),
                4: ("**The Vault** (100c cap + unlimited converts)", "**The Benefactor** (Free bounty every Monday)"),
                5: ("**Master Alchemist** (All potions = 2.0x)", "**Grandmaster Aura** (1.5x XP in Group Pomo)"),
            }
            p = result["new_prestige"]
            perks = perk_names.get(p, ("???", "???"))

            badge_cog = self.bot.cogs.get("Badges")
            if badge_cog:
                await badge_cog.check_prestige_badges(self.user_id)
                await badge_cog.check_economy_badges(self.user_id)

            embed = discord.Embed(
                title=f"🎖️ Prestige {p} Achieved!",
                description=f"You've ascended to **Prestige {p}**!\n\n"
                            f"**New Perks Unlocked:**\n• {perks[0]}\n• {perks[1]}",
                color=COLOR_GOLD
            )
            await interaction.response.edit_message(embed=embed, view=None)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            embed=discord.Embed(description="Prestige cancelled.", color=COLOR_MUTED),
            view=None
        )
        self.stop()


async def setup(bot):
    await bot.add_cog(Economy(bot))
