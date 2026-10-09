# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

"""User-owned forms. All writes happen on explicit submission."""
import logging
import discord

from constants import COLOR_PRIMARY
from services.scheduling import DEFAULT_TIMEZONE, DAYS, valid_timezone

log = logging.getLogger("StudyBot.Forms")


async def reply(interaction, content, **kwargs):
    if interaction.response.is_done():
        return await interaction.followup.send(content, ephemeral=True, **kwargs)
    return await interaction.response.send_message(content, ephemeral=True, **kwargs)


class OwnedModal(discord.ui.Modal):
    def __init__(self, bot, user_id, *, title):
        super().__init__(title=title, timeout=300)
        self.bot, self.user_id = bot, user_id

    async def interaction_check(self, interaction):
        if interaction.user.id != self.user_id:
            await reply(interaction, "This form belongs to another user.")
            return False
        return await self.bot.tree.interaction_check(interaction)

    async def on_error(self, interaction, error):
        log.error("Form submission failed", exc_info=error)
        await reply(interaction, "The form couldn't be saved. Please reopen it and try again.")

    def field(self, text, component, description=None):
        self.add_item(discord.ui.Label(text=text, component=component, description=description))
        return component


class ScheduleModal(OwnedModal):
    def __init__(self, bot, user_id, timezone_name):
        super().__init__(bot, user_id, title="Add study block")
        self.zone = timezone_name
        self.subject = self.field("Subject", discord.ui.TextInput(max_length=100))
        self.days = self.field("Study days", discord.ui.CheckboxGroup(options=[discord.CheckboxGroupOption(label=d.title(), value=d) for d in DAYS], min_values=1, max_values=7))
        self.time = self.field(f"Start time in {timezone_name}"[:45], discord.ui.TextInput(placeholder="09:00", max_length=5), "24-hour HH:MM")
        self.duration = self.field("Duration in minutes", discord.ui.TextInput(default="60", max_length=3))

    async def on_submit(self, interaction):
        try:
            hour, minute = map(int, self.time.value.split(":"))
            duration = int(self.duration.value)
            if not 0 <= hour <= 23 or not 0 <= minute <= 59 or not 15 <= duration <= 480:
                raise ValueError
        except ValueError:
            await reply(interaction, "Use a time such as 09:00 and a duration from 15 to 480 minutes.")
            return
        await interaction.response.defer(ephemeral=True)
        try:
            async with self.bot.user_locks[self.user_id]:
                await self.bot.db_worker.run(self.bot.db.ensure_user, self.user_id, str(interaction.user))
                block_id = await self.bot.db_worker.run(self.bot.db.add_schedule_block, self.user_id, self.subject.value.strip() or "Study", ",".join(self.days.values), hour, minute, duration, timezone_name=self.zone)
        except ValueError as exc:
            await reply(interaction, str(exc))
            return
        await reply(interaction, f"Saved block #{block_id}: {self.subject.value} at {hour:02d}:{minute:02d} in {self.zone}. Schedule reminders follow /settings.")


def preference_snapshot(bot, user_id):
    bot.db.ensure_user(user_id)
    return bot.db.get_user(user_id), bot.db.get_all_settings(user_id)


class PreferencesModal(OwnedModal):
    def __init__(self, bot, user_id, user, settings):
        from cogs.profile import DM_TOGGLES
        super().__init__(bot, user_id, title="Save preferences")
        entries = list(DM_TOGGLES.items())
        self.categories = (entries[:8], entries[8:])
        self.notifications = []
        for label, entries in zip(("Study and planning notifications", "Other notifications"), self.categories):
            self.notifications.append(self.field(label, discord.ui.CheckboxGroup(required=False, min_values=0, max_values=len(entries), options=[discord.CheckboxGroupOption(label=name, value=key, default=settings.get(f"dm_{key}", "1") == "1") for key, name in entries])))
        self.ghost = self.field("Ghost mode (hide public activity)", discord.ui.Checkbox(default=bool(user.get("ghost_mode"))))
        self.cheers = self.field("Block cheers", discord.ui.Checkbox(default=bool(user.get("block_cheers"))))
        self.zone = self.field("Timezone for new schedules and reminders", discord.ui.TextInput(default=settings.get("timezone", DEFAULT_TIMEZONE), max_length=100), "Example: America/New_York. Existing blocks keep their timezone.")

    async def on_submit(self, interaction):
        try:
            zone = valid_timezone(self.zone.value)
        except ValueError as exc:
            await reply(interaction, str(exc))
            return
        settings = {"timezone": zone}
        for entries, control in zip(self.categories, self.notifications):
            selected = set(control.values)
            settings.update({f"dm_{key}": "1" if key in selected else "0" for key, _ in entries})
        await interaction.response.defer(ephemeral=True)
        async with self.bot.user_locks[self.user_id]:
            await self.bot.db_worker.run(self.bot.db.save_preferences, self.user_id, ghost=self.ghost.value, block_cheers=self.cheers.value, settings=settings)
        await reply(interaction, f"Preferences saved. New schedules and wall-clock reminders use {zone}. Open /settings to see the updated values.")


class PreferencesView(discord.ui.View):
    def __init__(self, bot, user_id):
        super().__init__(timeout=300)
        self.bot, self.user_id = bot, user_id

    async def interaction_check(self, interaction):
        if interaction.user.id != self.user_id:
            await reply(interaction, "These settings belong to another user.")
            return False
        return await self.bot.tree.interaction_check(interaction)

    @discord.ui.button(label="Edit preferences", style=discord.ButtonStyle.primary)
    async def edit(self, interaction, button):
        user, settings = await self.bot.db_worker.run(preference_snapshot, self.bot, self.user_id)
        await interaction.response.send_modal(PreferencesModal(self.bot, self.user_id, user, settings))


