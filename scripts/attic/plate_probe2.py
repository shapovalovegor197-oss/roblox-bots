# -*- coding: utf-8 -*-
"""Шифт-лок: респавн смотрит на плиту. Идём вперёд, меряем свечение плиты.
Без вида сверху — он с шифт-локом ломается."""
import sys, time
sys.path.insert(0, "src")
from brainbot import config, log
from brainbot.window import enum_roblox_windows
from brainbot.inputs import Hand
from brainbot.farm import Farmer, FarmTuning

s = config.load(); log.setup(s.logs_dir)
w = enum_roblox_windows()[0]
f = Farmer(window=w, hand=Hand(w, s.input), tuning=FarmTuning(), screens_dir=s.screenshots_dir)

f.dismiss_modals()
f.reset_to_base(); time.sleep(1.6)
f.close_players_table()
time.sleep(0.3)
for i in range(16):
    fr = f.frame()
    glow = f.plate_glow(fr)
    left = f.lock_confirmed()
    wd, ht = fr.shape[1], fr.shape[0]
    if glow:
        gx, gy, area = glow
        print("шаг %2d: x=%.3f y=%.3f площадь %5d лок=%s" % (i, gx/wd, gy/ht, area, left), flush=True)
    else:
        print("шаг %2d: свечения нет лок=%s" % (i, left), flush=True)
    if left:
        print("ЗАПЕРТО на шаге %d, время лока %d" % (i, left), flush=True); break
    # мягкая коррекция вбок по свечению, шаг вперёд
    if glow:
        off = glow[0]/wd - 0.5
        if abs(off) > 0.05:
            key = "d" if off > 0 else "a"
            f.hand.hold(key, min(0.25, abs(off)/1.12)); time.sleep(0.1)
    f.hand.hold("w", 0.4); time.sleep(0.25)
