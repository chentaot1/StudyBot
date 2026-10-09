# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone, timedelta
import re
import logging
from zoneinfo import ZoneInfo
from services.scheduling import DEFAULT_TIMEZONE, valid_timezone

from constants import EST, COLOR_PRIMARY, COLOR_WARNING

log = logging.getLogger("StudyBot.Reminders")


def parse_time_est(s: str, timezone_name: str = DEFAULT_TIMEZONE) -> datetime | None:
    """
    Parse flexible time strings in the saved timezone (Eastern by default):
      Relative: 30m, 2h, 1d, 2h30m
      Time of day: 3:30pm, 15:30, tomorrow 9am
      Calendar (US-first): 6/5/2026 1:00pm, 06-05-2026 13:00, then ISO 2026-06-05 1:00pm
    Returns a timezone-aware datetime or None. Relative delays use elapsed UTC time.
    """
    s = s.strip().lower()
    zone = ZoneInfo(valid_timezone(timezone_name))
    now_est = datetime.now(zone)

    # Relative: 30m / 2h / 1d / 2h30m / 1d6h
    rel = re.match(r"^(?:(\d{1,4})d)?(?:(\d{1,4})h)?(?:(\d{1,4})m)?$", s)
    if rel and any(rel.groups()):
        d = int(rel.group(1) or 0)
        h = int(rel.group(2) or 0)
        m = int(rel.group(3) or 0)
        if d + h + m > 0:
            return (now_est.astimezone(timezone.utc) + timedelta(days=d, hours=h, minutes=m)).astimezone(zone)
        return now_est  # triggers the "in the past" guard in the caller

    # "tomorrow HH:MM" or "tomorrow H:MMam"
    if s.startswith("tomorrow"):
        time_part = s.replace("tomorrow", "").strip()
        base = now_est + timedelta(days=1)
        t = _parse_time_of_day(time_part, base)
        if t:
            return t

    # Absolute time of day: "3:30pm", "15:30", "9am"
    # If the time has already passed today, assume the user means tomorrow
    t = _parse_time_of_day(s, now_est)
    if t:
        if t > now_est:
            return t
        # Past — roll forward to tomorrow
        return _parse_time_of_day(s, now_est + timedelta(days=1))

    # Full date + time: US-style first, then ISO in the saved timezone.
    for fmt in (
        "%m/%d/%Y %I:%M%p",
        "%m/%d/%Y %I:%M %p",
        "%m/%d/%Y %H:%M",
        "%m-%d-%Y %I:%M%p",
        "%m-%d-%Y %I:%M %p",
        "%m-%d-%Y %H:%M",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %I:%M%p",
        "%Y-%m-%d %I:%M %p",
    ):
        try:
            dt = datetime.strptime(s, fmt)
            result = dt.replace(tzinfo=zone, fold=0)
            if result.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != dt:
                return None
            return result
        except ValueError:
            pass

    return None


def _parse_time_of_day(s: str, base: datetime) -> datetime | None:
    """Parse a wall-clock time, rejecting nonexistent daylight-saving times."""
    s = s.strip()
    for fmt in ("%I:%M%p", "%I%p", "%H:%M", "%I:%M %p", "%I %p"):
        try:
            t = datetime.strptime(s, fmt)
            result = base.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
            if result.astimezone(timezone.utc).astimezone(base.tzinfo).replace(tzinfo=None) != result.replace(tzinfo=None):
                return None
            return result
        except ValueError:
            pass
    return None


