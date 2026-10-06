# StudyBot

StudyBot is a self-hosted Discord bot that combines study tracking and planning tools with RPG progression. Solo and group Pomodoro sessions feed into a persistent history, while recorded study time can earn XP, points, and shared raid damage. A productivity-only mode keeps the study tools available without the game systems for configured users.

The project is written in Python with discord.py, SQLite, and APScheduler. It is under development; the automated checks cover selected database and reward invariants rather than proving every Discord interaction works.

## What it does

- **Study sessions:** start a live timer, pause and resume, extend a target, attach notes and tags, or switch subjects without ending the timer. Subject changes are recorded as segments so time can be attributed to the appropriate subject.
- **Pomodoro:** work and break phases for individual sessions, plus group lobbies with join codes, membership, start votes, and a host-controlled begin command.
- **Planning:** tasks grouped into projects, bulk task completion with a short undo window, spaced-repetition review tasks, weekday study goals, recurring study blocks, and one-off reminders. Schedules prompt users to study; they do not automatically start a session.
- **Progress and reflection:** daily snapshots, study streaks, subject and tag breakdowns, weekly activity, focus-hour summaries, and optional evening check-ins with adaptive goal suggestions.
- **Rewards:** XP tiers and milestone bonuses, levels and prestige, study points, personal reward shops, coins, inventory, potions, daily quests, and badges. Temptation bundling sends a configured reward message after a chosen study or Pomodoro condition is met.
- **Shared game systems:** raid bosses take damage from recorded study time. Bounties, beacons, cheers, and leaderboards add social incentives. The configurable private `/duo` board is separate from the general leaderboards.
- **Data and administration:** CSV exports packaged as a ZIP, database backups, owner-only diagnostics, and server allowlisting. Settings include notification controls and options that reduce public visibility of activity.

The bot records timer activity and user input. It does not independently verify that someone was studying, and its adaptive suggestions are rule-based rather than an AI service.

## Persistence and recovery

SQLite stores sessions, subject segments, planning records, progression, and scheduled state. Connections enable write-ahead logging and foreign keys, with configurable busy timeouts. Startup includes recovery paths for existing study and Pomodoro state.

A persistent notification outbox supports deduplication keys, claiming, retries, and recovery of stale sending records. Discord delivery and a local database commit are separate operations, so this is not a guarantee of exactly-once delivery. Regression tests also cover repeated bounty use and refunds, reward calculations, group start votes, reminder toggles, and repeated raid-end reward claims.

The gateway watchdog and reconnect loop detect stalled connections and back off after errors. Windows scripts provide optional process supervision through a watchdog or NSSM service. None of these mechanisms replaces testing the bot in the server where it will run.

## Setup

The publication copy was checked with Python 3.12. Use a fresh environment rather than copying another machine's virtual environment.

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

Open `/tutorial` for the detailed guide or `/help` for commands. Common starting points are `/study start`, `/pomodoro start`, `/group start`, `/task add`, `/schedule add`, and `/today`. `/source` provides the configured source link and license.

GitHub hosts the code. You still need a computer or server running the bot and a Discord application of your own.

## Development checks

Run from the repository root in the configured environment:

```powershell
.\venv\Scripts\python.exe -m pytest -q
.\venv\Scripts\python.exe smoke_check.py
.\venv\Scripts\python.exe scripts/db_sim_test.py
```

The tests create temporary databases. The smoke check parses application code and checks structural contracts; the database simulation exercises a small outbox flow without connecting to Discord. `scripts/bug_finder.py --lane outbox` runs the focused outbox test lane.

`requirements.txt` describes the dependency ranges. `requirements-lock.txt` records the versions used for publication checks.

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
