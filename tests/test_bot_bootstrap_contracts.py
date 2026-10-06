# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

"""Lightweight contracts for startup / shutdown paths (complements smoke_check hypothetical checks)."""

from __future__ import annotations

import re
from pathlib import Path


def test_discord_token_read_via_getenv() -> None:
    bot_src = (Path(__file__).resolve().parent.parent / "bot.py").read_text(encoding="utf-8")
    assert 'os.getenv("DISCORD_TOKEN"' in bot_src or "os.getenv('DISCORD_TOKEN'" in bot_src


def test_no_discord_token_assignment_in_bot_py() -> None:
    bot_src = (Path(__file__).resolve().parent.parent / "bot.py").read_text(encoding="utf-8")
    for line in bot_src.splitlines():
        head = line.split("#", 1)[0].strip()
        assert re.match(r"^DISCORD_TOKEN\s*=", head) is None, "hardcoded DISCORD_TOKEN assignment"


def test_graceful_shutdown_wal_checkpoint_present() -> None:
    bot_src = (Path(__file__).resolve().parent.parent / "bot.py").read_text(encoding="utf-8")
    assert "_graceful_shutdown" in bot_src
    assert "wal_checkpoint" in bot_src
