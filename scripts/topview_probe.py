# -*- coding: utf-8 -*-
"""Что видно в виде сверху и где именно: OCR-строки и зелёный пад с координатами."""
import sys, time, cv2, numpy as np
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
f.hand.pitch_down(); time.sleep(1.0)

fr = f.frame()
h, w = fr.shape[:2]
cv2.imwrite(str(s.screenshots_dir / "topview_probe.png"), fr)
print("кадр %dx%d -> topview_probe.png" % (w, h))

print("-- OCR (доля кадра):")
for text, xc, yc in ocr.lines(fr):
    t = text.strip()
    if len(t) >= 3:
        print("   %-28s x=%.3f y=%.3f" % (t[:28], xc / w, yc / h))

hsv = cv2.cvtColor(fr, cv2.COLOR_BGR2HSV)
m = cv2.inRange(hsv, np.array((40, 150, 150), np.uint8), np.array((85, 255, 255), np.uint8))
m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))
n, lab, st, cent = cv2.connectedComponentsWithStats(m, 8)
print("-- зелёные пятна > 4000 px:")
for i in range(1, n):
    a = int(st[i, cv2.CC_STAT_AREA])
    if a > 4000:
        print("   площадь %7d  x=%.3f y=%.3f" % (a, cent[i][0] / w, cent[i][1] / h))
