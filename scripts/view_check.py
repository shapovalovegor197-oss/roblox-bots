# -*- coding: utf-8 -*-
"""Как выглядит РАБОЧИЙ вид после респавна. Один кадр, без прогона."""
import sys, time
sys.path.insert(0, "src")
from brainbot import config, log
from brainbot.window import enum_roblox_windows
from brainbot.inputs import Hand
from brainbot.farm import Farmer, FarmTuning

s = config.load(); log.setup(s.logs_dir)
w = enum_roblox_windows()[0]
f = Farmer(window=w, hand=Hand(w, s.input), tuning=FarmTuning(), screens_dir=s.screenshots_dir)
f.reset_to_base(); time.sleep(1.5)
f.set_work_view(); f.close_players_table()
print("рабочий вид:", f.shot("work_view").name, flush=True)
