# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone, timedelta
import asyncio
import time
import logging

from constants import (
    EST, WORK_COLOR, BREAK_COLOR, LONG_BREAK_COLOR as LONG_COLOR,
    COLOR_PRIMARY, COLOR_SUCCESS, COLOR_WARNING, USER_NAV_FOOTER,
)
from utils import fmt_mins, parse_stored, utcnow_naive
from views.persistent import bind_buttons

log = logging.getLogger("StudyBot.Pomodoro")


def _group_leave_reward_note(session: dict | None) -> str:
    """Explain whether leaving a group counted for session rewards."""
    if not session:
        return ""
    m = int(session.get("duration_minutes") or 0)
    if m >= 5:
        return "\n\n✅ **Session saved** — rewards counted (XP, points, lucky loot for ≥5 min)."
    if m > 0:
        return "\n\n_Session under 5 min — no rewards for this exit._"
    return ""


class PomodoroSoloView(discord.ui.View):
    """Quick actions for an active solo Pomodoro (commands still work)."""

    def __init__(self, cog: "Pomodoro"):
        super().__init__(timeout=None)
        self.cog = cog

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        pomo = (await self.cog.bot.db_worker.run(lambda: self.cog.bot.db.get_active_pomodoro(interaction.user.id)))
        if not pomo:
            await interaction.response.send_message(
                "You don't have an active Pomodoro. Use `/pomodoro start`.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Status", style=discord.ButtonStyle.secondary, emoji="📊", row=0)
    async def status_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        pomo = (await self.cog.bot.db_worker.run(lambda: self.cog.bot.db.get_active_pomodoro(interaction.user.id)))
        await interaction.response.send_message(embed=build_pomo_embed(pomo), ephemeral=True)

    @discord.ui.button(label="Skip phase", style=discord.ButtonStyle.primary, emoji="⏭️", row=0)
    async def skip_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        pomo = (await self.cog.bot.db_worker.run(lambda: self.cog.bot.db.get_active_pomodoro(uid)))
        if not pomo:
            await interaction.response.send_message("❌ No active Pomodoro.", ephemeral=True)
            return
        self.cog._cancel_pomo_task(uid)
        await interaction.response.defer(ephemeral=True)
        await self.cog._transition_phase(uid, pomo, auto=False)
        new_pomo = (await self.cog.bot.db_worker.run(lambda: self.cog.bot.db.get_active_pomodoro(uid)))
        embed = discord.Embed(title="⏭️ Phase Skipped!", color=0x5865F2)
        if new_pomo:
            phase_map = {"work": "🔴 Focus", "break": "☕ Break", "long_break": "🏖️ Long Break"}
            embed.add_field(name="Now", value=phase_map.get(new_pomo["current_phase"], "—"))
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger, emoji="⏹️", row=0)
    async def stop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog._pomodoro_stop_from_button(interaction)


