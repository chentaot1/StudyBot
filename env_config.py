# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

"""Deployment settings; never include personal Discord IDs in source."""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load this deployment's file before constants are imported. Do not search parents.
load_dotenv(Path(__file__).with_name(".env"))


def env_ids(name: str) -> tuple[int, ...]:
    """Parse positive, comma-separated IDs, preserving order and removing duplicates."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return ()
    parts = [part.strip() for part in raw.split(",")]
    if any(not part.isascii() or not part.isdigit() or int(part) <= 0 for part in parts):
        raise ValueError(f"{name} must contain positive IDs separated by commas.")
    return tuple(dict.fromkeys(int(part) for part in parts))


def env_id(name: str) -> int:
    ids = env_ids(name)
    if len(ids) > 1:
        raise ValueError(f"{name} must contain one ID.")
    return ids[0] if ids else 0
