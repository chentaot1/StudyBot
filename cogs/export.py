# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

import discord
from discord import app_commands
from discord.ext import commands
import csv
import io
import zipfile
from datetime import datetime, timezone
import logging

from constants import EST, COLOR_SUCCESS
from utils import fmt_date_us_from_iso, fmt_datetime_us_est, parse_stored

log = logging.getLogger("StudyBot.Export")


def make_csv(headers: list[str], rows: list[dict]) -> io.BytesIO:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=headers, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return io.BytesIO(buf.getvalue().encode("utf-8"))


class Export(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="export", description="Download all your data as CSV files in a zip")
    async def export(self, interaction: discord.Interaction):
        self.bot.db.ensure_user(interaction.user.id, str(interaction.user))
        await interaction.response.defer(ephemeral=True)

        uid = interaction.user.id

        # ── Sessions ──────────────────────────────────────────────────────────
        sessions = self.bot.db.get_all_sessions(uid)
        session_rows = []
        for s in sessions:
            # Convert started_at to EST for readability
            try:
                started_utc = datetime.fromisoformat(s["started_at"]).replace(tzinfo=timezone.utc)
                started_est = fmt_datetime_us_est(started_utc, with_seconds=True)
            except Exception:
                started_est = s["started_at"]
            try:
                ended_utc = datetime.fromisoformat(s["ended_at"]).replace(tzinfo=timezone.utc)
                ended_est = fmt_datetime_us_est(ended_utc, with_seconds=True)
            except Exception:
                ended_est = s.get("ended_at", "")

            session_rows.append({
                "id": s["id"],
                "subject": s.get("subject") or "General",
                "started_at_est": started_est,
                "ended_at_est": ended_est,
                "duration_minutes": s.get("duration_minutes") or 0,
                "xp_earned": s.get("xp_earned") or 0,
                "focus_rating": s.get("focus_rating") or "",
                "target_minutes": s.get("target_minutes") or "",
                "total_paused_seconds": s.get("total_paused_seconds") or 0,
                "notes": (s.get("notes") or "").replace("\n", " | "),
            })

        session_csv = make_csv(
            ["id","subject","started_at_est","ended_at_est","duration_minutes","xp_earned","focus_rating","target_minutes","total_paused_seconds","notes"],
            session_rows
        )

        # ── Tasks ─────────────────────────────────────────────────────────────
        tasks = self.bot.db.get_user_tasks(uid, include_done=True)
        task_rows = []
        for t in tasks:
            task_rows.append({
                "id": t["id"],
                "title": t["title"],
                "description": t.get("description") or "",
                "points": t["points"],
                "priority": t["priority"],
                "project": t.get("project_name") or "",
                "is_review": "yes" if t.get("is_review") else "no",
                "review_interval_days": t.get("review_interval") or "",
                "due_date": fmt_date_us_from_iso(t["due_date"]) if t.get("due_date") else "",
                "completed": "yes" if t["completed"] else "no",
                "completed_at": fmt_date_us_from_iso((t.get("completed_at") or "")[:10]) if t.get("completed_at") else "",
                "created_at": fmt_date_us_from_iso((t.get("created_at") or "")[:10]) if t.get("created_at") else "",
            })

        task_csv = make_csv(
            ["id","title","description","points","priority","project","is_review","review_interval_days","due_date","completed","completed_at","created_at"],
            task_rows
        )

        # ── Point transactions ────────────────────────────────────────────────
        transactions = self.bot.db.get_point_history(uid, limit=10000)
        tx_rows = []
        for tx in reversed(transactions):
            raw_ca = tx.get("created_at") or ""
            try:
                ca_disp = fmt_datetime_us_est(
                    parse_stored(raw_ca).replace(tzinfo=timezone.utc), with_seconds=True
                )
            except Exception:
                ca_disp = raw_ca[:19] if raw_ca else ""
            tx_rows.append({
                "id": tx["id"],
                "delta": tx["delta"],
                "reason": tx.get("reason") or "",
                "created_at": ca_disp,
            })

        tx_csv = make_csv(["id","delta","reason","created_at"], tx_rows)

        # ── Redemptions ───────────────────────────────────────────────────────
        redemptions = self.bot.db.get_all_redemptions(uid)
        redemption_rows = []
        for r in redemptions:
            raw_r = r.get("redeemed_at") or ""
            try:
                r_disp = fmt_datetime_us_est(
                    parse_stored(raw_r).replace(tzinfo=timezone.utc), with_seconds=True
                )
            except Exception:
                r_disp = raw_r[:19] if raw_r else ""
            redemption_rows.append({
                "id": r["id"],
                "reward_name": r["reward_name"],
                "cost": r["cost"],
                "redeemed_at": r_disp,
            })
        redemption_csv = make_csv(["id","reward_name","cost","redeemed_at"], redemption_rows)

        # ── Check-ins ─────────────────────────────────────────────────────────
        checkins = self.bot.db.get_checkin_history(uid, days=365)
        checkin_rows = []
        for c in checkins:
            checkin_rows.append({
                "date": fmt_date_us_from_iso(c["date"]),
                "studied": "yes" if c["studied"] else "no",
                "note": (c.get("note") or "").replace("\n", " | "),
            })
        checkin_csv = make_csv(["date","studied","note"], checkin_rows)

        # ── Badges ────────────────────────────────────────────────────────────
        badges = self.bot.db.get_badges(uid)
        badge_rows = []
        for b in badges:
            raw_e = b.get("earned_at") or ""
            try:
                e_disp = fmt_datetime_us_est(
                    parse_stored(raw_e).replace(tzinfo=timezone.utc), with_seconds=True
                ) if raw_e else ""
            except Exception:
                e_disp = raw_e or ""
            badge_rows.append({"badge_key": b["badge_key"], "tier": b["tier"], "earned_at": e_disp})
        badge_csv = make_csv(["badge_key", "tier", "earned_at"], badge_rows)

        # ── Gacha ────────────────────────────────────────────────────────────
        gacha = self.bot.db.get_gacha_history(uid, limit=10000)
        gacha_rows = []
        for g in gacha:
            raw_g = g.get("created_at") or ""
            try:
                g_disp = fmt_datetime_us_est(
                    parse_stored(raw_g).replace(tzinfo=timezone.utc), with_seconds=True
                )
            except Exception:
                g_disp = raw_g or ""
            gacha_rows.append({
                "tier": g["tier"], "cost_coins": g["cost_coins"], "result_pts": g["result_pts"],
                "is_jackpot": g.get("is_jackpot", 0), "created_at": g_disp,
            })
        gacha_csv = make_csv(["tier", "cost_coins", "result_pts", "is_jackpot", "created_at"], gacha_rows)

        # ── Pack into zip ─────────────────────────────────────────────────────
        zip_buf = io.BytesIO()
        now_str = datetime.now(EST).strftime("%m%d%Y_%H%M")
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"sessions_{now_str}.csv", session_csv.getvalue())
            zf.writestr(f"tasks_{now_str}.csv", task_csv.getvalue())
            zf.writestr(f"transactions_{now_str}.csv", tx_csv.getvalue())
            zf.writestr(f"redemptions_{now_str}.csv", redemption_csv.getvalue())
            zf.writestr(f"checkins_{now_str}.csv", checkin_csv.getvalue())
            zf.writestr(f"badges_{now_str}.csv", badge_csv.getvalue())
            zf.writestr(f"gacha_{now_str}.csv", gacha_csv.getvalue())
        zip_buf.seek(0)

        embed = discord.Embed(
            title="💾 Data Export Ready",
            description="Your StudyBot data — open in Excel, Google Sheets, or Notion.",
            color=COLOR_SUCCESS
        )
        embed.add_field(name="📚 Sessions", value=str(len(session_rows)), inline=True)
        embed.add_field(name="✅ Tasks", value=str(len(task_rows)), inline=True)
        embed.add_field(name="💰 Transactions", value=str(len(tx_rows)), inline=True)
        embed.add_field(name="🎁 Redemptions", value=str(len(redemption_rows)), inline=True)
        embed.add_field(name="📋 Check-ins", value=str(len(checkin_rows)), inline=True)
        embed.add_field(name="🏅 Badges", value=str(len(badge_rows)), inline=True)
        embed.set_footer(text=f"Exported {fmt_datetime_us_est(datetime.now(EST))}")

        await interaction.followup.send(
            embed=embed,
            file=discord.File(zip_buf, filename=f"studybot_export_{now_str}.zip"),
            ephemeral=True
        )


async def setup(bot):
    await bot.add_cog(Export(bot))
