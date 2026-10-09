# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

import discord
from discord import app_commands
from discord.ext import commands
from services.scheduling import DEFAULT_TIMEZONE, valid_timezone
import logging

from constants import EST, COLOR_PRIMARY

log = logging.getLogger("StudyBot")  # (Customize logger name per file)


ALL_DAYS = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]
DAY_ABBRS = {
    "m":"monday","mo":"monday","mon":"monday","monday":"monday",
    "t":"tuesday","tu":"tuesday","tue":"tuesday","tues":"tuesday","tuesday":"tuesday",
    "w":"wednesday","we":"wednesday","wed":"wednesday","wednesday":"wednesday",
    "h":"thursday","th":"thursday","thu":"thursday","thur":"thursday","thurs":"thursday","thursday":"thursday",
    "f":"friday","fr":"friday","fri":"friday","friday":"friday",
    "sa":"saturday","sat":"saturday","saturday":"saturday",
    "su":"sunday","sun":"sunday","sunday":"sunday",
}
SHORTCUTS = {
    "daily":      ALL_DAYS,
    "everyday":   ALL_DAYS,
    "weekdays":   ["monday","tuesday","wednesday","thursday","friday"],
    "weekday":    ["monday","tuesday","wednesday","thursday","friday"],
    "weekend":    ["saturday","sunday"],
    "weekends":   ["saturday","sunday"],
    "mwf":        ["monday","wednesday","friday"],
    "tth":        ["tuesday","thursday"],
    "tuth":       ["tuesday","thursday"],
    "mw":         ["monday","wednesday"],
    "mwth":       ["monday","wednesday","thursday"],
}


def parse_days(raw: str) -> list[str] | None:
    s = raw.strip().lower().replace(" ", "")

    if s in SHORTCUTS:
        return SHORTCUTS[s]

    tokens = [t.strip() for t in s.replace("/", ",").split(",") if t.strip()]

    if len(tokens) == 1 and tokens[0] not in DAY_ABBRS:
        token = tokens[0]
        parsed = _split_abbreviations(token)
        if parsed is not None:
            return sorted(set(parsed), key=lambda d: ALL_DAYS.index(d))

    result = []
    for tok in tokens:
        day = DAY_ABBRS.get(tok)
        if day is None:
            return None
        result.append(day)
    return sorted(set(result), key=lambda d: ALL_DAYS.index(d)) if result else None


def _split_abbreviations(s: str) -> list[str] | None:
    if not s:
        return []
    for length in (2, 1):
        if len(s) >= length:
            prefix = s[:length]
            if prefix in DAY_ABBRS:
                rest = _split_abbreviations(s[length:])
                if rest is not None:
                    return [DAY_ABBRS[prefix]] + rest
    return None


# Discord embed field values are max 1024 chars; keep headroom for long schedules.
_SCHED_FIELD_SAFE = 1000
_MAX_SCHED_FIELDS = 23


def _chunk_schedule_lines(lines: list[str], max_len: int = _SCHED_FIELD_SAFE) -> list[str]:
    """Split a day's block list into one or more embed-safe strings."""
    if not lines:
        return [""]
    chunks: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for line in lines:
        sep = 1 if cur else 0
        add = sep + len(line)
        if cur and cur_len + add > max_len:
            chunks.append("\n".join(cur))
            cur = [line]
            cur_len = len(line)
        else:
            if cur:
                cur_len += sep
            cur.append(line)
            cur_len += len(line)
    if cur:
        chunks.append("\n".join(cur))
    return chunks


def fmt_days(days_str: str) -> str:
    days = [d.strip().capitalize() for d in days_str.split(",") if d.strip()]
    abbrs = {"Monday":"Mon","Tuesday":"Tue","Wednesday":"Wed","Thursday":"Thu",
             "Friday":"Fri","Saturday":"Sat","Sunday":"Sun"}
    short = [abbrs.get(d, d) for d in days]
    if len(short) == 7:
        return "Every day"
    if short == ["Mon","Tue","Wed","Thu","Fri"]:
        return "Weekdays"
    if short == ["Sat","Sun"]:
        return "Weekends"
    if short == ["Mon","Wed","Fri"]:
        return "MWF"
    if short == ["Tue","Thu"]:
        return "Tue/Thu"
    return " · ".join(short)


