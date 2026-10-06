# StudyBot

StudyBot is a self-hosted study and accountability system for Discord. It combines persistent focus tracking with collaborative Pomodoro sessions, a task and review planner, and an RPG economy built around recorded study activity.

The systems share the same study history: a completed focus session can update a daily goal, advance a quest, contribute damage to a community raid boss, and appear in a weekly report. Users can also define their own rewards or use a productivity-only mode without the RPG features.

The implementation uses Python, discord.py, SQLite, and APScheduler, with 20 feature modules covering the study workflow, progression, social interactions, and administration.

## Study and planning

### Focus sessions with a persistent history

`/study` provides a live session card with controls for pausing, resuming, adding notes, and stopping. Sessions can have a target, subject, and tags. Users can extend a target or switch subjects while keeping one timer running.

Subject switches create separate session segments. Each segment records its timing and pause offset, allowing subject and tag breakdowns to attribute time within a session rather than assigning the entire session to its final subject. Completed sessions support a focus rating, and the live card shows progress toward the target and upcoming XP bonuses.

Long-running sessions receive an inactivity check with a response window and an automatic stop path. This helps handle forgotten timers; it does not independently establish whether someone was studying.

### Solo and group Pomodoro

Solo Pomodoro supports configurable work periods, short and long breaks, cycle limits, phase notifications, and skip/stop controls. Work phases connect to study sessions and their reward calculations.

Group Pomodoro adds shared lobbies for up to 10 participants, join codes, a public lobby card, and synchronized work/break phases. A host can begin a session directly. A majority start vote can also begin a waiting lobby with at least three members. Lobbies with at least two members have a one-hour auto-start path with a five-minute warning and a host cancellation control.

Lobby membership and phase timestamps are persisted. Recovery code re-arms group countdowns after a restart or gateway reconnect and transitions overdue phases instead of always restarting the full countdown. Solo timers also have timestamp-based recovery paths.

### Tasks, projects, and spaced repetition

Tasks support priorities, due dates, descriptions, and project grouping. Autocomplete and per-user task numbers keep task selection separate from internal database IDs. Bulk completion supports up to 15 tasks, with a short undo window for individual or batch completions and rollback handling if a batch fails partway through.

Review tasks follow an interval progression from one day up to 60 days. Completing a review creates the next dated review task and carries forward its project and description. The review workflow has its own reward progression and daily reward limits; undo handling also accounts for the next review it created.

Recurring study blocks and one-off reminders connect planning to Discord notifications. Weekday-specific goals let users plan different workloads across the week. Scheduled blocks prompt users to begin; they do not start focus tracking automatically.

### Reflection and adaptive goals

Optional evening check-ins record whether a user studied and can include a note. The history combines check-in responses with recorded study activity. Streak handling includes weekend rules and earned freezes for missed days.

Adaptive goal suggestions use the previous seven days of goal completion. Consistently meeting a goal can prompt a higher target, while frequently missing it can prompt a lower one. Suggestions are rate-limited to once a week and leave the change to the user.

Daily dashboards and weekly reports bring together recorded minutes, task completions, goal days, and streaks. Subject and tag breakdowns, day-by-day activity bars, hourly focus patterns, and user-provided ratings offer different ways to review study habits. Tag reports include selectable time ranges.

## Progression and shared incentives

### An economy connected to study activity

Study time earns tiered XP with duration milestone bonuses. Levels lead into prestige progression with perks. Study points support personal reward shops and purchases, while conversion into boss coins introduces a separate currency with caps and expiring overflow.

Inventory and timed potions affect the reward pipeline. Potion bonuses are weighted by their overlap with the session, with a cap on the effective multiplier. Bounty XP also uses the overlap between the session and the activated bonus window. Shared calculation helpers keep those bonus rules available to both study and Pomodoro paths.

Longer sessions can produce weighted loot drops. The economy also includes tiered gacha outcomes, conversion tracking, and transaction history. A minimum recorded session duration gates study rewards to reduce rewards from trivial timer starts.

### Quests, raids, and seasons

