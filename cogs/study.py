# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone, timedelta
from typing import Optional
import asyncio
import time
import random
import logging

from constants import (
    EST, MOTIVATIONAL_QUOTES, STAR_LABELS, LUCKY_LOOT_TABLE,
    LUCKY_LOOT_MIN_MINUTES,
    COLOR_PRIMARY, COLOR_SUCCESS, COLOR_WARNING, COLOR_ERROR, COLOR_GOLD,
    USER_NAV_FOOTER,
)
from utils import fmt_date_us_from_iso, fmt_mins, fmt_secs, fmt_weekday_datetime_us_est, parse_stored
from temptation_bundle import bundle_live_line_for_study_session
from services.rewards_engine import calc_xp_bonus_breakdown

log = logging.getLogger("StudyBot.Study")


class SessionNoteModal(discord.ui.Modal, title="Session note"):
    text = discord.ui.TextInput(
        label="Note",
        style=discord.TextStyle.paragraph,
        placeholder="What did you cover?",
        max_length=900,
        required=True,
    )

    def __init__(self, cog: "Study", user_id: int):
        super().__init__()
        self.cog = cog
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This note is for the session owner only.", ephemeral=True)
            return
        if not self.cog.bot.db.add_session_note(self.user_id, self.text.value):
            await interaction.response.send_message("❌ No active session.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        session = self.cog.bot.db.get_active_session(self.user_id)
        if session:
            await self.cog._edit_live_session_message(self.user_id, session=session)
        await interaction.followup.send("📝 Note saved.", ephemeral=True)
        quest_cog = self.cog.bot.cogs.get("Quests")
        if quest_cog:
            await quest_cog.track_quest(self.user_id, "session_note")


class StopSessionModal(discord.ui.Modal, title="Stop session (optional note)"):
    text = discord.ui.TextInput(
        label="Note (optional)",
        style=discord.TextStyle.paragraph,
        placeholder="What did you cover?",
        max_length=900,
        required=False,
    )

    def __init__(self, cog: "Study", user_id: int):
        super().__init__()
        self.cog = cog
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This action is for the session owner only.", ephemeral=True)
            return
        await self.cog._stop_session(interaction, notes=self.text.value or "")


class StudySessionControlsView(discord.ui.View):
    """On the live session card: pause / resume / note / refresh (slash still works)."""

    def __init__(self, cog: "Study", user_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "These controls are only for the person in this study session.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Pause", style=discord.ButtonStyle.primary, emoji="⏸️", row=0)
    async def pause_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.cog.bot.db.get_active_session(self.user_id)
        if not session:
            await interaction.response.send_message("❌ No active session.", ephemeral=True)
            return
        if session.get("is_paused"):
            await interaction.response.send_message("⏸️ Already paused. Use **Resume**.", ephemeral=True)
            return
        self.cog.bot.db.pause_session(self.user_id)
        self.cog._cancel_live_task(self.user_id)
        session = self.cog.bot.db.get_active_session(self.user_id)
        if not session:
            log.error("pause_session left no active session row for user %s", self.user_id)
            await interaction.response.send_message(
                "⏸️ Paused, but session state is inconsistent. Use `/study status`.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        ok = await self.cog._edit_live_session_message(self.user_id, session=session)
        if not ok:
            await interaction.followup.send(
                "⏸️ Paused, but the live card couldn't be updated. Try `/study status`.",
                ephemeral=True,
            )

    @discord.ui.button(label="Resume", style=discord.ButtonStyle.success, emoji="▶️", row=0)
    async def resume_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.cog.bot.db.get_active_session(self.user_id)
        if not session:
            await interaction.response.send_message("❌ No active session.", ephemeral=True)
            return
        if not session.get("is_paused"):
            await interaction.response.send_message("▶️ Session isn't paused.", ephemeral=True)
            return
        self.cog.bot.db.resume_session(self.user_id)
        session = self.cog.bot.db.get_active_session(self.user_id)
        if not session:
            log.error("resume_session left no active session row for user %s", self.user_id)
            await interaction.response.send_message(
                "▶️ Resumed, but session state is inconsistent. Use `/study status`.",
                ephemeral=True,
            )
            return
        self.cog._start_live_task(self.user_id)
        if (session.get("target_minutes") or 0) >= 10:
            self.cog._ensure_motivation_task(self.user_id)
        quest_cog = self.cog.bot.cogs.get("Quests")
        if quest_cog:
            await quest_cog.track_quest(self.user_id, "pause_resume")
        await interaction.response.defer(ephemeral=True)
        ok = await self.cog._edit_live_session_message(self.user_id, session=session)
        if not ok:
            await interaction.followup.send(
                "▶️ Resumed, but the live card couldn't be updated. Try `/study status`.",
                ephemeral=True,
            )

    @discord.ui.button(label="Note", style=discord.ButtonStyle.secondary, emoji="📝", row=0)
    async def note_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SessionNoteModal(self.cog, self.user_id))

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, emoji="🔄", row=0)
    async def refresh_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.cog.bot.db.get_active_session(self.user_id)
        if not session:
            await interaction.response.send_message("❌ No active session.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        ok = await self.cog._edit_live_session_message(self.user_id, session=session)
        if not ok:
            await interaction.followup.send(
                "Couldn't refresh the live card. Try `/study status`.",
                ephemeral=True,
            )

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger, emoji="⏹️", row=1)
    async def stop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.cog.bot.db.get_active_session(self.user_id)
        if not session:
            await interaction.response.send_message("❌ No active session.", ephemeral=True)
            return
        await interaction.response.send_modal(StopSessionModal(self.cog, self.user_id))


def get_elapsed_and_paused(session: dict) -> tuple[int, int]:
    started = parse_stored(session["started_at"])
    total_paused = session.get("total_paused_seconds") or 0
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    if session.get("is_paused") and session.get("paused_at"):
        paused_at = parse_stored(session["paused_at"])
        elapsed_wall = int((paused_at - started).total_seconds())
    else:
        elapsed_wall = int((now_naive - started).total_seconds())
    return max(elapsed_wall - total_paused, 0), total_paused


def build_session_embed(
    session: dict,
    live: bool = True,
    bundle_line: str | None = None,
    buff_line: str | None = None,
) -> discord.Embed:
    active_secs, total_paused = get_elapsed_and_paused(session)
    elapsed_mins = active_secs // 60
    is_paused = bool(session.get("is_paused"))
    subject = session.get("subject") or "General"

    color = 0xFEE75C if is_paused else 0x5865F2
    status_icon = "⏸️" if is_paused else "🟢"
    embed = discord.Embed(title=f"{status_icon} {'Paused' if is_paused else 'Studying'} — {subject}", color=color)
    embed.add_field(name="⏱️ Active Time", value=fmt_mins(elapsed_mins), inline=True)

    target = session.get("target_minutes")
    if target:
        remaining_mins = max(target - elapsed_mins, 0)
        pct = min(int(elapsed_mins / target * 100), 100)
        filled = pct // 5
        bar = "█" * filled + "░" * (20 - filled)
        embed.add_field(
            name=f"🎯 Target — {pct}%",
            value=f"`{bar}`\n{fmt_mins(elapsed_mins)} / {fmt_mins(target)} — {fmt_mins(remaining_mins)} left",
            inline=False
        )

    if not is_paused and live:
        started_unix = int(parse_stored(session["started_at"]).replace(tzinfo=timezone.utc).timestamp())
        pomo_unix = started_unix + total_paused + 25 * 60
        deep_unix = started_unix + total_paused + 60 * 60
        mara_unix = started_unix + total_paused + 120 * 60
        now_unix = int(time.time())
        bonuses = []
        if now_unix < pomo_unix:
            bonuses.append(f"🍅 Pomodoro +25 XP: <t:{pomo_unix}:R>")
        elif now_unix < deep_unix:
            bonuses.append(f"✅ Pomodoro done!\n🧠 Deep Work +50 XP: <t:{deep_unix}:R>")
        elif now_unix < mara_unix:
            bonuses.append(f"✅ Pomodoro + Deep Work!\n🏃 Marathon +100 XP: <t:{mara_unix}:R>")
        else:
            bonuses.append("🏆 All bonuses unlocked!")
        embed.add_field(name="⚡ XP Bonuses", value="\n".join(bonuses), inline=False)
    elif is_paused and session.get("paused_at"):
        paused_unix = int(parse_stored(session["paused_at"]).replace(tzinfo=timezone.utc).timestamp())
        embed.add_field(name="⏸️ Paused", value=f"Paused <t:{paused_unix}:R>", inline=False)

    if session.get("notes"):
        lines = session["notes"].split("\n")
        preview = "\n".join(lines[:3]) + (f"\n_+{len(lines)-3} more_" if len(lines) > 3 else "")
        embed.add_field(name="📝 Notes", value=preview, inline=False)

    if bundle_line:
        embed.add_field(name="🎁 Treat bundle", value=bundle_line[:1024], inline=False)

    if buff_line:
        embed.add_field(name="🧪 XP Buffs (now)", value=buff_line[:1024], inline=False)

    embed.set_footer(text=f"StudyBot • /study pause · /study stop · /study note • {USER_NAV_FOOTER}")
    return embed


