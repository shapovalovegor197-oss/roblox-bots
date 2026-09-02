# -*- coding: utf-8 -*-
"""Посмотреть вверх и снять кадр: что у базы над головой.

Камеру возвращаем туда же, откуда взяли (view_pitch_back из FarmTuning).
"""
import sys
import time

sys.path.insert(0, "src")
from brainbot import config                                  # noqa: E402
from brainbot.window import enum_roblox_windows              # noqa: E402
from brainbot.inputs import Hand                             # noqa: E402
from brainbot.farm import Farmer, FarmTuning                 # noqa: E402

s = config.load()
w = enum_roblox_windows()[0]
f = Farmer(window=w, hand=Hand(w, s.input), tuning=FarmTuning(),
           screens_dir=s.screenshots_dir)
шагов = int(sys.argv[1]) if len(sys.argv) > 1 else 3
for i in range(шагов):
    f.hand.look(0, -160)
    time.sleep(0.4)
    p = f.shot("look_up_%d" % (i + 1))
    print(p)
f.hand.look(0, 160 * шагов)
time.sleep(0.4)
print(f.shot("look_up_back"))