Daily quests draw from difficulty-tiered pools and track study events alongside planning and social activity. Badge categories cover persistence, group participation, raid contributions, review activity, and economy milestones. Profiles can display featured badges.

Recorded study time contributes damage to a shared raid boss. Beacons provide temporary channel-based incentives, and bounties let one user sponsor another's study session. Expired unused bounties have a refund path. Raid resolution handles participation and podium rewards, and the next boss's HP is calculated from damage dealt and whether the previous boss was defeated.

Seasonal ranks and archived results provide progression beyond individual sessions. Leaderboards cover different activity metrics, while the configurable private `/duo` board offers a smaller comparison group. Cheers and a current-studying view support encouragement without requiring everyone to join a group timer.

### Personal rewards and notification choices

Personal reward shops let users define what their points can buy. Temptation bundling provides another option: a chosen treat label and optional link are sent after a completed Pomodoro work block, at the start of a break, or after a study session meets a configured duration. This is a reward-message workflow, not application blocking.

Productivity-only accounts keep access to focus and planning tools without the RPG systems. Settings offer notification-category controls, ghost mode, and a choice to block cheers. Server allowlisting and separate DM/RPG interaction gates define where different workflows are available.

`/export` packages study sessions, tasks, point transactions, reward redemptions, check-ins, badges, and gacha history into CSV files inside a ZIP. The deployment owner can also create database backups and inspect operational status.

## Engineering and recovery

The bot coordinates persistent records, scheduled jobs, and asynchronous Discord interactions:

- **State and accounting:** SQLite schema migrations, write-ahead logging, foreign keys, and configurable lock timeouts support the shared persistence layer. Per-user asynchronous locks coordinate selected session, task, and Pomodoro operations.
- **Repeat-safe operations:** guarded database updates cover operations such as claiming a raid's end, consuming a bounty, and issuing expired-bounty refunds. Regression tests exercise repeated calls to these paths.
- **Durable notifications:** the SQLite outbox stores delivery state and deduplication keys. Its worker claims batches, retries delivery, recovers stale sending records, and checks relevant notification settings. Discord delivery and database commits remain separate operations, so this does not guarantee exactly-once delivery.
- **Scheduled recovery:** startup reloads pending reminders and timer state. A bounded schedule catch-up path handles recently missed study blocks, while reconnect handling avoids rerunning the full startup catch-up on every connection recovery.
- **Process supervision:** a gateway watchdog detects stalled connection attempts and prolonged disconnects. The reconnect loop backs off and can exit after repeated identical failures so an external supervisor can restart it. Windows watchdog and NSSM installation scripts are included.
- **Operational controls:** owner-only diagnostics cover database health, scheduler state, usage, and configured channels. Graceful shutdown includes a SQLite WAL checkpoint.

The automated checks include database/outbox behavior, study segments, reward calculations, group start votes, schedule reminder settings, bounty use and refunds, and raid-end claims. Structural smoke checks also inspect startup contracts, extension loading, notification wiring, and schema requirements.

## Setup

Setup was checked with Python 3.12. Use a fresh environment rather than copying another machine's virtual environment.

```powershell
git clone https://github.com/chentaot1/StudyBot.git
cd StudyBot
py -3.12 -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements-lock.txt
Copy-Item .env.example .env
```

On Linux or macOS, create the environment with `python3.12 -m venv venv`, then use `venv/bin/python` for the equivalent install and run commands.