def fmt_est(dt: datetime) -> str:
    """US-readable Eastern timestamp (weekday, month name, MDY, 12h)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    est_dt = dt.astimezone(EST)
    h = int(est_dt.strftime("%I"))
    return (
        f"{est_dt.strftime('%A')}, {est_dt.strftime('%B')} {est_dt.day}, {est_dt.year} "
        f"at {h}{est_dt.strftime(':%M %p')} EST"
    )


class Reminders(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    remind = app_commands.Group(name="remind", description="Manage your reminders")

    # ── /remind add ───────────────────────────────────────────────────────────

    @remind.command(name="add", description="Set a reminder using a quick duration or your saved timezone")
    @app_commands.describe(
        when="When: 30m, 2h, 3:30pm, tomorrow 9am, 6/5/2026 1:00pm, or 2026-06-05 13:00",
        message="What to remind you about"
    )
    async def remind_add(self, interaction: discord.Interaction, when: str, message: app_commands.Range[str, 1, 200]):
        zone = await self.bot.db_worker.run(self.bot.db.get_setting, interaction.user.id, "timezone", DEFAULT_TIMEZONE)
        fire_at = parse_time_est(when, zone)
        if fire_at is None:
            await interaction.response.send_message(f"Use 30m, tomorrow 9am, or 10/31/2026 13:00. Wall-clock times use {zone}; nonexistent daylight-saving times are rejected.", ephemeral=True)
            return
        await self.save_reminder(interaction, fire_at, message)

    @remind.command(name="at", description="Set a reminder with Discord's date and time input")
    @app_commands.describe(when="Choose a Discord timestamp", message="What to remind you about")
    async def remind_at(self, interaction: discord.Interaction, when: app_commands.Timestamp, message: app_commands.Range[str, 1, 200]):
        await self.save_reminder(interaction, when, message)

    async def save_reminder(self, interaction, fire_at, message):
        fire_at = fire_at.astimezone(timezone.utc)
        if fire_at <= datetime.now(timezone.utc):
            await interaction.response.send_message("Choose a future time.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        uid = interaction.user.id
        await self.bot.db_worker.run(self.bot.db.ensure_user, uid, str(interaction.user))
        rid = await self.bot.db_worker.run(self.bot.db.add_reminder, uid, message, fire_at)
        self.bot._add_reminder_job({"id": rid, "user_id": uid, "message": message, "fire_at": fire_at})
        stamp = discord.utils.format_dt(fire_at)
        relative = discord.utils.format_dt(fire_at, "R")
        await interaction.followup.send(f"Reminder #{rid} saved for {stamp} ({relative}).\n{message}", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    # ── /remind list ──────────────────────────────────────────────────────────

    @remind.command(name="list", description="View your upcoming reminders")
    async def remind_list(self, interaction: discord.Interaction):
        (await self.bot.db_worker.run(lambda: self.bot.db.ensure_user(interaction.user.id, str(interaction.user))))
        reminders = (await self.bot.db_worker.run(lambda: self.bot.db.get_user_reminders(interaction.user.id)))

        if not reminders:
            await interaction.response.send_message(
                "📭 No upcoming reminders. Set one with `/remind add`!", ephemeral=True
            )
            return

        embed = discord.Embed(title="⏰ Your Reminders", color=COLOR_PRIMARY)
        now = datetime.now(timezone.utc)
        for r in reminders[:10]:
            raw_ts = r["fire_at"].split("+")[0].split("Z")[0]
            fire_utc = datetime.fromisoformat(raw_ts).replace(tzinfo=timezone.utc)
            delta_s = int((fire_utc - now).total_seconds())
            if delta_s < 3600:
                tl = f"{delta_s // 60}m"
            elif delta_s < 86400:
                tl = f"{delta_s // 3600}h {(delta_s % 3600) // 60}m"
            else:
                tl = f"{delta_s // 86400}d {(delta_s % 86400) // 3600}h"
            embed.add_field(
                name=f"#{r['id']} — in {tl}",
                value=f"📌 {r['message']}\n🕐 {discord.utils.format_dt(fire_utc)}",
                inline=False
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /remind delete ────────────────────────────────────────────────────────

    @remind.command(name="delete", description="Delete a reminder by ID")
    @app_commands.describe(reminder_id="Reminder to delete (from /remind list)")
    async def remind_delete(self, interaction: discord.Interaction, reminder_id: int):
        deleted = (await self.bot.db_worker.run(lambda: self.bot.db.delete_reminder(reminder_id, interaction.user.id)))
        if deleted:
            try:
                self.bot.scheduler.remove_job(f"reminder_{reminder_id}")
            except Exception:
                pass
            await interaction.response.send_message(f"🗑️ Reminder `#{reminder_id}` deleted.", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ Reminder `#{reminder_id}` not found.", ephemeral=True)

    @remind_delete.autocomplete("reminder_id")
    async def remind_delete_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        (await self.bot.db_worker.run(lambda: self.bot.db.ensure_user(interaction.user.id, str(interaction.user))))
        reminders = (await self.bot.db_worker.run(lambda: self.bot.db.get_user_reminders(interaction.user.id)))
        now = datetime.now(timezone.utc)
        choices = []
        for r in reminders:
            # Build a short "fires in X" label for display
            try:
                raw_ts = r["fire_at"].split("+")[0].split("Z")[0]
                fire_utc = datetime.fromisoformat(raw_ts).replace(tzinfo=timezone.utc)
                delta_s = max(int((fire_utc - now).total_seconds()), 0)
                if delta_s < 3600:
                    tl = f"{delta_s // 60}m"
                elif delta_s < 86400:
                    tl = f"{delta_s // 3600}h {(delta_s % 3600) // 60}m"
                else:
                    tl = f"{delta_s // 86400}d {(delta_s % 86400) // 3600}h"
                label = f"#{r['id']} (in {tl}) — {r['message']}"
            except Exception:
                label = f"#{r['id']} — {r['message']}"

            if current.lower() in r["message"].lower() or current == str(r["id"]) or current == "":
                choices.append(app_commands.Choice(name=label[:100], value=r["id"]))
        return choices[:25]


async def setup(bot):
    await bot.add_cog(Reminders(bot))
