# -*- coding: utf-8 -*-
"""One diagnostic lock attempt without the blind-lock preflight."""
from __future__ import annotations

import sys
import time

sys.path.insert(0, "src")

from brainbot import config, log
from brainbot.farm import Farmer, FarmTuning
from brainbot.inputs import Hand
from brainbot.window import enum_roblox_windows


settings = config.load()
log.setup(settings.logs_dir)
window = enum_roblox_windows()[0]
tuning = FarmTuning(blind_lock=False)
farmer = Farmer(window=window, hand=Hand(window, settings.input), tuning=tuning,
                screens_dir=settings.screenshots_dir)

started = time.time()
left = farmer.lock_with_retries(attempts=1)
print("ЗАПЕРТО %d с" % left if left else "НЕ ВЫШЛО", flush=True)
print("время %.1f с, причина %s" %
      (time.time() - started, getattr(farmer, "_lock_failure", None)), flush=True)
