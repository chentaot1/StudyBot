# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

"""A private Components V2 dashboard over the existing study data."""
from datetime import datetime, timezone
import discord

from constants import EST, COLOR_PRIMARY
from services.scheduling import next_block
from utils import fmt_mins
from views.forms import StartStudyModal, reply


def plain(value, limit=120):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(value)))[:limit]


def snapshot(bot, uid, show_rpg):
    db = bot.db
    date = datetime.now(EST).date().isoformat()
    data = {"date": date, "user": db.get_user(uid), "minutes": db.get_study_minutes_on_date(uid, date), "goal": db.get_today_goal(uid), "tasks": db.get_user_tasks(uid), "reviews": db.get_due_reviews(uid), "session": db.get_active_session(uid), "schedule": db.get_user_schedule(uid), "schedule_dms": db.get_dm_enabled(uid, "schedule_reminders")}
    if show_rpg:
        data.update(boss=db.get_active_boss(), quests=db.get_daily_quests(uid), potions=db.get_active_potions(uid))
    return data


class DashboardAction(discord.ui.Button):
    def __init__(self, view, action, label, *, style=discord.ButtonStyle.secondary):
        super().__init__(label=label, style=style)
        self.dashboard, self.action = view, action

    async def callback(self, interaction):
        bot = self.dashboard.bot
        if self.action == "study":
            await interaction.response.send_modal(StartStudyModal(bot, interaction.user.id))
        elif self.action == "pomo":
            cog = bot.get_cog("Pomodoro")
            await cog.pomo_start.callback(cog, interaction)
        elif self.action == "tasks":
            await bot.get_cog("Tasks").open_task_form(interaction)
        elif self.action == "schedule":
            await bot.get_cog("Schedule").open_schedule_form(interaction)
        elif self.action == "settings":
            from views.forms import PreferencesModal, preference_snapshot
            user, settings = await bot.db_worker.run(preference_snapshot, bot, interaction.user.id)
            await interaction.response.send_modal(PreferencesModal(bot, interaction.user.id, user, settings))
        elif self.action == "status":
            cog = bot.get_cog("Study")
            await cog.study_status.callback(cog, interaction)
        elif self.action == "refresh":
            await interaction.response.defer()
            view = await build_dashboard(bot, interaction)
            await interaction.edit_original_response(view=view)


class CompleteTaskSelect(discord.ui.Select):
    def __init__(self, bot, tasks):
        self.bot = bot
        super().__init__(placeholder="Complete a task or review…", options=[discord.SelectOption(label=f"#{task.get('user_task_num') or task['id']} · {task['title']}"[:100], value=str(task.get("user_task_num") or task["id"])) for task in tasks[:25]])

    async def callback(self, interaction):
        cog = self.bot.get_cog("Tasks")
        # Reuse existing validation, reward logic and undo behavior.
        await cog.task_complete.callback(cog, interaction, task_id=int(self.values[0]))


class TodayView(discord.ui.LayoutView):
    def __init__(self, bot, uid, data):
        super().__init__(timeout=900)
        self.bot, self.user_id = bot, uid
        goal, minutes = data["goal"], data["minutes"]
        progress = f"{fmt_mins(minutes)} / {fmt_mins(goal)} · {min(100, minutes * 100 // goal)}%" if goal else f"{fmt_mins(minutes)} studied · set a goal with /goals set"
        header = discord.ui.Container(discord.ui.TextDisplay("## Today"), discord.ui.TextDisplay(f"**Daily goal:** {progress}\n**Streak:** {data['user']['streak']} days\nStudy totals and due dates use US Eastern (ET)."), accent_colour=COLOR_PRIMARY)
        self.add_item(header)
        session = data["session"]
        if session:
            from cogs.study import get_elapsed_and_paused
            elapsed, _ = get_elapsed_and_paused(session)
            self.add_item(discord.ui.Section(discord.ui.TextDisplay(f"### Current session\n{plain(session.get('subject') or 'General')} · {fmt_mins(elapsed // 60)} active" + (" · paused" if session.get("is_paused") else "")), accessory=DashboardAction(self, "status", "Session controls")))
        tasks = data["tasks"]
        if tasks:
            date = data["date"]
            due = [task for task in tasks if task.get("due_date") and task["due_date"] <= date]
            reviews = data["reviews"]
            lines = [f"{len(tasks)} pending · {len(due)} due/overdue · {len(reviews)} reviews ready"]
            for task in (due + [t for t in tasks if t not in due])[:5]:
                source = f" · [source]({task['source_message_url']})" if task.get("source_message_url") else ""
                lines.append(f"• #{task.get('user_task_num') or task['id']} {plain(task['title'])}{source}")
            self.add_item(discord.ui.TextDisplay("### Tasks and reviews\n" + "\n".join(lines)))
            self.add_item(discord.ui.ActionRow(CompleteTaskSelect(bot, tasks)))
        upcoming = next_block(data["schedule"], datetime.now(timezone.utc))
        if upcoming:
            block, start = upcoming
            self.add_item(discord.ui.TextDisplay(f"### Next study block\n**{plain(block['subject'])}** · {discord.utils.format_dt(start)} ({discord.utils.format_dt(start, 'R')}) · {block['duration_minutes']} min" + ("\nSchedule reminders are off in /settings." if not data["schedule_dms"] else "")))
        boss = data.get("boss")
        quests = data.get("quests") or []
        rpg_lines = []
        if boss and boss.get("hp_remaining", 0) > 0:
            rpg_lines.append(f"**Raid:** {boss['hp_remaining']:,} / {boss['hp']:,} HP · /raid")
        if quests:
            rpg_lines.append("**Quests:** " + " · ".join(f"{plain(q['quest_key'].replace('_', ' '), 40)} {q['progress']}/{q['target']}" for q in quests[:3]))
        if data.get("potions"):
            rpg_lines.append(f"**Active potions:** {len(data['potions'])} · /inventory")
        if rpg_lines:
            self.add_item(discord.ui.TextDisplay("### Quests and raid\n" + "\n".join(rpg_lines)))
        self.add_item(discord.ui.Separator())
        self.add_item(discord.ui.ActionRow(DashboardAction(self, "study", "Start study", style=discord.ButtonStyle.primary), DashboardAction(self, "pomo", "Start Pomodoro"), DashboardAction(self, "refresh", "Refresh")))
        self.add_item(discord.ui.ActionRow(DashboardAction(self, "tasks", "Create task"), DashboardAction(self, "schedule", "Add study block"), DashboardAction(self, "settings", "Preferences")))

    async def interaction_check(self, interaction):
        if interaction.user.id != self.user_id:
            await reply(interaction, "This dashboard belongs to another user.")
            return False
        return await self.bot.tree.interaction_check(interaction)

    async def on_error(self, interaction, error, item):
        import logging
        logging.getLogger("StudyBot.Today").error("Dashboard action failed", exc_info=error)
        await reply(interaction, "That action couldn't finish. Reopen /today and try again.")


async def build_dashboard(bot, interaction):
    uid = interaction.user.id
    allowed = not bot.allowed_guild_ids or interaction.guild_id in bot.allowed_guild_ids
    show_rpg = interaction.guild_id is not None and allowed and not bot.is_lite_user(uid)
    data = await bot.db_worker.run(snapshot, bot, uid, show_rpg)
    return TodayView(bot, uid, data)
