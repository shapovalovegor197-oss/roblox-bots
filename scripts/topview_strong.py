# -*- coding: utf-8 -*-
"""Сильный наклон вниз: получается ли настоящий вид сверху и что в нём видно."""
import sys, time, cv2
sys.path.insert(0, "src")
from brainbot import config, log, ocr
from brainbot.window import enum_roblox_windows
from brainbot.inputs import Hand
from brainbot.farm import Farmer, FarmTuning

s = config.load(); log.setup(s.logs_dir)
win = enum_roblox_windows()[0]
f = Farmer(window=win, hand=Hand(win, s.input), tuning=FarmTuning(),
           screens_dir=s.screenshots_dir)

f.reset_to_base(); time.sleep(1.2)
f.close_players_table()
for name, strength in (("s06", 0.6), ("s10", 1.0), ("s14", 1.4)):
    f.hand.pitch_down(strength)
    time.sleep(0.9)
    fr = f.frame()
    h, w = fr.shape[:2]
    path = s.screenshots_dir / ("topview_%s.png" % name)
    cv2.imwrite(str(path), fr)
    texts = [t.strip() for t, x, y in ocr.lines(fr) if len(t.strip()) >= 4]
    print("%s -> %s" % (name, path.name))
    print("   строки: %s" % "; ".join(texts[:10]))
