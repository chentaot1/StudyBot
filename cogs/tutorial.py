# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

"""Long-form /tutorial — deep dives per bot area (separate from /help).

Overview copy is shared with /help via get_help_overview_preamble() — edit here only.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from constants import USER_NAV_FOOTER

TUTORIAL_COLOR = 0x5865F2


def _overview_blocks() -> dict[str, str]:
    """Single source for tutorial ch.0 + /help overview fields."""
    return {
        "intro": (
            "StudyBot is a **study tracker + light RPG**: focus time earns **XP**, **points**, and progress on "
            "**quests**, **raids**, and **badges**. Nothing auto-starts without you — you always choose "
            "`/study start` or `/pomodoro start`.\n\n"
            "**This menu** is the long guide. **`/help`** lists slash commands in compact form; the **first "
            "dropdown row there** opens this same tutorial overview."
        ),
        "quick_cmds": (
            "**Fast entry:** `/study start` or `/pomodoro start` · snapshot **`/today`** · identity **`/profile`** · "
            "compact list **`/help`** · this guide **`/tutorial`** · treat pairing **`/bundle`**."
        ),
        "core_loop_a": (
            "**1. Start focus** — `/study start` (open timer + live card) or `/pomodoro start` (work/break machine "
            "that **pauses/resumes** the linked study row).\n"
            "**2. Stay honest** — **Pause** when you leave the desk; **active** time drives streaks, loot gates, and "
            "integrity checks.\n"
            "**3. Stop cleanly** — `/study stop` or `/pomodoro stop` finalizes minutes, **lucky loot** (when eligible), "
            "**raid** damage, **quests**, and may DM **badges** / wellbeing nudges."
        ),
        "core_loop_b": (
            "**4. Spend currency** — **points** → `/convert` → **boss coins** (daily cap scales with **prestige**); "
            "coins → `/shop`, `/gacha`, `/bounty`, `/beacon`.\n"
            "**5. Layer habits** — `/goals`, `/checkin`, `/schedule`, `/remind`, `/task`, `/project` **plan** sessions; "
            "they do not auto-start focus.\n"
            "**6. Bundle (optional)** — `/bundle` DMs a treat only after **your** rule (Pomodoro work end, break "
            "window, or enough `/study` minutes) so rewards stay **closed** until earned."
        ),
        "systems_map": (
            "**Study** — live embed, notes, target bar, XP bonus countdowns, inactivity check after very long runs.\n"
            "**Pomodoro** — phase DMs, skip/stop, optional `max_cycles`, **group** lobbies with codes.\n"
            "**Economy** — points, coins, overflow, potions, prestige perks.\n"
            "**Quests & raid** — dailies + shared boss HP; minutes can get a **beacon** multiplier in-session.\n"
            "**Social & badges** — cheers, boards, ghost-style privacy in `/settings`.\n"
            "**Data** — `/stats`, `/breakdown`, `/export` for history and portability."
        ),
        "integrity": (
            "**5-minute floor** — sessions under **5** recorded active minutes grant **no** XP, points, lucky loot, "
            "or raid credit (anti-spam).\n"
            "**Lucky loot** — needs longer sessions (see `/help` rewards blurb); rolls can **DM** you.\n"
            "**Wellbeing** — crossing very high **daily** study triggers a rest nudge (by design)."
        ),
        "timezone": (
            "Calendar-style features (**streaks**, **daily goals**, **schedule**, **weekly report** windows) use "
            "**US Eastern (EST/EDT)** unless a command’s embed says otherwise."
        ),
        "nav_footer": f"Pick a **chapter** below anytime. {USER_NAV_FOOTER}",
    }


def _overview_blocks_lite() -> dict[str, str]:
    """Lite-mode overview: productivity-only, no RPG mentions."""
    return {
        "intro": (
            "StudyBot is a **study tracker**: focus time becomes logged sessions you can review later.\n\n"
            "**This menu** is the long guide. **`/help`** lists commands in compact form; the first dropdown row there "
            "opens this same overview."
        ),
        "quick_cmds": (
            "**Fast entry:** `/study start` or `/pomodoro start` · snapshot **`/today`** · identity **`/profile`** · "
            "settings **`/settings`** · compact list **`/help`** · this guide **`/tutorial`**."
        ),
        "core_loop_a": (
            "**1. Start focus** — `/study start` (timer + live card) or `/pomodoro start` (work/break cycles linked to a "
            "study session).\n"
            "**2. Stay honest** — pause when you step away so your logs reflect real focus time.\n"
            "**3. Stop cleanly** — `/study stop` or `/pomodoro stop` saves the session and updates your stats."
        ),
        "core_loop_b": (
            "**4. Plan** — `/goals`, `/task`, `/project` help you decide what to do next.\n"
            "**5. Remember** — `/remind` and `/schedule` can DM you nudges at the right time.\n"
            "**6. Review** — `/stats` and `/breakdown` show trends; `/export` downloads your history."
        ),
        "systems_map": (
            "**Study** — live timer, notes, targets, history.\n"
            "**Pomodoro** — phase transitions + optional group sessions.\n"
            "**Tasks & projects** — lightweight planning + reviews.\n"
            "**Schedule & reminders** — time-based nudges.\n"
            "**Data** — `/today`, `/stats`, `/breakdown`, `/export`."
        ),
        "integrity": (
            "**5-minute floor** — sessions under **5** minutes grant no rewards and are treated as quick test runs.\n"
            "**Wellbeing** — very high daily study totals can trigger a rest nudge (by design)."
        ),
        "timezone": (
            "Calendar-style features (**streaks**, **daily goals**, **schedule**, **weekly report** windows) use "
            "**US Eastern (EST/EDT)** unless a command’s embed says otherwise."
        ),
        "nav_footer": f"Pick a **chapter** below anytime. {USER_NAV_FOOTER}",
    }


def get_help_overview_preamble(*, is_lite: bool = False) -> list[tuple[str, str]]:
    """Sections inserted at the top of /help — must stay aligned with tutorial overview."""
    b = _overview_blocks_lite() if is_lite else _overview_blocks()
    return [
        ("🚀 Getting started", f"{b['intro']}\n\n{b['quick_cmds']}"),
        ("📖 Core loop (1–3)", b["core_loop_a"]),
        ("📖 Core loop (4–6)", b["core_loop_b"]),
        ("🗺️ Systems map", b["systems_map"]),
        ("⚖️ Integrity, time & tutorial", f"{b['integrity']}\n\n{b['timezone']}\n\n{b['nav_footer']}"),
    ]


def _embed(idx: int, *, is_lite: bool = False) -> discord.Embed:
    if idx == 0:
        b = _overview_blocks_lite() if is_lite else _overview_blocks()
        return (
            discord.Embed(
                title="📖 StudyBot — Tutorial (overview)",
                description=b["intro"],
                color=TUTORIAL_COLOR,
            )
            .add_field(name="Quick commands", value=b["quick_cmds"], inline=False)
            .add_field(name="Core loop (steps 1–3)", value=b["core_loop_a"], inline=False)
            .add_field(name="Core loop (steps 4–6)", value=b["core_loop_b"], inline=False)
            .add_field(name="Systems map", value=b["systems_map"], inline=False)
            .add_field(name="Integrity & wellbeing", value=b["integrity"], inline=False)
            .add_field(name="Time zone", value=b["timezone"], inline=False)
            .add_field(name="Where to go next", value=b["nav_footer"], inline=False)
        )

    if is_lite:
        # Lite-mode: only show productivity chapters (reuse existing detailed chapters where possible).
        allowed = {1, 2, 3, 8, 9}
        if idx not in allowed:
            return discord.Embed(
                title="📖 Tutorial",
                description="This chapter isn't available in Lite Mode.",
                color=TUTORIAL_COLOR,
            )

    if idx == 1:
        return (
            discord.Embed(
                title="📚 Chapter — Study sessions (`/study` …)",
                description=(
                    "**Open timer** until you stop: one DB-backed session row, optional **target**, **live embed**, "
                    "and the **5-minute** minimum for any RPG payout."
                ),
                color=TUTORIAL_COLOR,
            )
            .add_field(
                name="All `/study` subcommands",
                value=(
                    "`/study start` `[subject]` `[target]` · `/study pause` · `/study resume` · `/study stop` "
                    "`[notes]` · `/study note` · `/study extend` · `/study status` · `/study history`\n"
                    "Private cards refresh for 15 minutes; reopen `/study status` anytime. `/study timer` sends a lasting DM card with restart-safe controls."
                ),
                inline=False,
            )
            .add_field(
                name="Start / stop / pause",
                value=(
                    "**`start`** — begins active clock + XP bonus countdowns (**25 / 60 / 120** active minutes for "
                    "flat bonuses on the card). Subject autocomplete reuses your history.\n"
                    "**`pause` / `resume`** — only **active** time moves streaks, loot, and raid damage; breaks belong "
                    "here.\n"
                    "**`stop`** — finalizes minutes, completion embed (XP, points, lucky loot summary, raid, goals), "
                    "optional **focus rating** UI + suggested break length."
                ),
                inline=False,
            )
            .add_field(
                name="Live card & buttons",
                value=(
                    "Ephemeral embed: elapsed active time, target bar, XP bonus `<t:…:R>` lines, notes preview, "
                    "**bundle** hint if `/bundle` uses study-minutes rule.\n"
                    "**Buttons** = Pause, Resume, Note, Refresh (same as slash). **`status`** reprints/re-binds the "
                    "live message IDs if Discord lost the link."
                ),
                inline=False,
            )
            .add_field(
                name="Notes · target · history",
                value=(
                    "**`note`** — session log + quest hooks where implemented.\n"
                    "**`extend`** — add minutes to **target** without wiping elapsed time.\n"
                    "**`history`** — recent sessions with XP, points, pause notes, star ratings."
                ),
                inline=False,
            )
            .add_field(
                name="XP, loot, safety nets",
                value=(
                    "**Bonuses** stack at 25 / 60 / 120 **active** minutes; **potions**/**bounties** may still alter "
                    "the math — read `/study stop` output.\n"
                    "**Lucky loot** — needs longer sessions (see Rewards in `/help`); items may **DM**.\n"
                    "**Inactivity** — ~4h wall-time **check-in** DM; no response can **auto-stop** so timers never "
                    "run unattended forever."
                ),
                inline=False,
            )
            .add_field(
                name="`/streak` & `/goals` (same cog)",
                value=(
                    "The **`/streak`** card and the whole **`/goals`** subcommand group** are defined next to `/study` "
                    "in the bot — behavior is covered in **Social & goals** so daily-goal math stays next to "
                    "**`/checkin`** and **`/adaptive`**."
                ),
                inline=False,
            )
        )

    if idx == 2:
        return (
            discord.Embed(
                title="🍅 Chapter — Pomodoro (`/pomodoro` & `/group_pomo`)",
                description=(
                    "Phases drive a linked **study_sessions** row: **work → short break → … → long break every 4 work "
                    "blocks**."
                ),
                color=TUTORIAL_COLOR,
            )
            .add_field(
                name="Solo `/pomodoro`",
                value=(
                    "`/pomodoro start` — work / short / long minutes + optional **`max_cycles`** (0 = run until "
                    "manual stop).\n"
                    "`/pomodoro status` · `/pomodoro skip` · `/pomodoro stop`\n"
                    "**`skip`** jumps to the next phase immediately (cancels the internal scheduler task, then runs the "
                    "same transition path as an auto tick).\n"
                    "**DMs** fire on each phase with `<t:…:R>` for the next boundary. **`stop`** tears down Pomodoro "
                    "**and** the study row (same **≥5 min** payout rule), also cancelling study live-card helpers."
                ),
                inline=False,
            )
            .add_field(
                name="Study timer coupling",
                value=(
                    "**Work** = study timer runs. **Break / long break** = bot **pauses** `/study` active time so "
                    "snacks don’t count as focus. Next **work** phase **resumes** the row automatically."
                ),
                inline=False,
            )
            .add_field(
                name="Group `/group_pomo`",
                value=(
                    "`/group_pomo start` — host configures work/break/long + **cycle count**, get share **code**.\n"
                    "`/group_pomo join` · `/group_pomo begin` (host) · `/group_pomo status` · `/group_pomo leave`\n"
                    "Everyone receives **synced phase DMs**. Payout embeds explain **group XP multipliers** and "
                    "whether leaving early still **saved** minutes."
                ),
                inline=False,
            )
            .add_field(
                name="Mutual exclusion",
                value=(
                    "No **solo Pomodoro** + **group lobby** simultaneously; no conflicting `/study` starts — resolve "
                    "what the bot tells you before starting another mode."
                ),
                inline=False,
            )
        )

    if idx == 3:
        return (
            discord.Embed(
                title="🎁 Chapter — Temptation bundling (`/bundle`)",
                description=(
                    "**Behavioral trick:** keep the distracting reward **closed** until a **rule** you chose fires — "
                    "the bot only **DMs** a link/label at that moment."
                ),
                color=TUTORIAL_COLOR,
            )
            .add_field(
                name="Commands",
                value=(
                    "**`/bundle set`** — label + **rule** + optional **URL** + (for study rule) **minimum minutes**.\n"
                    "**`/bundle status`** — read-back.\n"
                    "**`/bundle clear`** — disable."
                ),
                inline=False,
            )
            .add_field(
                name="Rules (pick one)",
                value=(
                    "**After each Pomodoro work block** — DM when a **work** phase ends (solo or group).\n"
                    "**When a Pomodoro break starts** — DM marks a **time-boxed treat window** for that break only.\n"
                    "**When `/study stop` hits enough minutes** — DM only if **recorded** session minutes ≥ threshold "
                    "(still requires the normal **≥5** minute payout gate)."
                ),
                inline=False,
            )
            .add_field(
                name="Live study card",
                value=(
                    "If you use the **study-minutes** rule, the `/study` live embed gains a **Treat bundle** line "
                    "showing **how many focused minutes remain** before you qualify for the stop-DM."
                ),
                inline=False,
            )
            .add_field(
                name="DMs & honesty",
                value=(
                    "You must **allow DMs** from the bot (like other features). The bot never opens apps for you — "
                    "it only sends the embed you configured. **Honor system:** close the treat when the next focus "
                    "block begins."
                ),
                inline=False,
            )
        )

    if idx == 4:
        return (
            discord.Embed(
                title="🪙 Chapter — Economy & sinks (points → coins → shop)",
                description=(
                    "**Points** grind from study (and related bonuses). **Boss coins** buy power spikes & social "
                    "buffs. Always read each slash embed — numbers evolve with balance patches."
                ),
                color=TUTORIAL_COLOR,
            )
            .add_field(
                name="`/points`",
                value=(
                    "Shows **balance** plus rolling **history** of point gains and spends so you can audit grinds "
                    "before converting."
                ),
                inline=False,
            )
            .add_field(
                name="`/convert`",
                value=(
                    "**Prestige 0:** **2,000** pts ⇒ **1** coin. **Prestige 1+:** **1,800** pts ⇒ **1** coin "
                    "(failed conversions still echo the exact cost).\n"
                    "**Batch:** `amount` is clamped **1–50** per command; **5s** cooldown between invocations.\n"
                    "**Daily cap** scales with **prestige** (tight at low prestige, effectively uncapped by **P4**) — "
                    "when you hit the cap mid-batch you keep coins from successful rows, then get the orange "
                    "**partial** embed explaining the stop."
                ),
                inline=False,
            )
            .add_field(
                name="`/inventory` · overflow",
                value=(
                    "Lists **potions**, overflow buckets, and anything else stored server-side. **Coin cap** pushes "
                    "extras into overflow — `/today` also surfaces overflow warnings when relevant."
                ),
                inline=False,
            )
            .add_field(
                name="`/use_potion`",
                value=(
                    "**Autocomplete** only lists potion rows you actually own (`/inventory`). Unknown keys are "
                    "rejected.\n"
                    "Drinking applies the same catalog multipliers/durations as `/shop` potions, then **Prestige 3 "
                    "Time Lord** doubles **hours** and **Prestige 5 Master Alchemist** forces **2.0×** on the buff.\n"
                    "Re-drinking the **same** potion **extends** time (verb in the reply) instead of stacking a second "
                    "parallel buff."
                ),
                inline=False,
            )
            .add_field(
                name="`/bounty` · `/beacon` (shop cog)",
                value=(
                    "**`/bounty`** — **5c**, **10s** cooldown. Places a **pending** bounty on the target; it **activates** "
                    "only when they run **`/study start`** (one active bounty per target).\n"
                    "**XP buff:** when it activates, both sponsor and target get **2.0× XP** for **exactly 2 hours** "
                    "(overlap-based: can apply to a current session if you were already studying).\n"
                    "**Point payout:** when the **target** ends their first qualifying session after activation, both users "
                    "get points: **60m=+500**, then **+100** per extra hour up to **+800 max** at 4h.\n"
                    "DM toggles: **`Bounty Activated`** and **`Bounty Payout`** in `/settings`.\n"
                    "**`/beacon`** — **10c**, **10s** cooldown. Command text: **1.5× raid damage** aura plus **200** "
                    "bonus study **points/hour** in the beacon channel for **2h** (raid embeds still explain stacking)."
                ),
                inline=False,
            )
            .add_field(
                name="`/prestige` (hard gate)",
                value=(
                    "You **cannot** prestige until you hit **Level 50** (the RPG cap before resetting). At **Prestige "
                    "5** the bot refuses further prestiges — that is the current max tier.\n"
                    "The confirm embed lists the real consequences: you go back to **Level 1**, **banked XP** is "
                    "wiped, and the next perk track unlocks — **points, coins, streak, and badges** are kept. "
                    "Only tap **Prestige Up!** when you have read that card.\n"
                    "Unlike hidden **badge** criteria, prestige is meant to be **readable progression** — below is "
                    "what the code actually applies today (perks **stack** as you climb)."
                ),
                inline=False,
            )
            .add_field(
                name="Prestige — coins & `/convert`",
                value=(
                    "These use your numeric **prestige** row, not mystery rolls:\n"
                    "**P1+** — each `/convert` costs **1,800** study points per coin (**2,000** at **P0**).\n"
                    "**P3+** — up to **3** successful point→coin **conversions** per **EST** day (**1**/day before that).\n"
                    "**P4+** — `/convert` **daily cap effectively removed**; **boss-coin wallet** cap rises from **50** "
                    "to **100** coins.\n"
                    "**P5** — while stuck at **Level 50**, **banked XP** can grow to a **much higher ceiling** than at "
                    "lower prestiges."
                ),
                inline=False,
            )
            .add_field(
                name="Prestige — named perks (stack)",
                value=(
                    "**P1 — Resilient** — **Emergency Save** costs **2c** instead of **3c** (same Mon–Fri / empty-freeze "
                    "rules).\n"
                    "**P2 — Tactician** — `/quest reroll` once per EST day (see Quests chapter).\n"
                    "**P2 — Scavenger** — extra **raid participation** loot rolls when you are **not** on the podium "
                    "(raid resolution code), plus a safety roll for some edge cases.\n"
                    "**P3 — Time Lord** — potion buff **duration doubles**; **Beacon** runs **4 hours** instead of **2**.\n"
                    "**P5 — Master Alchemist** — active potions use a **2.0×** XP multiplier (overrides lower multi).\n"
                    "**P5 — Grandmaster Aura** — if **any** member in a completed **Group Pomodoro** has this perk, "
                    "everyone’s qualifying session payout uses **1.5×** group XP (**1.25×** otherwise)."
                ),
                inline=False,
            )
            .add_field(
                name="Coin sinks: `/shop` · `/gacha`",
                value=(
                    "**`/shop`** — buy potions and other catalog rows for coins (prices on the browse embed).\n"
                    "**`/gacha`** — spend coins for variable **point** payouts; history lands in **`/export`** as "
                    "`gacha_*.csv`. See **`/bounty` · `/beacon`** above for social/raid sinks."
                ),
                inline=False,
            )
            .add_field(
                name="Personal rewards: `/reward` …",
                value=(
                    "`/reward add` · `/reward list` · `/reward redeem` · `/reward delete` · `/reward history`\n"
                    "Build a **private points shop** (movie night, snack, etc.). Costs are **study points only** — "
                    "never boss coins.\n"
                    "**`/reward redeem`** opens a **confirm button** view before points leave your balance; "
                    "autocomplete on IDs matches `/reward list`."
                ),
                inline=False,
            )
        )

    if idx == 5:
        return (
            discord.Embed(
                title="⚔️ Chapter — Quests & raids",
                description="Daily **quests** + rotating **raid bosses** convert honest minutes into points, badges, and community drama.",
                color=TUTORIAL_COLOR,
            )
            .add_field(
                name="`/quests`",
                value=(
                    "Shows **three tiers** (easy/medium/hard) with per-quest progress bars, names/descriptions from "
                    "the quest pools, and per-tier **point payouts** baked into the headers (**+25 / +75 / +150** "
                    "pts today).\n"
                    "Finishing **all three** grants an extra **+250** pts **and** **+200** XP (footer celebrates when "
                    "done).\n"
                    "**Quest Completion** in `/settings` controls whether solo completion / all-done celebrations "
                    "**DM** you."
                ),
                inline=False,
            )
            .add_field(
                name="How quests advance (metrics)",
                value=(
                    "Progress hooks into study/social/task/pomo flows — e.g. **Revisit** (“Open your stats”) ticks when "
                    "you run **`/stats`** (`stats_viewed`). Other rows map to cheers, tasks, notes, pauses, pomo "
                    "cycles, group pomo, SRS reviews, etc. If a row looks stuck, do the literal action from its "
                    "description."
                ),
                inline=False,
            )
            .add_field(
                name="`/quest reroll`",
                value=(
                    "Requires **Prestige 2 — Tactician** perk. Pick tier **1–3** to replace that row once per **EST "
                    "day** (bot enforces daily key). Fails softly if quest already complete or perk missing."
                ),
                inline=False,
            )
            .add_field(
                name="`/raid`",
                value=(
                    "When a boss is live: **HP**, time remaining, **your damage** contributed this cycle. **1 recorded "
                    "study minute ⇒ 1 damage** baseline; **Beacon** in the same channel as your session’s live card "
                    "can raise damage (see raid embed footers).\n"
                    "When **no** boss is active the reply literally says **“Check back Monday!”** (and `/raid_leaderboard` "
                    "shows a compact **no active boss** state)."
                ),
                inline=False,
            )
            .add_field(
                name="Raid announcements",
                value=(
                    "Boss spawn/kill fanfare is posted to the server’s configured **Raid** announcement channel, "
                    "falling back to **General** when raid isn’t set (host-side setup).\n"
                    "Pair with the **Raid Announcements** DM toggle in `/settings` if you want companion DMs."
                ),
                inline=False,
            )
            .add_field(
                name="`/raid_leaderboard`",
                value=(
                    "Top **15** damage dealers for the **current** boss with medals, usernames, and raw damage totals. "
                    "Podium finishes can feed **badge** checks when the boss dies (see badges chapter)."
                ),
                inline=False,
            )
        )

    if idx == 6:
        return (
            discord.Embed(
                title="🏆 Chapter — Profile, settings, badges, boards",
                description="Identity, privacy, cosmetics, and flex surfaces — all optional but deeply hooked into the RPG loop.",
                color=TUTORIAL_COLOR,
            )
            .add_field(
                name="`/profile`",
                value=(
                    "Shows **your** RPG card by default; pass **`user`** to inspect another member’s **full** stats "
                    "card (level/XP, streak, freezes, coins, points, season rank, featured badges — cosmetic LB icon "
                    "thumbnail when equipped).\n"
                    "**Ghost** still hides people from **`/who`** / leaderboards; `/profile` itself does not redact "
                    "numbers."
                ),
                inline=False,
            )
            .add_field(
                name="`/settings`",
                value=(
                    "**Buttons:** toggle **Ghost** (hides you from `/who`, leaderboards, bounty targeting) and **Block "
                    "Cheers**.\n"
                    "**Select menu:** flip individual **DM** switches — **Raid Announcements**, **Quest Completion**, "
                    "**Badge Unlocks**, **Inactivity Warnings**, **Schedule Reminders**, **Morning Briefing**, "
                    "**Weekly Report**, **Cheer Received**, **Bounty Activated**, **Bounty Payout**.\n"
                    "Some **wellbeing** alerts still fire regardless — the embed calls that out. Ghost + cheer blocks "
                    "stack with `/cheer` validation on the social cog."
                ),
                inline=False,
            )
            .add_field(
                name="`/badges`",
                value=(
                    "Shows only badges **you have already earned** (names + tiers come from the live registry). "
                    "Nothing in **this tutorial** lists secret badge keys, full rosters, or unlock thresholds — those "
                    "stay for you to discover in-app as you play.\n"
                    "Empty state nudges toward hidden achievements. Footer echoes **featured** picks if configured."
                ),
                inline=False,
            )
            .add_field(
                name="`/badge` group",
                value=(
                    "`/badge featured` — pick up to **three** showcase keys (autocomplete only shows badges you own).\n"
                    "`/badge clear` — wipe featured slots so `/profile` falls back to defaults."
                ),
                inline=False,
            )
            .add_field(
                name="`/leaderboard`",
                value=(
                    "Server-scoped, **top 15** per category (default **XP** if you omit the option): **XP**, **Study "
                    "Minutes**, **Streak**, **Seasonal Rank**, **Raid Damage**.\n"
                    "**Ghost** users are filtered out — you will not appear even if you would podium."
                ),
                inline=False,
            )
            .add_field(
                name="Badges ↔ economy",
                value=(
                    "Each time you **first** hit a new badge **tier**, the bot grants a fixed **study-point** bundle "
                    "(same amount shown in the unlock DM — not listing counts here to avoid spoiling discovery).\n"
                    "`/badges` is read-only; `/badge featured` / `/badge clear` manage the three profile slots."
                ),
                inline=False,
            )
        )

    if idx == 7:
        return (
            discord.Embed(
                title="🤝 Chapter — Social, streaks, goals, check-ins",
                description="Light accountability + calendar math (everything still **EST**-anchored).",
                color=TUTORIAL_COLOR,
            )
            .add_field(
                name="`/cheer` · `/who`",
                value=(
                    "**`/cheer`** — **10s** per-user cooldown; cannot target yourself. **Ghost** or **Block Cheers** "
                    "targets hard-stop the send. Grants quest credit where applicable; optional **Cheer Received** "
                    "DM for the target.\n"
                    "**`/who`** — **5s** cooldown; lists active sessions (subject + elapsed) while skipping **Ghost** "
                    "rows entirely."
                ),
                inline=False,
            )
            .add_field(
                name="`/streak`",
                value=(
                    "Shows current streak tier, freezes, weekend bonuses, and guard rails. Read the embed footers "
                    "for how midnight EST rolls streak days."
                ),
                inline=False,
            )
            .add_field(
                name="`/goals` …",
                value=(
                    "`/goals set` — default daily minute target.\n"
                    "`/goals set-day` — per-weekday overrides (great for class vs weekend cadence).\n"
                    "`/goals view` — table of all configured targets.\n"
                    "`/goals progress` — compares **today’s** logged minutes vs the active goal (ties into `/today`)."
                ),
                inline=False,
            )
            .add_field(
                name="`/checkin` …",
                value=(
                    "`/checkin settings` — enable/disable + pick **hour (0–23 EST)** for the nightly DM.\n"
                    "When enabled: already-studied days auto-✅ silently; otherwise you get **✅/❌** buttons. "
                    "The settings embed states that tapping **❌** **does not** break your streak — only skipping "
                    "real study does.\n"
                    "`/checkin history` — last **14** days with icons + optional minutes column.\n"
                    "`/checkin now` — manual log for **today**; marking **studied** updates streak-facing fields per "
                    "the confirmation embed (no studied ⇒ honest miss path)."
                ),
                inline=False,
            )
            .add_field(
                name="`/adaptive`",
                value=(
                    "Binary toggle stored per user. When **on**, the bot’s weekly copy explains it will look at "
                    "goal-hit cadence after resets: **6+/7** days hit suggests **raising** the default goal, "
                    "**2/7 or fewer** suggests **scaling back**.\n"
                    "`/stats` and `/today` **invoke the same adaptive-goal maintenance** the bot runs after resets, so "
                    "opening those dashboards refreshes suggestions without a separate slash."
                ),
                inline=False,
            )
        )

    if idx == 8:
        return (
            discord.Embed(
                title="✅ Chapter — Tasks, projects, reminders, schedule",
                description="Operational layer: todos, long-running workstreams, pings, and weekly cadence — **none** auto-start `/study`.",
                color=TUTORIAL_COLOR,
            )
            .add_field(
                name="`/task` …",
                value=(
                    "`create` (guided form) · `add` · `list` · `complete` · `complete_many` · `delete` · `history` · `reviews`\n"
                    "**`add`** — completion **points** clamp **1–500**, priority high/medium/low, optional due date "
                    "(**MM/DD/YYYY**, **MM-DD-YYYY**, or **YYYY-MM-DD**), optional **`project_id`**, optional **SRS review** with interval **1–60** "
                    "days.\n"
                    "**`complete_many`** — paste up to **15** task IDs (commas/spaces); rejects junk with explicit "
                    "errors.\n"
                    "**`reviews`** — due spaced-repetition rows; finishing them feeds quest + badge hooks in-command."
                ),
                inline=False,
            )
            .add_field(
                name="`/project` …",
                value=(
                    "`add` · `list` · `view` · `done` · `delete`\n"
                    "Projects group mental scope; deleting a project **does not** delete tasks (see warning text)."
                ),
                inline=False,
            )
            .add_field(
                name="`/remind` …",
                value=(
                    "`at` — Discord date/time picker. `add` — **your saved timezone** (`30m`, `2h30m`, `3:30pm`, `tomorrow 9am`, "
                    "`6/5/2026 1:00pm`, or `2026-06-05 13:00`); **`message` 1–200** characters.\n"
                    "`list` / `delete` — numeric IDs from `list`; fire times stored in UTC, displayed in your Discord timezone. Right-click a message → Apps → Remind me about this to keep its link."
                ),
                inline=False,
            )
            .add_field(
                name="`/schedule` …",
                value=(
                    "`add` — recurring blocks with **day grammar** (`MWF`, `weekdays`, `daily`, comma lists, etc.).\n"
                    "`create` — guided day/time form. New blocks use the timezone in `/settings`; existing blocks keep their original timezone.\n"
                    "`view` — splits across multiple embed fields when you hit many blocks.\n"
                    "`delete` — remove by block ID from `view`.\n"
                    "Footer reminds you to enable **Schedule Reminders** in `/settings` for DM nudges."
                ),
                inline=False,
            )
        )

    if idx == 9:
        return (
            discord.Embed(
                title="📊 Chapter — Stats, breakdown & export",
                description="Numbers for introspection; pair with `/today` when you only want the essentials.",
                color=TUTORIAL_COLOR,
            )
            .add_field(
                name="`/stats`",
                value=(
                    "Full **dashboard** embed: XP bar, points/coins, streak tier, weekly vs all-time minutes, session "
                    "counts, best/avg session, tasks/projects counts, rewards shop stats, seasonal rank, goals, raid "
                    "snapshot. Triggers adaptive goal maintenance when viewed."
                ),
                inline=False,
            )
            .add_field(
                name="`/today`",
                value=(
                    "Interactive daily dashboard: goal, streak, active session controls, tasks and reviews, next study block, plus quick forms and Pomodoro. Quests, raids and potions appear in supported server contexts. Refresh to update the card."
                ),
                inline=False,
            )
            .add_field(
                name="`/breakdown` …",
                value=(
                    "`subjects` — top subjects over **30** days with bars + session counts + XP + optional focus stars; "
                    "Discord caps fields so only the **top 24** subjects render (+ “more not shown” line).\n"
                    "`week` — rolling **7** EST days with goal markers (**✅/🎯/🛌**) explained in the footer.\n"
                    "`focus` — hourly histogram (**AM/PM** split) + insight block; footer reminds you to **star-rate** "
                    "`/study stop` so the model has signal."
                ),
                inline=False,
            )
            .add_field(
                name="`/export`",
                value=(
                    "Builds **`studybot_export_YYYYMMDD_HHMM.zip`** (timestamp in **EST**) with **seven** CSVs: "
                    "**sessions**, **tasks**, **point transactions**, **reward redemptions**, **check-ins**, "
                    "**badges**, **gacha pulls** — counts echo in the embed fields.\n"
                    "Sessions timestamps are normalized to **EST strings** inside the CSV for spreadsheet friendliness."
                ),
                inline=False,
            )
            .add_field(
                name="Wellbeing & long sessions",
                value=(
                    "**Long `/study`** runs the inactivity monitor (DM check-in; auto-stop if ignored).\n"
                    "**Very high daily study totals** can trigger a wellbeing nudge even when other DM toggles are "
                    "off — the embed explains why."
                ),
                inline=False,
            )
        )

    return discord.Embed(
        title="📖 Tutorial",
        description="Unknown topic index.",
        color=TUTORIAL_COLOR,
    )


class TutorialTopicSelect(discord.ui.Select):
    def __init__(self, *, is_lite: bool):
        if is_lite:
            opts = [
                discord.SelectOption(label="Overview", value="0", description="Core loop & time zones"),
                discord.SelectOption(label="Study sessions", value="1", description="Every /study subcommand"),
                discord.SelectOption(label="Pomodoro", value="2", description="/pomodoro & /group_pomo"),
                discord.SelectOption(label="Temptation bundle", value="3", description="/bundle rules"),
                discord.SelectOption(label="Tasks & schedule", value="8", description="/task /project /remind /schedule"),
                discord.SelectOption(label="Stats & export", value="9", description="/stats /today /breakdown /export"),
            ]
        else:
            opts = [
                discord.SelectOption(label="Overview", value="0", description="Core loop & time zones"),
                discord.SelectOption(label="Study sessions", value="1", description="Every /study subcommand"),
                discord.SelectOption(label="Pomodoro", value="2", description="/pomodoro & /group_pomo"),
                discord.SelectOption(label="Temptation bundle", value="3", description="/bundle rules"),
                discord.SelectOption(label="Economy & rewards", value="4", description="Points, coins, shop, potions"),
                discord.SelectOption(label="Quests & raids", value="5", description="/quests /quest /raid …"),
                discord.SelectOption(label="Profile & badges", value="6", description="/profile /settings /badges …"),
                discord.SelectOption(label="Social & goals", value="7", description="/cheer /who /streak /goals …"),
                discord.SelectOption(label="Tasks & schedule", value="8", description="/task /project /remind /schedule"),
                discord.SelectOption(label="Stats & export", value="9", description="/stats /today /breakdown /export"),
            ]
        super().__init__(placeholder="Choose a chapter…", min_values=1, max_values=1, options=opts, row=0)

    async def callback(self, interaction: discord.Interaction):
        idx = int(self.values[0])
        embed = _embed(idx, is_lite=getattr(self.view, "is_lite", False))
        await interaction.response.edit_message(embed=embed, view=self.view)


class TutorialNavView(discord.ui.View):
    def __init__(self, *, is_lite: bool = False):
        super().__init__(timeout=900)
        self.is_lite = is_lite
        self.add_item(TutorialTopicSelect(is_lite=is_lite))


class Tutorial(commands.Cog):
    """Interactive long-form tutorial."""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="tutorial", description="Deep dive: how each part of StudyBot works")
    async def tutorial(self, interaction: discord.Interaction):
        is_lite = getattr(self.bot, "is_lite_user", lambda _uid: False)(interaction.user.id)
        embed = _embed(0, is_lite=is_lite)
        await interaction.response.send_message(embed=embed, view=TutorialNavView(is_lite=is_lite), ephemeral=True)


async def setup(bot):
    await bot.add_cog(Tutorial(bot))
