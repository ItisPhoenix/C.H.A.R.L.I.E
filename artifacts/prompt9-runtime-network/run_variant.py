from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from charlie.config import config

variant = sys.argv[1] if len(sys.argv) > 1 else "telegram"
if variant in {"telegram", "all"}:
    config.telegram_enabled = False
if variant == "all":
    config.browser_enabled = False
    config.vision_enabled = False
    config.memory_auto_extract = False

from main import main

print({"variant": variant, "pid": os.getpid(), "telegram_enabled": config.telegram_enabled, "browser_enabled": config.browser_enabled, "vision_enabled": config.vision_enabled, "memory_auto_extract": config.memory_auto_extract}, flush=True)
asyncio.run(main())
