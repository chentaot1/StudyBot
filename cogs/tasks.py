# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timedelta
from typing import Optional
import logging
import re

from constants import EST, PRI_EMOJI, PRI_COLOR, COLOR_PRIMARY, COLOR_SUCCESS, COLOR_WARNING, COLOR_ERROR
from utils import fmt_date_us_from_iso, next_srs_interval, parse_due_date

log = logging.getLogger("StudyBot.Tasks")

_MAX_BATCH_COMPLETE = 15


def _parse_bulk_task_ids(raw: str) -> tuple[list[int], Optional[str]]:
    """Parse comma/space-separated task numbers; dedupe preserving order. Max _MAX_BATCH_COMPLETE."""
    parts = [p for p in re.split(r"[\s,]+", (raw or "").strip()) if p]
    if not parts:
        return [], "Provide task numbers separated by commas or spaces (e.g. `1,2,3`)."
    out: list[int] = []
    seen: set[int] = set()
    for p in parts:
        try:
            n = int(p)
        except ValueError:
            return [], f"Not a number: `{p}`"
        if n <= 0:
            return [], f"Invalid task #: `{n}`"
        if n in seen:
            continue
        seen.add(n)
        out.append(n)
        if len(out) > _MAX_BATCH_COMPLETE:
            return [], f"Maximum **{_MAX_BATCH_COMPLETE}** tasks per batch."
    return out, None


