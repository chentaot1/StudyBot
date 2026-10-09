# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

"""Message actions and optional server study events/polls."""
from datetime import datetime, timedelta, timezone
import discord
from discord import app_commands
from discord.ext import commands

from services.scheduling import DEFAULT_TIMEZONE
from views.forms import OwnedModal, reply


class MessageReminderModal(OwnedModal):
    def __init__(self, bot, user_id, message, zone):
        super().__init__(bot, user_id, title="Remind me about this")
        self.zone, self.url = zone, message.jump_url
        # Leave room for the source URL in the stored 200-character reminder.
        self.note = self.field("Reminder", discord.ui.TextInput(default=(message.content or "Review this message")[:100], max_length=100))
        self.when = self.field("When", discord.ui.TextInput(placeholder="30m or tomorrow 9am", max_length=80), f"Wall-clock times use {zone}.")

    async def on_submit(self, interaction):
        from cogs.reminders import parse_time_est
        fire_at = parse_time_est(self.when.value, self.zone)
        if fire_at is None:
            await reply(interaction, "Use 30m, tomorrow 9am, or a date and time such as 10/31/2026 13:00.")
            return
        message = f"{self.note.value}\n{self.url}"
        if len(message) > 200:
            await reply(interaction, "Shorten the note so it fits with the original message link.")
            return
        cog = self.bot.get_cog("Reminders")
        await cog.save_reminder(interaction, fire_at, message)


class Planning(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.task_menu = app_commands.ContextMenu(name="Create task", callback=self.message_task)
        self.reminder_menu = app_commands.ContextMenu(name="Remind me about this", callback=self.message_reminder)

    async def cog_load(self):
        self.bot.tree.add_command(self.task_menu)
        self.bot.tree.add_command(self.reminder_menu)

    def cog_unload(self):
        self.bot.tree.remove_command(self.task_menu.name, type=discord.AppCommandType.message)
        self.bot.tree.remove_command(self.reminder_menu.name, type=discord.AppCommandType.message)

    async def message_task(self, interaction: discord.Interaction, message: discord.Message):
        text = message.content.strip() or "Review this message"
        await self.bot.get_cog("Tasks").open_task_form(interaction, title=text.splitlines()[0][:100], description=text[:500], source_url=message.jump_url)

    async def message_reminder(self, interaction: discord.Interaction, message: discord.Message):
        zone = await self.bot.db_worker.run(self.bot.db.get_setting, interaction.user.id, "timezone", DEFAULT_TIMEZONE)
        await interaction.response.send_modal(MessageReminderModal(self.bot, interaction.user.id, message, zone))

    plan = app_commands.Group(name="study_plan", description="Plan group study sessions with events and polls", guild_only=True)

    @plan.command(name="event", description="Create a scheduled group study event with Discord RSVPs")
    @app_commands.describe(subject="Study subject", starts_at="Choose the start date and time", duration="Minutes", channel="Optional voice channel")
    @app_commands.checks.cooldown(1, 30.0, key=lambda i: (i.guild_id, i.user.id))
    async def event(self, interaction: discord.Interaction, subject: app_commands.Range[str, 1, 100], starts_at: app_commands.Timestamp, duration: app_commands.Range[int, 15, 480] = 60, channel: discord.VoiceChannel | None = None):
        if interaction.guild is None:
            await reply(interaction, "Create study events in the server.")
            return
        if not interaction.permissions.create_events:
            await reply(interaction, "You need the Create Events permission to schedule a server event.")
            return
        if channel and (not channel.permissions_for(interaction.user).view_channel or not channel.permissions_for(interaction.guild.me).view_channel):
            await reply(interaction, "Choose a voice channel you and the bot can access.")
            return
        if starts_at <= datetime.now(timezone.utc):
            await reply(interaction, "Choose a future start time.")
            return
        await interaction.response.defer(ephemeral=True)
        kwargs = {"name": f"Study: {subject}"[:100], "start_time": starts_at, "end_time": starts_at + timedelta(minutes=duration), "privacy_level": discord.PrivacyLevel.guild_only, "description": f"Study {subject} together. RSVP here, then use /group_pomo start at the scheduled time to create a shared timer. Host: {interaction.user.display_name}.", "reason": f"Study session requested by {interaction.user.id}"}
        if channel:
            kwargs.update(entity_type=discord.EntityType.voice, channel=channel)
        else:
            kwargs.update(entity_type=discord.EntityType.external, location=f"Discord study group in #{getattr(interaction.channel, 'name', 'study')}"[:100])
        try:
            event = await interaction.guild.create_scheduled_event(**kwargs)
        except discord.Forbidden:
            await reply(interaction, "The bot needs Create Events permission for this server/channel.")
            return
        except discord.HTTPException:
            await reply(interaction, "Discord couldn't create the event. Check the time and channel and try again.")
            return
        await reply(interaction, f"Study event created: {event.url}\nRSVP in Discord, then start a group Pomodoro when it's time.")

    @plan.command(name="poll", description="Vote on a study subject or meeting time")
    @app_commands.describe(question="What should the group decide?", choices="2–10 choices separated by |", hours="How long voting stays open", multiple="Allow more than one choice")
    @app_commands.checks.cooldown(1, 30.0, key=lambda i: (i.guild_id, i.user.id))
    async def poll(self, interaction: discord.Interaction, question: app_commands.Range[str, 1, 300], choices: str, hours: app_commands.Range[int, 1, 768] = 24, multiple: bool = False):
        answers = [choice.strip() for choice in choices.split("|")]
        if not 2 <= len(answers) <= 10 or any(not answer or len(answer) > 55 for answer in answers) or len(set(answer.casefold() for answer in answers)) != len(answers):
            await reply(interaction, "Provide 2–10 distinct choices of up to 55 characters, separated by |.")
            return
        if interaction.guild is None or not interaction.permissions.send_messages or not interaction.permissions.send_polls:
            await reply(interaction, "You need Send Messages and Send Polls in this server channel.")
            return
        if not interaction.app_permissions.send_polls:
            await reply(interaction, "The bot needs Send Polls in this channel.")
            return
        poll = discord.Poll(question=question, duration=timedelta(hours=hours), multiple=multiple)
        for answer in answers:
            poll.add_answer(text=answer)
        try:
            await interaction.response.send_message(poll=poll, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            await reply(interaction, "Discord couldn't post the poll in this channel.")


async def setup(bot):
    await bot.add_cog(Planning(bot))