def _lucky_loot_probs_for_hour(hour_index: int) -> list[float]:
    """Base weights from LUCKY_LOOT_TABLE, shifted slightly toward better tiers on later hours (same session)."""
    base = [row[0] for row in LUCKY_LOOT_TABLE]
    depth = min(max(hour_index - 1, 0), 4)
    # Up to ~4.8% moved from points into potions/coin, spread by their relative weights.
    steal = 0.012 * depth
    rest = sum(base[1:])
    if rest <= 0 or steal <= 0:
        return base
    adj = base[:]
    adj[0] = max(adj[0] - steal, 0.01)
    for i in range(1, len(adj)):
        adj[i] = base[i] + steal * (base[i] / rest)
    s = sum(adj)
    return [p / s for p in adj]


def _draw_lucky_loot_item(hour_index: int = 1) -> dict:
    """Single weighted draw; hour_index 1..5 biases slightly better for longer continuous study."""
    probs = _lucky_loot_probs_for_hour(hour_index)
    roll = random.random()
    cumulative = 0.0
    for prob, (_, rarity, item, value_range) in zip(probs, LUCKY_LOOT_TABLE):
        cumulative += prob
        if roll <= cumulative:
            if item == "pts" and value_range:
                return {"rarity": rarity, "type": "pts", "value": random.randint(*value_range)}
            if item == "small_potion":
                return {"rarity": rarity, "type": "potion", "key": "small_potion"}
            if item == "large_potion":
                return {"rarity": rarity, "type": "potion", "key": "large_potion"}
            if item == "boss_coin":
                return {"rarity": rarity, "type": "coin", "value": 1}
            break
    return {"rarity": "common", "type": "pts", "value": random.randint(50, 150)}