class GroupLobbyWaitingView(discord.ui.View):
    """Begin / leave / refresh / join on the public lobby card (slash commands still work)."""

    def __init__(self, cog: "Pomodoro", lobby_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.lobby_id = lobby_id
        bind_buttons(self, f"sb:group:{lobby_id}:waiting")

    async def interaction_check(self, interaction):
        lobby = await self.cog.bot.db_worker.run(self.cog.bot.db.get_group_lobby_by_id, self.lobby_id)
        if not lobby or lobby.get("state") != "waiting" or lobby.get("source_guild_id") != interaction.guild_id:
            await interaction.response.send_message("This lobby card is no longer available here. Use /group_pomo status.", ephemeral=True)
            return False
        return await self.cog.bot.tree.interaction_check(interaction)

    @discord.ui.button(label="Begin session", style=discord.ButtonStyle.success, emoji="▶️", row=0)
    async def begin_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        ug = (await self.cog.bot.db_worker.run(lambda: self.cog.bot.db.get_user_group_lobby(interaction.user.id)))
        if not ug or ug["id"] != self.lobby_id:
            await interaction.response.send_message("You're not in this lobby.", ephemeral=True)
            return
        lobby = (await self.cog.bot.db_worker.run(lambda: self.cog.bot.db.get_group_lobby_by_id(self.lobby_id)))
        if not lobby or lobby["state"] != "waiting":
            await interaction.response.send_message(
                "This lobby can't be started from here anymore.", ephemeral=True
            )
            return
        if lobby["host_id"] != interaction.user.id:
            await interaction.response.send_message("Only the **host** can begin the session.", ephemeral=True)
            return
        await self.cog._execute_group_begin(interaction, lobby, edit_message=True)

    @discord.ui.button(label="Leave lobby", style=discord.ButtonStyle.secondary, emoji="🚪", row=0)
    async def leave_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        ug = (await self.cog.bot.db_worker.run(lambda: self.cog.bot.db.get_user_group_lobby(uid)))
        if not ug or ug["id"] != self.lobby_id:
            await interaction.response.send_message("You're not in this lobby.", ephemeral=True)
            return
        session = None
        async with self.cog.bot.user_locks[uid]:
            (await self.cog.bot.db_worker.run(lambda: self.cog.bot.db.leave_group_lobby(uid)))
            study_cog = self.cog.bot.cogs.get("Study")
            if study_cog:
                study_cog._cancel_live_task(uid)
                study_cog._cancel_motivation_task(uid)
                study_cog._cancel_inactivity_monitor(uid)
                study_cog._session_panel_views.pop(uid, None)
            session = (await self.cog.bot.db_worker.run(lambda: self.cog.bot.db.end_session(uid)))
            if session and session["duration_minutes"] >= 5 and study_cog:
                await study_cog._process_session_rewards(uid, session)

        note = _group_leave_reward_note(session)
        lobby_now = (await self.cog.bot.db_worker.run(lambda: self.cog.bot.db.get_group_lobby_by_id(self.lobby_id)))
        if not lobby_now or lobby_now["state"] == "completed":
            await interaction.response.edit_message(
                embed=discord.Embed(
                    title="🍅 Lobby closed",
                    description="Everyone has left, or the lobby ended." + note,
                    color=0x747F8D,
                ),
                view=None,
            )
            return
        if lobby_now["state"] != "waiting":
            await interaction.response.edit_message(
                embed=discord.Embed(
                    title="🍅 Session in progress",
                    description="This group has already started. Use `/group_pomo status` for details." + note,
                    color=WORK_COLOR,
                ),
                view=None,
            )
            return
        embed = (await self.cog._waiting_lobby_embed(lobby_now))
        if note.strip():
            embed.add_field(name="Your study", value=note.strip(), inline=False)
        await interaction.response.edit_message(embed=embed, view=GroupLobbyWaitingView(self.cog, self.lobby_id))

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, emoji="🔄", row=0)
    async def refresh_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        lobby_now = (await self.cog.bot.db_worker.run(lambda: self.cog.bot.db.get_group_lobby_by_id(self.lobby_id)))
        if not lobby_now or lobby_now["state"] == "completed":
            await interaction.response.edit_message(
                embed=discord.Embed(title="🍅 Lobby closed", description="This lobby no longer exists.", color=0x747F8D),
                view=None,
            )
            return
        if lobby_now["state"] != "waiting":
            await interaction.response.edit_message(
                embed=discord.Embed(
                    title="🍅 Session in progress",
                    description="Use `/group_pomo status` for the current phase.",
                    color=WORK_COLOR,
                ),
                view=None,
            )
            return
        embed = (await self.cog._waiting_lobby_embed(lobby_now))
        await interaction.response.edit_message(embed=embed, view=GroupLobbyWaitingView(self.cog, self.lobby_id))

    @discord.ui.button(label="Join this lobby", style=discord.ButtonStyle.primary, emoji="➕", row=1)
    async def join_public_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        lobby = (await self.cog.bot.db_worker.run(lambda: self.cog.bot.db.get_group_lobby_by_id(self.lobby_id)))
        if not lobby or lobby["state"] != "waiting":
            await interaction.response.send_message(
                "This lobby isn't accepting joins anymore.", ephemeral=True
            )
            return
        code = lobby["code"]

        already = (await self.cog.bot.db_worker.run(lambda: self.cog.bot.db.get_user_group_lobby(uid)))
        if already and already["id"] == self.lobby_id:
            await interaction.response.send_message(
                "You're already in this lobby. When the host is ready, they'll start the session.",
                ephemeral=True,
            )
            return

        async with self.cog.bot.user_locks[uid]:
            (await self.cog.bot.db_worker.run(lambda: self.cog.bot.db.ensure_user(uid, str(interaction.user))))

            # Users are allowed to study while waiting in the lobby. The host starting the
            # group session will auto-end the personal session and award rewards.
            ug = (await self.cog.bot.db_worker.run(lambda: self.cog.bot.db.get_user_group_lobby(uid)))
            if ug and ug["id"] != self.lobby_id:
                await interaction.response.send_message(
                    "You're already in a different lobby. Leave it first with `/group_pomo leave`.",
                    ephemeral=True,
                )
                return

            result = (await self.cog.bot.db_worker.run(lambda: self.cog.bot.db.join_group_lobby(code, uid)))
            if not result:
                await interaction.response.send_message(
                    "Couldn't join — the lobby may have just closed.", ephemeral=True
                )
                return
            if isinstance(result, dict) and result.get("error") == "full":
                await interaction.response.send_message("Lobby is full (max 10).", ephemeral=True)
                return

        await interaction.response.defer(ephemeral=True)
        lobby_now = (await self.cog.bot.db_worker.run(lambda: self.cog.bot.db.get_group_lobby_by_id(self.lobby_id)))
        if lobby_now and lobby_now["state"] == "waiting" and interaction.message is not None:
            try:
                await interaction.message.edit(
                    embed=(await self.cog._waiting_lobby_embed(lobby_now)),
                    view=GroupLobbyWaitingView(self.cog, self.lobby_id),
                )
            except Exception as e:
                log.debug("Group lobby public card edit failed: %s", e)
        else:
            pass

        members = (await self.cog.bot.db_worker.run(lambda: self.cog.bot.db.get_group_members(result["id"])))
        roster = self.cog._group_member_display_lines(members, result["host_id"])
        embed = discord.Embed(
            title="🍅 Joined Group Pomodoro!",
            description=f"**{result.get('subject', 'Study Group')}** · Code: `{str(code).upper()}`\n"
            f"Members: {len(members)}/10",
            color=BREAK_COLOR,
        )
        embed.add_field(name="Who's in", value=roster, inline=False)
        embed.set_footer(text="Buttons below — or use /group_pomo leave · /group_pomo status")
        view = GroupJoinedView(self.cog, result["id"], str(code).upper())
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="Vote to start", style=discord.ButtonStyle.secondary, emoji="🗳️", row=1)
    async def vote_start_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Always defer first so we can safely respond exactly once, then edit the message or send a followup.
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        uid = interaction.user.id
        ug = (await self.cog.bot.db_worker.run(lambda: self.cog.bot.db.get_user_group_lobby(uid)))
        if not ug or ug["id"] != self.lobby_id:
            await interaction.followup.send("You're not in this lobby.", ephemeral=True)
            return
        lobby = (await self.cog.bot.db_worker.run(lambda: self.cog.bot.db.get_group_lobby_by_id(self.lobby_id)))
        if not lobby or lobby["state"] != "waiting":
            await interaction.followup.send("This lobby can't be voted on anymore.", ephemeral=True)
            return

        (await self.cog.bot.db_worker.run(lambda: self.cog.bot.db.vote_start_group_lobby(self.lobby_id, uid)))

        members = (await self.cog.bot.db_worker.run(lambda: self.cog.bot.db.get_group_members(self.lobby_id)))
        n = len(members)
        votes = (await self.cog.bot.db_worker.run(lambda: self.cog.bot.db.get_group_lobby_vote_count(self.lobby_id)))
        needed = (n // 2) + 1

        # Auto-start only if 3+ members and majority voted.
        if n >= 3 and votes >= needed:
            lobby = (await self.cog.bot.db_worker.run(lambda: self.cog.bot.db.get_group_lobby_by_id(self.lobby_id)))
            if lobby and lobby["state"] == "waiting":
                await self.cog._execute_group_begin(interaction, lobby, edit_message=True)
            return

        # Otherwise: update the lobby card to reflect vote count.
        try:
            await interaction.edit_original_response(
                embed=(await self.cog._waiting_lobby_embed(lobby)),
                view=GroupLobbyWaitingView(self.cog, self.lobby_id),
            )
        except Exception:
            try:
                await interaction.followup.send(
                    f"🗳️ Vote recorded. Votes: **{votes}/{needed}** (needs 3+ people to auto-start).",
                    ephemeral=True,
                )
            except Exception:
                pass


class GroupLobbyActiveView(discord.ui.View):
    """Refresh-only controls for the public in-progress card."""

    def __init__(self, cog: "Pomodoro", lobby_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.lobby_id = lobby_id
        bind_buttons(self, f"sb:group:{lobby_id}:active")

    async def interaction_check(self, interaction):
        lobby = await self.cog.bot.db_worker.run(self.cog.bot.db.get_group_lobby_by_id, self.lobby_id)
        if not lobby or lobby.get("state") != "active" or lobby.get("source_guild_id") != interaction.guild_id:
            await interaction.response.send_message("This lobby card has ended or belongs to another server.", ephemeral=True)
            return False
        member = await self.cog.bot.db_worker.run(self.cog.bot.db.get_user_group_lobby, interaction.user.id)
        if not member or member["id"] != self.lobby_id:
            await interaction.response.send_message("These controls are for lobby participants.", ephemeral=True)
            return False
        return await self.cog.bot.tree.interaction_check(interaction)

    @discord.ui.button(label="Refresh status", style=discord.ButtonStyle.secondary, emoji="🔄")
    async def refresh_status_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        lobby = (await self.cog.bot.db_worker.run(lambda: self.cog.bot.db.get_group_lobby_by_id(self.lobby_id)))
        if not lobby or lobby.get("state") != "active":
            await interaction.response.edit_message(
                embed=discord.Embed(
                    title="🍅 Session ended",
                    description="This group Pomodoro is no longer active.",
                    color=0x747F8D,
                ),
                view=None,
            )
            return
        await interaction.response.edit_message(
            embed=(await self.cog._active_lobby_embed(lobby)),
            view=GroupLobbyActiveView(self.cog, self.lobby_id),
        )


class GroupJoinedView(discord.ui.View):
    """Ephemeral join confirmation — leave or refresh roster."""

    def __init__(self, cog: "Pomodoro", lobby_id: int, code_display: str):
        super().__init__(timeout=3600)
        self.cog = cog
        self.lobby_id = lobby_id
        self.code_display = code_display

    async def _join_embed(self) -> discord.Embed | None:
        lobby = (await self.cog.bot.db_worker.run(lambda: self.cog.bot.db.get_group_lobby_by_id(self.lobby_id)))
        if not lobby or lobby["state"] == "completed":
            return None
        members = (await self.cog.bot.db_worker.run(lambda: self.cog.bot.db.get_group_members(self.lobby_id)))
        roster = self.cog._group_member_display_lines(members, lobby["host_id"])
        embed = discord.Embed(
            title="🍅 Joined Group Pomodoro!",
            description=f"**{lobby.get('subject', 'Study Group')}** · Code: `{self.code_display}`\n"
            f"Members: {len(members)}/10",
            color=BREAK_COLOR,
        )
        embed.add_field(name="Who's in", value=roster, inline=False)
        embed.set_footer(text="Slash: /group_pomo leave · /group_pomo begin (host)")
        return embed

    @discord.ui.button(label="Leave lobby", style=discord.ButtonStyle.danger, emoji="🚪", row=0)
    async def leave_joined(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        ug = (await self.cog.bot.db_worker.run(lambda: self.cog.bot.db.get_user_group_lobby(uid)))
        if not ug or ug["id"] != self.lobby_id:
            await interaction.response.send_message("You're not in this lobby.", ephemeral=True)
            return
        session = None
        async with self.cog.bot.user_locks[uid]:
            (await self.cog.bot.db_worker.run(lambda: self.cog.bot.db.leave_group_lobby(uid)))
            study_cog = self.cog.bot.cogs.get("Study")
            if study_cog:
                study_cog._cancel_live_task(uid)
                study_cog._cancel_motivation_task(uid)
                study_cog._cancel_inactivity_monitor(uid)
                study_cog._session_panel_views.pop(uid, None)
            session = (await self.cog.bot.db_worker.run(lambda: self.cog.bot.db.end_session(uid)))
            if session and session["duration_minutes"] >= 5 and study_cog:
                await study_cog._process_session_rewards(uid, session)
        note = _group_leave_reward_note(session)
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="👋 Left lobby",
                description="You're no longer in this group Pomodoro." + note,
                color=0x747F8D,
            ),
            view=None,
        )

    @discord.ui.button(label="Refresh roster", style=discord.ButtonStyle.secondary, emoji="🔄", row=0)
    async def refresh_joined(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = (await self._join_embed())
        if not embed:
            await interaction.response.edit_message(
                embed=discord.Embed(title="Lobby ended", description="This lobby is no longer active.", color=0x747F8D),
                view=None,
            )
            return
        await interaction.response.edit_message(embed=embed, view=GroupJoinedView(self.cog, self.lobby_id, self.code_display))


class CancelAutostartView(discord.ui.View):
    def __init__(self, cog: "Pomodoro", lobby_id: int, host_id: int):
        super().__init__(timeout=5 * 60)
        self.cog = cog
        self.lobby_id = lobby_id
        self.host_id = host_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.host_id:
            await interaction.response.send_message("Host only.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Cancel auto-start", style=discord.ButtonStyle.danger, emoji="🛑")
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.cog.group_autostart_cancelled.add(self.lobby_id)
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="🛑 Auto-start cancelled",
                description="This lobby will not auto-start. You can still start it manually with the **Begin session** button or `/group_pomo begin`.",
                color=0xED4245,
            ),
            view=None,
        )
        self.stop()


