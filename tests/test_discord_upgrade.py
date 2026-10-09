# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

import asyncio
import json
import threading
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from database import Database
from services.db_worker import DatabaseWorker
from services.scheduling import due_blocks, next_block, occurrence, valid_timezone


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / "upgrade.db"))
    database.initialize()
    database.ensure_user(1, "Student")
    return database


def fake_bot(db):
    return SimpleNamespace(db=db, db_worker=DatabaseWorker(), user_locks=defaultdict(asyncio.Lock), tree=SimpleNamespace(interaction_check=AsyncMock(return_value=True)), is_lite_user=lambda uid: False, allowed_guild_ids={99}, get_channel=Mock(), fetch_channel=AsyncMock(), get_cog=Mock(return_value=None), cogs={})


def interaction(bot, uid=1):
    return SimpleNamespace(client=bot, user=SimpleNamespace(id=uid, display_name="Student"), guild_id=99, response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock(), send_modal=AsyncMock(), edit_message=AsyncMock(), is_done=Mock(return_value=False)), followup=SimpleNamespace(send=AsyncMock()), expires_at=datetime.now(timezone.utc) + timedelta(minutes=15))


def test_schedule_dst_skip_and_single_fall_occurrence():
    block = {"id": 1, "user_id": 1, "days_of_week": "sunday", "hour": 2, "minute": 30, "timezone": "America/New_York"}
    assert occurrence(block, datetime(2026, 3, 8).date()) is None
    block.update(hour=1)
    assert len(due_blocks([block], datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc))) == 1
    assert not due_blocks([block], datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc))


def test_schedule_timezone_and_next_week(db):
    block_id = db.add_schedule_block(1, "Math", "monday", 9, 0, 60, timezone_name="Europe/London")
    block = db.get_user_schedule(1)[0]
    assert block["id"] == block_id and block["timezone"] == "Europe/London"
    now = datetime(2026, 10, 12, 8, 1, tzinfo=timezone.utc)
    selected, start = next_block([block], now)
    assert selected["id"] == block_id
    assert start == datetime(2026, 10, 19, 8, tzinfo=timezone.utc)
    assert due_blocks([block], datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc))[0]["starts_at"].hour == 8


def test_schedule_default_migration_preserves_eastern(db):
    block_id = db.add_schedule_block(1, "Legacy", "monday", 9, 0, 60)
    session_id = db.start_session(1, "Legacy study")
    task_id = db.add_task(1, "Legacy task", "", 10, "medium", None)
    # Recreate the pre-upgrade schema with real saved rows, then migrate it twice.
    with sqlite3.connect(db.path) as conn:
        conn.execute("ALTER TABLE schedule_blocks DROP COLUMN timezone")
        conn.execute("ALTER TABLE study_sessions DROP COLUMN live_kind")
        conn.execute("ALTER TABLE tasks DROP COLUMN source_message_url")
    db.initialize()
    db.initialize()
    block = db.get_user_schedule(1)[0]
    session = db.get_active_session(1)
    task = db.get_user_tasks(1)[0]
    assert block["id"] == block_id and block["timezone"] == "America/New_York"
    assert session["id"] == session_id and session["live_kind"] == "ephemeral"
    assert task["user_task_num"] == task_id and task["source_message_url"] is None
    with pytest.raises(ValueError):
        valid_timezone("This/DoesNotExist")


def test_explicit_preferences_are_atomic_and_keep_existing_blocks(db):
    db.add_schedule_block(1, "Math", "monday", 9, 0, 60)
    db.save_preferences(1, ghost=True, block_cheers=True, settings={"timezone": "Europe/London", "dm_schedule_reminders": "0"})
    assert db.get_user(1)["ghost_mode"] == 1
    assert not db.get_dm_enabled(1, "schedule_reminders")
    assert db.get_user_schedule(1)[0]["timezone"] == "America/New_York"
    with pytest.raises(ValueError):
        db.save_preferences(1, ghost=False, block_cheers=False, settings={"timezone": "Invalid/Zone"})
    assert db.get_user(1)["ghost_mode"] == 1


def test_migration_preserves_custom_deployment_timezone(db, monkeypatch):
    import database
    db.add_schedule_block(1, "Legacy London block", "monday", 9, 0, 60)
    with sqlite3.connect(db.path) as conn:
        conn.execute("ALTER TABLE schedule_blocks DROP COLUMN timezone")
    monkeypatch.setattr(database, "DEFAULT_TIMEZONE", "Europe/London")
    db.initialize()
    db.initialize()
    block = db.get_user_schedule(1)[0]
    assert block["timezone"] == "Europe/London" and block["hour"] == 9


