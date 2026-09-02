# -*- coding: utf-8 -*-
"""Осмотреться на базе: вернуть рабочий вид, отойти назад, поднять камеру.

Нужно, чтобы увидеть базу целиком, а не только пол под ногами: у базы есть
второй этаж, и весь код до 02.09 знал только про нижний ряд пад.
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
f.set_work_view()
time.sleep(0.5)
print(f.shot("look_0_work"))
for i in range(3):
    f.hand.hold("s", 0.6)
    time.sleep(0.3)
print(f.shot("look_1_back"))
f.hand.look(0, -70)
time.sleep(0.5)
print(f.shot("look_2_up"))
f.hand.look(0, 70)
time.sleep(0.3)
