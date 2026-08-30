# -*- coding: utf-8 -*-
"""Определиться сверху, довернуться и вернуться в обычный вид."""
import sys, time, math, cv2, numpy as np
sys.path.insert(0, "src")
from brainbot import config, log, nav, ocr
from brainbot.window import enum_roblox_windows
from brainbot.inputs import Hand
from brainbot.farm import Farmer, FarmTuning

s = config.load(); log.setup(s.logs_dir)
win = enum_roblox_windows()[0]
f = Farmer(window=win, hand=Hand(win, s.input), tuning=FarmTuning(),
           screens_dir=s.screenshots_dir)


def pad_in_top(fr):
    """Пад в виде сверху: крупное зелёное пятно разумного размера.

    Проверка размера обязательна: на «зелёном» событии маска ловила 66% кадра и
    выдавала центр земли за центр пада.
    """
    h, w = fr.shape[:2]
    hsv = cv2.cvtColor(fr, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, np.array((35, 90, 90), np.uint8), np.array((90, 255, 255), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((11, 11), np.uint8))
    n, lab, st, cent = cv2.connectedComponentsWithStats(m, 8)
    best, best_area = None, 0
    for i in range(1, n):
        a = int(st[i, cv2.CC_STAT_AREA])
        share = a / float(h * w)
        if 0.03 <= share <= 0.40 and a > best_area:
            best, best_area = (float(cent[i][0]), float(cent[i][1]), share), a
    return best


f.close_players_table()
f.hand.pitch_top(); time.sleep(1.0)
top = f.frame()
h, w = top.shape[:2]
cv2.imwrite(str(s.screenshots_dir / "loc_1_top.png"), top)

pad = pad_in_top(top)
if not pad:
    print("пад сверху не опознан"); sys.exit(1)
px, py, share = pad
print("пад: x=%.3f y=%.3f, доля кадра %.2f" % (px / w, py / h, share))

dx, dy = px - w / 2.0, py - h / 2.0
bearing = math.degrees(math.atan2(dx, -dy))
turn = f.nav.full_turn
units = int(turn * bearing / 360.0)
print("пеленг %+.1f град -> доворот %+d ед. (оборот %d)" % (bearing, units, turn))

f.hand.pitch_normal(); time.sleep(1.0)
cv2.imwrite(str(s.screenshots_dir / "loc_2_normal.png"), f.frame())

f.hand.look(units, 0); time.sleep(1.0)
aimed = f.frame()
cv2.imwrite(str(s.screenshots_dir / "loc_3_aimed.png"), aimed)
marks = nav.find_text_landmarks(aimed)
print("после доворота видно: %s" % ("; ".join(
    "%s@%.3f" % (k, v.x / w) for k, v in marks.items()) or "ничего"))
