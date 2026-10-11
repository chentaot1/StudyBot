# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

from __future__ import annotations

import os
import sys
from pathlib import Path

# Explicit test configuration only; production must choose its own timezone.
os.environ.setdefault("TIMEZONE", "UTC")

# Ensure the repository root is importable in tests (so `import database` works).
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
