# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from constants import COLOR_MUTED, COLOR_PRIMARY
from cogs.tutorial import TutorialNavView, _embed as tutorial_chapter_embed

if TYPE_CHECKING:
    from bot import StudyBot


class OnboardingView(discord.ui.View):
    def __init__(self, bot: "StudyBot", user_id: int):
        super().__init__(timeout=300)
        self.bot = bot
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    @discord.ui.button(label="Open settings", style=discord.ButtonStyle.primary, emoji="⚙️")
    async def open_settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        from cogs.profile import DM_TOGGLES, SettingsView

        uid = interaction.user.id
        self.bot.db.ensure_user(uid, interaction.user.display_name)
        user = self.bot.db.get_user(uid)

        embed = discord.Embed(title="⚙️ Settings", color=COLOR_PRIMARY)
        ghost = "🟢 ON" if user.get("ghost_mode") else "🔴 OFF"
        cheers = "🔴 Blocked" if user.get("block_cheers") else "🟢 Allowed"
        embed.add_field(name="👻 Ghost Mode", value=ghost, inline=True)
        embed.add_field(name="📣 Cheers", value=cheers, inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=True)

        dm_lines = []
        for key, label in DM_TOGGLES.items():
            enabled = self.bot.db.get_dm_enabled(uid, key)
            status = "✅" if enabled else "❌"
            dm_lines.append(f"{status} {label}")
        embed.add_field(name="📬 DM Notifications", value="\n".join(dm_lines), inline=False)

        # Send a separate ephemeral message so the onboarding card/buttons remain usable.
        await interaction.response.send_message(embed=embed, view=SettingsView(self.bot, uid), ephemeral=True)

    @discord.ui.button(label="Open tutorial", style=discord.ButtonStyle.secondary, emoji="📖")
    async def open_tutorial(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Send a separate ephemeral message so the onboarding card/buttons remain usable.
        await interaction.response.send_message(
            embed=tutorial_chapter_embed(0, is_lite=getattr(self.bot, "is_lite_user", lambda _uid: False)(interaction.user.id)),
            view=TutorialNavView(is_lite=getattr(self.bot, "is_lite_user", lambda _uid: False)(interaction.user.id)),
            ephemeral=True,
        )

    @discord.ui.button(label="Dismiss", style=discord.ButtonStyle.secondary)
    async def dismiss(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            embed=discord.Embed(
                description="Onboarding dismissed. Use `/settings` or `/tutorial` anytime.",
                color=COLOR_MUTED,
            ),
            view=None,
        )