Create a Discord application and bot in the [Discord Developer Portal](https://discord.com/developers/applications). Enable the **Server Members Intent** and **Message Content Intent**, which the code requests. Install the bot in your server with the `bot` and `applications.commands` scopes. Give it the permissions needed for the features you use, including reading and sending messages, embeds and attachments, message history, and reactions. The optional reaction-role feature also needs Manage Roles and a bot role above the announcement role.

Edit `.env` locally:

- `DISCORD_TOKEN`: your bot token.
- `OWNER_ID`: the Discord user ID allowed to use owner administration commands.
- `ALLOWED_GUILD_IDS`: the server IDs for this deployment, separated by commas. At least one is required before connecting.
- `TIMEZONE`: an IANA timezone name; defaults to `America/New_York`. Some existing command labels still refer to Eastern time.
- `DB_PATH` and `LOG_DIR`: local database and log locations. Relative paths are resolved from the directory where you start the bot.
- `LITE_USER_IDS`: optional productivity-only user IDs.
- `DUO_LEADERBOARD_USER_IDS`: optional IDs for the private duo board, in display order.
- `SB_PING_ROLE_ID`: optional announcement/reaction role. Leave blank if you do not configure that feature.
- `BOT_SOURCE_URL`: the repository or release containing the source of the version you run.

The ID settings do not ship with personal defaults. The `.env` file, databases, exports, logs, and backups belong to the deployment and are excluded from Git.

### Register commands and run

The example configuration sets `SYNC_GUILD_COMMANDS=true` to register commands in the configured server on the first boot. When one server is allowlisted, it is used as the sync target; otherwise set `DEV_GUILD_ID`. Turn syncing off after registration and enable it again when updating commands.

For supported commands in DMs, set `SYNC_COMMANDS=true` for a boot to register global commands, or use `SYNC_GLOBAL_COMMAND_SYNC=true` alongside guild sync. RPG commands have separate server-only gates. User-install support also depends on the application's Discord installation settings. Guild-only registration does not make commands available in DMs.

```powershell
.\venv\Scripts\python.exe bot.py
```

Open `/tutorial` for the detailed guide or `/help` for commands. Common starting points are `/study start`, `/pomodoro start`, `/group_pomo start`, `/task add`, `/schedule add`, and `/today`. `/source` provides the configured source link and license.

GitHub hosts the code. You still need a computer or server running the bot and a Discord application of your own.

## Verification

StudyBot is a working, self-hosted Discord bot. Automated checks passed **53 tests**, the structural smoke check, a small database simulation, and an offline startup that loaded all 20 feature modules.

Recorded time and focus ratings are user-reported signals. Adaptive goal suggestions are rule-based, and no AI service is required. The bot is self-hosted: the operator controls its database and credentials, and Discord carries the messages and interactions.

## Running the checks

Run from the repository root in the configured environment:

```powershell
.\venv\Scripts\python.exe -m pytest -q
.\venv\Scripts\python.exe smoke_check.py
.\venv\Scripts\python.exe scripts/db_sim_test.py
```

The tests create temporary databases. The smoke check parses application code and checks structural contracts; the database simulation exercises a small outbox flow without connecting to Discord. `scripts/bug_finder.py --lane outbox` runs the focused outbox test lane.

`requirements.txt` describes the dependency ranges. `requirements-lock.txt` records the dependency versions used for the automated checks.

## Source layout

- `bot.py`: Discord startup, interaction gates, scheduled jobs, notification delivery, and recovery.
- `cogs/`: slash commands and feature-specific Discord interactions.
- `database.py`: SQLite schema, migrations, and persistence operations.
- `services/rewards_engine.py`: reward calculations shared by study and Pomodoro paths.
- `constants.py` and `env_config.py`: balance values and deployment settings.
- `views/`: onboarding controls; `utils.py` and `temptation_bundle.py`: shared helpers.
- `tests/`, `smoke_check.py`, and `scripts/`: regression and diagnostic checks.
- `run_forever.ps1` and `install_nssm_service.ps1`: optional Windows supervision.

## License

Copyright (c) 2026 chentaot1.

StudyBot's original source code and documentation are licensed under the [GNU Affero General Public License, version 3](LICENSE) (`AGPL-3.0-only`). The software is provided without warranty. Third-party dependencies and material retain their own licenses and terms.

If you operate a modified version that users interact with over a network, Section 13 requires a prominent offer of access to the corresponding source for that version. Set `BOT_SOURCE_URL` to your deployed fork or release, including your modifications, and keep `/source` accessible to those users. The command is a source-link mechanism; the linked material must actually satisfy the license's corresponding-source requirements.
