# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime
import logging

from utils import fmt_date_us_from_iso, parse_due_date
from constants import EST, COLOR_PRIMARY, COLOR_SUCCESS

log = logging.getLogger("StudyBot")  # (Customize logger name per file)


class Projects(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    project = app_commands.Group(name="project", description="Manage study projects")

    # ── Shared autocomplete helper ────────────────────────────────────────────

    async def _autocomplete_project_id(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        self.bot.db.ensure_user(interaction.user.id, str(interaction.user))
        projects = self.bot.db.get_projects(interaction.user.id)
        today = datetime.now(EST).date()
        choices = []
        for p in projects:
            label = f"#{p['id']} — {p['name']}"
            if p.get("due_date"):
                due = datetime.strptime(p["due_date"], "%Y-%m-%d").date()
                days_left = (due - today).days
                label += f" ({days_left}d left)" if days_left >= 0 else " (overdue)"
            if current.lower() in p["name"].lower() or current == str(p["id"]) or current == "":
                choices.append(app_commands.Choice(name=label[:100], value=p["id"]))
        return choices[:25]

    # ── /project add ──────────────────────────────────────────────────────────

    @project.command(name="add", description="Create a new project to group tasks under")
    @app_commands.describe(
        name="Project name (e.g. 'Chem Midterm', 'Essay')",
        due_date="Deadline: MM/DD, MM-DD-YYYY, MM/DD/YYYY, or YYYY-MM-DD",
        description="Optional description"
    )
    async def project_add(
        self,
        interaction: discord.Interaction,
        name: str,
        due_date: str = "",
        description: str = ""
    ):
        self.bot.db.ensure_user(interaction.user.id, str(interaction.user))

        parsed_due = None
        if due_date:
            parsed_due = parse_due_date(due_date)
            if not parsed_due:
                await interaction.response.send_message(
                    "❌ Invalid date. Use **MM/DD**, **MM/DD/YYYY**, **MM-DD-YYYY**, or **YYYY-MM-DD**.",
                    ephemeral=True,
                )
                return

        pid = self.bot.db.add_project(interaction.user.id, name, description, parsed_due)

        embed = discord.Embed(title="📁 Project Created!", color=COLOR_PRIMARY)
        embed.add_field(name="Name", value=name, inline=True)
        if parsed_due:
            today = datetime.now(EST).date()
            due = datetime.strptime(parsed_due, "%Y-%m-%d").date()
            days_left = (due - today).days
            embed.add_field(
                name="📅 Deadline",
                value=f"{fmt_date_us_from_iso(parsed_due)} ({days_left}d)",
                inline=True,
            )
        if description:
            embed.add_field(name="Description", value=description, inline=False)
        embed.set_footer(text=f"Project #{pid} • Add tasks with /task add project_id:{pid}")
        await interaction.response.send_message(embed=embed)

    # ── /project list ─────────────────────────────────────────────────────────

    @project.command(name="list", description="View all your active projects")
    async def project_list(self, interaction: discord.Interaction):
        self.bot.db.ensure_user(interaction.user.id, str(interaction.user))
        projects = self.bot.db.get_projects(interaction.user.id)

        if not projects:
            await interaction.response.send_message("📭 No projects yet. Create one with `/project add`!", ephemeral=True)
            return

        embed = discord.Embed(title="📁 Your Projects", color=COLOR_PRIMARY)
        today = datetime.now(EST).date()

        for p in projects:
            stats = self.bot.db.get_project_task_stats(p["id"])
            pct = int(stats["done"] / stats["total"] * 100) if stats["total"] else 0
            bar_filled = pct // 10
            bar = "█" * bar_filled + "░" * (10 - bar_filled)

            due_str = ""
            urgency = ""
            if p["due_date"]:
                due = datetime.strptime(p["due_date"], "%Y-%m-%d").date()
                days_left = (due - today).days
                if days_left < 0:
                    due_str = f"⚠️ **Overdue by {-days_left}d**"
                elif days_left == 0:
                    due_str = "🔥 **Due today!**"
                elif days_left <= 3:
                    due_str = f"🔴 Due in {days_left}d"
                    urgency = " 🔴"
                elif days_left <= 7:
                    due_str = f"🟡 Due in {days_left}d"
                else:
                    due_str = f"📅 {fmt_date_us_from_iso(p['due_date'])} ({days_left}d)"

                if stats["remaining"] > 0 and days_left > 0:
                    tasks_per_day = stats["remaining"] / days_left
                    if tasks_per_day > 3:
                        urgency += " ⚡"

            value_parts = [
                f"`{bar}` {pct}% — {stats['done']}/{stats['total']} tasks",
            ]
            if due_str:
                value_parts.append(due_str)
            if stats["pts_remaining"] > 0:
                value_parts.append(f"💰 {stats['pts_remaining']} pts remaining")

            embed.add_field(
                name=f"📁 #{p['id']} {p['name']}{urgency}",
                value="\n".join(value_parts),
                inline=False
            )
        embed.set_footer(text="/project view <id> for detailed breakdown")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /project view ─────────────────────────────────────────────────────────

    @project.command(name="view", description="View a project's details and tasks")
    @app_commands.describe(project_id="Project ID from /project list")
    async def project_view(self, interaction: discord.Interaction, project_id: int):
        self.bot.db.ensure_user(interaction.user.id, str(interaction.user))
        project = self.bot.db.get_project(project_id, interaction.user.id)
        if not project:
            await interaction.response.send_message(f"❌ Project `#{project_id}` not found.", ephemeral=True)
            return

        stats = self.bot.db.get_project_task_stats(project_id)
        tasks = self.bot.db.get_user_tasks(interaction.user.id, include_done=True, project_id=project_id)
        today = datetime.now(EST).date()

        embed = discord.Embed(title=f"📁 {project['name']}", color=COLOR_PRIMARY)
        if project.get("description"):
            embed.description = project["description"]

        pct = int(stats["done"] / stats["total"] * 100) if stats["total"] else 0
        bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
        embed.add_field(name="Progress", value=f"`{bar}` {pct}%\n{stats['done']}/{stats['total']} tasks", inline=False)

        if project["due_date"]:
            due = datetime.strptime(project["due_date"], "%Y-%m-%d").date()
            days_left = (due - today).days
            embed.add_field(
                name="📅 Deadline",
                value=f"{fmt_date_us_from_iso(project['due_date'])} ({'⚠️ Overdue' if days_left < 0 else f'{days_left}d left'})",
                inline=True
            )

            if stats["remaining"] > 0 and days_left > 0:
                tasks_per_day = stats["remaining"] / days_left
                embed.add_field(
                    name="⚡ Pace Needed",
                    value=f"{tasks_per_day:.1f} tasks/day to finish on time",
                    inline=True
                )
            elif days_left <= 0 and stats["remaining"] > 0:
                embed.add_field(
                    name="⚠️ Behind Schedule",
                    value=f"{stats['remaining']} tasks still pending!",
                    inline=True
                )

        if stats["pts_remaining"] > 0:
            embed.add_field(name="💰 Points Remaining", value=str(stats["pts_remaining"]), inline=True)

        pending = [t for t in tasks if not t["completed"]]
        done_tasks = [t for t in tasks if t["completed"]]

        if pending:
            lines = []
            for t in pending[:8]:
                icon = "🔁" if t["is_review"] else {"high":"🔴","medium":"🟡","low":"🟢"}.get(t["priority"],"📌")
                due_note = ""
                if t["due_date"]:
                    td = datetime.strptime(t["due_date"], "%Y-%m-%d").date()
                    diff = (td - today).days
                    if diff < 0: due_note = " ⚠️"
                    elif diff == 0: due_note = " 🔥"
                lines.append(f"{icon} `#{t['id']}` {t['title']}{due_note}")
            if len(pending) > 8:
                lines.append(f"_...and {len(pending)-8} more_")
            embed.add_field(name=f"⬜ {len(pending)} Pending", value="\n".join(lines), inline=False)

        if done_tasks:
            embed.add_field(name=f"✅ {len(done_tasks)} Completed", value=f"Great progress!", inline=True)

        embed.set_footer(text=f"Project #{project_id} • Add tasks: /task add project_id:{project_id}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @project_view.autocomplete("project_id")
    async def project_view_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return await self._autocomplete_project_id(interaction, current)

    # ── /project done ─────────────────────────────────────────────────────────

    @project.command(name="done", description="Mark a project as complete")
    @app_commands.describe(project_id="Project ID to complete")
    async def project_done(self, interaction: discord.Interaction, project_id: int):
        project = self.bot.db.get_project(project_id, interaction.user.id)
        if not project:
            await interaction.response.send_message(f"❌ Project `#{project_id}` not found.", ephemeral=True)
            return
        self.bot.db.complete_project(project_id, interaction.user.id)
        embed = discord.Embed(
            title="🎉 Project Complete!",
            description=f"**{project['name']}** is done! Great work.",
            color=COLOR_SUCCESS
        )
        await interaction.response.send_message(embed=embed)

    @project_done.autocomplete("project_id")
    async def project_done_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return await self._autocomplete_project_id(interaction, current)

    # ── /project delete ───────────────────────────────────────────────────────

    @project.command(name="delete", description="Delete a project (tasks are NOT deleted)")
    @app_commands.describe(project_id="Project ID to delete")
    async def project_delete(self, interaction: discord.Interaction, project_id: int):
        project = self.bot.db.get_project(project_id, interaction.user.id)
        if not project:
            await interaction.response.send_message(f"❌ Project `#{project_id}` not found.", ephemeral=True)
            return
        view = ConfirmDeleteView(self.bot, project_id, interaction.user.id, project["name"])
        await interaction.response.send_message(
            f"⚠️ Delete project **{project['name']}**? Tasks will be unlinked but not deleted.",
            view=view, ephemeral=True
        )
        view.message = await interaction.original_response()

    @project_delete.autocomplete("project_id")
    async def project_delete_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return await self._autocomplete_project_id(interaction, current)


class ConfirmDeleteView(discord.ui.View):
    def __init__(self, bot, project_id: int, user_id: int, name: str):
        super().__init__(timeout=30)
        self.bot = bot
        self.project_id = project_id
        self.user_id = user_id
        self.name = name
        self.message: discord.Message | None = None

    async def on_timeout(self):
        if self.message:
            try:
                await self.message.edit(content="Timed out.", view=None)
            except Exception:
                pass

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.bot.db.delete_project(self.project_id, self.user_id)
        await interaction.response.edit_message(content=f"🗑️ Project **{self.name}** deleted.", view=None)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Cancelled.", view=None)
        self.stop()


async def setup(bot):
    await bot.add_cog(Projects(bot))