def test_outbox_accepts_serialized_embed_and_dedupes(db):
    payload = json.dumps({"discord": {"title": "Study time"}})
    kwargs = dict(target_type="user", target_id=1, embed_json=payload, dedupe_key="same-occurrence")
    first = db.enqueue_outbox(**kwargs)
    assert db.enqueue_outbox(**kwargs) == first
    assert db.claim_outbox_batch()[0]["embed_json"] == payload
    with pytest.raises(ValueError):
        db.enqueue_outbox(target_type="user", target_id=1, embed={}, embed_json=payload)


def test_worker_does_not_block_event_loop_and_serializes_writes():
    async def run():
        worker = DatabaseWorker()
        entered, release = threading.Event(), threading.Event()
        sequence = []
        def first():
            entered.set()
            assert release.wait(2)
            sequence.append(1)
        try:
            job = asyncio.create_task(worker.run(first))
            for _ in range(200):
                if entered.is_set(): break
                await asyncio.sleep(.005)
            assert entered.is_set()
            second = asyncio.create_task(worker.run(lambda: sequence.append(2)))
            await asyncio.sleep(.02)
            assert sequence == []
            release.set()
            await asyncio.gather(job, second)
            assert sequence == [1, 2]
        finally:
            release.set()
            worker.close()
    asyncio.run(run())


def test_cancelled_write_finishes_before_releasing_callers():
    async def run():
        worker = DatabaseWorker()
        entered, release = threading.Event(), threading.Event()
        def write():
            entered.set()
            assert release.wait(2)
        try:
            job = asyncio.create_task(worker.run(write))
            while not entered.is_set(): await asyncio.sleep(.005)
            job.cancel()
            await asyncio.sleep(.02)
            assert not job.done()
            release.set()
            with pytest.raises(asyncio.CancelledError): await job
        finally:
            release.set()
            worker.close()
    asyncio.run(run())


def test_forms_serialize_and_reject_wrong_owner(db):
    from views.forms import ScheduleModal, PreferencesModal, TaskModal
    async def run():
        bot = fake_bot(db)
        try:
            forms = [ScheduleModal(bot, 1, "America/New_York"), PreferencesModal(bot, 1, db.get_user(1), {}), TaskModal(bot, 1, [])]
            for form in forms:
                payload = form.to_components()
                assert 1 <= len(payload) <= 5
                assert all(item["type"] == 18 for item in payload)
                wrong = interaction(bot, 2)
                assert not await form.interaction_check(wrong)
                wrong.response.send_message.assert_awaited_once()
        finally:
            bot.db_worker.close()
    asyncio.run(run())


def test_preferences_form_saves_all_explicit_choices(db):
    from cogs.profile import DM_TOGGLES
    from views.forms import PreferencesModal
    async def run():
        bot = fake_bot(db)
        try:
            form = PreferencesModal(bot, 1, db.get_user(1), {})
            form.zone._value = "Europe/London"
            form.ghost._value = True
            form.cheers._value = False
            form.notifications[0]._values = ["schedule_reminders"]
            form.notifications[1]._values = []
            await form.on_submit(interaction(bot))
            settings = db.get_all_settings(1)
            assert settings["timezone"] == "Europe/London"
            assert db.get_user(1)["ghost_mode"] == 1
            assert all(settings[f"dm_{key}"] == ("1" if key == "schedule_reminders" else "0") for key in DM_TOGGLES)
        finally:
            bot.db_worker.close()
    asyncio.run(run())


def test_timer_buttons_are_persistent_and_bound_to_session(db):
    from cogs.study import Study, StudySessionControlsView
    from cogs.pomodoro import GroupLobbyActiveView, GroupLobbyWaitingView
    async def run():
        bot = fake_bot(db)
        try:
            sid = db.start_session(1, "Math")
            cog = Study(bot)
            view = StudySessionControlsView(cog, 1, sid)
            assert view.is_persistent()
            assert all(f"sb:study:1:{sid}:" in button.custom_id for button in view.children)
            assert GroupLobbyWaitingView(None, 3).is_persistent()
            assert GroupLobbyActiveView(None, 3).is_persistent()
            db.end_session(1)
            db.start_session(1, "New session")
            assert not await view.interaction_check(interaction(bot))
        finally:
            bot.db_worker.close()
    asyncio.run(run())


def test_private_timer_edits_webhook_without_channel_fetch(db):
    from cogs.study import Study
    async def run():
        bot = fake_bot(db)
        try:
            sid = db.start_session(1, "Math")
            db.set_session_live_message(sid, 10, 20)
            cog = Study(bot)
            message = SimpleNamespace(edit=AsyncMock())
            cog._private_panels[1] = (message, datetime.now(timezone.utc) + timedelta(minutes=10))
            assert await cog._edit_live_session_message(1)
            message.edit.assert_awaited_once()
            bot.get_channel.assert_not_called()
            cog._private_panels[1] = (message, datetime.now(timezone.utc) - timedelta(seconds=1))
            assert not await cog._edit_live_session_message(1)
            assert message.edit.await_count == 1
        finally:
            bot.db_worker.close()
    asyncio.run(run())


