# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

"""Temptation bundling — pair a treat with study/Pomodoro (contingent access)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from temptation_bundle import (
    RULE_POMO_BREAK,
    RULE_POMO_WORK,
    RULE_STUDY_MIN,
    ALL_RULES,
    bundle_config,
    rule_display,
)

if TYPE_CHECKING:
    from bot import StudyBot

log = logging.getLogger(__name__)


class Temptation(commands.Cog):
    """Configure treat links tied to study completion."""

    def __init__(self, bot: StudyBot):
        self.bot = bot

    bundle = app_commands.Group(name="bundle", description="Temptation bundling — treat after work, not during")

    @bundle.command(name="set", description="Enable bundle: label + when you earn the DM")
    @app_commands.describe(
        label="What you're earning (e.g. 'one YouTube video')",
        rule="When the bot DMs you the treat",
        study_minutes="For 'study minutes' rule: minimum focused minutes before /study stop",
        url="Optional link (playlist, bookmark, etc.)",
    )
    @app_commands.choices(
        rule=[
            app_commands.Choice(name="After each Pomodoro work block", value=RULE_POMO_WORK),
            app_commands.Choice(name="When a Pomodoro break starts", value=RULE_POMO_BREAK),
            app_commands.Choice(name="When /study stop (enough focus minutes)", value=RULE_STUDY_MIN),
        ]
    )
    async def bundle_set(
        self,
        interaction: discord.Interaction,
        label: str,
        rule: str,
        study_minutes: app_commands.Range[int, 5, 480] = 25,
        url: str | None = None,
    ):
        uid = interaction.user.id
        self.bot.db.set_setting(uid, "temptation_bundle_enabled", "1")
        self.bot.db.set_setting(uid, "temptation_bundle_label", label.strip()[:500])
        self.bot.db.set_setting(uid, "temptation_bundle_rule", rule if rule in ALL_RULES else RULE_POMO_WORK)
        self.bot.db.set_setting(uid, "temptation_bundle_study_minutes", str(int(study_minutes)))
        if url and url.strip():
            self.bot.db.set_setting(uid, "temptation_bundle_url", url.strip()[:500])
        else:
            self.bot.db.set_setting(uid, "temptation_bundle_url", "")
        await interaction.response.send_message(
            f"✅ **Bundle on.** Treat: **{label.strip()[:200]}**\n"
            f"**When:** {rule_display(rule)}\n"
            "_Keep the treat off-screen until the DM — that's the point._",
            ephemeral=True,
        )

    @bundle.command(name="clear", description="Disable temptation bundling")
    async def bundle_clear(self, interaction: discord.Interaction):
        uid = interaction.user.id
        self.bot.db.set_setting(uid, "temptation_bundle_enabled", "0")
        await interaction.response.send_message("Bundle cleared.", ephemeral=True)

    @bundle.command(name="status", description="Show your bundle settings")
    async def bundle_status(self, interaction: discord.Interaction):
        uid = interaction.user.id
        c = bundle_config(self.bot.db, uid)
        if not c:
            await interaction.response.send_message(
                "No active bundle. Use `/bundle set` — pair a **specific** treat with **when** you earn it.",
                ephemeral=True,
            )
            return
        url_part = f"\n**Link:** {c['url']}" if c.get("url") else ""
        extra = ""
        if c["rule"] == RULE_STUDY_MIN:
            extra = f"\n**Min minutes:** {c['study_mins']}"
        await interaction.response.send_message(
            f"**Treat:** {c['label']}\n**When:** {rule_display(c['rule'])}{extra}{url_part}",
            ephemeral=True,
        )

    def _enqueue_bundle_dm(
        self,
        user_id: int,
        *,
        dedupe_key: str,
        title: str,
        body: str,
        url: str | None = None,
    ) -> None:
        if not self.bot.db.get_dm_enabled(user_id, "temptation_bundle"):
            return
        embed: dict = {"title": title, "description": body, "color": int(0xF1C40F)}
        if url:
            embed["fields"] = [{"name": "Link", "value": url[:1024], "inline": False}]
        self.bot.db.enqueue_outbox(
            target_type="user",
            target_id=int(user_id),
            kind="temptation_bundle",
            dedupe_key=dedupe_key,
            settings_key="temptation_bundle",
            embed=embed,
        )

    async def on_pomodoro_work_complete(self, user_id: int, *, work_segment_key: str) -> None:
        c = bundle_config(self.bot.db, user_id)
        if not c or c["rule"] != RULE_POMO_WORK:
            return
        lab = c["label"]
        u = c.get("url") or ""
        self._enqueue_bundle_dm(
            user_id,
            dedupe_key=f"temptation_bundle:{user_id}:pomo_work:{work_segment_key}",
            title="🎁 Work block done — treat earned",
            body=(
                f"**{lab}**\n_Open this only now — keep it closed during the next focus block._"
            ),
            url=u or None,
        )

    async def on_pomodoro_break_started(
        self, user_id: int, break_minutes: int, *, break_segment_key: str
    ) -> None:
        c = bundle_config(self.bot.db, user_id)
        if not c or c["rule"] != RULE_POMO_BREAK:
            return
        lab = c["label"]
        u = c.get("url") or ""
        self._enqueue_bundle_dm(
            user_id,
            dedupe_key=f"temptation_bundle:{user_id}:pomo_break:{break_segment_key}",
            title="☕ Break started — treat window",
            body=(
                f"**{lab}**\n_This break is ~{break_minutes} min — enjoy the treat **only** during the break, "
                "then close it._"
            ),
            url=u or None,
        )

    async def on_study_session_end(self, user_id: int, duration_minutes: int, *, session_id: int) -> None:
        c = bundle_config(self.bot.db, user_id)
        need = int(c.get("study_mins", 25)) if c else 0
        if not c or c["rule"] != RULE_STUDY_MIN:
            return
        if duration_minutes < need:
            return
        if session_id <= 0:
            return
        lab = c["label"]
        u = c.get("url") or ""
        self._enqueue_bundle_dm(
            user_id,
            dedupe_key=f"temptation_bundle:{user_id}:study_end:{session_id}",
            title="🎁 Study session complete — treat earned",
            body=(
                f"**{lab}**\n_You hit **{duration_minutes}** min focused (≥{need}). Enjoy mindfully._"
            ),
            url=u or None,
        )


async def setup(bot: StudyBot):
    await bot.add_cog(Temptation(bot))
