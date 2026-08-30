# -*- coding: utf-8 -*-
"""Лок через локализацию сверху."""
import sys, time, cv2
sys.path.insert(0, "src")
from brainbot import config, log
from brainbot.window import enum_roblox_windows
from brainbot.inputs import Hand
from brainbot.farm import Farmer, FarmTuning

s = config.load(); log.setup(s.logs_dir)
win = enum_roblox_windows()[0]
f = Farmer(window=win, hand=Hand(win, s.input), tuning=FarmTuning(),
           screens_dir=s.screenshots_dir)
f.reset_to_base(); time.sleep(1.5)
left = f.lock_with_retries()
cv2.imwrite(str(s.screenshots_dir / "lock_top_end.png"), f.frame())
print("ИТОГ: %s" % ("заперто, осталось %d с" % left if left else "не подтвердилось"))
