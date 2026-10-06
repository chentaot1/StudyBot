# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 chentaot1.

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the repository root is importable in tests (so `import database` works).
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
