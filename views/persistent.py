# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

"""Route durable buttons by session/lobby identity, including after a restart."""
import discord


def bind_buttons(view, prefix):
    for item in view.children:
        if isinstance(item, discord.ui.Button) and item.url is None:
            item.custom_id = f"{prefix}:{item.callback.callback.__name__}"


class StudyControl(discord.ui.DynamicItem[discord.ui.Button], template=r"sb:study:(?P<user>\d+):(?P<session>\d+):(?P<action>[a-z_]+)"):
    def __init__(self, item, user_id, session_id, action):
        super().__init__(item)
        self.user_id, self.session_id, self.action = user_id, session_id, action

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(item, int(match["user"]), int(match["session"]), match["action"])

    async def callback(self, interaction):
        bot = interaction.client
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("These controls belong to another user.", ephemeral=True)
            return
        if not await bot.tree.interaction_check(interaction):
            return
        cog = bot.get_cog("Study")
        if cog is None:
            await interaction.response.send_message("Study controls are temporarily unavailable.", ephemeral=True)
            return
        from cogs.study import StudySessionControlsView
        view = StudySessionControlsView(cog, self.user_id, self.session_id)
        if not await view.interaction_check(interaction):
            return
        button = getattr(view, self.action, None)
        if isinstance(button, discord.ui.Button):
            await button.callback(interaction)


class GroupControl(discord.ui.DynamicItem[discord.ui.Button], template=r"sb:group:(?P<lobby>\d+):(?P<state>waiting|active):(?P<action>[a-z_]+)"):
    def __init__(self, item, lobby_id, state, action):
        super().__init__(item)
        self.lobby_id, self.state, self.action = lobby_id, state, action

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(item, int(match["lobby"]), match["state"], match["action"])

    async def callback(self, interaction):
        bot = interaction.client
        if not await bot.tree.interaction_check(interaction):
            return
        lobby = await bot.db_worker.run(bot.db.get_group_lobby_by_id, self.lobby_id)
        if not lobby or lobby.get("state") != self.state:
            await interaction.response.send_message("This lobby card has changed. Open /group_pomo status.", ephemeral=True)
            return
        if lobby.get("source_guild_id") != interaction.guild_id:
            await interaction.response.send_message("Use these controls in the lobby's server.", ephemeral=True)
            return
        cog = bot.get_cog("Pomodoro")
        if cog is None:
            await interaction.response.send_message("Group controls are temporarily unavailable.", ephemeral=True)
            return
        from cogs.pomodoro import GroupLobbyWaitingView, GroupLobbyActiveView
        view = (GroupLobbyWaitingView if self.state == "waiting" else GroupLobbyActiveView)(cog, self.lobby_id)
        if not await view.interaction_check(interaction):
            return
        button = getattr(view, self.action, None)
        if isinstance(button, discord.ui.Button):
            await button.callback(interaction)