def test_stale_stop_modal_does_not_end_new_session(db):
    from cogs.study import Study
    async def run():
        bot = fake_bot(db)
        try:
            old = db.start_session(1, "Old")
            db.end_session(1)
            current = db.start_session(1, "New")
            await Study(bot)._stop_session(interaction(bot), expected_session_id=old)
            assert db.get_active_session(1)["id"] == current
        finally:
            bot.db_worker.close()
    asyncio.run(run())


def test_today_components_include_tasks_schedule_and_hide_rpg(db):
    from views.today import build_dashboard
    async def run():
        bot = fake_bot(db)
        try:
            db.add_task(1, "Read chapter", "", 10, "medium", None)
            db.add_schedule_block(1, "Math", "monday,tuesday,wednesday,thursday,friday,saturday,sunday", 9, 0, 60)
            awaiter = interaction(bot)
            awaiter.guild_id = None
            view = await build_dashboard(bot, awaiter)
            serialized = json.dumps(view.to_components())
            assert "Read chapter" in serialized and "Next study block" in serialized
            assert "Quests and raid" not in serialized
            assert "Create task" in serialized and "Start Pomodoro" in serialized
            assert not await view.interaction_check(interaction(bot, 2))
        finally:
            bot.db_worker.close()
    asyncio.run(run())


def test_task_draft_submit_is_idempotent_and_keeps_source(db):
    from views.forms import TaskDraftView, TaskDetailsModal
    async def run():
        bot = fake_bot(db)
        try:
            data = {"title": "Read chapter", "description": "", "priority": "medium", "project_id": None, "review": False, "source_url": "https://discord.com/channels/99/10/20"}
            draft = TaskDraftView(bot, 1, data)
            first, duplicate = TaskDetailsModal(draft), TaskDetailsModal(draft)
            for form in (first, duplicate):
                form.points._value, form.interval._value, form.due._value = "10", "1", ""
            await asyncio.gather(first.on_submit(interaction(bot)), duplicate.on_submit(interaction(bot)))
            tasks = db.get_user_tasks(1)
            assert len(tasks) == 1 and tasks[0]["source_message_url"] == data["source_url"]
        finally:
            bot.db_worker.close()
    asyncio.run(run())


def test_dm_timer_restores_destination_and_private_status_keeps_it(db):
    from cogs.study import Study
    async def run():
        bot = fake_bot(db)
        try:
            sid = db.start_session(1, "Math")
            db.set_session_live_message(sid, 10, 20, kind="dm")
            message = SimpleNamespace(edit=AsyncMock())
            channel = SimpleNamespace(get_partial_message=Mock(return_value=message))
            bot.get_channel.return_value = channel
            cog = Study(bot)
            assert await cog._edit_live_session_message(1)
            channel.get_partial_message.assert_called_once_with(20)
            message.edit.assert_awaited_once()
            await Study.study_status.callback(cog, interaction(bot))
            session = db.get_active_session(1)
            assert (session["live_kind"], session["live_channel_id"], session["live_message_id"]) == ("dm", 10, 20)
        finally:
            bot.db_worker.close()
    asyncio.run(run())


def test_restored_dynamic_study_button_rechecks_owner_and_session(db):
    from cogs.study import Study
    from views.persistent import StudyControl
    async def run():
        bot = fake_bot(db)
        try:
            sid = db.start_session(1, "Math")
            cog = Study(bot)
            cog._set_paused = AsyncMock()
            bot.get_cog.return_value = cog
            item = StudyControl(discord.ui.Button(custom_id=f"sb:study:1:{sid}:pause_btn"), 1, sid, "pause_btn")
            await item.callback(interaction(bot, 2))
            cog._set_paused.assert_not_awaited()
            await item.callback(interaction(bot))
            assert cog._set_paused.await_args.kwargs == {"expected_session_id": sid}
            db.end_session(1)
            db.start_session(1, "New")
            await item.callback(interaction(bot))
            assert cog._set_paused.await_count == 1
        finally:
            bot.db_worker.close()
    asyncio.run(run())


