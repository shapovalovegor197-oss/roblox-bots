# -*- coding: utf-8 -*-
"""Куда двигают a и d — по смещению свечения плиты рядом с персонажем."""
import sys, time
sys.path.insert(0, "src")
from brainbot import config, log
from brainbot.window import enum_roblox_windows
from brainbot.inputs import Hand
from brainbot.farm import Farmer, FarmTuning

s = config.load(); log.setup(s.logs_dir)
win = enum_roblox_windows()[0]
f = Farmer(window=win, hand=Hand(win, s.input), tuning=FarmTuning(),
           screens_dir=s.screenshots_dir)

def gx():
    fr = f.frame()
    g = f.plate_glow(fr)
    return (g[0] / fr.shape[1], g[2]) if g else (None, 0)

for key in ("d", "a", "d"):
    a, area0 = gx()
    if a is None:
        print("свечение потеряно — стоп"); break
    f.hand.hold(key, 0.22)
    time.sleep(0.5)
    b, area1 = gx()
    if b is None:
        print("%s: свечение ушло из кадра (было %.3f)" % (key, a)); break
    d = b - a
    where = "ВПРАВО" if d < -0.01 else "ВЛЕВО" if d > 0.01 else "никуда"
    print("%s: свечение %.3f -> %.3f (%+.3f), площадь %d->%d => персонаж %s"
          % (key, a, b, d, area0, area1, where))
