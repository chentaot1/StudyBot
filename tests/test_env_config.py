# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

import os
from pathlib import Path
import subprocess
import sys

import pytest
from env_config import env_ids, env_id, required_timezone


def test_absent_ids_have_no_personal_defaults(monkeypatch):
    monkeypatch.delenv("TEST_DEPLOYMENT_IDS", raising=False)
    assert env_ids("TEST_DEPLOYMENT_IDS") == ()
    assert env_id("TEST_DEPLOYMENT_IDS") == 0


def test_ordered_ids_ignore_whitespace_and_duplicates(monkeypatch):
    monkeypatch.setenv("TEST_DEPLOYMENT_IDS", " 3, 1,3,2 ")
    assert env_ids("TEST_DEPLOYMENT_IDS") == (3, 1, 2)


@pytest.mark.parametrize("value", ["0", "-1", "1,x", "1,", "1,,2", "1.5", "１２３"])
def test_invalid_config_does_not_silently_enable_access(monkeypatch, value):
    monkeypatch.setenv("TEST_DEPLOYMENT_IDS", value)
    with pytest.raises(ValueError):
        env_ids("TEST_DEPLOYMENT_IDS")


def test_single_id_rejects_an_accidental_list(monkeypatch):
    monkeypatch.setenv("TEST_DEPLOYMENT_ID", "1,2")
    with pytest.raises(ValueError):
        env_id("TEST_DEPLOYMENT_ID")


@pytest.mark.parametrize("value", [None, "", "   ", "Invalid/Zone", "../UTC"])
def test_timezone_requires_an_explicit_valid_choice(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("TIMEZONE", raising=False)
    else:
        monkeypatch.setenv("TIMEZONE", value)
    with pytest.raises(ValueError, match="TIMEZONE"):
        required_timezone()


@pytest.mark.parametrize("value", ["UTC", "Europe/London", "Asia/Tokyo"])
def test_timezone_accepts_and_trims_explicit_choices(monkeypatch, value):
    monkeypatch.setenv("TIMEZONE", f" {value} ")
    assert required_timezone() == value


@pytest.mark.parametrize("value", [None, "", "   ", "Invalid/Zone"])
def test_bot_rejects_unconfigured_timezone_before_creating_state(tmp_path, value):
    env = os.environ.copy()
    env.pop("TIMEZONE", None)
    if value is not None:
        env["TIMEZONE"] = value
    env["DB_PATH"] = str(tmp_path / "study.db")
    env["LOG_DIR"] = str(tmp_path / "logs")
    # Isolate this subprocess from any operator's .env without touching that file.
    result = subprocess.run(
        [sys.executable, "-c", "import dotenv; dotenv.load_dotenv = lambda *a, **kw: False; import bot"],
        cwd=Path(__file__).resolve().parents[1], env=env,
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode != 0
    assert "ValueError: TIMEZONE" in result.stderr
    assert not list(tmp_path.iterdir())
