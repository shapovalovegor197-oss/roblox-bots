# -*- coding: utf-8 -*-
"""Подобрать подъём камеры от упора сверху так, чтобы персонаж встал по центру кадра."""
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


def avatar(fr):
    """Персонаж по ярко-синим волосам — единственное такое пятно в центре кадра."""
    h, w = fr.shape[:2]
    hsv = cv2.cvtColor(fr, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, np.array((95, 120, 120), np.uint8),
                    np.array((120, 255, 255), np.uint8))
    m[:, : int(w * 0.25)] = 0
    m[:, int(w * 0.75):] = 0
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))
    n, _l, st, cent = cv2.connectedComponentsWithStats(m, 8)
    best, area = None, 0
    for i in range(1, n):
        a = int(st[i, cv2.CC_STAT_AREA])
        if a > 800 and a > area:
            best, area = (cent[i][0] / w, cent[i][1] / h, a), a
    return best


for back in (0, 300, 500, 700, 900, 1100):
    f.hand.pitch_top(); time.sleep(0.7)
    if back:
        f.hand.look(0, -back); time.sleep(0.7)
    fr = f.frame()
    cv2.imwrite(str(s.screenshots_dir / ("centre_%04d.png" % back)), fr)
    a = avatar(fr)
    if a:
        print("подъём %4d: персонаж x=%.3f y=%.3f (площадь %d)" % (back, a[0], a[1], a[2]))
    else:
        print("подъём %4d: персонажа не видно" % back)
