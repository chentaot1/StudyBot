# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

import discord
from discord import app_commands
from discord.ext import commands
from datetime import timezone
import logging

from constants import COLOR_GOLD, COLOR_SUCCESS, COLOR_ERROR, COLOR_MUTED
from utils import fmt_date_us_from_iso, fmt_datetime_us_est, parse_stored

log = logging.getLogger("StudyBot.Rewards")


class Rewards(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    reward = app_commands.Group(name="reward", description="Manage your personal rewards shop")

    # ── Shared autocomplete helper ────────────────────────────────────────────

    async def _autocomplete_reward_id(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        self.bot.db.ensure_user(interaction.user.id, str(interaction.user))
        rewards = self.bot.db.get_rewards(interaction.user.id)
        user = self.bot.db.get_user(interaction.user.id)
        choices = []
        for r in rewards:
            can_afford = "✅" if user["points"] >= r["cost"] else "❌"
            label = f"{can_afford} #{r['id']} — {r['name']} — 💎 {r['cost']}"
            if current.lower() in r["name"].lower() or current == str(r["id"]) or current == "":
                choices.append(app_commands.Choice(name=label[:100], value=r["id"]))
        return choices[:25]

    # ── /reward add ───────────────────────────────────────────────────────────

    @reward.command(name="add", description="Add a reward to your personal shop")
    @app_commands.describe(
        name="What the reward is (e.g. '30 min gaming', 'snack break')",
        cost="How many points it costs",
        description="Optional description"
    )
    async def reward_add(
        self,
        interaction: discord.Interaction,
        name: str,
        cost: app_commands.Range[int, 1, 100000],
        description: str = ""
    ):
        self.bot.db.ensure_user(interaction.user.id, str(interaction.user))
        rid = self.bot.db.add_reward(interaction.user.id, name, description, cost)
        embed = discord.Embed(title="🏪 Reward Added!", color=COLOR_GOLD)
        embed.add_field(name="Reward", value=name, inline=True)
        embed.add_field(name="Cost", value=f"💎 {cost}", inline=True)
        if description:
            embed.add_field(name="Description", value=description, inline=False)
        embed.set_footer(text=f"Reward #{rid} • Redeem with /reward redeem {rid}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /reward list ──────────────────────────────────────────────────────────

    @reward.command(name="list", description="Browse your rewards shop")
    async def reward_list(self, interaction: discord.Interaction):
        self.bot.db.ensure_user(interaction.user.id, str(interaction.user))
        rewards = self.bot.db.get_rewards(interaction.user.id)
        user = self.bot.db.get_user(interaction.user.id)

        if not rewards:
            await interaction.response.send_message(
                "🏪 Your shop is empty! Add rewards with `/reward add`.\n"
                "Ideas: `30 min gaming`, `snack break`, `episode of a show`, `day off`",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title="🏪 Your Rewards Shop",
            description=f"Balance: **💎 {user['points']} points**",
            color=COLOR_GOLD
        )
        for r in rewards:
            can_afford = "✅" if user["points"] >= r["cost"] else "❌"
            embed.add_field(
                name=f"{can_afford} #{r['id']} — {r['name']} — 💎 {r['cost']}",
                value=r.get("description") or "—",
                inline=False
            )
        embed.set_footer(text="Redeem with /reward redeem <id>")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /reward redeem ────────────────────────────────────────────────────────

    @reward.command(name="redeem", description="Redeem a reward with your points")
    @app_commands.describe(reward_id="Reward to redeem (from /reward list)")
    async def reward_redeem(self, interaction: discord.Interaction, reward_id: int):
        self.bot.db.ensure_user(interaction.user.id, str(interaction.user))
        reward = self.bot.db.get_reward(reward_id, interaction.user.id)

        if not reward:
            await interaction.response.send_message(f"❌ Reward `#{reward_id}` not found.", ephemeral=True)
            return

        user = self.bot.db.get_user(interaction.user.id)
        if user["points"] < reward["cost"]:
            short = reward["cost"] - user["points"]
            await interaction.response.send_message(
                f"❌ Need **{short} more points** to redeem **{reward['name']}**.",
                ephemeral=True,
            )
            return

        view = RedeemView(self.bot, interaction.user.id, reward)
        embed = discord.Embed(title="🛒 Confirm Redemption?", color=COLOR_GOLD)
        embed.add_field(name="Reward", value=reward["name"], inline=True)
        embed.add_field(name="Cost", value=f"💎 {reward['cost']}", inline=True)
        embed.add_field(name="After", value=f"💎 {user['points'] - reward['cost']}", inline=True)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        view.message = await interaction.original_response()

    @reward_redeem.autocomplete("reward_id")
    async def reward_redeem_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return await self._autocomplete_reward_id(interaction, current)

    # ── /reward delete ────────────────────────────────────────────────────────

    @reward.command(name="delete", description="Remove a reward from your shop")
    @app_commands.describe(reward_id="Reward to delete")
    async def reward_delete(self, interaction: discord.Interaction, reward_id: int):
        if self.bot.db.delete_reward(reward_id, interaction.user.id):
            await interaction.response.send_message(f"🗑️ Reward `#{reward_id}` deleted.", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ Reward `#{reward_id}` not found.", ephemeral=True)

    @reward_delete.autocomplete("reward_id")
    async def reward_delete_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return await self._autocomplete_reward_id(interaction, current)

    # ── /reward history ───────────────────────────────────────────────────────

    @reward.command(name="history", description="View your redemption history")
    async def reward_history(self, interaction: discord.Interaction):
        history = self.bot.db.get_redemption_history(interaction.user.id)
        if not history:
            await interaction.response.send_message("No redemptions yet!", ephemeral=True)
            return
        embed = discord.Embed(title="🎁 Redemption History", color=COLOR_GOLD)
        for r in history:
            raw = r.get("redeemed_at") or ""
            try:
                rd = parse_stored(raw).replace(tzinfo=timezone.utc)
                when = fmt_datetime_us_est(rd, with_seconds=False)
            except Exception:
                when = fmt_date_us_from_iso(raw[:10]) if raw else "—"
            embed.add_field(
                name=f"🎁 {r['reward_name']} — 💎 {r['cost']}",
                value=when,
                inline=False
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /points ───────────────────────────────────────────────────────────────

    @app_commands.command(name="points", description="Check your points balance and history")
    async def points(self, interaction: discord.Interaction):
        if getattr(self.bot, "is_lite_user", lambda _uid: False)(interaction.user.id):
            await interaction.response.send_message("Lite mode: RPG/economy features are disabled for your account.", ephemeral=True)
            return
        self.bot.db.ensure_user(interaction.user.id, str(interaction.user))
        user = self.bot.db.get_user(interaction.user.id)
        history = self.bot.db.get_point_history(interaction.user.id, limit=8)

        embed = discord.Embed(title="💎 Points Balance", color=COLOR_GOLD)
        embed.set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar.url)
        embed.add_field(name="Balance", value=f"💎 **{user['points']}**", inline=True)
        embed.add_field(name="Total XP", value=f"⭐ **{user['total_xp']}**", inline=True)
        embed.add_field(name="Streak", value=f"🔥 **{user['streak']} days**", inline=True)

        if history:
            lines = []
            for h in history:
                sign = "+" if h["delta"] > 0 else ""
                raw = h.get("created_at") or ""
                try:
                    hd = parse_stored(raw).replace(tzinfo=timezone.utc)
                    dlabel = fmt_datetime_us_est(hd, with_seconds=False)
                except Exception:
                    dlabel = fmt_date_us_from_iso(raw[:10]) if raw else "—"
                lines.append(f"`{sign}{h['delta']}` {h['reason'] or '—'} _{dlabel}_")
            embed.add_field(name="Recent Transactions", value="\n".join(lines), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class RedeemView(discord.ui.View):
    def __init__(self, bot, user_id: int, reward: dict):
        super().__init__(timeout=30)
        self.bot = bot
        self.user_id = user_id
        self.reward = reward
        self.message: discord.Message | None = None

    async def on_timeout(self):
        if self.message:
            try:
                await self.message.edit(content="Timed out.", embed=None, view=None)
            except Exception:
                pass

    @discord.ui.button(label="Yes, redeem!", style=discord.ButtonStyle.success, emoji="🎁")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        ok = self.bot.db.redeem_reward(self.user_id, self.reward["id"])
        if not ok:
            await interaction.response.edit_message(
                content="❌ Redemption failed — check your balance.", embed=None, view=None
            )
            self.stop()
            return

        user = self.bot.db.get_user(self.user_id)
        embed = discord.Embed(
            title="🎉 Enjoy your reward!",
            description=f"**{self.reward['name']}** redeemed!",
            color=COLOR_SUCCESS
        )
        embed.add_field(name="Spent", value=f"💎 {self.reward['cost']}", inline=True)
        embed.add_field(name="Remaining", value=f"💎 {user['points']}", inline=True)
        await interaction.response.edit_message(embed=embed, view=None)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Cancelled.", embed=None, view=None)
        self.stop()


async def setup(bot):
    await bot.add_cog(Rewards(bot))
