# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

import discord
from discord.ext import commands
from discord import app_commands
import logging

from datetime import timezone

from utils import parse_stored
from constants import EST, COLOR_GOLD, BADGE_UNLOCK_POINTS

log = logging.getLogger("StudyBot.Badges")

BADGE_REGISTRY = {
    # Raid badges
    "boss_slayer":   {"name": "Boss Slayer",   "cat": "Raid",       "tiers": [1, 4, 10, 20, 35], "desc": "Participate in raid boss kills"},
    "the_mvp":       {"name": "The MVP",       "cat": "Raid",       "tiers": [5],                "desc": "Finish on the podium 5 times"},
    "vanguard":      {"name": "Vanguard",       "cat": "Raid",      "tiers": [25000],            "desc": "Deal 25,000 total raid damage"},
    "valiant_pity":  {"name": "Valiant Pity",   "cat": "Raid",      "tiers": [3],                "desc": "Receive the +1c pity prize 3 times (top damage when boss escapes)"},

    # Social badges
    "squad_player":  {"name": "Squad Player",  "cat": "Social",     "tiers": [5, 15, 35, 60, 100], "desc": "Join Group Pomodoros"},
    "squad_leader":  {"name": "Squad Leader",  "cat": "Social",     "tiers": [5, 20, 50, 100],     "desc": "Host Group Pomodoros"},
    "motivator":     {"name": "Motivator",     "cat": "Social",     "tiers": [50],                 "desc": "Send 50 Cheers"},
    "sponsor":       {"name": "Sponsor",       "cat": "Social",     "tiers": [3],                  "desc": "Send 3 Study Bounties"},
    "the_beacon":    {"name": "The Beacon",    "cat": "Social",     "tiers": [3],                  "desc": "Activate 3 Beacons"},

    # Persistence badges
    "streak_master": {"name": "Streak Master", "cat": "Persistence","tiers": [7, 30, 100, 365],   "desc": "Maintain study streaks"},
    "marathoner":    {"name": "Marathoner",    "cat": "Persistence","tiers": [120, 240, 480],      "desc": "Single session milestones (mins)"},
    "centurion":     {"name": "Centurion",     "cat": "Persistence","tiers": [6000],               "desc": "Study 100 total hours"},
    "the_scholar_b": {"name": "The Scholar",   "cat": "Persistence","tiers": [100],                "desc": "Complete 100 SRS reviews"},
    "deep_roots":    {"name": "Deep Roots",    "cat": "Persistence","tiers": [5],                  "desc": "Complete 5 SRS reviews at a 30+ day interval"},
    "review_streak": {"name": "Review Streak", "cat": "Persistence","tiers": [7],                  "desc": "Complete at least one SRS review for 7 consecutive days"},

    # Time/Vibes badges
    "night_owl":     {"name": "Night Owl",     "cat": "Vibes",      "tiers": [6000],  "desc": "100 hours studying 10PM-5AM"},
    "early_bird":    {"name": "Early Bird",    "cat": "Vibes",      "tiers": [50],    "desc": "50 sessions started 5-7AM"},
    "weekend_warrior":{"name":"Weekend Warrior","cat": "Vibes",     "tiers": [3000],  "desc": "50 weekend hours"},
    "the_phantom":   {"name": "The Phantom",   "cat": "Vibes",      "tiers": [6000],  "desc": "Study 100 hours while Ghost Mode is enabled"},

    # Economy badges
    "the_mint":      {"name": "The Mint",      "cat": "Economy",    "tiers": [100000],            "desc": "Convert 100,000 pts (excl. gacha)"},
    "the_broker":    {"name": "The Broker",    "cat": "Economy",    "tiers": [7],                 "desc": "Convert for 7 consecutive days"},
    "the_stimulus":  {"name": "The Stimulus",  "cat": "Economy",    "tiers": [50],                "desc": "Spend 50c lifetime"},
    "the_whale":     {"name": "The Whale",     "cat": "Economy",    "tiers": [1],                 "desc": "Hit the coin cap"},
    "jackpot":       {"name": "Jackpot",       "cat": "Economy",    "tiers": [1],                 "desc": "Pull the Gold Gacha jackpot"},
    "degenerate_gambler": {"name": "Degenerate Gambler", "cat": "Economy", "tiers": [10],          "desc": "Use the gacha 10 times"},
    "calculated_risk": {"name": "Calculated Risk", "cat": "Economy", "tiers": [1],                 "desc": "Make a shop purchase that leaves you with exactly 0 coins"},
    "venture_capitalist": {"name": "Venture Capitalist", "cat": "Economy", "tiers": [5000],        "desc": "Earn 5,000 study points from bounty payouts"},

    # Legacy badges
    "initiate":      {"name": "Initiate",      "cat": "Legacy",     "tiers": [1],    "desc": "Reach Prestige 1"},
    "veteran":       {"name": "Veteran",       "cat": "Legacy",     "tiers": [1],    "desc": "Reach Prestige 3"},
    "legend":        {"name": "Legend",         "cat": "Legacy",     "tiers": [1],    "desc": "Reach Prestige 5"},
    "perfect_year":  {"name": "Perfect Year",  "cat": "Legacy",     "tiers": [1],    "desc": "Hit Godslayer 8k mins in 4 consecutive seasons"},
}

