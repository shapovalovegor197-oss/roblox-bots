# -*- coding: utf-8 -*-
"""Где персонаж в виде сверху — начало отсчёта для пеленга."""
import sys, time, cv2, numpy as np
sys.path.insert(0, "src")
from brainbot import config, log
from brainbot.window import enum_roblox_windows
from brainbot.inputs import Hand
from brainbot.farm import Farmer, FarmTuning

s = config.load(); log.setup(s.logs_dir)
win = enum_roblox_windows()[0]
f = Farmer(window=win, hand=Hand(win, s.input), tuning=FarmTuning(),
           screens_dir=s.screenshots_dir)
f.close_players_table()
f.hand.pitch_top(); time.sleep(1.0)
fr = f.frame()
h, w = fr.shape[:2]
cv2.imwrite(str(s.screenshots_dir / "whereami_top.png"), fr)

hsv = cv2.cvtColor(fr, cv2.COLOR_BGR2HSV)
m = cv2.inRange(hsv, np.array((95, 120, 120), np.uint8), np.array((120, 255, 255), np.uint8))
m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
n, _l, st, cent = cv2.connectedComponentsWithStats(m, 8)
print("синие пятна (голова — компактная, близкая к квадрату):")
for i in range(1, n):
    a = int(st[i, cv2.CC_STAT_AREA])
    if a < 400:
        continue
    bw, bh = int(st[i, cv2.CC_STAT_WIDTH]), int(st[i, cv2.CC_STAT_HEIGHT])
    fill = a / float(bw * bh)
    ratio = bw / float(bh)
    print("   площадь %6d  %3dx%3d  заполн %.2f  отнош %.2f  x=%.3f y=%.3f"
          % (a, bw, bh, fill, ratio, cent[i][0] / w, cent[i][1] / h))