def test_stale_pause_and_note_leave_new_session_untouched(db):
    from cogs.study import Study, SessionNoteModal
    async def run():
        bot = fake_bot(db)
        try:
            old = db.start_session(1, "Old")
            db.end_session(1)
            current = db.start_session(1, "New")
            cog = Study(bot)
            await cog._set_paused(interaction(bot), True, expected_session_id=old)
            form = SessionNoteModal(cog, 1, old)
            form.text._value = "Old note"
            await form.on_submit(interaction(bot))
            session = db.get_active_session(1)
            assert session["id"] == current and not session["is_paused"] and not session["notes"]
            assert not db.pause_session(1, expected_session_id=old)
            assert not db.add_session_note(1, "Old", expected_session_id=old)
        finally:
            bot.db_worker.close()
    asyncio.run(run())


def test_schedule_form_validates_before_saving(db):
    from views.forms import ScheduleModal
    async def run():
        bot = fake_bot(db)
        try:
            form = ScheduleModal(bot, 1, "Europe/London")
            form.subject._value, form.duration._value = "Math", "60"
            form.days._values = ["monday", "wednesday"]
            form.time._value = "25:00"
            await form.on_submit(interaction(bot))
            assert db.get_user_schedule(1) == []
            form.time._value = "09:30"
            await form.on_submit(interaction(bot))
            block = db.get_user_schedule(1)[0]
            assert (block["days_of_week"], block["hour"], block["minute"], block["timezone"]) == ("monday,wednesday", 9, 30, "Europe/London")
        finally:
            bot.db_worker.close()
    asyncio.run(run())


def test_planning_posts_native_poll_and_rejects_duplicate_choices(db):
    from cogs.planning import Planning
    async def run():
        bot = fake_bot(db)
        try:
            cog = Planning(bot)
            event = interaction(bot)
            event.guild = SimpleNamespace()
            event.permissions = SimpleNamespace(send_messages=True, send_polls=True)
            event.app_permissions = SimpleNamespace(send_polls=True)
            await Planning.poll.callback(cog, event, "Subject?", "Math | Reading", 2, True)
            poll = event.response.send_message.await_args.kwargs["poll"]
            assert len(poll.answers) == 2 and poll.multiple and poll.duration == timedelta(hours=2)
            invalid = interaction(bot)
            await Planning.poll.callback(cog, invalid, "Subject?", "Math | math", 2, False)
            assert "distinct choices" in invalid.response.send_message.await_args.args[0]
        finally:
            bot.db_worker.close()
    asyncio.run(run())


def test_planning_event_sets_schedule_and_honors_permissions(db):
    from cogs.planning import Planning
    async def run():
        bot = fake_bot(db)
        try:
            cog = Planning(bot)
            event = interaction(bot)
            event.guild = SimpleNamespace(create_scheduled_event=AsyncMock(return_value=SimpleNamespace(url="https://discord.com/events/99/1")))
            event.channel = SimpleNamespace(name="study")
            event.permissions = SimpleNamespace(create_events=False)
            start = datetime.now(timezone.utc) + timedelta(days=1)
            await Planning.event.callback(cog, event, "Math", start, 60)
            event.guild.create_scheduled_event.assert_not_awaited()
            event.permissions.create_events = True
            await Planning.event.callback(cog, event, "Math", start, 60)
            payload = event.guild.create_scheduled_event.await_args.kwargs
            assert payload["start_time"] == start and payload["end_time"] == start + timedelta(hours=1)
            assert payload["entity_type"] == discord.EntityType.external
        finally:
            bot.db_worker.close()
    asyncio.run(run())


def test_full_bootstrap_and_command_scopes_without_discord_login(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "bootstrap.db"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    monkeypatch.setenv("SYNC_COMMANDS", "false")
    monkeypatch.setenv("SYNC_GUILD_COMMANDS", "false")
    from bot import StudyBot
    async def run():
        bot = StudyBot()
        bot.scheduler.start = Mock()
        try:
            await bot.setup_hook()
            assert len(bot.cogs) == 21
            assert not bot.intents.message_content and bot.intents.members
            for name in ("today", "task", "schedule", "study", "remind", "Create task", "Remind me about this"):
                command = next(c for c in bot.tree.get_commands() if c.name == name)
                payload = command.to_dict(bot.tree)
                assert payload["contexts"] == [0, 1, 2] and payload["integration_types"] == [0, 1]
            for name in ("study_plan", "admin", "raid", "group_pomo"):
                command = next(c for c in bot.tree.get_commands() if c.name == name)
                payload = command.to_dict(bot.tree)
                assert payload["contexts"] == [0] and payload["integration_types"] == [0]
        finally:
            await bot.close()
            bot.db_worker.close()
    asyncio.run(run())


def test_complete_smoke_check(monkeypatch):
    import smoke_check
    monkeypatch.setenv("SMOKE_WITH_DB_SIM", "false")
    smoke_check.main()
