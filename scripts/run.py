#!/usr/bin/env python
"""便捷入口：python scripts/run.py <command>。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from research_kit.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