def build_pomo_embed(pomo: dict, *, live: bool = True) -> discord.Embed:
    phase = pomo["current_phase"]
    is_work = phase == "work"
    is_long = phase == "long_break"
    cycle = pomo["total_cycles"]

    if is_work:
        duration = pomo["work_minutes"]
        color = WORK_COLOR
        title = f"🍅 Focus Session #{cycle + 1}"
        icon = "🔴"
    elif is_long:
        duration = pomo.get("long_break_minutes") or 15
        color = LONG_COLOR
        title = f"🏖️ Long Break (after {cycle} cycles)"
        icon = "🔵"
    else:
        duration = pomo["break_minutes"]
        color = BREAK_COLOR
        title = "☕ Short Break"
        icon = "🟢"

    embed = discord.Embed(title=title, color=color)
    embed.add_field(name="Phase", value=f"{icon} {phase.replace('_', ' ').title()}", inline=True)
    embed.add_field(name="Duration", value=fmt_mins(duration), inline=True)
    embed.add_field(name="Cycles Done", value=str(cycle), inline=True)

    total_focus = cycle * pomo["work_minutes"]
    if total_focus > 0:
        embed.add_field(name="⏱️ Total Focus So Far", value=fmt_mins(total_focus), inline=True)

    if live:
        phase_started = parse_stored(pomo["phase_started_at"]).replace(tzinfo=timezone.utc)
        phase_end_unix = int(phase_started.timestamp()) + duration * 60
        now_unix = int(time.time())
        if now_unix < phase_end_unix:
            embed.add_field(name="⏳ Phase ends", value=f"<t:{phase_end_unix}:R>", inline=False)
        else:
            embed.add_field(name="⏳ Phase ends", value="Any moment now!", inline=False)

    embed.set_footer(text="StudyBot Pomodoro • /pomodoro skip  /pomodoro stop")
    return embed


