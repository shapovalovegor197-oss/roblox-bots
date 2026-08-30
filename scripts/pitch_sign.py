# -*- coding: utf-8 -*-
"""Какой знак вертикального сдвига даёт вид сверху."""
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
f.close_players_table()

for tag, dy in (("minus", -1800), ("plus", 1800)):
    f.hand.look(0, dy); time.sleep(1.0)
    fr = f.frame()
    cv2.imwrite(str(s.screenshots_dir / ("sign_%s.png" % tag)), fr)
    texts = [t.strip() for t, x, y in ocr.lines(fr) if len(t.strip()) >= 4][:6]
    print("dy=%+5d -> sign_%s.png | %s" % (dy, tag, "; ".join(texts)))
    f.hand.look(0, -dy); time.sleep(0.8)   # вернуть