class Tasks(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    task = app_commands.Group(name="task", description="Manage your study tasks")

    # ── Shared autocomplete helpers ───────────────────────────────────────────

    async def _autocomplete_project_id(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        (await self.bot.db_worker.run(lambda: self.bot.db.ensure_user(interaction.user.id, str(interaction.user))))
        projects = (await self.bot.db_worker.run(lambda: self.bot.db.get_projects(interaction.user.id)))
        choices = []
        for p in projects:
            label = f"#{p['id']} — {p['name']}"
            if p.get("due_date"):
                label += f" (due {fmt_date_us_from_iso(p['due_date'])})"
            if current == "" or current.lower() in p["name"].lower() or current == str(p["id"]):
                choices.append(app_commands.Choice(name=label[:100], value=p["id"]))
        return choices[:25]

    # ── /task add ─────────────────────────────────────────────────────────────

    @task.command(name="add", description="Add a task to your list")
    @app_commands.describe(
        title="Task name",
        points="Points on completion (1–500)",
        priority="Priority level",
        description="Optional description",
        due_date="Due date: MM/DD, MM-DD-YYYY, MM/DD/YYYY, or YYYY-MM-DD",
        project_id="Link to a project ID (from /project list)",
        review="Make this a spaced-repetition review task",
        interval="Starting SRS interval in days (default 1)"
    )
    @app_commands.choices(priority=[
        app_commands.Choice(name="🔴 High", value="high"),
        app_commands.Choice(name="🟡 Medium", value="medium"),
        app_commands.Choice(name="🟢 Low", value="low"),
    ])
    async def task_add(
        self,
        interaction: discord.Interaction,
        title: app_commands.Range[str, 0, 100] = "",
        points: app_commands.Range[int, 1, 500] = 10,
        priority: str = "medium",
        description: app_commands.Range[str, 0, 500] = "",
        due_date: str = "",
        project_id: Optional[int] = None,
        review: bool = False,
        interval: app_commands.Range[int, 1, 60] = 1
    ):
        if not title.strip():
            await self.open_task_form(interaction)
            return
        await interaction.response.defer()
        (await self.bot.db_worker.run(lambda: self.bot.db.ensure_user(interaction.user.id, str(interaction.user))))

        parsed_due = None
        if due_date:
            parsed_due = parse_due_date(due_date)
            if not parsed_due:
                await interaction.followup.send(
                    "❌ Invalid date. Use **MM/DD**, **MM/DD/YYYY**, **MM-DD-YYYY**, or **YYYY-MM-DD**.",
                    ephemeral=True,
                )
                return

        if project_id:
            project = (await self.bot.db_worker.run(lambda: self.bot.db.get_project(project_id, interaction.user.id)))
            if not project:
                await interaction.followup.send(f"❌ Project `#{project_id}` not found.", ephemeral=True)
                return

        task_id = (await self.bot.db_worker.run(lambda: self.bot.db.add_task(
            interaction.user.id, title, description, points, priority, parsed_due,
            project_id=project_id, is_review=review, review_interval=interval
        )))

        embed = discord.Embed(title=f"{'🔁' if review else PRI_EMOJI[priority]} Task Added", color=PRI_COLOR[priority])
        embed.add_field(name="Task", value=title, inline=False)
        embed.add_field(name="💰 Points", value=str(points), inline=True)
        embed.add_field(name="Priority", value=priority.capitalize(), inline=True)
        if project_id:
            project = (await self.bot.db_worker.run(lambda: self.bot.db.get_project(project_id, interaction.user.id)))
            embed.add_field(name="📁 Project", value=project["name"], inline=True)
        if review:
            embed.add_field(name="🔁 SRS Review", value=f"Repeats every {interval}d → {next_srs_interval(interval)}d → ...", inline=False)
        if parsed_due:
            embed.add_field(name="📅 Due", value=fmt_date_us_from_iso(parsed_due), inline=True)
        if description:
            embed.add_field(name="Notes", value=description, inline=False)
        embed.set_footer(text=f"Task #{task_id} • Complete with /task complete {task_id}")
        await interaction.followup.send(embed=embed)

        quest_cog = self.bot.cogs.get("Quests")
        if quest_cog:
            await quest_cog.track_quest(interaction.user.id, "task_added")

    @task_add.autocomplete("project_id")
    async def task_add_project_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return await self._autocomplete_project_id(interaction, current)

    async def open_task_form(self, interaction, *, title="", description="", source_url=None):
        from views.forms import TaskModal
        projects = await self.bot.db_worker.run(self.bot.db.get_projects, interaction.user.id)
        await interaction.response.send_modal(TaskModal(self.bot, interaction.user.id, projects, title=title, description=description, source_url=source_url))

    @task.command(name="create", description="Create a task with a guided form")
    async def task_create(self, interaction: discord.Interaction):
        await self.open_task_form(interaction)

    # ── /task list ────────────────────────────────────────────────────────────

    @task.command(name="list", description="View your pending tasks")
    @app_commands.describe(show_completed="Also show completed tasks", project_id="Filter by project")
    async def task_list(self, interaction: discord.Interaction, show_completed: bool = False, project_id: Optional[int] = None):
        await interaction.response.defer(ephemeral=True)
        (await self.bot.db_worker.run(lambda: self.bot.db.ensure_user(interaction.user.id, str(interaction.user))))
        tasks = (await self.bot.db_worker.run(lambda: self.bot.db.get_user_tasks(interaction.user.id, include_done=show_completed, project_id=project_id)))

        if not tasks:
            await interaction.followup.send("✅ No pending tasks! Add one with `/task add`.", ephemeral=True)
            return

        embed = discord.Embed(title="📋 Tasks", color=0x5865F2)
        now = datetime.now(EST).date()
        pending = [t for t in tasks if not t["completed"]]

        by_project: dict = {}
        for t in tasks[:25]:
            proj = t.get("project_name") or "No Project"
            by_project.setdefault(proj, []).append(t)

        for proj, proj_tasks in by_project.items():
            lines = []
            for t in proj_tasks:
                status = "✅" if t["completed"] else ("🔁" if t["is_review"] else PRI_EMOJI.get(t["priority"], "📌"))
                due_note = ""
                if t["due_date"] and not t["completed"]:
                    due = datetime.strptime(t["due_date"], "%Y-%m-%d").date()
                    diff = (due - now).days
                    if diff < 0:   due_note = f" ⚠️ **Overdue {-diff}d**"
                    elif diff == 0: due_note = " 🔥 **Due today**"
                    elif diff <= 3: due_note = f" 📅 {diff}d"
                tnum = t.get("user_task_num") or t["id"]
                lines.append(f"{status} `#{tnum}` **{t['title']}**{due_note} — 💰 {t['points']}" + (f" · [source]({t['source_message_url']})" if t.get("source_message_url") else ""))
            embed.add_field(
                name=f"📁 {proj}" if proj != "No Project" else "📋 Tasks",
                value="\n".join(lines), inline=False
            )

        pts_avail = sum(t["points"] for t in pending)
        embed.set_footer(text=f"{len(pending)} pending · {pts_avail} pts available")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @task_list.autocomplete("project_id")
    async def task_list_project_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return await self._autocomplete_project_id(interaction, current)

    # ── /task complete ────────────────────────────────────────────────────────

    @task.command(name="complete", description="Mark a task done and earn points")
    @app_commands.describe(task_id="Task number from /task list")
    async def task_complete(self, interaction: discord.Interaction, task_id: int):
        await interaction.response.defer(ephemeral=True)
        async with self.bot.user_locks[interaction.user.id]:
            await self._task_complete_inner(interaction, task_id)

    async def _task_complete_inner(self, interaction: discord.Interaction, task_id: int):
        (await self.bot.db_worker.run(lambda: self.bot.db.ensure_user(interaction.user.id, str(interaction.user))))

        all_tasks = (await self.bot.db_worker.run(lambda: self.bot.db.get_user_tasks(interaction.user.id, include_done=False)))
        task = next((t for t in all_tasks if (t.get("user_task_num") or t["id"]) == task_id), None)
        if not task:
            await interaction.followup.send(f"❌ Task `#{task_id}` not found or already done.", ephemeral=True)
            return

        db_id = task["id"]

        new_interval = None
        next_date = None
        if task.get("is_review"):
            old_interval = task.get("review_interval") or 1
            new_interval = next_srs_interval(old_interval)
            next_date = (datetime.now(EST).date() + timedelta(days=new_interval)).strftime("%Y-%m-%d")

        result = (await self.bot.db_worker.run(lambda: self.bot.db.complete_task_with_review(db_id, interaction.user.id)))

        if not result:
            await interaction.followup.send("❌ Task completion failed.", ephemeral=True)
            return

        user_points = result.get("user_points", 0)
        user_total_xp = result.get("user_total_xp", 0)

        pts_earned = result.get("pts_earned", task["points"])
        xp_earned = result.get("xp_earned", 0)

        embed = discord.Embed(
            title="🎉 Task Complete!",
            description=f"**{task['title']}**",
            color=0x57F287
        )
        embed.add_field(name="Earned", value=f"💰 +{pts_earned} pts  ⭐ +{xp_earned} XP", inline=True)
        embed.add_field(name="Balance", value=f"💎 {user_points}", inline=True)
        embed.add_field(name="Total XP", value=f"⭐ {user_total_xp}", inline=True)

        if result.get("next_review_id") is not None:
            old_interval = task.get("review_interval") or 1
            new_interval = result["next_review_interval"]
            next_date = result["next_review_date"]
            embed.add_field(
                name="🔁 Review Rescheduled",
                value=f"Next review in **{new_interval} days** ({fmt_date_us_from_iso(next_date)})\n"
                      f"Interval: {old_interval}d → {new_interval}d",
                inline=False
            )

        if task.get("project_id"):
            stats = (await self.bot.db_worker.run(lambda: self.bot.db.get_project_task_stats(task["project_id"])))
            pct = int(stats["done"] / stats["total"] * 100) if stats["total"] else 0
            embed.add_field(
                name=f"📁 {task.get('project_name', 'Project')}",
                value=f"{stats['done']}/{stats['total']} tasks done ({pct}%)",
                inline=False
            )

        for m in [100, 250, 500, 1000, 2500, 5000]:
            if user_total_xp - xp_earned < m <= user_total_xp:
                embed.add_field(name="🎊 Milestone!", value=f"You've hit **{m} total XP**!", inline=False)

        view = UndoTaskView(
            self.bot,
            interaction.user.id,
            db_id,
            pts_earned,
            task.get("is_review", False),
            next_review_id=result.get("next_review_id"),
        )
        embed.set_footer(text="Made a mistake? You have 60 seconds to undo.")
        msg = await interaction.followup.send(embed=embed, view=view, wait=True, ephemeral=True)
        view.message = msg

        # Quest hooks
        if not getattr(self.bot, "is_lite_user", lambda _uid: False)(interaction.user.id):
            quest_cog = self.bot.cogs.get("Quests")
            if quest_cog:
                await quest_cog.track_quest(interaction.user.id, "task_completed")
                if task.get("is_review"):
                    await quest_cog.track_quest(interaction.user.id, "srs_review")
            badge_cog = self.bot.cogs.get("Badges")
            if badge_cog and task.get("is_review"):
                old_interval = int(task.get("review_interval") or 1)
                await badge_cog.check_srs_review_badges(interaction.user.id, interval_before_days=old_interval)

    @task.command(name="complete_many", description="Mark several tasks done at once (comma or space separated)")
    @app_commands.describe(task_ids="Task numbers, e.g. 1,2,3 or 1 2 3 (max 15)")
    async def task_complete_many(self, interaction: discord.Interaction, task_ids: str):
        await interaction.response.defer(ephemeral=True)
        async with self.bot.user_locks[interaction.user.id]:
            await self._task_complete_many_inner(interaction, task_ids)

    async def _task_complete_many_inner(self, interaction: discord.Interaction, task_ids: str):
        ids, err = _parse_bulk_task_ids(task_ids)
        if err:
            await interaction.followup.send(f"❌ {err}", ephemeral=True)
            return

        (await self.bot.db_worker.run(lambda: self.bot.db.ensure_user(interaction.user.id, str(interaction.user))))
        pending = (await self.bot.db_worker.run(lambda: self.bot.db.get_user_tasks(interaction.user.id, include_done=False)))
        by_num = {(t.get("user_task_num") or t["id"]): t for t in pending}
        missing = [n for n in ids if n not in by_num]
        if missing:
            await interaction.followup.send(
                "❌ Not found or already done: " + ", ".join(f"`#{n}`" for n in missing),
                ephemeral=True,
            )
            return

        undo_items: list[dict] = []
        lines: list[str] = []
        total_pts = 0
        total_xp = 0
        quest_cog = self.bot.cogs.get("Quests")
        badge_cog = self.bot.cogs.get("Badges")

        uid = interaction.user.id

        async def _rollback_batch(done: list[dict]):
            for item in reversed(done):
                t = (await self.bot.db_worker.run(lambda: self.bot.db.undo_task_complete(item["task_id"], uid)))
                if not t:
                    continue
                user_pre = (await self.bot.db_worker.run(lambda: self.bot.db.get_user(uid)))
                pts = item["pts_earned"]
                rev = min(pts, user_pre["points"]) if user_pre else 0
                (await self.bot.db_worker.run(lambda: self.bot.db.add_points(
                    uid, -rev, f"Rollback batch: {t['title']}", track_earned=False
                )))
                if item["is_review"]:
                    if item.get("next_review_id") is not None:
                        (await self.bot.db_worker.run(lambda: self.bot.db.delete_task(item["next_review_id"], uid)))
                    else:
                        pend = (await self.bot.db_worker.run(lambda: self.bot.db.get_user_tasks(uid, include_done=False)))
                        for row in pend:
                            if row["title"] == t["title"] and row["is_review"] and not row["completed"]:
                                (await self.bot.db_worker.run(lambda: self.bot.db.delete_task(row["id"], uid)))
                                break

        for n in ids:
            task = by_num[n]
            db_id = task["id"]
            result = (await self.bot.db_worker.run(lambda: self.bot.db.complete_task_with_review(db_id, interaction.user.id)))
            if not result:
                await _rollback_batch(undo_items)
                await interaction.followup.send(
                    f"❌ Task `#{n}` failed mid-batch — **earlier tasks were reverted**. Try `/task complete {n}`.",
                    ephemeral=True,
                )
                return
            pts = result.get("pts_earned", task["points"])
            xp = result.get("xp_earned", 0)
            total_pts += pts
            total_xp += xp
            icon = "🔁" if task.get("is_review") else PRI_EMOJI.get(task.get("priority"), "📌")
            lines.append(f"{icon} `#{n}` **{task['title'][:60]}** — +{pts} pts" + (f" · +{xp} XP" if xp else ""))
            undo_items.append({
                "task_id": db_id,
                "pts_earned": pts,
                "is_review": bool(task.get("is_review")),
                "next_review_id": result.get("next_review_id"),
                "title": task["title"],
            })
            if not getattr(self.bot, "is_lite_user", lambda _uid: False)(interaction.user.id):
                if quest_cog:
                    await quest_cog.track_quest(interaction.user.id, "task_completed")
                    if task.get("is_review"):
                        await quest_cog.track_quest(interaction.user.id, "srs_review")
                if badge_cog and task.get("is_review"):
                    old_interval = int(task.get("review_interval") or 1)
                    await badge_cog.check_srs_review_badges(interaction.user.id, interval_before_days=old_interval)

        user = (await self.bot.db_worker.run(lambda: self.bot.db.get_user(interaction.user.id)))
        embed = discord.Embed(
            title=f"🎉 {len(ids)} Tasks Complete!",
            color=COLOR_SUCCESS,
        )
        body = "\n".join(lines)
        if len(body) > 3500:
            body = "\n".join(lines[:12]) + f"\n_…and {len(lines) - 12} more_"
        embed.description = body
        embed.add_field(name="Total earned", value=f"💰 +{total_pts} pts  ⭐ +{total_xp} XP", inline=False)
        embed.add_field(name="Balance", value=f"💎 {user['points']:,}", inline=True)
        embed.add_field(name="Total XP", value=f"⭐ {user['total_xp']:,}", inline=True)

        view = UndoBatchTaskView(self.bot, interaction.user.id, undo_items) if undo_items else None
        embed.set_footer(text="Made a mistake? Undo reverses all of these within 60s.")
        msg = await interaction.followup.send(embed=embed, view=view, wait=True, ephemeral=True)
        if view:
            view.message = msg

    @task_complete.autocomplete("task_id")
    async def task_complete_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
        (await self.bot.db_worker.run(lambda: self.bot.db.ensure_user(interaction.user.id, str(interaction.user))))
        tasks = (await self.bot.db_worker.run(lambda: self.bot.db.get_user_tasks(interaction.user.id, include_done=False)))
        choices = []
        for t in tasks:
            tnum = t.get("user_task_num") or t["id"]
            if current.lower() in t["title"].lower() or current == str(tnum):
                pri = "🔁" if t["is_review"] else PRI_EMOJI.get(t["priority"], "📌")
                choices.append(app_commands.Choice(
                    name=f"{pri} #{tnum} — {t['title']} ({t['points']} pts)"[:100],
                    value=tnum
                ))
        return choices[:25]

    # ── /task delete ──────────────────────────────────────────────────────────

    @task.command(name="delete", description="Delete a task")
    @app_commands.describe(task_id="Task number to delete")
    async def task_delete(self, interaction: discord.Interaction, task_id: int):
        db_id = (await self.bot.db_worker.run(lambda: self.bot.db.resolve_task_num(interaction.user.id, task_id)))
        if db_id and (await self.bot.db_worker.run(lambda: self.bot.db.delete_task(db_id, interaction.user.id))):
            await interaction.response.send_message(f"🗑️ Task `#{task_id}` deleted.", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ Task `#{task_id}` not found.", ephemeral=True)

    @task_delete.autocomplete("task_id")
    async def task_delete_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
        (await self.bot.db_worker.run(lambda: self.bot.db.ensure_user(interaction.user.id, str(interaction.user))))
        tasks = (await self.bot.db_worker.run(lambda: self.bot.db.get_user_tasks(interaction.user.id, include_done=True)))
        choices = []
        for t in tasks:
            tnum = t.get("user_task_num") or t["id"]
            if current.lower() in t["title"].lower() or current == str(tnum):
                status = "✅" if t["completed"] else PRI_EMOJI.get(t["priority"], "📌")
                choices.append(app_commands.Choice(
                    name=f"{status} #{tnum} — {t['title']}"[:100],
                    value=tnum
                ))
        return choices[:25]

    # ── /task history ─────────────────────────────────────────────────────────

    @task.command(name="history", description="View recently completed tasks")
    async def task_history(self, interaction: discord.Interaction):
        tasks = (await self.bot.db_worker.run(lambda: self.bot.db.get_user_tasks(interaction.user.id, include_done=True)))
        done = [t for t in tasks if t["completed"]][-10:]
        if not done:
            await interaction.response.send_message("No completed tasks yet!", ephemeral=True)
            return
        embed = discord.Embed(title="✅ Completed Tasks", color=0x57F287)
        for t in reversed(done):
            date_raw = (t.get("completed_at") or "")[:10]
            date_show = fmt_date_us_from_iso(date_raw) if date_raw else "—"
            proj = f" [{t['project_name']}]" if t.get("project_name") else ""
            review_icon = "🔁 " if t.get("is_review") else ""
            embed.add_field(name=f"✅ {review_icon}{t['title']}{proj}", value=f"💰 {t['points']} pts · {date_show}", inline=False)
        embed.set_footer(text=f"{len(done)} shown")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /task reviews ─────────────────────────────────────────────────────────

    @task.command(name="reviews", description="View all your due SRS review tasks")
    async def task_reviews(self, interaction: discord.Interaction):
        (await self.bot.db_worker.run(lambda: self.bot.db.ensure_user(interaction.user.id, str(interaction.user))))
        reviews = (await self.bot.db_worker.run(lambda: self.bot.db.get_due_reviews(interaction.user.id)))
        if not reviews:
            await interaction.response.send_message("🎉 No reviews due! Check back later.", ephemeral=True)
            return
        embed = discord.Embed(
            title="🔁 Due Reviews",
            description=f"{len(reviews)} review(s) due today",
            color=0x5865F2
        )
        for r in reviews[:10]:
            rnum = r.get("user_task_num") or r["id"]
            embed.add_field(
                name=f"🔁 #{rnum} {r['title']}",
                value=f"💰 {r['points']} pts · Interval: {r['review_interval']}d" +
                      (f" · Project: {r['project_name']}" if r.get("project_name") else ""),
                inline=False
            )
        embed.set_footer(text="Complete with /task complete <id>")
        await interaction.response.send_message(embed=embed, ephemeral=True)


class UndoBatchTaskView(discord.ui.View):
    """Undo multiple completions in reverse order (matches single-task undo semantics)."""

    def __init__(self, bot, user_id: int, items: list[dict]):
        super().__init__(timeout=60)
        self.bot = bot
        self.user_id = user_id
        self.items = items
        self.message: discord.Message | None = None

    @discord.ui.button(label="↩️ Undo all", style=discord.ButtonStyle.secondary)
    async def undo(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your batch.", ephemeral=True)
            return
        for item in reversed(self.items):
            task = (await self.bot.db_worker.run(lambda: self.bot.db.undo_task_complete(item["task_id"], self.user_id)))
            if not task:
                await interaction.response.edit_message(
                    content="❌ Couldn't undo — a task may have changed. Check `/task list`.",
                    embed=None,
                    view=None,
                )
                self.stop()
                return
            user_pre = (await self.bot.db_worker.run(lambda: self.bot.db.get_user(self.user_id)))
            pts = item["pts_earned"]
            if user_pre and user_pre["points"] < pts:
                reversal = user_pre["points"]
            else:
                reversal = pts
            (await self.bot.db_worker.run(lambda: self.bot.db.add_points(
                self.user_id,
                -reversal,
                f"Undo batch: {task['title']}",
                track_earned=False,
            )))
            if item["is_review"]:
                if item.get("next_review_id") is not None:
                    (await self.bot.db_worker.run(lambda: self.bot.db.delete_task(item["next_review_id"], self.user_id)))
                else:
                    all_tasks = (await self.bot.db_worker.run(lambda: self.bot.db.get_user_tasks(self.user_id, include_done=False)))
                    for t in all_tasks:
                        if t["title"] == task["title"] and t["is_review"] and not t["completed"]:
                            (await self.bot.db_worker.run(lambda: self.bot.db.delete_task(t["id"], self.user_id)))
                            break

        user = (await self.bot.db_worker.run(lambda: self.bot.db.get_user(self.user_id)))
        embed = discord.Embed(
            title="↩️ Batch undone",
            description=f"Re-opened **{len(self.items)}** task(s).",
            color=COLOR_WARNING,
        )
        embed.add_field(name="Balance", value=f"💎 {user['points']:,}", inline=True)
        await interaction.response.edit_message(embed=embed, view=None)
        self.stop()

    async def on_timeout(self):
        if self.message:
            try:
                await self.message.edit(view=None)
            except Exception:
                pass


class UndoTaskView(discord.ui.View):
    def __init__(self, bot, user_id: int, task_id: int, points: int, is_review: bool, next_review_id: int | None = None):
        super().__init__(timeout=60)
        self.bot       = bot
        self.user_id   = user_id
        self.task_id   = task_id
        self.points    = points
        self.is_review = is_review
        self.next_review_id = next_review_id
        self.message: discord.Message | None = None

    @discord.ui.button(label="↩️ Undo", style=discord.ButtonStyle.secondary)
    async def undo(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your task.", ephemeral=True)
            return
        task = (await self.bot.db_worker.run(lambda: self.bot.db.undo_task_complete(self.task_id, self.user_id)))
        if not task:
            await interaction.response.edit_message(
                content="❌ Couldn't undo — task may have been modified.", view=None
            )
            self.stop()
            return

        user_pre = (await self.bot.db_worker.run(lambda: self.bot.db.get_user(self.user_id)))
        if user_pre and user_pre["points"] < self.points:
            reversal = user_pre["points"]
        else:
            reversal = self.points

        (await self.bot.db_worker.run(lambda: self.bot.db.add_points(
            self.user_id,
            -reversal,
            f"Undo task: {task['title']}",
            track_earned=False,
        )))

        if self.is_review:
            if self.next_review_id is not None:
                (await self.bot.db_worker.run(lambda: self.bot.db.delete_task(self.next_review_id, self.user_id)))
            else:
                all_tasks = (await self.bot.db_worker.run(lambda: self.bot.db.get_user_tasks(self.user_id, include_done=False)))
                for t in all_tasks:
                    if t["title"] == task["title"] and t["is_review"] and not t["completed"]:
                        (await self.bot.db_worker.run(lambda: self.bot.db.delete_task(t["id"], self.user_id)))
                        break
        user = (await self.bot.db_worker.run(lambda: self.bot.db.get_user(self.user_id)))
        embed = discord.Embed(
            title="↩️ Undone",
            description=f"**{task['title']}** marked as pending again.",
            color=0xFEE75C
        )
        embed.add_field(name="Points Reversed", value=f"-{reversal}", inline=True)
        embed.add_field(name="Balance", value=f"💎 {user['points']}", inline=True)
        await interaction.response.edit_message(embed=embed, view=None)
        self.stop()

    async def on_timeout(self):
        if hasattr(self, "message") and self.message:
            try:
                await self.message.edit(view=None)
            except Exception:
                pass


async def setup(bot):
    await bot.add_cog(Tasks(bot))