class TaskModal(OwnedModal):
    def __init__(self, bot, user_id, projects, *, title="", description="", source_url=None):
        super().__init__(bot, user_id, title="Create task · 1 of 2")
        self.source_url = source_url
        self.name = self.field("Task", discord.ui.TextInput(default=title[:100] or None, max_length=100))
        self.description = self.field("Notes", discord.ui.TextInput(default=description[:500] or None, style=discord.TextStyle.paragraph, required=False, max_length=500))
        self.priority = self.field("Priority", discord.ui.RadioGroup(options=[discord.RadioGroupOption(label=label.title(), value=label, default=label == "medium") for label in ("high", "medium", "low")]))
        self.project = self.field("Project", discord.ui.Select(placeholder="No project", required=False, min_values=0, max_values=1, options=[discord.SelectOption(label="No project", value="none")] + [discord.SelectOption(label=p["name"][:100], value=str(p["id"])) for p in projects[:24]]))
        self.review = self.field("Repeat as a spaced-repetition review", discord.ui.Checkbox(default=False))

    async def on_submit(self, interaction):
        data = {"title": self.name.value.strip(), "description": self.description.value, "priority": self.priority.value, "project_id": int(self.project.values[0]) if self.project.values and self.project.values[0] != "none" else None, "review": self.review.value, "source_url": self.source_url}
        if not data["title"]:
            await reply(interaction, "Give the task a title.")
            return
        await interaction.response.send_message("Task details are ready. Continue to set the due date and save; nothing has been saved yet.", view=TaskDraftView(self.bot, self.user_id, data), ephemeral=True)


class TaskDraftView(discord.ui.View):
    def __init__(self, bot, user_id, data):
        super().__init__(timeout=300)
        self.bot, self.user_id, self.data = bot, user_id, data
        self.saved = False

    async def interaction_check(self, interaction):
        if interaction.user.id != self.user_id or self.saved:
            await reply(interaction, "This task draft is no longer available.")
            return False
        return await self.bot.tree.interaction_check(interaction)

    @discord.ui.button(label="Due date and save", style=discord.ButtonStyle.primary)
    async def finish(self, interaction, button):
        await interaction.response.send_modal(TaskDetailsModal(self))

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        self.saved = True
        self.stop()
        await interaction.response.edit_message(content="Task draft cancelled.", view=None)


class TaskDetailsModal(OwnedModal):
    def __init__(self, draft):
        super().__init__(draft.bot, draft.user_id, title="Save task · 2 of 2")
        self.draft = draft
        self.due = self.field("Due date (optional)", discord.ui.TextInput(required=False, placeholder="MM/DD/YYYY", max_length=20))
        self.points = self.field("Points on completion (1–500)", discord.ui.TextInput(default="10", max_length=3))
        self.interval = self.field("Review interval in days (1–60)", discord.ui.TextInput(default="1", max_length=2))

    async def on_submit(self, interaction):
        from cogs.tasks import parse_due_date
        data = self.draft.data
        try:
            points, interval = int(self.points.value), int(self.interval.value)
            if not 1 <= points <= 500 or not 1 <= interval <= 60:
                raise ValueError
        except ValueError:
            await reply(interaction, "Points must be 1–500 and the review interval must be 1–60 days.")
            return
        due = parse_due_date(self.due.value) if self.due.value else None
        if self.due.value and due is None:
            await reply(interaction, "Use a due date such as 10/31/2026.")
            return
        await interaction.response.defer(ephemeral=True)
        async with self.bot.user_locks[self.user_id]:
            if self.draft.saved:
                await reply(interaction, "This draft was already saved or cancelled.")
                return
            def save():
                self.bot.db.ensure_user(self.user_id, str(interaction.user))
                project = data["project_id"]
                if project and not self.bot.db.get_project(project, self.user_id):
                    raise ValueError("That project no longer exists. Reopen /task create.")
                number = self.bot.db.add_task(self.user_id, data["title"], data["description"], points, data["priority"], due, project_id=project, is_review=data["review"], review_interval=interval, source_message_url=data["source_url"])
                return number
            try:
                number = await self.bot.db_worker.run(save)
            except ValueError as exc:
                await reply(interaction, str(exc))
                return
            self.draft.saved = True
            self.draft.stop()
        embed = discord.Embed(title="Task saved", description=f"#{number} · {data['title']}", color=COLOR_PRIMARY)
        if data["source_url"]:
            embed.add_field(name="Source", value=f"[Open original message]({data['source_url']})")
        await interaction.followup.send(embed=embed, ephemeral=True)
        quests = self.bot.get_cog("Quests")
        if quests:
            await quests.track_quest(self.user_id, "task_added")


class StartStudyModal(OwnedModal):
    def __init__(self, bot, user_id):
        super().__init__(bot, user_id, title="Start studying")
        self.subject = self.field("Subject", discord.ui.TextInput(default="General", max_length=100))
        self.target = self.field("Goal in minutes (optional)", discord.ui.TextInput(required=False, max_length=3))
        self.tags = self.field("Tags (optional)", discord.ui.TextInput(required=False, max_length=100))

    async def on_submit(self, interaction):
        try:
            target = int(self.target.value) if self.target.value else None
            if target is not None and not 1 <= target <= 480:
                raise ValueError
        except ValueError:
            await reply(interaction, "Choose a goal from 1 to 480 minutes, or leave it blank.")
            return
        cog = self.bot.get_cog("Study")
        await cog.study_start.callback(cog, interaction, subject=self.subject.value, target=target, tags=self.tags.value)
