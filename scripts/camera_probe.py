# -*- coding: utf-8 -*-
"""Как выглядит кадр при разных зумах и наклонах. Кадры — в var/screens."""
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
f.close_players_table()
for zoom, back in ((8, 700), (14, 700), (14, 500), (20, 500)):
    f.hand.scroll(60); time.sleep(0.4)
    f.hand.scroll(-zoom); time.sleep(0.5)
    f.hand.pitch_normal(back=back); time.sleep(0.6)
    name = f.shot("cam_z%d_b%d" % (zoom, back))
    print("зум %d, наклон %d -> %s" % (zoom, back, name.name), flush=True)