class Schedule(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    sched = app_commands.Group(
        name="schedule",
        description="Weekly study blocks in your timezone — reminders follow /settings",
    )

    # ── /schedule add ─────────────────────────────────────────────────────────

    @sched.command(name="add", description="Add a recurring study block (supports multiple days)")
    @app_commands.describe(
        subject="What you'll be studying",
        days="Days — e.g. MWF · weekdays · daily · Mon,Wed,Fri · Tue/Thu · Saturday",
        hour="Start hour in your saved timezone (0–23)",
        minute="Start minute (0–59)",
        duration="Duration in minutes (default 60)"
    )
    async def schedule_add(
        self,
        interaction: discord.Interaction,
        subject: app_commands.Range[str, 0, 100] = "",
        days: str = "",
        hour: app_commands.Range[int, 0, 23] = 9,
        minute: app_commands.Range[int, 0, 59] = 0,
        duration: app_commands.Range[int, 15, 480] = 60
    ):
        if not subject.strip() or not days.strip():
            await self.open_schedule_form(interaction)
            return
        await interaction.response.defer(ephemeral=True)
        (await self.bot.db_worker.run(lambda: self.bot.db.ensure_user(interaction.user.id, str(interaction.user))))

        parsed = parse_days(days)
        if not parsed:
            await interaction.followup.send(
                "❌ Couldn't understand those days. Try:\n"
                "• `MWF` `TTh` `weekdays` `weekends` `daily`\n"
                "• `Mon,Wed,Fri` `Tuesday,Thursday` `Saturday`\n"
                "• `Mon/Wed/Fri`",
                ephemeral=True
            )
            return

        existing = (await self.bot.db_worker.run(lambda: self.bot.db.get_user_schedule(interaction.user.id)))
        if len(existing) >= 25:
            await interaction.followup.send("❌ Max 25 schedule blocks reached.", ephemeral=True)
            return

        days_str = ",".join(parsed)
        block_id = (await self.bot.db_worker.run(lambda: self.bot.db.add_schedule_block(
            interaction.user.id, subject, days_str, hour, minute, duration,
            timezone_name=self.bot.db.get_setting(interaction.user.id, "timezone", DEFAULT_TIMEZONE)
        )))

        end_min = minute + duration
        end_hour = (hour + end_min // 60) % 24
        end_min = end_min % 60

        embed = discord.Embed(title="📅 Schedule Block Added!", color=COLOR_PRIMARY)
        embed.add_field(name="Subject", value=subject, inline=True)
        embed.add_field(name="Days", value=fmt_days(days_str), inline=True)
        embed.add_field(
            name="Local start time",
            value=f"{hour:02d}:{minute:02d} – {end_hour:02d}:{end_min:02d}",
            inline=True
        )
        embed.add_field(name="Duration", value=f"{duration} min", inline=True)
        embed.set_footer(
            text=f"Block #{block_id} • DM at the saved local start time — enable “Schedule Reminders” in /settings if needed."
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def open_schedule_form(self, interaction):
        from views.forms import ScheduleModal
        zone = await self.bot.db_worker.run(self.bot.db.get_setting, interaction.user.id, "timezone", DEFAULT_TIMEZONE)
        await interaction.response.send_modal(ScheduleModal(self.bot, interaction.user.id, zone))

    @sched.command(name="create", description="Choose study days and times in a guided form")
    async def schedule_create(self, interaction: discord.Interaction):
        await self.open_schedule_form(interaction)

    # ── /schedule view ────────────────────────────────────────────────────────

    @sched.command(name="view", description="View your weekly study schedule (splits across pages if long)")
    async def schedule_view(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        (await self.bot.db_worker.run(lambda: self.bot.db.ensure_user(interaction.user.id, str(interaction.user))))
        blocks = (await self.bot.db_worker.run(lambda: self.bot.db.get_user_schedule(interaction.user.id)))

        if not blocks:
            await interaction.followup.send(
                "📭 No schedule blocks yet. Add one with `/schedule add`!", ephemeral=True
            )
            return

        by_day: dict[str, list] = {d: [] for d in ALL_DAYS}
        for b in blocks:
            days_list = [d.strip() for d in (b["days_of_week"] or "").split(",") if d.strip()]
            for day in days_list:
                if day in by_day:
                    by_day[day].append(b)

        fields: list[tuple[str, str]] = []
        for day in ALL_DAYS:
            day_blocks = by_day[day]
            if not day_blocks:
                continue
            lines = []
            for b in day_blocks:
                end_min = b["minute"] + b["duration_minutes"]
                end_hour = (b["hour"] + end_min // 60) % 24
                end_min = end_min % 60
                days_display = fmt_days(b["days_of_week"])
                lines.append(
                    f"`#{b['id']}` **{b['subject']}** "
                    f"{b['hour']:02d}:{b['minute']:02d}–{end_hour:02d}:{end_min:02d} "
                    f"({b['duration_minutes']}min · {b.get('timezone') or DEFAULT_TIMEZONE})"
                    + (f" · _{days_display}_" if "·" in days_display or days_display not in ("Every day","Weekdays","Weekends","MWF","Tue/Thu") else "")
                )
            day_chunks = _chunk_schedule_lines(lines)
            for pi, chunk in enumerate(day_chunks):
                name = f"📆 {day.capitalize()}"
                if len(day_chunks) > 1:
                    name += f" ({pi + 1}/{len(day_chunks)})"
                fields.append((name, chunk))

        total = sum(b["duration_minutes"] for b in blocks)
        footer = f"{len(blocks)} blocks • ~{total // 60}h {total % 60}m scheduled/week"

        batches: list[list[tuple[str, str]]] = []
        batch: list[tuple[str, str]] = []
        for f in fields:
            batch.append(f)
            if len(batch) >= _MAX_SCHED_FIELDS:
                batches.append(batch)
                batch = []
        if batch:
            batches.append(batch)

        for bi, bfields in enumerate(batches):
            title = "📅 Your Weekly Schedule"
            if len(batches) > 1:
                title = f"📅 Your schedule ({bi + 1}/{len(batches)})"
            embed = discord.Embed(title=title, color=COLOR_PRIMARY)
            for name, val in bfields:
                embed.add_field(name=name, value=val, inline=False)
            if bi == len(batches) - 1:
                embed.set_footer(text=footer)
            await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /schedule delete ──────────────────────────────────────────────────────

    @sched.command(name="delete", description="Remove a schedule block by ID")
    @app_commands.describe(block_id="Schedule block to remove (from /schedule view)")
    async def schedule_delete(self, interaction: discord.Interaction, block_id: int):
        block = None
        all_blocks = (await self.bot.db_worker.run(lambda: self.bot.db.get_user_schedule(interaction.user.id)))
        for b in all_blocks:
            if b["id"] == block_id:
                block = b
                break
        if not block:
            await interaction.response.send_message(f"❌ Block `#{block_id}` not found.", ephemeral=True)
            return
        (await self.bot.db_worker.run(lambda: self.bot.db.delete_schedule_block(block_id, interaction.user.id)))
        try:
            self.bot.scheduler.remove_job(f"proc_{interaction.user.id}_{block_id}")
        except Exception:
            pass
        await interaction.response.send_message(
            f"🗑️ Removed **{block['subject']}** ({fmt_days(block['days_of_week'])}).", ephemeral=True
        )

    @schedule_delete.autocomplete("block_id")
    async def schedule_delete_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        (await self.bot.db_worker.run(lambda: self.bot.db.ensure_user(interaction.user.id, str(interaction.user))))
        blocks = (await self.bot.db_worker.run(lambda: self.bot.db.get_user_schedule(interaction.user.id)))
        choices = []
        for b in blocks:
            days_label = fmt_days(b["days_of_week"])
            label = f"#{b['id']} — {b['subject']} · {days_label} {b['hour']:02d}:{b['minute']:02d} ({b['duration_minutes']}min)"
            if current.lower() in b["subject"].lower() or current == str(b["id"]) or current == "":
                choices.append(app_commands.Choice(name=label[:100], value=b["id"]))
        return choices[:25]


async def setup(bot):
    await bot.add_cog(Schedule(bot))