class Pomodoro(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.pomo_tasks: dict[int, asyncio.Task] = {}
        self.group_tasks: dict[int, asyncio.Task] = {}
        self.group_autostart_tasks: dict[int, asyncio.Task] = {}
        self.group_autostart_cancelled: set[int] = set()

    async def recover_group_timers_after_gateway_reconnect(self) -> None:
        """Re-arm group Pomodoro background work after a Discord reconnect.

        `cog_load` runs only once per process; `on_ready` fires again after gateway
        resume, but in-memory asyncio tasks may have been cancelled or completed.
        """
        # Waiting lobbies: restore auto-start countdown (same rules as cold start).
        try:
            for lobby in (await self.bot.db_worker.run(lambda: self.bot.db.get_waiting_group_lobbies())):
                created = parse_stored(lobby.get("created_at") or "")
                age = (utcnow_naive() - created).total_seconds()
                if 0 <= age < 60 * 60:
                    self._ensure_group_autostart(lobby["id"])
        except Exception:
            log.debug("recover_group_timers: waiting lobbies failed", exc_info=True)

        # Active sessions: reschedule phase countdown from DB timestamps.
        try:
            for lobby in (await self.bot.db_worker.run(lambda: self.bot.db.get_active_group_lobbies())):
                self._schedule_group_phase_safe(lobby)
        except Exception:
            log.debug("recover_group_timers: active lobbies failed", exc_info=True)

    async def cog_load(self) -> None:
        (await self.recover_group_timers_after_gateway_reconnect())

    async def _temptation_pomodoro_work_ended(self, user_id: int, *, work_segment_key: str) -> None:
        t = self.bot.cogs.get("Temptation")
        if t:
            try:
                await t.on_pomodoro_work_complete(user_id, work_segment_key=work_segment_key)
            except Exception:
                log.debug("Temptation work-complete hook failed", exc_info=True)

    async def _temptation_pomodoro_break_started(
        self, user_id: int, break_minutes: int, *, break_segment_key: str
    ) -> None:
        t = self.bot.cogs.get("Temptation")
        if t:
            try:
                await t.on_pomodoro_break_started(
                    user_id, break_minutes, break_segment_key=break_segment_key
                )
            except Exception:
                log.debug("Temptation break-started hook failed", exc_info=True)

    async def _enqueue_solo_pomo_phase_dm(
        self,
        user_id: int,
        *,
        dedupe_key: str,
        title: str,
        description: str,
        color: int,
        footer: str | None = None,
        fields: list[dict] | None = None,
    ) -> None:
        if not (await self.bot.db_worker.run(lambda: self.bot.db.get_dm_enabled(user_id, "pomodoro_phases"))):
            return
        embed: dict = {"title": title, "description": description, "color": int(color)}
        if fields:
            embed["fields"] = fields
        if footer:
            embed["footer"] = footer
        (await self.bot.db_worker.run(lambda: self.bot.db.enqueue_outbox(
            target_type="user",
            target_id=int(user_id),
            kind="pomodoro_phase",
            dedupe_key=dedupe_key,
            settings_key="pomodoro_phases",
            embed=embed,
        )))

    def _ensure_group_autostart(self, lobby_id: int) -> None:
        # Only one task per lobby.
        if lobby_id in self.group_autostart_tasks:
            return
        task = asyncio.create_task(self._group_autostart_loop(lobby_id))
        self.group_autostart_tasks[lobby_id] = task

    def _group_member_display_lines(self, members: list[dict], host_id: int) -> str:
        """Short roster display for lobby embeds (mentions are fine in-server)."""
        lines: list[str] = []
        host_id = int(host_id)
        for m in members[:10]:
            uid = int(m.get("user_id") or 0)
            if not uid:
                continue
            tag = " (host)" if uid == host_id else ""
            lines.append(f"<@{uid}>{tag}")
        return "\n".join(lines) if lines else "_No one yet_"

    async def _waiting_lobby_embed(self, lobby: dict) -> discord.Embed:
        members = (await self.bot.db_worker.run(lambda: self.bot.db.get_group_members(lobby["id"])))
        roster = self._group_member_display_lines(members, lobby["host_id"])
        votes = (await self.bot.db_worker.run(lambda: self.bot.db.get_group_lobby_vote_count(lobby["id"])))
        n = len(members)
        needed = (n // 2) + 1
        work = lobby["work_mins"]
        sb = lobby["break_mins"]
        lb = lobby["long_break_mins"]
        cycles = lobby["cycles"]
        subject = lobby.get("subject") or "Study Group"
        code = lobby["code"]
        embed = discord.Embed(
            title=f"🍅 Group Pomodoro Created — {subject}",
            description=f"Share this code with your group:\n\n# `{code}`\n\n"
            f"**Settings:** {work}/{sb}/{lb} min · {cycles} cycles\n"
            f"Max 10 participants. Code expires in 60 minutes.",
            color=WORK_COLOR,
        )
        embed.add_field(name=f"Participants ({len(members)}/10)", value=roster, inline=False)
        if n >= 3:
            embed.add_field(
                name="🗳️ Vote to start",
                value=f"Votes: **{votes}/{needed}** (majority of {n})",
                inline=False,
            )
        else:
            embed.add_field(
                name="🗳️ Vote to start",
                value="Needs **3+** people to enable majority-start.",
                inline=False,
            )
        embed.set_footer(
            text=f"Join: button below or /group_pomo join {code} · Host: Begin button or /group_pomo begin"
        )
        return embed

    async def _active_lobby_embed(self, lobby: dict) -> discord.Embed:
        members = (await self.bot.db_worker.run(lambda: self.bot.db.get_group_members(lobby["id"])))
        roster = self._group_member_display_lines(members, lobby["host_id"])
        subject = lobby.get("subject") or "Study Group"
        phase = lobby.get("current_phase") or "work"
        cycle = int(lobby.get("current_cycle") or 1)
        total_cycles = int(lobby.get("cycles") or 4)

        if phase == "work":
            duration_m = int(lobby.get("work_mins") or 25)
            color = WORK_COLOR
            phase_label = "🍅 Work"
        elif phase == "long_break":
            duration_m = int(lobby.get("long_break_mins") or 15)
            color = LONG_COLOR
            phase_label = "🏖️ Long break"
        else:
            duration_m = int(lobby.get("break_mins") or 5)
            color = BREAK_COLOR
            phase_label = "☕ Break"

        embed = discord.Embed(
            title="🍅 Group Pomodoro — In progress",
            description=f"**{subject}** · {len(members)} participants\nCycle {cycle}/{total_cycles} · Phase: **{phase_label}**",
            color=color,
        )
        embed.add_field(name="Participants", value=roster, inline=False)

        try:
            started = parse_stored(lobby.get("phase_started_at") or "").replace(tzinfo=timezone.utc)
            end_unix = int(started.timestamp()) + duration_m * 60
            embed.add_field(name="⏳ Phase ends", value=f"<t:{end_unix}:R>", inline=False)
        except Exception:
            pass

        embed.set_footer(text="Use Refresh status to update phase/time.")
        return embed

    async def _execute_group_begin(self, interaction: discord.Interaction, lobby: dict, *, edit_message: bool):
        # If the host starts early, cancel any pending autostart.
        self.group_autostart_cancelled.add(lobby["id"])
        if not (await self.bot.db_worker.run(lambda: self.bot.db.begin_group_lobby(lobby["id"]))):
            # Someone else started it already (race). Just refresh what we can and return.
            lobby_now = (await self.bot.db_worker.run(lambda: self.bot.db.get_group_lobby_by_id(lobby["id"])))
            if edit_message and lobby_now and lobby_now.get("state") == "active":
                try:
                    if interaction.response.is_done():
                        await interaction.edit_original_response(
                            embed=(await self._active_lobby_embed(lobby_now)),
                            view=GroupLobbyActiveView(self, lobby_now["id"]),
                        )
                    else:
                        await interaction.response.edit_message(
                            embed=(await self._active_lobby_embed(lobby_now)),
                            view=GroupLobbyActiveView(self, lobby_now["id"]),
                        )
                except Exception:
                    pass
            return
        members = (await self.bot.db_worker.run(lambda: self.bot.db.get_group_members(lobby["id"])))
        study_cog = self.bot.cogs.get("Study")
        for m in members:
            uid = m["user_id"]
            (await self.bot.db_worker.run(lambda: self.bot.db.ensure_user(uid)))
            # If a member has an active solo pomodoro timer, end it now so it doesn't keep firing in the background.
            if (await self.bot.db_worker.run(lambda: self.bot.db.get_active_pomodoro(uid))):
                (await self.bot.db_worker.run(lambda: self.bot.db.end_pomodoro(uid)))
                self._cancel_pomo_task(uid)
            # Allow members to study while waiting; auto-end personal session on group start.
            if (await self.bot.db_worker.run(lambda: self.bot.db.get_active_session(uid))):
                async with self.bot.user_locks[uid]:
                    if study_cog:
                        study_cog._cancel_live_task(uid)
                        study_cog._cancel_motivation_task(uid)
                        study_cog._cancel_inactivity_monitor(uid)
                        study_cog._session_panel_views.pop(uid, None)
                    s = (await self.bot.db_worker.run(lambda: self.bot.db.end_session(uid, "Auto-ended for Group Pomodoro start")))
                    if s and s.get("duration_minutes", 0) >= 5 and study_cog:
                        await study_cog._process_session_rewards(uid, s)
            (await self.bot.db_worker.run(lambda: self.bot.db.start_session(
                uid,
                lobby.get("subject", "Group Study"),
                is_group=True,
                group_lobby_id=lobby["id"],
                source_guild_id=interaction.guild_id,
                allowed_member=True,
            )))
        lobby = (await self.bot.db_worker.run(lambda: self.bot.db.get_group_lobby_by_id(lobby["id"])))
        roster = self._group_member_display_lines(members, lobby["host_id"])
        embed = discord.Embed(
            title="🍅 Group Pomodoro Started!",
            description=f"**{lobby.get('subject', 'Study Group')}** · {len(members)} participants\n"
            f"**{lobby['work_mins']}/{lobby['break_mins']}/{lobby['long_break_mins']}** min · "
            f"{lobby['cycles']} cycles\n\n"
            f"First break in **{fmt_mins(lobby['work_mins'])}**!",
            color=WORK_COLOR,
        )
        embed.add_field(name="Participants", value=roster, inline=False)
        self._schedule_group_phase(lobby["id"], lobby["work_mins"] * 60)

        # DM all members that the session has started (reliable via outbox).
        ends_unix = int(time.time()) + int(lobby["work_mins"]) * 60
        phase_key = lobby.get("phase_started_at") or lobby.get("started_at") or str(int(time.time()))
        for m in members:
            uid = int(m["user_id"])
            (await self.bot.db_worker.run(lambda: self.bot.db.enqueue_outbox(
                target_type="user",
                target_id=uid,
                kind="group_pomo_started",
                dedupe_key=f"group_pomo_started:{lobby['id']}:{uid}:{phase_key}",
                settings_key="group_pomo",
                embed={
                    "title": "🍅 Group Pomodoro Started!",
                    "description": (
                        f"**{lobby.get('subject', 'Group Study')}** · {len(members)} participants\n"
                        f"Cycle {lobby.get('current_cycle', 1)}/{lobby['cycles']} · Phase: **Work**"
                    ),
                    "color": int(WORK_COLOR),
                    "fields": [{"name": "⏳ Work ends in", "value": f"<t:{ends_unix}:R>", "inline": False}],
                },
            )))

        if edit_message:
            # Component interactions can only be responded to once; support both direct edit and deferred edits.
            if interaction.response.is_done():
                await interaction.edit_original_response(
                    embed=(await self._active_lobby_embed(lobby)),
                    view=GroupLobbyActiveView(self, lobby["id"]),
                )
            else:
                await interaction.response.edit_message(
                    embed=(await self._active_lobby_embed(lobby)),
                    view=GroupLobbyActiveView(self, lobby["id"]),
                )
        else:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed)
            else:
                await interaction.response.send_message(embed=embed)

        # Always try to flip the public lobby card (if we know where it is) to the in-progress embed + refresh button.
        # This covers starts triggered by /group_pomo begin (which isn't invoked from the lobby message itself).
        try:
            ch_id = lobby.get("announce_channel_id")
            msg_id = lobby.get("announce_message_id")
            if ch_id and msg_id:
                ch = self.bot.get_channel(int(ch_id)) or await self.bot.fetch_channel(int(ch_id))
                msg = await ch.fetch_message(int(msg_id))
                await msg.edit(
                    embed=(await self._active_lobby_embed(lobby)),
                    view=GroupLobbyActiveView(self, int(lobby["id"])),
                )
        except Exception as e:
            log.debug("Group lobby announce card edit failed: %s", e)

    async def _group_autostart_loop(self, lobby_id: int):
        try:
            lobby = (await self.bot.db_worker.run(lambda: self.bot.db.get_group_lobby_by_id(lobby_id)))
            if not lobby or lobby.get("state") != "waiting":
                return
            created = parse_stored(lobby.get("created_at") or "")
            now = utcnow_naive()
            warn_at = created + timedelta(minutes=55)
            start_at = created + timedelta(minutes=60)

            # Sleep until T-5m
            s1 = (warn_at - now).total_seconds()
            if s1 > 0:
                await asyncio.sleep(s1)

            # Re-check state and member count
            lobby = (await self.bot.db_worker.run(lambda: self.bot.db.get_group_lobby_by_id(lobby_id)))
            if not lobby or lobby.get("state") != "waiting":
                return
            members = (await self.bot.db_worker.run(lambda: self.bot.db.get_group_members(lobby_id)))
            if len(members) < 2:
                return

            # DM host with cancel button
            host_id = int(lobby["host_id"])
            host = await self.bot.get_user_or_fetch(host_id)
            if host:
                try:
                    embed = discord.Embed(
                        title="⏳ Group Pomodoro auto-start in 5 minutes",
                        description="Your lobby has 2+ people. It will auto-start in 5 minutes unless you cancel.",
                        color=0xFEE75C,
                    )
                    await host.send(embed=embed, view=CancelAutostartView(self, lobby_id, host_id))
                except discord.Forbidden:
                    pass

            # Sleep remaining to T
            now2 = utcnow_naive()
            s2 = (start_at - now2).total_seconds()
            if s2 > 0:
                await asyncio.sleep(s2)

            if lobby_id in self.group_autostart_cancelled:
                return

            lobby = (await self.bot.db_worker.run(lambda: self.bot.db.get_group_lobby_by_id(lobby_id)))
            if not lobby or lobby.get("state") != "waiting":
                return
            members = (await self.bot.db_worker.run(lambda: self.bot.db.get_group_members(lobby_id)))
            if len(members) < 2:
                return

            # Auto-begin by editing the public lobby card if we can locate it.
            if not (await self.bot.db_worker.run(lambda: self.bot.db.begin_group_lobby(lobby_id))):
                # Someone else started it already (host/vote). Avoid double-starting sessions.
                return

            # Start sessions for all members (reuse same logic by calling the existing begin code path)
            # We simulate the interaction-less begin by performing the same member logic here.
            study_cog = self.bot.cogs.get("Study")
            for m in members:
                uid = m["user_id"]
                (await self.bot.db_worker.run(lambda: self.bot.db.ensure_user(uid)))
                if (await self.bot.db_worker.run(lambda: self.bot.db.get_active_pomodoro(uid))):
                    (await self.bot.db_worker.run(lambda: self.bot.db.end_pomodoro(uid)))
                    self._cancel_pomo_task(uid)
                if (await self.bot.db_worker.run(lambda: self.bot.db.get_active_session(uid))):
                    async with self.bot.user_locks[uid]:
                        if study_cog:
                            study_cog._cancel_live_task(uid)
                            study_cog._cancel_motivation_task(uid)
                            study_cog._cancel_inactivity_monitor(uid)
                            study_cog._session_panel_views.pop(uid, None)
                        s = (await self.bot.db_worker.run(lambda: self.bot.db.end_session(uid, "Auto-ended for Group Pomodoro start")))
                        if s and s.get("duration_minutes", 0) >= 5 and study_cog:
                            await study_cog._process_session_rewards(uid, s)
                (await self.bot.db_worker.run(lambda: self.bot.db.start_session(
                    uid,
                    lobby.get("subject", "Group Study"),
                    is_group=True,
                    group_lobby_id=lobby_id,
                    source_guild_id=lobby.get("source_guild_id"),
                    allowed_member=True if lobby.get("source_guild_id") is not None else None,
                )))

            self._schedule_group_phase(lobby_id, int(lobby["work_mins"]) * 60)

            # DM all members that the session has started (reliable via outbox).
            ends_unix = int(time.time()) + int(lobby["work_mins"]) * 60
            phase_key = lobby.get("phase_started_at") or lobby.get("started_at") or str(int(time.time()))
            for m in members:
                uid = int(m["user_id"])
                (await self.bot.db_worker.run(lambda: self.bot.db.enqueue_outbox(
                    target_type="user",
                    target_id=uid,
                    kind="group_pomo_started_auto",
                    dedupe_key=f"group_pomo_started_auto:{lobby_id}:{uid}:{phase_key}",
                    settings_key="group_pomo",
                    embed={
                        "title": "🍅 Group Pomodoro Started! (Auto)",
                        "description": (
                            f"**{lobby.get('subject', 'Group Study')}** · {len(members)} participants\n"
                            f"Cycle {lobby.get('current_cycle', 1)}/{lobby['cycles']} · Phase: **Work**"
                        ),
                        "color": int(WORK_COLOR),
                        "fields": [{"name": "⏳ Work ends in", "value": f"<t:{ends_unix}:R>", "inline": False}],
                    },
                )))

            # Update the public lobby card if possible
            ch_id = lobby.get("announce_channel_id")
            msg_id = lobby.get("announce_message_id")
            if ch_id and msg_id:
                try:
                    ch = self.bot.get_channel(int(ch_id)) or await self.bot.fetch_channel(int(ch_id))
                    msg = await ch.fetch_message(int(msg_id))
                    await msg.edit(
                        embed=(await self._active_lobby_embed(lobby)),
                        view=GroupLobbyActiveView(self, lobby_id),
                    )
                except Exception as e:
                    log.debug("Auto-start lobby message edit failed: %s", e)
        finally:
            self.group_autostart_tasks.pop(lobby_id, None)

    async def _pomodoro_stop_inner(self, interaction: discord.Interaction, uid: int):
        pomo = (await self.bot.db_worker.run(lambda: self.bot.db.end_pomodoro(uid)))
        if not pomo:
            await interaction.followup.send("❌ No active Pomodoro.", ephemeral=True)
            return

        self._cancel_pomo_task(uid)
        study_cog = self.bot.cogs.get("Study")
        if study_cog:
            study_cog._cancel_live_task(uid)
            study_cog._cancel_motivation_task(uid)
            study_cog._cancel_inactivity_monitor(uid)

        session = None
        loot_drops: list[dict] = []
        async with self.bot.user_locks[uid]:
            session = (await self.bot.db_worker.run(lambda: self.bot.db.end_session(uid)))
            if session and session["duration_minutes"] >= 5 and study_cog:
                _, loot_drops = await study_cog._process_session_rewards(uid, session)

        total_focus = pomo["total_cycles"] * pomo["work_minutes"]
        embed = discord.Embed(title="🍅 Pomodoro Session Ended", color=0x747F8D)
        embed.add_field(name="Cycles Completed", value=str(pomo["total_cycles"]), inline=True)
        embed.add_field(name="Total Focus Time", value=fmt_mins(total_focus), inline=True)

        if session and session.get("id") and session.get("duration_minutes", 0) >= 5:
            from cogs.study import SessionRatingView, format_loot_summary

            loot_line = format_loot_summary(loot_drops)
            if loot_line:
                embed.add_field(name="🎁 Lucky loot (this session)", value=loot_line, inline=False)
            suggested_break = min(max(total_focus // 5, 5), 30)
            embed.set_footer(
                text=f"Rate your focus — suggested break: {suggested_break} min • {USER_NAV_FOOTER}"
            )
            view = SessionRatingView(self.bot, uid, session["id"], suggested_break)
            msg = await interaction.followup.send(embed=embed, view=view, ephemeral=True, wait=True)
            view.message = msg
        else:
            await interaction.followup.send(embed=embed, ephemeral=True)

        if interaction.message is not None:
            try:
                await interaction.message.edit(view=None)
            except Exception as e:
                log.debug("Solo Pomodoro message view clear failed: %s", e)

    async def _pomodoro_stop_from_button(self, interaction: discord.Interaction):
        uid = interaction.user.id
        if not (await self.bot.db_worker.run(lambda: self.bot.db.get_active_pomodoro(uid))):
            await interaction.response.send_message("❌ No active Pomodoro.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await self._pomodoro_stop_inner(interaction, uid)

    def cog_unload(self):
        for task in self.pomo_tasks.values():
            task.cancel()
        for task in self.group_tasks.values():
            task.cancel()
        for task in self.group_autostart_tasks.values():
            task.cancel()

    # ── Solo phase scheduling ─────────────────────────────────────────────────

    def _schedule_phase(self, user_id: int, duration_seconds: float):
        self._cancel_pomo_task(user_id)
        task = asyncio.create_task(self._phase_countdown(user_id, max(duration_seconds, 0)))
        self.pomo_tasks[user_id] = task

    def _cancel_pomo_task(self, user_id: int):
        if user_id in self.pomo_tasks:
            self.pomo_tasks[user_id].cancel()
            del self.pomo_tasks[user_id]

    async def _phase_countdown(self, user_id: int, seconds: float):
        try:
            await asyncio.sleep(max(seconds, 0))
            pomo = (await self.bot.db_worker.run(lambda: self.bot.db.get_active_pomodoro(user_id)))
            if not pomo:
                return
            await self._transition_phase(user_id, pomo, auto=True)
        except asyncio.CancelledError:
            pass

    async def _schedule_phase_safe(self, user_id: int, pomo: dict):
        phase = pomo["current_phase"]
        if phase == "work":
            duration_secs = pomo["work_minutes"] * 60
        elif phase == "long_break":
            duration_secs = (pomo.get("long_break_minutes") or 15) * 60
        else:
            duration_secs = pomo["break_minutes"] * 60

        from utils import utcnow_naive
        phase_started = parse_stored(pomo["phase_started_at"])
        elapsed = int((utcnow_naive() - phase_started).total_seconds())
        remaining = duration_secs - elapsed

        if remaining < -(duration_secs * 2):
            log.warning(f"Pomodoro for user {user_id} is {-remaining}s overdue — ending")
            (await self.bot.db_worker.run(lambda: self.bot.db.end_pomodoro(user_id)))
            session = (await self.bot.db_worker.run(lambda: self.bot.db.end_session(user_id)))
            if session and session.get("duration_minutes", 0) >= 5:
                study_cog = self.bot.cogs.get("Study")
                if study_cog:
                    asyncio.create_task(study_cog._process_session_rewards(user_id, session))
                else:
                    (await self.bot.db_worker.run(lambda: self.bot.db.add_points(user_id, session["duration_minutes"], "Pomodoro: recovered expired session")))
                    (await self.bot.db_worker.run(lambda: self.bot.db.add_daily_minutes(user_id, session["duration_minutes"])))
            return
        self._schedule_phase(user_id, max(remaining, 0))

    async def _transition_phase(self, user_id: int, pomo: dict, auto: bool = False):
        current = pomo["current_phase"]
        cycle = pomo["total_cycles"]
        work_mins = pomo["work_minutes"]
        break_mins = pomo["break_minutes"]
        long_break_mins = pomo.get("long_break_minutes") or 15

        if current == "work":
            new_cycle = cycle + 1
            if new_cycle % 4 == 0:
                next_phase = "long_break"
                next_duration = long_break_mins
                dm_title = "🏖️ Long Break Time!"
                dm_desc = f"You've completed **{new_cycle} Pomodoro cycles**! Take a well-earned **{fmt_mins(long_break_mins)} break**."
                dm_color = LONG_COLOR
            else:
                next_phase = "break"
                next_duration = break_mins
                dm_title = "☕ Break Time!"
                dm_desc = f"Cycle #{new_cycle} complete! Enjoy your **{fmt_mins(break_mins)} break**."
                dm_color = BREAK_COLOR

            work_segment_key = f"{pomo['id']}:{pomo['phase_started_at']}"
            await self._temptation_pomodoro_work_ended(user_id, work_segment_key=work_segment_key)

            max_cyc = pomo.get("max_cycles") or 0
            if max_cyc > 0 and new_cycle >= max_cyc:
                new_pomo = (await self.bot.db_worker.run(lambda: self.bot.db.advance_pomodoro(user_id, next_phase)))
                if next_phase in ("break", "long_break"):
                    (await self.bot.db_worker.run(lambda: self.bot.db.pause_session(user_id)))
                break_seg = ""
                if new_pomo:
                    break_seg = f"{new_pomo['id']}:{new_pomo['phase_started_at']}"
                await self._temptation_pomodoro_break_started(
                    user_id, int(next_duration), break_segment_key=break_seg
                )
                pomo_row_id = int(pomo["id"])

                async def _auto_stop_after_break(
                    uid=user_id,
                    dur=next_duration,
                    mc=max_cyc,
                    wm=work_mins,
                    ended_pomo_id=pomo_row_id,
                    is_grp=bool(pomo.get("is_group")),
                ):
                    await asyncio.sleep(dur * 60)
                    (await self.bot.db_worker.run(lambda: self.bot.db.end_pomodoro(uid)))
                    self._cancel_pomo_task(uid)
                    study_cog = self.bot.cogs.get("Study")
                    if study_cog:
                        study_cog._cancel_live_task(uid)
                        study_cog._cancel_inactivity_monitor(uid)
                    async with self.bot.user_locks[uid]:
                        session = (await self.bot.db_worker.run(lambda: self.bot.db.end_session(uid)))
                        if session and session["duration_minutes"] >= 5:
                            await study_cog._process_session_rewards(uid, session) if study_cog else None
                    (await self._enqueue_solo_pomo_phase_dm(
                        uid,
                        dedupe_key=f"pomo_solo_complete:{uid}:{ended_pomo_id}:{mc}",
                        title="🏁 Pomodoro Complete!",
                        description=f"All **{mc} cycles** done — amazing work!",
                        color=0xFFD700,
                        fields=[
                            {"name": "Cycles Done", "value": str(mc), "inline": True},
                            {"name": "Total Focus Time", "value": fmt_mins(mc * wm), "inline": True},
                        ],
                    ))
                    quest_cog = self.bot.cogs.get("Quests")
                    if quest_cog:
                        await quest_cog.track_quest(uid, "pomo_cycle", mc)
                        if mc >= 4:
                            await quest_cog.track_quest(
                                uid, "the_traditionalist" if not is_grp else "group_pomo_4cycle"
                            )

                asyncio.create_task(_auto_stop_after_break())
                (await self._enqueue_solo_pomo_phase_dm(
                    user_id,
                    dedupe_key=f"pomo_solo_final_break:{user_id}:{pomo_row_id}:{new_cycle}",
                    title=dm_title,
                    description=dm_desc,
                    color=int(dm_color),
                    footer="Final break — session ends automatically after!",
                ))
                return
        else:
            next_phase = "work"
            next_duration = work_mins
            dm_title = "🍅 Back to Work!"
            dm_desc = f"Break's over. Time to focus! Cycle #{cycle + 1} starting."
            dm_color = WORK_COLOR

        (await self.bot.db_worker.run(lambda: self.bot.db.advance_pomodoro(user_id, next_phase)))
        fresh = (await self.bot.db_worker.run(lambda: self.bot.db.get_active_pomodoro(user_id)))
        break_seg = f"{fresh['id']}:{fresh['phase_started_at']}" if fresh else ""

        if next_phase in ("break", "long_break"):
            await self._temptation_pomodoro_break_started(
                user_id, int(next_duration), break_segment_key=break_seg
            )
            (await self.bot.db_worker.run(lambda: self.bot.db.pause_session(user_id)))
        elif next_phase == "work":
            (await self.bot.db_worker.run(lambda: self.bot.db.resume_session(user_id)))

        if fresh:
            (await self._enqueue_solo_pomo_phase_dm(
                user_id,
                dedupe_key=(
                    f"pomo_solo_phase:{user_id}:{fresh['id']}:"
                    f"{fresh['current_phase']}:{fresh['phase_number']}"
                ),
                title=dm_title,
                description=dm_desc,
                color=int(dm_color),
                footer="StudyBot Pomodoro • /pomodoro status  /pomodoro skip",
                fields=[
                    {
                        "name": "Next phase ends",
                        "value": f"<t:{int(time.time()) + next_duration * 60}:R>",
                        "inline": False,
                    }
                ],
            ))

        self._schedule_phase(user_id, next_duration * 60)

    # ── Solo Commands ─────────────────────────────────────────────────────────

    pomo = app_commands.Group(name="pomodoro", description="Pomodoro timer — auto cycles with DMs")

    @pomo.command(name="start", description="Start a solo Pomodoro session")
    @app_commands.describe(
        work="Focus duration in minutes (default 25)",
        short_break="Short break in minutes (default 5)",
        long_break="Long break every 4 cycles (default 15)",
        max_cycles="Max work cycles before auto-stop (0 = unlimited)",
        subject="What you're studying"
    )
    async def pomo_start(self, interaction: discord.Interaction,
                         work: app_commands.Range[int, 5, 120] = 25,
                         short_break: app_commands.Range[int, 1, 60] = 5,
                         long_break: app_commands.Range[int, 5, 60] = 15,
                         max_cycles: app_commands.Range[int, 0, 20] = 0,
                         subject: str = "General"):
        uid = interaction.user.id
        await interaction.response.defer(ephemeral=True)
        async with self.bot.user_locks[uid]:
            (await self.bot.db_worker.run(lambda: self.bot.db.ensure_user(uid, str(interaction.user))))

            if (await self.bot.db_worker.run(lambda: self.bot.db.get_active_pomodoro(uid))):
                await interaction.followup.send("You already have an active Pomodoro. Use `/pomodoro stop` first.", ephemeral=True)
                return
            # Allow solo Pomodoro to start even if the user is already studying or waiting in a group lobby.
            # We'll auto-end the personal study session (and award rewards) so the Pomodoro session starts cleanly.
            study_cog = self.bot.cogs.get("Study")
            if (await self.bot.db_worker.run(lambda: self.bot.db.get_active_session(uid))):
                if study_cog:
                    study_cog._cancel_live_task(uid)
                    study_cog._cancel_motivation_task(uid)
                    study_cog._cancel_inactivity_monitor(uid)
                    study_cog._session_panel_views.pop(uid, None)
                s = (await self.bot.db_worker.run(lambda: self.bot.db.end_session(uid, "Auto-ended for solo Pomodoro start")))
                if s and s.get("duration_minutes", 0) >= 5 and study_cog:
                    await study_cog._process_session_rewards(uid, s)

            # Snapshot allowlist status for DM-origin sessions so buff display is accurate.
            is_allowed_member = (
                interaction.guild_id is not None
                or await getattr(self.bot, "is_member_of_allowed_guild", lambda _uid: False)(uid)
            )
            (await self.bot.db_worker.run(lambda: self.bot.db.start_session(
                uid,
                subject,
                source_guild_id=interaction.guild_id,
                allowed_member=is_allowed_member,
            )))
            (await self.bot.db_worker.run(lambda: self.bot.db.start_pomodoro(uid, work, short_break, long_break, max_cycles)))

        end_unix = int(time.time()) + work * 60
        embed = discord.Embed(
            title=f"🍅 Pomodoro Started — {subject}",
            description=f"**{work}/{short_break}/{long_break}** min (work / short break / long break)",
            color=WORK_COLOR
        )
        embed.add_field(name="🔴 Focus", value=fmt_mins(work), inline=True)
        embed.add_field(name="☕ Short Break", value=fmt_mins(short_break), inline=True)
        embed.add_field(name="🏖️ Long Break", value=f"Every 4 cycles · {fmt_mins(long_break)}", inline=True)
        embed.add_field(name="⏳ First break in", value=f"<t:{end_unix}:R>", inline=False)
        embed.set_footer(text="I'll DM you at every phase transition! • /pomodoro status")
        await interaction.followup.send(embed=embed, ephemeral=True)
        self._schedule_phase(uid, work * 60)

    @pomo.command(name="status", description="Check your current Pomodoro phase")
    async def pomo_status(self, interaction: discord.Interaction):
        pomo = (await self.bot.db_worker.run(lambda: self.bot.db.get_active_pomodoro(interaction.user.id)))
        if not pomo:
            await interaction.response.send_message("📭 No active Pomodoro. Start one with `/pomodoro start`!", ephemeral=True)
            return
        await interaction.response.send_message(embed=build_pomo_embed(pomo), ephemeral=True)

    @pomo.command(name="skip", description="Skip the current phase")
    async def pomo_skip(self, interaction: discord.Interaction):
        pomo = (await self.bot.db_worker.run(lambda: self.bot.db.get_active_pomodoro(interaction.user.id)))
        if not pomo:
            await interaction.response.send_message("❌ No active Pomodoro.", ephemeral=True)
            return
        self._cancel_pomo_task(interaction.user.id)
        await interaction.response.defer(ephemeral=True)
        await self._transition_phase(interaction.user.id, pomo, auto=False)
        new_pomo = (await self.bot.db_worker.run(lambda: self.bot.db.get_active_pomodoro(interaction.user.id)))
        embed = discord.Embed(title="⏭️ Phase Skipped!", color=0x5865F2)
        if new_pomo:
            phase_map = {"work": "🔴 Focus", "break": "☕ Break", "long_break": "🏖️ Long Break"}
            embed.add_field(name="Now", value=phase_map.get(new_pomo["current_phase"], "—"))
        await interaction.followup.send(embed=embed, ephemeral=True)

    @pomo.command(name="stop", description="Stop your Pomodoro session")
    async def pomo_stop(self, interaction: discord.Interaction):
        uid = interaction.user.id
        if not (await self.bot.db_worker.run(lambda: self.bot.db.get_active_pomodoro(uid))):
            await interaction.response.send_message("❌ No active Pomodoro.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        pomo = (await self.bot.db_worker.run(lambda: self.bot.db.end_pomodoro(uid)))
        if not pomo:
            await interaction.followup.send("❌ No active Pomodoro.", ephemeral=True)
            return

        self._cancel_pomo_task(uid)
        study_cog = self.bot.cogs.get("Study")
        if study_cog:
            study_cog._cancel_live_task(uid)
            study_cog._cancel_motivation_task(uid)
            study_cog._cancel_inactivity_monitor(uid)

        session = None
        loot_drops: list[dict] = []
        async with self.bot.user_locks[uid]:
            session = (await self.bot.db_worker.run(lambda: self.bot.db.end_session(uid)))
            if session and session["duration_minutes"] >= 5 and study_cog:
                _, loot_drops = await study_cog._process_session_rewards(uid, session)

        total_focus = pomo["total_cycles"] * pomo["work_minutes"]
        embed = discord.Embed(title="🍅 Pomodoro Session Ended", color=0x747F8D)
        embed.add_field(name="Cycles Completed", value=str(pomo["total_cycles"]), inline=True)
        embed.add_field(name="Total Focus Time", value=fmt_mins(total_focus), inline=True)

        if session and session.get("id") and session.get("duration_minutes", 0) >= 5:
            from cogs.study import SessionRatingView, format_loot_summary
            loot_line = format_loot_summary(loot_drops)
            if loot_line:
                embed.add_field(name="🎁 Lucky loot (this session)", value=loot_line, inline=False)
            suggested_break = min(max(total_focus // 5, 5), 30)
            embed.set_footer(
                text=f"Rate your focus — suggested break: {suggested_break} min • {USER_NAV_FOOTER}"
            )
            view = SessionRatingView(self.bot, uid, session["id"], suggested_break)
            msg = await interaction.followup.send(embed=embed, view=view, ephemeral=True, wait=True)
            view.message = msg
        else:
            await interaction.followup.send(embed=embed, ephemeral=True)

    # ── Group Pomodoro Commands ───────────────────────────────────────────────

    group = app_commands.Group(name="group_pomo", description="Group Pomodoro sessions")

    @group.command(name="start", description="Host a Group Pomodoro session")
    @app_commands.describe(
        work="Focus duration (default 25)", short_break="Short break (default 5)",
        long_break="Long break (default 15)", cycles="Number of cycles (default 4)",
        subject="What the group is studying"
    )
    async def group_start(self, interaction: discord.Interaction,
                          work: app_commands.Range[int, 5, 120] = 25,
                          short_break: app_commands.Range[int, 1, 60] = 5,
                          long_break: app_commands.Range[int, 5, 60] = 15,
                          cycles: app_commands.Range[int, 1, 12] = 4,
                          subject: str = "Study Group"):
        uid = interaction.user.id
        async with self.bot.user_locks[uid]:
            (await self.bot.db_worker.run(lambda: self.bot.db.ensure_user(uid, str(interaction.user))))

            # Users may study while waiting/hosting; group begin will auto-end personal sessions.
            if (await self.bot.db_worker.run(lambda: self.bot.db.get_user_group_lobby(uid))):
                await interaction.response.send_message("You're already in a group lobby.", ephemeral=True)
                return

            result = (await self.bot.db_worker.run(lambda: self.bot.db.create_group_lobby(uid, work, short_break, long_break, cycles, subject)))

        lobby = (await self.bot.db_worker.run(lambda: self.bot.db.get_group_lobby_by_id(result["id"])))
        embed = (await self._waiting_lobby_embed(lobby))
        view = GroupLobbyWaitingView(self, result["id"])
        await interaction.response.send_message(embed=embed, view=view)

        # Store where the lobby card was posted so auto-start can edit it later.
        try:
            msg = await interaction.original_response()
            (await self.bot.db_worker.run(lambda: self.bot.db.set_group_lobby_announce(
                result["id"],
                source_guild_id=interaction.guild_id,
                channel_id=interaction.channel_id,
                message_id=msg.id,
            )))
        except Exception:
            # Not fatal; auto-start will still happen, just without editing the card.
            pass

        # Schedule auto-start (1 hour after creation, if 2+ people).
        self._ensure_group_autostart(result["id"])

    @group.command(name="join", description="Join a Group Pomodoro with a code")
    @app_commands.describe(code="The 6-character lobby code")
    async def group_join(self, interaction: discord.Interaction, code: str):
        uid = interaction.user.id
        async with self.bot.user_locks[uid]:
            (await self.bot.db_worker.run(lambda: self.bot.db.ensure_user(uid, str(interaction.user))))

            # Users may study while waiting in the lobby; group begin will auto-end personal sessions.
            if (await self.bot.db_worker.run(lambda: self.bot.db.get_user_group_lobby(uid))):
                await interaction.response.send_message("You're already in a lobby.", ephemeral=True)
                return

            result = (await self.bot.db_worker.run(lambda: self.bot.db.join_group_lobby(code, uid)))
            if not result:
                await interaction.response.send_message("Invalid or expired code.", ephemeral=True)
                return
            if isinstance(result, dict) and result.get("error") == "full":
                await interaction.response.send_message("Lobby is full (max 10).", ephemeral=True)
                return

        members = (await self.bot.db_worker.run(lambda: self.bot.db.get_group_members(result["id"])))
        embed = discord.Embed(
            title=f"🍅 Joined Group Pomodoro!",
            description=f"**{result.get('subject', 'Study Group')}** · Code: `{code.upper()}`\n"
                        f"Members: {len(members)}/10",
            color=BREAK_COLOR
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @group.command(name="begin", description="Start the Group Pomodoro (host only)")
    async def group_begin(self, interaction: discord.Interaction):
        uid = interaction.user.id
        await interaction.response.defer()
        lobby = (await self.bot.db_worker.run(lambda: self.bot.db.get_user_group_lobby(uid)))
        if not lobby:
            await interaction.followup.send("You're not in a lobby.", ephemeral=True)
            return
        if lobby["host_id"] != uid:
            await interaction.followup.send("Only the host can start the session.", ephemeral=True)
            return
        if lobby["state"] != "waiting":
            await interaction.followup.send("Session already started.", ephemeral=True)
            return
        await self._execute_group_begin(interaction, lobby, edit_message=False)

    @group.command(name="leave", description="Leave the Group Pomodoro")
    async def group_leave(self, interaction: discord.Interaction):
        uid = interaction.user.id
        await interaction.response.defer(ephemeral=True)
        session = None
        async with self.bot.user_locks[uid]:
            lobby = (await self.bot.db_worker.run(lambda: self.bot.db.leave_group_lobby(uid)))
            if not lobby:
                await interaction.followup.send("You're not in a lobby.", ephemeral=True)
                return

            # End their personal session
            study_cog = self.bot.cogs.get("Study")
            if study_cog:
                study_cog._cancel_live_task(uid)
                study_cog._cancel_motivation_task(uid)
                study_cog._cancel_inactivity_monitor(uid)
                study_cog._session_panel_views.pop(uid, None)
            session = (await self.bot.db_worker.run(lambda: self.bot.db.end_session(uid)))
            if session and session["duration_minutes"] >= 5 and study_cog:
                await study_cog._process_session_rewards(uid, session)

        note = _group_leave_reward_note(session)
        embed = discord.Embed(
            title="👋 Left Group Pomodoro",
            description="Your study time has been saved." + note,
            color=0x747F8D,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @group.command(name="status", description="Check group lobby status")
    async def group_status(self, interaction: discord.Interaction):
        lobby = (await self.bot.db_worker.run(lambda: self.bot.db.get_user_group_lobby(interaction.user.id)))
        if not lobby:
            await interaction.response.send_message("You're not in a lobby.", ephemeral=True)
            return

        members = (await self.bot.db_worker.run(lambda: self.bot.db.get_group_members(lobby["id"])))
        member_names = []
        for m in members:
            u = (await self.bot.db_worker.run(lambda: self.bot.db.get_user(m["user_id"])))
            name = u.get("username", f"User {m['user_id']}") if u else f"User {m['user_id']}"
            host_tag = " 👑" if m["user_id"] == lobby["host_id"] else ""
            member_names.append(f"• {name}{host_tag}")

        embed = discord.Embed(
            title=f"🍅 Group Pomodoro — {lobby.get('subject', 'Study Group')}",
            color=WORK_COLOR if lobby.get("current_phase") == "work" else BREAK_COLOR
        )
        embed.add_field(name="Code", value=f"`{lobby['code']}`", inline=True)
        embed.add_field(name="State", value=lobby["state"].title(), inline=True)
        embed.add_field(name="Phase", value=lobby.get("current_phase", "waiting").replace("_", " ").title(), inline=True)
        embed.add_field(name="Cycle", value=f"{lobby.get('current_cycle', 0)}/{lobby['cycles']}", inline=True)
        embed.add_field(name=f"Members ({len(members)}/10)", value="\n".join(member_names), inline=False)

        # Time remaining (only meaningful once active with phase_started_at)
        if lobby.get("state") == "active" and lobby.get("phase_started_at"):
            phase = lobby.get("current_phase") or "work"
            if phase == "work":
                duration_m = int(lobby.get("work_mins") or 25)
            elif phase == "long_break":
                duration_m = int(lobby.get("long_break_mins") or 15)
            else:
                duration_m = int(lobby.get("break_mins") or 5)
            try:
                started = parse_stored(lobby["phase_started_at"]).replace(tzinfo=timezone.utc)
                end_unix = int(started.timestamp()) + duration_m * 60
                embed.add_field(name="⏳ Phase ends", value=f"<t:{end_unix}:R>", inline=False)
            except Exception:
                pass

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── Group phase scheduling ────────────────────────────────────────────────

    def _schedule_group_phase(self, lobby_id: int, duration_seconds: float):
        if lobby_id in self.group_tasks:
            self.group_tasks[lobby_id].cancel()
        self.group_tasks[lobby_id] = asyncio.create_task(
            self._group_phase_countdown(lobby_id, max(duration_seconds, 0))
        )

    def _schedule_group_phase_safe(self, lobby: dict) -> None:
        """Recover group countdown based on phase_started_at (restart-safe)."""
        phase = lobby.get("current_phase") or "work"
        if phase == "work":
            duration_secs = int(lobby.get("work_mins") or 25) * 60
        elif phase == "long_break":
            duration_secs = int(lobby.get("long_break_mins") or 15) * 60
        else:
            duration_secs = int(lobby.get("break_mins") or 5) * 60

        try:
            started = parse_stored(lobby.get("phase_started_at") or "")
        except Exception:
            # If missing, just schedule a full phase window from now.
            self._schedule_group_phase(int(lobby["id"]), duration_secs)
            return

        elapsed = int((utcnow_naive() - started).total_seconds())
        remaining = duration_secs - elapsed

        # If we're overdue, transition immediately. If it's meaningfully overdue, DM a quiet recovery note.
        if remaining < 0:
            overdue = abs(int(remaining))
            if overdue >= 45:
                asyncio.create_task(self._dm_group_recovered(lobby, overdue_seconds=overdue))
            self._schedule_group_phase(int(lobby["id"]), 0)
            return

        self._schedule_group_phase(int(lobby["id"]), max(remaining, 0))

    async def _dm_group_recovered(self, lobby: dict, *, overdue_seconds: int) -> None:
        """Quiet DM note after restart if we missed a phase transition."""
        try:
            members = (await self.bot.db_worker.run(lambda: self.bot.db.get_group_members(int(lobby["id"]))))
            phase = (lobby.get("current_phase") or "work").replace("_", " ").title()
            subj = lobby.get("subject") or "Group Study"
            ps = lobby.get("phase_started_at") or ""
            lid = int(lobby["id"])
            for m in members:
                uid = int(m["user_id"])
                if not (await self.bot.db_worker.run(lambda: self.bot.db.get_dm_enabled(uid, "group_pomo"))):
                    continue
                (await self.bot.db_worker.run(lambda: self.bot.db.enqueue_outbox(
                    target_type="user",
                    target_id=uid,
                    kind="group_pomo_recovered",
                    settings_key="group_pomo",
                    dedupe_key=f"group_pomo_recover:{lid}:{uid}:{ps}:{overdue_seconds}",
                    embed={
                        "title": "ℹ️ Group Pomodoro recovered",
                        "description": (
                            f"Resynced your group session after a brief restart.\n"
                            f"**{subj}** · Phase was **{phase}**\n"
                            f"_Recovered from ~{fmt_mins(max(overdue_seconds // 60, 1))} delay._"
                        ),
                        "color": int(0x99AAB5),
                    },
                )))
                await asyncio.sleep(0.15)
        except Exception:
            log.debug("group_pomo recovery DM enqueue failed", exc_info=True)

    async def _group_phase_countdown(self, lobby_id: int, seconds: float):
        try:
            await asyncio.sleep(seconds)
            lobby = (await self.bot.db_worker.run(lambda: self.bot.db.get_group_lobby_by_id(lobby_id)))
            if not lobby or lobby["state"] != "active":
                return
            await self._group_transition(lobby)
        except asyncio.CancelledError:
            pass

    async def _group_transition(self, lobby: dict):
        phase = lobby["current_phase"]
        cycle = lobby.get("current_cycle", 0)
        members = (await self.bot.db_worker.run(lambda: self.bot.db.get_group_members(lobby["id"])))

        if phase == "work":
            new_cycle = cycle
            if new_cycle % 4 == 0 and new_cycle > 0:
                next_phase = "long_break"
                next_duration = lobby["long_break_mins"]
            else:
                next_phase = "break"
                next_duration = lobby["break_mins"]

            # Check if all cycles complete
            if new_cycle >= lobby["cycles"]:
                await self._end_group_session(lobby, members)
                return

            for m in members:
                (await self.bot.db_worker.run(lambda: self.bot.db.pause_session(m["user_id"])))
        else:
            next_phase = "work"
            next_duration = lobby["work_mins"]
            new_cycle = cycle + 1

            for m in members:
                (await self.bot.db_worker.run(lambda: self.bot.db.resume_session(m["user_id"])))

        old_phase_started = lobby.get("phase_started_at") or ""
        (await self.bot.db_worker.run(lambda: self.bot.db.advance_group_phase(lobby["id"], next_phase, new_cycle)))
        lobby_after = (await self.bot.db_worker.run(lambda: self.bot.db.get_group_lobby_by_id(int(lobby["id"])))) or lobby
        new_phase_started = lobby_after.get("phase_started_at") or ""

        if phase == "work" and next_phase in ("break", "long_break"):
            work_seg = f"g{lobby['id']}:{old_phase_started}"
            break_seg = f"g{lobby['id']}:{new_phase_started}"
            for m in members:
                uid = m["user_id"]
                await self._temptation_pomodoro_work_ended(uid, work_segment_key=work_seg)
                await self._temptation_pomodoro_break_started(
                    uid, int(next_duration), break_segment_key=break_seg
                )

        # Notify all members
        phase_map = {"work": ("🍅 Back to Work!", WORK_COLOR), "break": ("☕ Break Time!", BREAK_COLOR),
                     "long_break": ("🏖️ Long Break!", LONG_COLOR)}
        title, color = phase_map.get(next_phase, ("🍅 Phase Change", 0x5865F2))

        for m in members:
            uid = int(m["user_id"])
            phase_key = lobby.get("phase_started_at") or lobby.get("started_at") or str(int(time.time()))
            (await self.bot.db_worker.run(lambda: self.bot.db.enqueue_outbox(
                target_type="user",
                target_id=uid,
                kind="group_pomo_phase",
                settings_key="group_pomo",
                dedupe_key=f"group_pomo_phase:{lobby['id']}:{uid}:{next_phase}:{phase_key}",
                embed={
                    "title": title,
                    "description": (
                        f"**{lobby.get('subject', 'Group Study')}** · "
                        f"Cycle {new_cycle}/{lobby['cycles']}"
                    ),
                    "color": int(color),
                    "fields": [{"name": "⏳ Ends in", "value": f"<t:{int(time.time()) + next_duration * 60}:R>", "inline": False}],
                },
            )))
            await asyncio.sleep(0.15)

        self._schedule_group_phase(lobby["id"], next_duration * 60)

    async def _end_group_session(self, lobby: dict, members: list[dict]):
        (await self.bot.db_worker.run(lambda: self.bot.db.end_group_lobby(lobby["id"])))
        study_cog = self.bot.cogs.get("Study")
        quest_cog = self.bot.cogs.get("Quests")

        perks_with_aura = set()
        for m in members:
            if "grandmaster_aura" in (await self.bot.db_worker.run(lambda: self.bot.db.get_prestige_perks(m["user_id"]))):
                perks_with_aura.add(m["user_id"])

        group_xp_mult = 1.5 if perks_with_aura else 1.25

        for m in members:
            uid = m["user_id"]
            async with self.bot.user_locks[uid]:
                session = (await self.bot.db_worker.run(lambda: self.bot.db.end_session(uid)))
                if session and session["duration_minutes"] >= 5 and study_cog:
                    await study_cog._process_session_rewards(uid, session, group_xp_mult=group_xp_mult)

            if quest_cog and lobby["cycles"] >= 4:
                await quest_cog.track_quest(uid, "group_pomo_4cycle")

            try:
                phase_key = lobby.get("phase_started_at") or lobby.get("started_at") or str(int(time.time()))
                (await self.bot.db_worker.run(lambda: self.bot.db.enqueue_outbox(
                    target_type="user",
                    target_id=int(uid),
                    kind="group_pomo_complete",
                    settings_key="group_pomo",
                    dedupe_key=f"group_pomo_complete:{lobby['id']}:{uid}:{phase_key}",
                    embed={
                        "title": "🏁 Group Pomodoro Complete!",
                        "description": (
                            f"**{lobby.get('subject', 'Group Study')}** finished!\n"
                            f"**{lobby['cycles']} cycles** · Group XP: {group_xp_mult}x"
                        ),
                        "color": 0xFFD700,
                    },
                )))
            except Exception:
                pass
            await asyncio.sleep(0.15)

        badge_cog = self.bot.cogs.get("Badges")
        if badge_cog and members:
            uids = [m["user_id"] for m in members]
            host_id = lobby.get("host_id") or 0
            if host_id and uids:
                await badge_cog.check_squad_badges_after_group_complete(host_id, uids)


async def setup(bot):
    await bot.add_cog(Pomodoro(bot))