def roll_lucky_loot(session_minutes: int) -> list[dict]:
    """One lucky loot drop per full hour studied (max 5). Requires at least LUCKY_LOOT_MIN_MINUTES."""
    if session_minutes < LUCKY_LOOT_MIN_MINUTES:
        return []
    hours = min(session_minutes // 60, 5)
    return [_draw_lucky_loot_item(hour_index=i + 1) for i in range(hours)]


def format_loot_summary(drops: list[dict]) -> str:
    """Short text for session summary embeds (rewards already granted in DB)."""
    if not drops:
        return ""
    parts = []
    for d in drops:
        r = d.get("rarity", "common")
        if d["type"] == "pts":
            parts.append(f"+{d['value']:,} pts ({r})")
        elif d["type"] == "potion":
            parts.append(f"{d['key'].replace('_', ' ')} ({r})")
        elif d["type"] == "coin":
            parts.append(f"+{d['value']} coin 🪙 ({r})")
    return " · ".join(parts)


class Study(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.live_tasks: dict[int, asyncio.Task] = {}
        self.motivation_tasks: dict[int, asyncio.Task] = {}
        self.inactivity_tasks: dict[int, asyncio.Task] = {}
        self._session_panel_views: dict[int, StudySessionControlsView] = {}

    def _session_bundle_line(self, session: dict) -> str | None:
        uid = session.get("user_id")
        if uid is None:
            return None
        active_secs, _ = get_elapsed_and_paused(session)
        return bundle_live_line_for_study_session(self.bot.db, int(uid), active_secs // 60)

    def _session_buff_line(self, session: dict) -> str | None:
        uid = session.get("user_id")
        if uid is None:
            return None
        uid = int(uid)

        # Lite mode hides RPG/economy systems.
        if getattr(self.bot, "is_lite_user", lambda _uid: False)(uid):
            return None

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        lines: list[str] = []

        potions = self.bot.db.get_active_potions(uid)
        if potions:
            cur = 1.0
            for p in potions:
                try:
                    expires = parse_stored(p["expires_at"])
                    if expires <= now:
                        continue
                    cur += max(float(p.get("multiplier", 1.0)) - 1.0, 0.0)
                except Exception:
                    continue
            cur = min(cur, 2.0)
            lines.append(f"**Potions:** up to **{round(cur, 2)}× XP** (cap 2.0×)")
            for p in potions[:4]:
                try:
                    expires = parse_stored(p["expires_at"])
                    remaining_m = max(int((expires - now).total_seconds() / 60), 0)
                    lines.append(f"• `{p['effect_type']}` **{p['multiplier']}×** · {fmt_mins(remaining_m)} left")
                except Exception:
                    continue
            lines.append("_Final potion bonus is overlap-weighted at session end._")

        # Bounty XP window (2.0×). (DB returns None if none active.)
        # Display gating: use the session's allowlist snapshot if present.
        # This avoids network calls in embed building and keeps DM/guild behavior consistent.
        am = session.get("allowed_member")
        if am is None:
            allowed_for_display = session.get("source_guild_id") is not None
        else:
            allowed_for_display = bool(int(am))

        if allowed_for_display:
            b = self.bot.db.get_active_bounty_xp_window(uid)
            if b:
                try:
                    buff_end = parse_stored(b.get("buff_expires_at") or "")
                    if buff_end > now:
                        buff_end_unix = int(buff_end.replace(tzinfo=timezone.utc).timestamp())
                        lines.append(f"**Bounty:** **2.0× XP** until <t:{buff_end_unix}:R>")
                except Exception:
                    pass

        return "\n".join(lines) if lines else None

    def cog_unload(self):
        for task in self.live_tasks.values(): task.cancel()
        for task in self.motivation_tasks.values(): task.cancel()
        for task in self.inactivity_tasks.values(): task.cancel()
        self._session_panel_views.clear()

    def _start_live_task(self, user_id: int):
        self._cancel_live_task(user_id)
        self.live_tasks[user_id] = asyncio.create_task(self._live_loop(user_id))

    def _cancel_live_task(self, user_id: int):
        if user_id in self.live_tasks:
            self.live_tasks[user_id].cancel()
            del self.live_tasks[user_id]

    async def _edit_live_session_message(self, user_id: int, *, session: dict | None = None) -> bool:
        """Re-fetch and edit the live study card (same path as the 30s live loop).

        Ephemeral slash follow-ups often break ``interaction.response.edit_message`` on buttons;
        channel ``fetch_message`` + ``edit`` is reliable for those messages.
        """
        if session is None:
            session = self.bot.db.get_active_session(user_id)
        if not session:
            return False
        ch_id, msg_id = session.get("live_channel_id"), session.get("live_message_id")
        if not ch_id or not msg_id:
            return False
        view = self._session_panel_views.get(user_id)
        try:
            ch = self.bot.get_channel(ch_id) or await self.bot.fetch_channel(ch_id)
            msg = await ch.fetch_message(msg_id)
            await msg.edit(
                embed=build_session_embed(
                    session,
                    bundle_line=self._session_bundle_line(session),
                    buff_line=self._session_buff_line(session),
                ),
                view=view,
            )
            return True
        except Exception as e:
            log.warning("Live message edit failed: %s", e)
            return False

    async def _stop_session(self, interaction: discord.Interaction, *, notes: str = ""):
        """Shared stop handler for slash + button/modal stop."""
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        uid = interaction.user.id
        async with self.bot.user_locks[uid]:
            self.bot.db.ensure_user(uid, str(interaction.user))
            session = self.bot.db.end_session(uid, notes)
            if not session:
                await interaction.followup.send("❌ No active session.", ephemeral=True)
                return

            self._cancel_live_task(uid)
            self._cancel_motivation_task(uid)
            self._cancel_inactivity_monitor(uid)
            self._session_panel_views.pop(uid, None)

            minutes = session["duration_minutes"]
            xp = session["xp_earned"]
            points = session.get("points_earned", minutes)

            actual_xp = 0
            loot_drops: list[dict] = []
            if minutes >= 5:
                actual_xp, loot_drops = await self._process_session_rewards(uid, session)
            xp = actual_xp or xp

            user = self.bot.db.get_user(uid)

        ch_id, msg_id = session.get("live_channel_id"), session.get("live_message_id")
        if ch_id and msg_id:
            async def _update_live_msg():
                try:
                    ch = self.bot.get_channel(ch_id) or await self.bot.fetch_channel(ch_id)
                    ended_embed = discord.Embed(title=f"✅ Session Complete — {session['subject'] or 'General'}", color=0x57F287)
                    ended_embed.add_field(name="Duration", value=fmt_mins(minutes))
                    ended_embed.add_field(name="XP", value=f"+{xp}")
                    ended_embed.add_field(name="Points", value=f"+{points}")
                    await (await ch.fetch_message(msg_id)).edit(embed=ended_embed, view=None)
                except Exception:
                    pass
            asyncio.create_task(_update_live_msg())

        if minutes < 5:
            await interaction.followup.send("⚠️ Session under 5 min — no rewards. Keep at it!", ephemeral=True)
            return

        suggested_break = min(max(minutes // 5, 5), 30)

        embed = discord.Embed(
            title="🎓 Session Complete!",
            description=f"Great work on **{session['subject'] or 'your studies'}**!",
            color=0x57F287
        )
        embed.add_field(name="⏱️ Duration", value=fmt_mins(minutes), inline=True)
        embed.add_field(name="⭐ XP Earned", value=f"+{xp}", inline=True)
        embed.add_field(name="💎 Points", value=f"+{points} (total: {user['points']:,})", inline=True)
        embed.add_field(name="🪙 Coins", value=str(user.get("coins", 0)), inline=True)

        paused_total = session.get("total_paused_seconds") or 0
        if paused_total > 60:
            embed.add_field(name="⏸️ Paused", value=fmt_secs(paused_total), inline=True)

        bonuses = []
        if minutes >= 25:  bonuses.append("🍅 Pomodoro (+25 XP)")
        if minutes >= 60:  bonuses.append("🧠 Deep Work (+50 XP)")
        if minutes >= 120: bonuses.append("🏃 Marathon (+100 XP)")
        if bonuses:
            embed.add_field(name="🏅 Bonuses", value="  ".join(bonuses), inline=False)

        if session.get("notes"):
            embed.add_field(name="📝 Notes", value=session["notes"], inline=False)

        loot_line = format_loot_summary(loot_drops)
        if loot_line:
            embed.add_field(name="🎁 Lucky loot (this session)", value=loot_line, inline=False)

        goal = self.bot.db.get_today_goal(uid)
        today_iso = datetime.now(EST).date().isoformat()
        today_mins = self.bot.db.get_study_minutes_on_date(uid, today_iso)

        if goal > 0:
            pct = min(int(today_mins / goal * 100), 100)
            bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
            embed.add_field(name="🎯 Daily Goal", value=f"`{bar}` {pct}%  {fmt_mins(today_mins)}/{fmt_mins(goal)}", inline=False)
            if today_mins >= goal and today_mins - minutes < goal:
                embed.add_field(name="🏆 Goal Reached!", value="You hit your daily target! 🎉", inline=False)

        embed.set_footer(
            text=f"Suggested break: {suggested_break} min • How focused were you? • {USER_NAV_FOOTER}"
        )
        view = SessionRatingView(self.bot, uid, session["id"], suggested_break)
        msg = await interaction.followup.send(embed=embed, view=view, wait=True, ephemeral=True)
        view.message = msg

    async def _live_loop(self, user_id: int):
        try:
            await asyncio.sleep(30)
            consecutive_errors = 0
            while True:
                session = self.bot.db.get_active_session(user_id)
                if not session or session.get("is_paused"):
                    break
                ch_id = session.get("live_channel_id")
                msg_id = session.get("live_message_id")
                if ch_id and msg_id:
                    try:
                        ch = self.bot.get_channel(ch_id) or await self.bot.fetch_channel(ch_id)
                        msg = await ch.fetch_message(msg_id)
                        view = self._session_panel_views.get(user_id)
                        await msg.edit(
                            embed=build_session_embed(
                                session,
                                bundle_line=self._session_bundle_line(session),
                                buff_line=self._session_buff_line(session),
                            ),
                            view=view,
                        )
                        consecutive_errors = 0
                    except discord.NotFound:
                        break
                    except discord.Forbidden:
                        break
                    except Exception as e:
                        log.debug("Live loop edit error: %s", e)
                        consecutive_errors += 1
                        if consecutive_errors >= 5:
                            break
                await asyncio.sleep(30)
        except asyncio.CancelledError:
            pass

    def _ensure_motivation_task(self, user_id: int):
        task = self.motivation_tasks.get(user_id)
        if task and not task.done():
            return
        self.motivation_tasks[user_id] = asyncio.create_task(self._motivation_loop(user_id))

    def _cancel_motivation_task(self, user_id: int):
        task = self.motivation_tasks.pop(user_id, None)
        if task:
            task.cancel()

    async def _motivation_loop(self, user_id: int):
        try:
            while True:
                session = self.bot.db.get_active_session(user_id)
                if not session or session.get("motivation_sent"):
                    return
                target = session.get("target_minutes") or 0
                if target < 10:
                    return
                if session.get("is_paused"):
                    await asyncio.sleep(30)
                    continue
                active_secs, _ = get_elapsed_and_paused(session)
                threshold = (target * 60) // 2
                if active_secs >= threshold:
                    self.bot.db.set_motivation_sent(session["id"], True)
                    if self.bot.db.get_dm_enabled(user_id, "study_motivation"):
                        sid = int(session["id"])
                        quote = random.choice(MOTIVATIONAL_QUOTES)
                        self.bot.db.enqueue_outbox(
                            target_type="user",
                            target_id=int(user_id),
                            kind="study_motivation",
                            dedupe_key=f"study_motivation:{user_id}:{sid}",
                            settings_key="study_motivation",
                            embed={
                                "title": "🌟 Halfway there!",
                                "description": quote,
                                "color": int(0xFFD700),
                                "fields": [
                                    {
                                        "name": "Progress",
                                        "value": f"{fmt_mins(active_secs//60)} / {fmt_mins(target)}",
                                        "inline": False,
                                    }
                                ],
                            },
                        )
                    return
                remaining = max(threshold - active_secs, 0)
                await asyncio.sleep(min(30, max(10, remaining // 2 or 10)))
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.warning(f"Motivation loop failed for {user_id}: {e}")
        finally:
            current = asyncio.current_task()
            if self.motivation_tasks.get(user_id) is current:
                self.motivation_tasks.pop(user_id, None)

    # ── Inactivity safety net ─────────────────────────────────────────────────

    def _start_inactivity_monitor(self, user_id: int):
        task = self.inactivity_tasks.get(user_id)
        if task and not task.done():
            return
        self.inactivity_tasks[user_id] = asyncio.create_task(self._inactivity_loop(user_id))

    def _cancel_inactivity_monitor(self, user_id: int):
        task = self.inactivity_tasks.pop(user_id, None)
        if task:
            task.cancel()

    async def _inactivity_loop(self, user_id: int):
        """After 4 hours total time, send a DM dropdown. Auto-stop after 30m no response."""
        try:
            while True:
                session = self.bot.db.get_active_session(user_id)
                if not session:
                    return

                started = parse_stored(session["started_at"])
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                wall_secs = (now - started).total_seconds()

                if wall_secs < 4 * 3600:
                    await asyncio.sleep(min(300, max(60, 4 * 3600 - wall_secs)))
                    continue

                if not self.bot.db.get_dm_enabled(user_id, "inactivity_warnings"):
                    return

                user = await self.bot.get_user_or_fetch(user_id)
                if not user:
                    return

                view = InactivityView(self.bot, user_id)
                embed = discord.Embed(
                    title="⚠️ Long Session Check-in",
                    description="You've been in a session for **4+ hours**.\nAre you still studying?",
                    color=0xFEE75C
                )
                try:
                    msg = await user.send(embed=embed, view=view)
                    view.message = msg
                except (discord.Forbidden, discord.HTTPException):
                    return

                # Wait 30 minutes for response
                await asyncio.sleep(30 * 60)

                if not view.responded:
                    session = self.bot.db.get_active_session(user_id)
                    if session:
                        await self._auto_stop_session(user_id, "Inactivity auto-stop (30m no response)")
                return
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.warning(f"Inactivity loop error for {user_id}: {e}")

    async def _auto_stop_session(self, user_id: int, reason: str = ""):
        """Stops a session outside of a slash command context (e.g., inactivity DM)."""
        async with self.bot.user_locks[user_id]:
            session = self.bot.db.end_session(user_id, reason)
            if not session:
                return

            self._cancel_live_task(user_id)
            self._cancel_motivation_task(user_id)
            self._cancel_inactivity_monitor(user_id)
            self._session_panel_views.pop(user_id, None)

            minutes = int(session.get("duration_minutes") or 0)
            base_xp = int(session.get("xp_earned") or 0)
            base_points = int(session.get("points_earned") or 0)

            actual_xp = 0
            loot_drops: list[dict] = []
            if minutes >= 5:
                actual_xp, loot_drops = await self._process_session_rewards(user_id, session)

        # Ensure we display the non-zero, post-reward XP where applicable.
        xp_display = int(actual_xp or base_xp)
        pts_display = int(base_points or (minutes if minutes >= 5 else 0))

        # Update the live message (if any) so the UI matches reality.
        ch_id, msg_id = session.get("live_channel_id"), session.get("live_message_id")
        if ch_id and msg_id:
            async def _update_live_msg():
                try:
                    ch = self.bot.get_channel(ch_id) or await self.bot.fetch_channel(ch_id)
                    ended_embed = discord.Embed(title=f"✅ Session Complete — {session['subject'] or 'General'}", color=0x57F287)
                    ended_embed.add_field(name="Duration", value=fmt_mins(minutes))
                    ended_embed.add_field(name="XP", value=f"+{xp_display}")
                    ended_embed.add_field(name="Points", value=f"+{pts_display}")
                    await (await ch.fetch_message(msg_id)).edit(embed=ended_embed, view=None)
                except Exception:
                    pass
            asyncio.create_task(_update_live_msg())

        if self.bot.db.get_dm_enabled(user_id, "inactivity_warnings"):
            sid = int(session.get("id") or 0)
            embed: dict = {
                "title": "⏹️ Session Auto-Stopped",
                "description": (
                    f"Your session was stopped: {reason}\n\n"
                    f"**Duration:** {fmt_mins(minutes)}\n"
                    f"**XP:** +{xp_display}\n"
                    f"**Points:** +{pts_display}"
                ),
                "color": int(0xFEE75C),
            }
            loot_line = format_loot_summary(loot_drops)
            if loot_line:
                embed["fields"] = [
                    {"name": "🎁 Lucky loot (this session)", "value": loot_line, "inline": False}
                ]
            if sid > 0:
                self.bot.db.enqueue_outbox(
                    target_type="user",
                    target_id=int(user_id),
                    kind="session_auto_stopped",
                    dedupe_key=f"session_autostop:{user_id}:{sid}",
                    settings_key="inactivity_warnings",
                    embed=embed,
                )

    async def _process_session_rewards(
        self, user_id: int, session: dict, group_xp_mult: float = 1.0
    ) -> tuple[int, list[dict]]:
        """Apply all RPG rewards after a session ends. Returns (total_xp, lucky_loot_drops)."""
        minutes = session["duration_minutes"]
        if minutes < 5:
            return 0, []

        # Lite mode: keep time tracking, skip all RPG rewards/systems.
        if getattr(self.bot, "is_lite_user", lambda _uid: False)(user_id):
            self.bot.db.add_daily_minutes(user_id, minutes)
            return 0, []

        points = session.get("points_earned", minutes)

        # Beacon: +200 study pts per hour while an active beacon is up in this session's channel
        live_ch = session.get("live_channel_id")
        if live_ch and self.bot.db.get_active_beacon(int(live_ch)):
            points += int(minutes * 200 / 60)

        # Weekend bonus: +50 pts for first session >=25m on Sat/Sun
        now_est = datetime.now(EST)
        if now_est.weekday() >= 5 and minutes >= 25:
            user = self.bot.db.get_user(user_id)
            today_str = now_est.date().isoformat()
            if user and user.get("weekend_bonus_date") != today_str:
                points += 50
                self.bot.db.set_weekend_bonus_date(user_id, today_str)

        # Award points (1 min = 1 pt, immune to multipliers)
        self.bot.db.add_points(user_id, points, f"Study: {session.get('subject', 'General')}")

        # Calculate potion XP multiplier (proportional overlap)
        base_xp = int(session["xp_earned"])

        # Bounty effects: only for users who are actually in the allowlisted server(s).
        is_allowed_member = await getattr(self.bot, "is_member_of_allowed_guild", lambda _uid: False)(user_id)

        bonus = calc_xp_bonus_breakdown(self.bot.db, user_id=user_id, session=session, is_allowed_member=is_allowed_member)
        bonus_xp = int(bonus.total_bonus_xp)

        # Pomodoro +25 (and other session flats) live in database._calc_xp → session["xp_earned"]
        total_xp = base_xp + bonus_xp
        if group_xp_mult > 1.0:
            total_xp = int(total_xp * group_xp_mult)
        level_result = self.bot.db.add_xp(user_id, total_xp)

        # Bounty point payout (scales from the target's session duration; pays BOTH users).
        bounty_for_target = self.bot.db.get_active_bounty(user_id) if is_allowed_member else None
        if bounty_for_target and int(bounty_for_target.get("activated") or 0) == 1 and int(bounty_for_target.get("used") or 0) == 0:
            if bounty_for_target.get("target_id") == user_id:
                # Claim before payout to prevent double-payout under concurrent reward processing.
                bounty_id = int(bounty_for_target["id"])
                if self.bot.db.use_bounty(bounty_id):
                    bonus_pts = 0
                    if minutes >= 60:
                        # 60m => 500. Each additional hour up to 4h => +100, capped at 800.
                        extra_hours = min(minutes, 240) // 60 - 1
                        bonus_pts = min(500 + max(extra_hours, 0) * 100, 800)
                    if bonus_pts > 0:
                        owner_id = int(bounty_for_target["owner_id"])
                        self.bot.db.add_points(user_id, bonus_pts, "Bounty payout")
                        self.bot.db.add_points(owner_id, bonus_pts, "Bounty payout")
                        badge_cog = self.bot.cogs.get("Badges")
                        if badge_cog:
                            await badge_cog.check_venture_capitalist_badge(user_id, bounty_points=bonus_pts)
                            await badge_cog.check_venture_capitalist_badge(owner_id, bounty_points=bonus_pts)
                        # DM payout notices (respects /settings toggle)
                        if self.bot.db.get_dm_enabled(user_id, "bounty_payout"):
                            self.bot.db.enqueue_outbox(
                                target_type="user",
                                target_id=int(user_id),
                                kind="bounty_payout",
                                dedupe_key=f"bounty_payout:{bounty_id}:{user_id}",
                                settings_key="bounty_payout",
                                embed={
                                    "title": "💰 Bounty Payout!",
                                    "description": f"You earned **+{bonus_pts:,}** study points from a bounty payout.",
                                    "color": int(COLOR_GOLD),
                                },
                            )
                        if self.bot.db.get_dm_enabled(owner_id, "bounty_payout"):
                            self.bot.db.enqueue_outbox(
                                target_type="user",
                                target_id=int(owner_id),
                                kind="bounty_payout",
                                dedupe_key=f"bounty_payout:{bounty_id}:{owner_id}",
                                settings_key="bounty_payout",
                                embed={
                                    "title": "💰 Bounty Payout!",
                                    "description": f"You earned **+{bonus_pts:,}** study points from your bounty sponsor bonus.",
                                    "color": int(COLOR_GOLD),
                                },
                            )

        badge_cog = self.bot.cogs.get("Badges")

        # Daily minutes tracking
        self.bot.db.add_daily_minutes(user_id, minutes)

        # Seasonal minutes
        self.bot.db.add_seasonal_minutes(user_id, minutes)

        # Raid damage: only sessions started in an allowlisted guild can impact the server raid boss.
        boss = self.bot.db.get_active_boss()
        raid_badge_ctx: Optional[dict] = None
        source_guild_id = session.get("source_guild_id")
        can_affect_raid = False
        if source_guild_id is not None:
            can_affect_raid = (
                not getattr(self.bot, "allowed_guild_ids", None)
                or int(source_guild_id) in getattr(self.bot, "allowed_guild_ids", set())
            )
        else:
            # DM-started session: allow raid impact only if the user is actually in the allowlisted server.
            can_affect_raid = await getattr(self.bot, "is_member_of_allowed_guild", lambda _uid: False)(user_id)
        if can_affect_raid and boss and boss.get("hp_remaining", 0) > 0:
            raw_damage = minutes
            if live_ch and self.bot.db.get_active_beacon(int(live_ch)):
                raw_damage = int(raw_damage * 1.5)
            dmg_res = self.bot.db.add_raid_damage(user_id, boss["id"], raw_damage)
            raid_badge_ctx = {
                "killed": bool(dmg_res.get("killed")),
                "boss_id": boss["id"],
            }

        # Quest tracking (fire-and-forget to avoid blocking the interaction response)
        quest_cog = self.bot.cogs.get("Quests")
        if quest_cog:
            async def _quest_tracking():
                try:
                    await quest_cog.track_quest(user_id, "study_minutes", minutes)
                    await quest_cog.track_quest(user_id, "session_complete")
                    if minutes >= 45:
                        await quest_cog.track_quest(user_id, "long_session", minutes)
                    paused_total = session.get("total_paused_seconds") or 0
                    if paused_total == 0:
                        await quest_cog.track_quest(user_id, "deep_focus", minutes)
                    today_iso = now_est.date().isoformat()
                    today_mins = self.bot.db.get_study_minutes_on_date(user_id, today_iso)
                    goal = self.bot.db.get_today_goal(user_id)
                    if goal > 0 and today_mins >= goal:
                        await quest_cog.track_quest(user_id, "streak_guard")
                except Exception as e:
                    log.warning(f"Quest tracking error: {e}")
            asyncio.create_task(_quest_tracking())

        # Weekend freeze earning (study >=20m on Sat/Sun)
        if now_est.weekday() == 5 and minutes >= 20:
            self.bot.db.earn_weekend_freeze(user_id, "sat")
        elif now_est.weekday() == 6 and minutes >= 20:
            self.bot.db.earn_weekend_freeze(user_id, "sun")

        loot_drops = roll_lucky_loot(minutes)

        # Lucky Loot, badges, daily cap — fire-and-forget DM sends
        async def _post_session_notifications():
            try:
                tempt = self.bot.cogs.get("Temptation")
                if tempt:
                    try:
                        await tempt.on_study_session_end(
                            user_id,
                            int(session.get("duration_minutes", 0)),
                            session_id=int(session.get("id") or 0),
                        )
                    except Exception:
                        log.warning("Temptation on_study_session_end failed", exc_info=True)
                if loot_drops:
                    await self._deliver_lucky_loot(user_id, loot_drops)
                badge_cog = self.bot.cogs.get("Badges")
                if badge_cog:
                    await badge_cog.check_session_badges(user_id, session)
                    if raid_badge_ctx is not None:
                        boss_id = raid_badge_ctx["boss_id"]
                        if raid_badge_ctx.get("killed"):
                            for uid in self.bot.db.get_raid_damage_user_ids(boss_id):
                                await badge_cog.check_raid_badges(uid, {"killed": True})
                            lb = self.bot.db.get_raid_leaderboard(boss_id, limit=3)
                            for row in lb[:3]:
                                await badge_cog.check_mvp_podium_finish(row["user_id"])
                        total_rd = self.bot.db.get_user_total_raid_damage(user_id)
                        await badge_cog.check_vanguard_badge(user_id, total_rd)
                daily_total = self.bot.db.get_daily_minutes_today(user_id)
                if daily_total >= 480:
                    # Mandatory wellbeing DM: not tied to DM toggles; once per EST day when threshold is crossed.
                    wellbeing_key = "wellbeing_8h_sent_date"
                    today_est = now_est.date().isoformat()
                    if self.bot.db.get_setting(user_id, wellbeing_key, "") != today_est:
                        user = await self.bot.get_user_or_fetch(user_id)
                        if user:
                            embed = discord.Embed(
                                title="😴 Time to Rest!",
                                description="You've studied **8 hours** today. That's incredible, but your brain needs rest.\n"
                                            "Take a proper break — you've earned it!",
                                color=COLOR_ERROR
                            )
                            try:
                                await user.send(embed=embed)
                            except discord.Forbidden:
                                pass
                            else:
                                self.bot.db.set_setting(user_id, wellbeing_key, today_est)
            except Exception as e:
                log.warning(f"Post-session notification error: {e}")
        asyncio.create_task(_post_session_notifications())

        return total_xp, loot_drops

    async def _deliver_lucky_loot(self, user_id: int, drops: list[dict]):
        """Grant loot in DB always; DM notification via outbox when enabled."""
        rarity_colors = {"common": 0x95A5A6, "uncommon": 0x57F287, "rare": 0x5865F2, "legendary": 0xF1C40F}
        for i, drop in enumerate(drops):
            color = rarity_colors.get(drop["rarity"], 0x5865F2)
            title = f"🎁 Lucky Loot! ({drop['rarity'].title()})"
            if drop["type"] == "pts":
                self.bot.db.add_points(user_id, drop["value"], "Lucky Loot")
                desc = f"You found **{drop['value']:,} points**!"
            elif drop["type"] == "potion":
                self.bot.db.add_inventory_item(user_id, "potion", drop["key"])
                name = drop["key"].replace("_", " ").title()
                desc = (
                    f"You found a **{name}**! It's in `/inventory` — drink it with `/use_potion`."
                )
            elif drop["type"] == "coin":
                self.bot.db.add_coins(user_id, drop["value"])
                desc = f"You found **{drop['value']} Boss Coin(s)**! 🪙"
            else:
                continue
            dk = (
                f"lucky_loot:{user_id}:{drop['type']}:{drop.get('key', '')}:"
                f"{drop.get('value', '')}:{drop.get('rarity', '')}:{i}"
            )
            if self.bot.db.get_dm_enabled(user_id, "lucky_loot"):
                self.bot.db.enqueue_outbox(
                    target_type="user",
                    target_id=int(user_id),
                    kind="lucky_loot",
                    dedupe_key=dk,
                    settings_key="lucky_loot",
                    embed={"title": title, "description": desc, "color": int(color)},
                )

    # ──── /study ──────────────────────────────────────────────────────────────

    study = app_commands.Group(name="study", description="Track your study sessions")

    @study.command(name="start", description="Start a study session with a live countdown")
    @app_commands.describe(
        subject="What are you studying?",
        target="Optional goal in minutes (e.g. 90)",
        tags="Optional tags (comma-separated, e.g. anki,math)"
    )
    async def study_start(
        self,
        interaction: discord.Interaction,
        subject: str = "General",
        target: Optional[int] = None,
        tags: str = "",
    ):
        uid = interaction.user.id
        await interaction.response.defer(ephemeral=True)
        async with self.bot.user_locks[uid]:
            self.bot.db.ensure_user(uid, str(interaction.user))

            # Note: users are allowed to study while waiting in a Group Pomodoro lobby.
            # The group host starting the session will auto-end any personal session and
            # begin a group-tracked one.

            existing = self.bot.db.get_active_session(uid)
            if existing:
                active_secs, _ = get_elapsed_and_paused(existing)
                embed = discord.Embed(title="📚 Already Studying!", description=f"Session for **{existing['subject'] or 'General'}** is active.", color=0xFEE75C)
                embed.add_field(name="Active Time", value=fmt_mins(active_secs // 60))
                embed.set_footer(text="Use /study stop to end it first.")
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            # Study Bounty activation: only for users who are actually in the allowlisted server(s).
            is_allowed_member = await getattr(self.bot, "is_member_of_allowed_guild", lambda _uid: False)(uid)
            activated_bounty = self.bot.db.activate_pending_bounty_for_target(uid) if is_allowed_member else None
            if activated_bounty:
                owner_id = activated_bounty["owner_id"]
                target_user = await self.bot.get_user_or_fetch(uid)
                target_name = target_user.display_name if target_user else f"User {uid}"
                if self.bot.db.get_dm_enabled(int(owner_id), "bounty_activated"):
                    self.bot.db.enqueue_outbox(
                        target_type="user",
                        target_id=int(owner_id),
                        kind="bounty_activated",
                        dedupe_key=f"bounty_activated:{activated_bounty['id']}:{owner_id}",
                        settings_key="bounty_activated",
                        embed={
                            "title": "🎯 Bounty Activated!",
                            "description": (
                                f"Your bounty target **{target_name}** started studying.\n\n"
                                f"Both of you now have a **2.0× XP buff** for **2 hours**."
                            ),
                            "color": int(COLOR_GOLD),
                        },
                    )

            session_id = self.bot.db.start_session(
                uid,
                subject,
                target,
                source_guild_id=interaction.guild_id,
                allowed_member=is_allowed_member,
                tags=tags,
            )
            session = self.bot.db.get_active_session(uid)

        subject_display = subject[:200] if len(subject) > 200 else subject
        embed = build_session_embed(
            session,
            bundle_line=self._session_bundle_line(session),
            buff_line=self._session_buff_line(session),
        )
        embed.title = f"📚 Session Started — {subject_display}"
        if target:
            embed.description = f"Goal: **{fmt_mins(target)}** · You've got this!"
        embed.set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar.url)

        view = self._session_panel_views.get(uid)
        if view is None:
            view = StudySessionControlsView(self, uid)
            self._session_panel_views[uid] = view
        msg = await interaction.followup.send(embed=embed, view=view, ephemeral=True, wait=True)
        self.bot.db.set_session_live_message(session_id, msg.channel.id, msg.id)
        self._start_live_task(uid)
        self._start_inactivity_monitor(uid)
        if target and target >= 10:
            self._ensure_motivation_task(uid)

        # Quest: target set
        if target:
            quest_cog = self.bot.cogs.get("Quests")
            if quest_cog:
                await quest_cog.track_quest(uid, "target_set")

    @study.command(name="switch", description="Switch subjects mid-session (keeps one timer running)")
    @app_commands.describe(
        subject="New subject name",
        tags="Optional tags for this subject segment (comma-separated)",
    )
    async def study_switch(self, interaction: discord.Interaction, subject: str, tags: str = ""):
        uid = interaction.user.id
        await interaction.response.defer(ephemeral=True)
        async with self.bot.user_locks[uid]:
            session = self.bot.db.get_active_session(uid)
            if not session:
                await interaction.followup.send("❌ No active session. Use `/study start` first.", ephemeral=True)
                return
            if session.get("is_paused"):
                await interaction.followup.send("⏸️ You’re paused. Resume first, then switch subjects.", ephemeral=True)
                return
            ok = self.bot.db.switch_active_session_segment(uid, subject=subject.strip()[:200] or "General", tags=tags)
            if not ok:
                await interaction.followup.send("❌ Couldn’t switch subjects. Try again.", ephemeral=True)
                return
            session = self.bot.db.get_active_session(uid)
            if session:
                await self._edit_live_session_message(uid, session=session)
        await interaction.followup.send(f"✅ Switched to **{subject.strip()[:200] or 'General'}**.", ephemeral=True)

    @study_start.autocomplete("subject")
    async def study_start_subject_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        self.bot.db.ensure_user(interaction.user.id, str(interaction.user))
        sessions = self.bot.db.get_user_sessions(interaction.user.id, limit=50)
        seen: dict[str, int] = {}
        for s in sessions:
            subj = (s.get("subject") or "General").strip()
            if subj:
                seen[subj] = seen.get(subj, 0) + 1
        sorted_subjects = sorted(seen.keys(), key=lambda x: (-seen[x], x))
        choices = []
        for subj in sorted_subjects:
            if current.lower() in subj.lower() or current == "":
                label = f"{subj} ({seen[subj]}×)" if seen[subj] > 1 else subj
                choices.append(app_commands.Choice(name=label[:100], value=subj))
        return choices[:25]

    @study.command(name="pause", description="Pause your session timer")
    async def study_pause(self, interaction: discord.Interaction):
        uid = interaction.user.id
        session = self.bot.db.get_active_session(uid)
        if not session:
            await interaction.response.send_message("❌ No active session.", ephemeral=True); return
        if session.get("is_paused"):
            await interaction.response.send_message("⏸️ Already paused. Use `/study resume`.", ephemeral=True); return

        self.bot.db.pause_session(uid)
        self._cancel_live_task(uid)
        session = self.bot.db.get_active_session(uid)

        await self._edit_live_session_message(uid, session=session)

        active_secs, _ = get_elapsed_and_paused(session)
        embed = discord.Embed(title="⏸️ Session Paused", description="Timer stopped. Resume when ready.", color=0xFEE75C)
        embed.add_field(name="Active Time So Far", value=fmt_mins(active_secs // 60))
        embed.set_footer(text="Use /study resume to continue")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @study.command(name="resume", description="Resume your paused session")
    async def study_resume(self, interaction: discord.Interaction):
        uid = interaction.user.id
        session = self.bot.db.get_active_session(uid)
        if not session:
            await interaction.response.send_message("❌ No active session.", ephemeral=True); return
        if not session.get("is_paused"):
            await interaction.response.send_message("▶️ Session isn't paused!", ephemeral=True); return

        pause_secs = self.bot.db.resume_session(uid)
        session = self.bot.db.get_active_session(uid)

        await self._edit_live_session_message(uid, session=session)

        self._start_live_task(uid)
        if (session.get("target_minutes") or 0) >= 10:
            self._ensure_motivation_task(uid)

        # Quest: pause/resume
        quest_cog = self.bot.cogs.get("Quests")
        if quest_cog:
            await quest_cog.track_quest(uid, "pause_resume")

        embed = discord.Embed(title="▶️ Session Resumed!", description="Timer running again. Let's go! 💪", color=0x57F287)
        embed.add_field(name="Break Was", value=fmt_secs(pause_secs or 0), inline=True)
        active_secs, _ = get_elapsed_and_paused(session)
        embed.add_field(name="Active Time", value=fmt_mins(active_secs // 60), inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @study.command(name="stop", description="End your session and collect XP")
    @app_commands.describe(notes="Optional session notes")
    async def study_stop(self, interaction: discord.Interaction, notes: str = ""):
        await self._stop_session(interaction, notes=notes)

    @study.command(name="note", description="Add a note to your current session")
    @app_commands.describe(text="Your note")
    async def study_note(self, interaction: discord.Interaction, text: str):
        uid = interaction.user.id
        if not self.bot.db.add_session_note(uid, text):
            await interaction.response.send_message("❌ No active session.", ephemeral=True); return
        embed = discord.Embed(title="📝 Note Added", description=text, color=0x5865F2)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        session = self.bot.db.get_active_session(uid)
        if session:
            await self._edit_live_session_message(uid, session=session)

        quest_cog = self.bot.cogs.get("Quests")
        if quest_cog:
            await quest_cog.track_quest(uid, "session_note")

    @study.command(name="extend", description="Add more minutes to your session target")
    @app_commands.describe(minutes="Extra minutes to add")
    async def study_extend(self, interaction: discord.Interaction, minutes: app_commands.Range[int, 5, 480]):
        new_target = self.bot.db.extend_session_target(interaction.user.id, minutes)
        if new_target is None:
            await interaction.response.send_message("❌ No active session.", ephemeral=True); return
        if new_target >= 10:
            self._ensure_motivation_task(interaction.user.id)
        embed = discord.Embed(title="⏱️ Target Extended!", description=f"New target: **{fmt_mins(new_target)}**", color=0x57F287)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        session = self.bot.db.get_active_session(interaction.user.id)
        if session:
            await self._edit_live_session_message(interaction.user.id, session=session)

    @study.command(name="status", description="Refresh your live session status")
    async def study_status(self, interaction: discord.Interaction):
        session = self.bot.db.get_active_session(interaction.user.id)
        if not session:
            await interaction.response.send_message("📭 No active session. Use `/study start`!", ephemeral=True); return
        embed = build_session_embed(
            session,
            bundle_line=self._session_bundle_line(session),
            buff_line=self._session_buff_line(session),
        )
        uid = interaction.user.id
        view = self._session_panel_views.get(uid)
        if view is None:
            view = StudySessionControlsView(self, uid)
            self._session_panel_views[uid] = view
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        msg = await interaction.original_response()
        self.bot.db.set_session_live_message(session["id"], msg.channel.id, msg.id)
        if not session.get("is_paused"):
            self._start_live_task(interaction.user.id)

    @study.command(name="history", description="View recent study sessions")
    async def study_history(self, interaction: discord.Interaction):
        self.bot.db.ensure_user(interaction.user.id, str(interaction.user))
        sessions = self.bot.db.get_user_sessions(interaction.user.id, limit=8)
        total = self.bot.db.get_total_study_minutes(interaction.user.id)

        embed = discord.Embed(title="📚 Study History", color=0x5865F2)
        if not sessions:
            embed.description = "No sessions yet! Use `/study start`."
        else:
            for s in sessions:
                try:
                    started_utc = parse_stored(s["started_at"]).replace(tzinfo=timezone.utc)
                    date_est = fmt_weekday_datetime_us_est(started_utc)
                except Exception:
                    date_est = fmt_date_us_from_iso((s.get("started_at") or "")[:10])
                paused_note = f" (⏸️ {fmt_secs(s['total_paused_seconds'])} paused)" if s.get("total_paused_seconds", 0) > 60 else ""
                rating_str = ""
                if s.get("focus_rating"):
                    stars = "⭐" * s["focus_rating"]
                    rating_str = f" · {stars}"
                pts = s.get("points_earned", s.get("duration_minutes", 0))
                embed.add_field(
                    name=f"📅 {date_est} EST — {s['subject'] or 'General'}{'📝' if s.get('notes') else ''}",
                    value=f"⏱️ {fmt_mins(s['duration_minutes'])}{paused_note} · ⭐ {s['xp_earned']} XP · 💎 {pts} pts{rating_str}",
                    inline=False
                )
        embed.set_footer(text=f"Total study time: {fmt_mins(total)}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ──── /streak & /goals ────────────────────────────────────────────────────

    @app_commands.command(name="streak", description="View your study streak and stats")
    async def streak(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        uid = interaction.user.id
        self.bot.db.ensure_user(uid, str(interaction.user))
        await self.bot._check_adaptive_goal(uid)

        user = self.bot.db.get_user(uid)
        total = self.bot.db.get_total_study_minutes(uid)
        weekly = self.bot.db.get_weekly_study_minutes(uid)
        avg_rating = self.bot.db.get_average_focus_rating(uid)
        freezes = self.bot.db.get_freezes(uid)

        s = user["streak"]
        if s>=30:   tier,color="🏆 Legendary",0xFFD700
        elif s>=14: tier,color="💎 Diamond",0x4FC3F7
        elif s>=7:  tier,color="🔥 On Fire",0xFF6B35
        elif s>=3:  tier,color="⚡ Building",0xFEE75C
        elif s>=1:  tier,color="🌱 Starting",0x57F287
        else:       tier,color="😴 Inactive",0x747F8D

        embed = discord.Embed(title=f"🔥 Study Streak — {tier}", color=color)
        embed.set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar.url)
        embed.add_field(name="Current Streak", value=f"🔥 **{s} days**", inline=True)
        embed.add_field(name="Longest Streak", value=f"🏆 **{user['longest_streak']} days**", inline=True)

        freeze_display = f"🧊 ×{freezes['count']}" if freezes["count"] > 0 else "None"
        embed.add_field(name="Streak Freezes", value=freeze_display, inline=True)

        embed.add_field(name="Total XP", value=f"⭐ {user['total_xp']:,}", inline=True)
        embed.add_field(name="This Week", value=fmt_mins(weekly), inline=True)
        embed.add_field(name="All Time", value=fmt_mins(total), inline=True)
        embed.add_field(name="💎 Points", value=f"{user['points']:,}", inline=True)
        embed.add_field(name="🪙 Coins", value=str(user.get("coins", 0)), inline=True)
        if avg_rating:
            stars = "⭐" * round(avg_rating)
            embed.add_field(name="🎯 Avg Focus", value=f"{stars} {avg_rating}/5", inline=True)

        embed.set_footer(text="Sat/Sun never break your streak • Study ≥20m on weekends to earn freezes")
        await interaction.followup.send(embed=embed, ephemeral=True)

    goals = app_commands.Group(name="goals", description="Manage your study goals")

    @goals.command(name="set", description="Set your default daily study goal")
    @app_commands.describe(minutes="Minutes per day (e.g. 90)")
    async def goals_set(self, interaction: discord.Interaction, minutes: app_commands.Range[int, 5, 720]):
        self.bot.db.ensure_user(interaction.user.id, str(interaction.user))
        self.bot.db.set_daily_goal(interaction.user.id, minutes)
        embed = discord.Embed(title="🎯 Daily Goal Set!", description=f"Your default goal is **{fmt_mins(minutes)}**.", color=0x57F287)
        embed.set_footer(text="Set per-day goals with /goals set-day")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @goals.command(name="set-day", description="Set a goal for a specific day of the week")
    @app_commands.describe(day="Day of the week", minutes="Goal in minutes (0 = rest day, no tracking)")
    @app_commands.choices(day=[
        app_commands.Choice(name="Monday", value="mon"),
        app_commands.Choice(name="Tuesday", value="tue"),
        app_commands.Choice(name="Wednesday", value="wed"),
        app_commands.Choice(name="Thursday", value="thu"),
        app_commands.Choice(name="Friday", value="fri"),
        app_commands.Choice(name="Saturday", value="sat"),
        app_commands.Choice(name="Sunday", value="sun"),
    ])
    async def goals_set_day(self, interaction: discord.Interaction, day: str, minutes: app_commands.Range[int, 0, 720]):
        self.bot.db.ensure_user(interaction.user.id, str(interaction.user))
        self.bot.db.set_day_goal(interaction.user.id, day, minutes)
        if minutes == 0:
            msg = f"🛌 **{day.capitalize()}** is now a rest day — no goal tracking that day."
        else:
            msg = f"🎯 **{day.capitalize()}** goal set to **{fmt_mins(minutes)}**."
        await interaction.response.send_message(msg, ephemeral=True)

    @goals.command(name="view", description="View all your daily goals")
    async def goals_view(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        self.bot.db.ensure_user(interaction.user.id, str(interaction.user))
        await self.bot._check_adaptive_goal(interaction.user.id)
        user = self.bot.db.get_user(interaction.user.id)
        default = user["daily_goal_minutes"]
        days = [("Mon","mon"),("Tue","tue"),("Wed","wed"),("Thu","thu"),("Fri","fri"),("Sat","sat"),("Sun","sun")]
        embed = discord.Embed(title="🎯 Your Goals", color=0x5865F2)
        embed.add_field(name="Default", value=fmt_mins(default), inline=False)
        lines = []
        for name, key in days:
            override = user.get(f"goal_{key}")
            if override is not None:
                display = "🛌 Rest Day" if override == 0 else fmt_mins(override)
                lines.append(f"**{name}**: {display}")
            else:
                lines.append(f"**{name}**: _{fmt_mins(default)} (default)_")
        embed.add_field(name="Per-Day", value="\n".join(lines), inline=False)
        adaptive = "✅ On" if user.get("adaptive_goals", 1) else "❌ Off"
        embed.add_field(name="Adaptive Suggestions", value=adaptive, inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @goals.command(name="progress", description="Check today's study progress")
    async def goals_progress(self, interaction: discord.Interaction):
        self.bot.db.ensure_user(interaction.user.id, str(interaction.user))
        today_iso = datetime.now(EST).date().isoformat()
        today_mins = self.bot.db.get_study_minutes_on_date(interaction.user.id, today_iso)
        goal = self.bot.db.get_today_goal(interaction.user.id)

        active = self.bot.db.get_active_session(interaction.user.id)
        if active:
            active_secs, _ = get_elapsed_and_paused(active)
            today_mins += active_secs // 60

        if goal == 0:
            embed = discord.Embed(title="🛌 Rest Day", description="No goal set for today — enjoy the break!", color=0x747F8D)
            if today_mins > 0:
                embed.add_field(name="Studied anyway?", value=f"Nice! {fmt_mins(today_mins)} logged.", inline=False)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        pct = min(int(today_mins / goal * 100), 100)
        bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
        color = 0x57F287 if pct >= 100 else (0xFEE75C if pct >= 50 else 0xED4245)
        embed = discord.Embed(title="🎯 Today's Progress", color=color)
        embed.add_field(name=f"{pct}% Complete", value=f"`{bar}`\n{fmt_mins(today_mins)} / {fmt_mins(goal)}", inline=False)
        if pct < 100:
            embed.add_field(name="⏳ Remaining", value=fmt_mins(max(goal - today_mins, 0)), inline=True)
        else:
            embed.add_field(name="Status", value="✅ Goal reached! 🎉", inline=True)
        if active:
            active_mins = get_elapsed_and_paused(active)[0] // 60
            embed.add_field(
                name="🟢 Active Session",
                value=f"**{active['subject']}** — {fmt_mins(active_mins)}" + (" ⏸️" if active.get("is_paused") else ""),
                inline=False
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ─── Inactivity View ─────────────────────────────────────────────────────────

class InactivityView(discord.ui.View):
    def __init__(self, bot, user_id: int):
        super().__init__(timeout=30 * 60)
        self.bot = bot
        self.user_id = user_id
        self.responded = False
        self.message: discord.Message | None = None

    @discord.ui.button(label="Still studying!", style=discord.ButtonStyle.success, emoji="📚")
    async def still_studying(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.responded = True
        await interaction.response.edit_message(
            embed=discord.Embed(description="✅ Great, keep going! I'll check in again later.", color=0x57F287),
            view=None
        )
        self.stop()

    @discord.ui.button(label="Stop my session", style=discord.ButtonStyle.danger, emoji="⏹️")
    async def stop_session(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.responded = True
        study_cog = self.bot.cogs.get("Study")
        if study_cog:
            await study_cog._auto_stop_session(self.user_id, "Stopped via inactivity check")
        await interaction.response.edit_message(
            embed=discord.Embed(description="⏹️ Session stopped. Full rewards awarded!", color=0x57F287),
            view=None
        )
        self.stop()

    @discord.ui.button(label="15 more minutes", style=discord.ButtonStyle.secondary, emoji="⏰")
    async def snooze(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.responded = True
        await interaction.response.edit_message(
            embed=discord.Embed(description="⏰ Got it! I'll check again in 15 minutes.", color=0xFEE75C),
            view=None
        )
        self.stop()
        await asyncio.sleep(15 * 60)
        study_cog = self.bot.cogs.get("Study")
        if study_cog:
            session = self.bot.db.get_active_session(self.user_id)
            if session:
                study_cog._start_inactivity_monitor(self.user_id)


# ─── Focus Rating View ────────────────────────────────────────────────────────

class SessionRatingView(discord.ui.View):
    def __init__(self, bot, user_id: int, session_id: int, break_mins: int):
        super().__init__(timeout=300)
        self.bot = bot
        self.user_id = user_id
        self.session_id = session_id
        self.break_mins = break_mins
        self.message: discord.Message | None = None
        for rating in range(1, 6):
            label = f"{'⭐' * rating}"
            btn = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary)
            btn.callback = self._make_callback(rating)
            self.add_item(btn)

    async def on_timeout(self):
        if self.message:
            try:
                await self.message.edit(view=None)
            except Exception:
                pass

    def _make_callback(self, rating: int):
        async def callback(interaction: discord.Interaction):
            self.bot.db.set_session_rating(self.session_id, rating)
            label = STAR_LABELS.get(rating, "")
            embed = discord.Embed(
                title=f"{'⭐' * rating} Focus rated: {label}",
                description="Rating saved! This helps track your best study times. 📊",
                color=0x57F287
            )
            view = BreakSuggestionView(self.bot, self.user_id, self.break_mins)
            embed.set_footer(text=f"Suggested break: {self.break_mins} min")
            await interaction.response.edit_message(embed=embed, view=view)
            try:
                view.message = await interaction.original_response()
            except Exception:
                pass
            self.stop()
        return callback


class BreakSuggestionView(discord.ui.View):
    def __init__(self, bot, user_id: int, suggested_mins: int):
        super().__init__(timeout=60)
        self.bot = bot
        self.user_id = user_id
        self.suggested_mins = suggested_mins
        self.message: discord.Message | None = None

    async def on_timeout(self):
        if self.message:
            try:
                await self.message.edit(view=None)
            except Exception:
                pass

    @discord.ui.button(label="Start break timer", style=discord.ButtonStyle.secondary, emoji="☕")
    async def start_break(self, interaction: discord.Interaction, button: discord.ui.Button):
        fire_at = datetime.now(timezone.utc) + timedelta(seconds=self.suggested_mins * 60)
        rid = self.bot.db.add_reminder(self.user_id, "☕ Break over! Time to get back to studying.", fire_at)
        self.bot._add_reminder_job({"id": rid, "user_id": self.user_id, "message": "☕ Break over!", "fire_at": fire_at})
        embed = discord.Embed(title=f"☕ Break timer started — {self.suggested_mins} min", description="I'll DM you when it's time!", color=0x57F287)
        await interaction.response.edit_message(embed=embed, view=None)
        self.stop()

    @discord.ui.button(label="No break needed", style=discord.ButtonStyle.secondary)
    async def no_break(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(view=None)
        self.stop()


async def setup(bot):
    await bot.add_cog(Study(bot))