BADGE_EMOJIS = {
    "Raid": "⚔️", "Social": "📣", "Persistence": "🔥",
    "Vibes": "🌙", "Economy": "💰", "Legacy": "🏆",
}


class Badges(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _try_award(self, user_id: int, badge_key: str, tier: int = 1) -> bool:
        if self.bot.db.award_badge(user_id, badge_key, tier):
            info = BADGE_REGISTRY.get(badge_key, {})
            name = info.get("name", badge_key)
            cat = info.get("cat", "")
            emoji = BADGE_EMOJIS.get(cat, "🏅")

            self.bot.db.add_points(
                user_id,
                BADGE_UNLOCK_POINTS,
                reason=f"Badge: {name}" + (f" (T{tier})" if tier > 1 else ""),
            )

            if self.bot.db.get_dm_enabled(user_id, "badge_unlocks"):
                self.bot.db.enqueue_outbox(
                    target_type="user",
                    target_id=int(user_id),
                    kind="badge_unlock",
                    dedupe_key=f"badge_unlock:{user_id}:{badge_key}:{tier}",
                    settings_key="badge_unlocks",
                    embed={
                        "title": f"{emoji} Badge Unlocked!",
                        "description": f"**{name}**" + (f" (Tier {tier})" if tier > 1 else ""),
                        "color": int(COLOR_GOLD),
                        "fields": [
                            {
                                "name": "Reward",
                                "value": f"💎 **+{BADGE_UNLOCK_POINTS:,}** study points",
                                "inline": False,
                            },
                            {"name": "Category", "value": cat, "inline": True},
                            {
                                "name": "Description",
                                "value": info.get("desc", ""),
                                "inline": True,
                            },
                        ],
                        "footer": "Use /badges to see all your badges!",
                    },
                )
            return True
        return False

    async def check_session_badges(self, user_id: int, session: dict):
        """Called after a study session ends."""
        minutes = session.get("duration_minutes", 0)
        if minutes < 20:
            return

        # Marathoner
        for tier_mins in [120, 240, 480]:
            if minutes >= tier_mins:
                await self._try_award(user_id, "marathoner", tier_mins)

        # Centurion (100 total hours)
        total = self.bot.db.get_total_study_minutes(user_id)
        if total >= 6000:
            await self._try_award(user_id, "centurion", 6000)

        # Streak Master
        user = self.bot.db.get_user(user_id)
        if user:
            streak = user["streak"]
            for tier_days in [7, 30, 100, 365]:
                if streak >= tier_days:
                    await self._try_award(user_id, "streak_master", tier_days)

        # Night Owl (10PM-5AM)
        started = parse_stored(session["started_at"])
        started_est = started.replace(tzinfo=timezone.utc).astimezone(EST)
        hour = started_est.hour
        if hour >= 22 or hour < 5:
            progress = self.bot.db.increment_badge_progress(user_id, "night_owl", minutes)
            if progress >= 6000:
                await self._try_award(user_id, "night_owl", 6000)

        # Early Bird (5-7AM)
        if 5 <= hour < 7:
            progress = self.bot.db.increment_badge_progress(user_id, "early_bird", 1)
            if progress >= 50:
                await self._try_award(user_id, "early_bird", 50)

        # Weekend Warrior
        day_of_week = started_est.weekday()
        if day_of_week >= 5:
            progress = self.bot.db.increment_badge_progress(user_id, "weekend_warrior", minutes)
            if progress >= 3000:
                await self._try_award(user_id, "weekend_warrior", 3000)

        # The Phantom (Ghost Mode hours)
        user = self.bot.db.get_user(user_id)
        if user and user.get("ghost_mode"):
            progress = self.bot.db.increment_badge_progress(user_id, "the_phantom", minutes)
            if progress >= 6000:
                await self._try_award(user_id, "the_phantom", 6000)

    async def check_social_badges(self, user_id: int):
        cheers = self.bot.db.get_cheers_sent(user_id)
        if cheers >= 50:
            await self._try_award(user_id, "motivator", 50)

    async def check_economy_badges(self, user_id: int):
        user = self.bot.db.get_user(user_id)
        if not user:
            return

        converted = user.get("total_converted_pts", 0)
        if converted >= 100000:
            await self._try_award(user_id, "the_mint", 100000)

        coins = user.get("coins", 0)
        cap = self.bot.db.get_coin_cap(user_id)
        if coins >= cap:
            await self._try_award(user_id, "the_whale", 1)

        if self.bot.db.has_gold_jackpot(user_id):
            await self._try_award(user_id, "jackpot", 1)

        broker_streak = int(self.bot.db.get_badge_progress(user_id, "the_broker_streak"))
        if broker_streak >= 7:
            await self._try_award(user_id, "the_broker", 7)

        spent = int(user.get("total_coins_spent") or 0)
        if spent >= 50:
            await self._try_award(user_id, "the_stimulus", 50)

    async def check_prestige_badges(self, user_id: int):
        p = self.bot.db.get_prestige(user_id)
        if p >= 1:
            await self._try_award(user_id, "initiate", 1)
        if p >= 3:
            await self._try_award(user_id, "veteran", 1)
        if p >= 5:
            await self._try_award(user_id, "legend", 1)

    async def check_srs_badges(self, user_id: int):
        total_progress = self.bot.db.increment_badge_progress(user_id, "the_scholar_b", 1)
        if total_progress >= 100:
            await self._try_award(user_id, "the_scholar_b", 100)

    async def check_srs_review_badges(self, user_id: int, *, interval_before_days: int | None = None):
        """Call once per completed SRS review task."""
        await self.check_srs_badges(user_id)

        if interval_before_days is not None and interval_before_days >= 30:
            n = int(self.bot.db.increment_badge_progress(user_id, "deep_roots", 1))
            if n >= 5:
                await self._try_award(user_id, "deep_roots", 5)

        # Review streak (EST day)
        from datetime import datetime
        last = self.bot.db.get_setting(user_id, "review_streak_last_date", "")
        today = datetime.now(EST).date().isoformat()
        if last == today:
            return
        if last:
            try:
                from datetime import timedelta
                y = (datetime.now(EST).date() - timedelta(days=1)).isoformat()
                prev_ok = (last == y)
            except Exception:
                prev_ok = False
        else:
            prev_ok = False
        cur = int(self.bot.db.get_setting(user_id, "review_streak_count", "0") or 0)
        cur = (cur + 1) if prev_ok else 1
        self.bot.db.set_setting(user_id, "review_streak_last_date", today)
        self.bot.db.set_setting(user_id, "review_streak_count", str(cur))
        if cur >= 7:
            await self._try_award(user_id, "review_streak", 7)

    async def check_raid_badges(self, user_id: int, boss: dict):
        if boss.get("killed"):
            progress = self.bot.db.increment_badge_progress(user_id, "boss_slayer", 1)
            for t in [1, 4, 10, 20, 35]:
                if progress >= t:
                    await self._try_award(user_id, "boss_slayer", t)

    async def check_vanguard_badge(self, user_id: int, total_raw_damage: int):
        if total_raw_damage >= 25000:
            await self._try_award(user_id, "vanguard", 25000)

    async def check_mvp_podium_finish(self, user_id: int):
        """Call once per raid podium placement (top 3 damage when a boss cycle resolves or is slain)."""
        n = int(self.bot.db.increment_badge_progress(user_id, "the_mvp", 1))
        if n >= 5:
            await self._try_award(user_id, "the_mvp", 5)

    async def check_sponsor_badge(self, user_id: int):
        n = int(self.bot.db.increment_badge_progress(user_id, "sponsor", 1))
        if n >= 3:
            await self._try_award(user_id, "sponsor", 3)

    async def check_beacon_badge(self, user_id: int):
        n = int(self.bot.db.increment_badge_progress(user_id, "the_beacon", 1))
        if n >= 3:
            await self._try_award(user_id, "the_beacon", 3)

    async def check_squad_badges_after_group_complete(self, host_id: int, member_user_ids: list[int]):
        """One completed Group Pomodoro session (all cycles done)."""
        for uid in set(member_user_ids):
            p = int(self.bot.db.increment_badge_progress(uid, "squad_player", 1))
            for t in (5, 15, 35, 60, 100):
                if p >= t:
                    await self._try_award(uid, "squad_player", t)
        h = int(self.bot.db.increment_badge_progress(host_id, "squad_leader", 1))
        for t in (5, 20, 50, 100):
            if h >= t:
                await self._try_award(host_id, "squad_leader", t)

    async def check_valiant_pity_badge(self, user_id: int):
        n = int(self.bot.db.increment_badge_progress(user_id, "valiant_pity", 1))
        if n >= 3:
            await self._try_award(user_id, "valiant_pity", 3)

    async def check_deg_gambler_badge(self, user_id: int):
        n = int(self.bot.db.increment_badge_progress(user_id, "degenerate_gambler", 1))
        if n >= 10:
            await self._try_award(user_id, "degenerate_gambler", 10)

    async def check_calculated_risk_badge(self, user_id: int):
        await self._try_award(user_id, "calculated_risk", 1)

    async def check_venture_capitalist_badge(self, user_id: int, *, bounty_points: int):
        if bounty_points <= 0:
            return
        total = self.bot.db.increment_badge_progress(user_id, "venture_capitalist", bounty_points)
        if total >= 5000:
            await self._try_award(user_id, "venture_capitalist", 5000)

    @app_commands.command(name="badges", description="View your earned badges")
    async def badges_cmd(self, interaction: discord.Interaction):
        if getattr(self.bot, "is_lite_user", lambda _uid: False)(interaction.user.id):
            await interaction.response.send_message("Lite mode: RPG features (badges) are disabled for your account.", ephemeral=True)
            return
        uid = interaction.user.id
        self.bot.db.ensure_user(uid, interaction.user.display_name)
        earned = self.bot.db.get_badges(uid)

        if not earned:
            await interaction.response.send_message(
                "You haven't earned any badges yet! Keep studying to unlock hidden achievements. 🏅",
                ephemeral=True
            )
            return

        by_cat: dict[str, list] = {}
        for b in earned:
            info = BADGE_REGISTRY.get(b["badge_key"], {})
            cat = info.get("cat", "Unknown")
            if cat not in by_cat:
                by_cat[cat] = []
            name = info.get("name", b["badge_key"])
            tier_str = f" (T{b['tier']})" if b["tier"] > 1 else ""
            by_cat[cat].append(f"**{name}**{tier_str}")

        embed = discord.Embed(title="🏅 Your Badges", color=COLOR_GOLD)
        for cat, badges in by_cat.items():
            emoji = BADGE_EMOJIS.get(cat, "🏅")
            embed.add_field(name=f"{emoji} {cat}", value="\n".join(badges), inline=True)

        featured = self.bot.db.get_featured_badges(uid)
        if featured:
            feat_names = []
            for fk in featured:
                info = BADGE_REGISTRY.get(fk, {})
                feat_names.append(info.get("name", fk))
            embed.set_footer(text=f"Featured: {', '.join(feat_names)}")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    badges_group = app_commands.Group(name="badge", description="Badge management")

    @badges_group.command(name="featured", description="Set your 3 featured profile badges")
    @app_commands.describe(
        badge1="First badge (pick from your earned badges)",
        badge2="Second badge (optional)",
        badge3="Third badge (optional)"
    )
    async def set_featured(self, interaction: discord.Interaction,
                           badge1: str, badge2: str = "", badge3: str = ""):
        if getattr(self.bot, "is_lite_user", lambda _uid: False)(interaction.user.id):
            await interaction.response.send_message("Lite mode: RPG features (badges) are disabled for your account.", ephemeral=True)
            return
        uid = interaction.user.id
        keys = [k for k in [badge1, badge2, badge3] if k]
        earned = self.bot.db.get_badges(uid)
        earned_keys = {b["badge_key"] for b in earned}

        seen = set()
        valid = []
        for k in keys:
            if k in earned_keys and k not in seen:
                valid.append(k)
                seen.add(k)
        if not valid:
            await interaction.response.send_message("You haven't earned any of those badges yet. Use `/badges` to see what you've unlocked.", ephemeral=True)
            return

        self.bot.db.set_featured_badges(uid, valid)
        names = [BADGE_REGISTRY.get(k, {}).get("name", k) for k in valid]
        await interaction.response.send_message(
            f"Featured badges set: **{', '.join(names)}**",
            ephemeral=True
        )

    @badges_group.command(name="clear", description="Remove all your featured badges")
    async def clear_featured(self, interaction: discord.Interaction):
        if getattr(self.bot, "is_lite_user", lambda _uid: False)(interaction.user.id):
            await interaction.response.send_message("Lite mode: RPG features (badges) are disabled for your account.", ephemeral=True)
            return
        uid = interaction.user.id
        self.bot.db.set_featured_badges(uid, [])
        await interaction.response.send_message("Featured badges cleared.", ephemeral=True)

    async def _autocomplete_badge(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        self.bot.db.ensure_user(interaction.user.id, str(interaction.user))
        earned = self.bot.db.get_badges(interaction.user.id)
        earned_keys = {b["badge_key"] for b in earned}
        choices = []
        for key in earned_keys:
            info = BADGE_REGISTRY.get(key, {})
            name = info.get("name", key)
            if current == "" or current.lower() in name.lower() or current.lower() in key.lower():
                choices.append(app_commands.Choice(name=f"{name} ({key})"[:100], value=key))
        return choices[:25]

    @set_featured.autocomplete("badge1")
    async def badge1_autocomplete(self, interaction: discord.Interaction, current: str):
        return await self._autocomplete_badge(interaction, current)

    @set_featured.autocomplete("badge2")
    async def badge2_autocomplete(self, interaction: discord.Interaction, current: str):
        return await self._autocomplete_badge(interaction, current)

    @set_featured.autocomplete("badge3")
    async def badge3_autocomplete(self, interaction: discord.Interaction, current: str):
        return await self._autocomplete_badge(interaction, current)


async def setup(bot):
    await bot.add_cog(Badges(bot))
