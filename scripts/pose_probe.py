# -*- coding: utf-8 -*-
"""Положение после респавна и куда из него смотреть на ленту.

Респавн, рабочий вид, пад сверху (x, y) — это «положение». Затем, не двигаясь,
поворот камеры шагами по 90 и кадр на каждом: какой угол смотрит вдоль ленты.
Запуск: python scripts/pose_probe.py [тег]
"""
import sys
import time

sys.path.insert(0, "src")
import cv2                                                  # noqa: E402
from brainbot import config, log, nav                      # noqa: E402
from brainbot.window import enum_roblox_windows            # noqa: E402
from brainbot.inputs import Hand                           # noqa: E402
from brainbot.farm import Farmer, FarmTuning               # noqa: E402

тег = sys.argv[1] if len(sys.argv) > 1 else "pose"
s = config.load(); log.setup(s.logs_dir)
win = enum_roblox_windows()[0]
f = Farmer(window=win, hand=Hand(win, s.input), tuning=FarmTuning(),
           screens_dir=s.screenshots_dir)
f.reset_to_base(); time.sleep(1.2)
f.set_work_view(); f.close_players_table()
f.hand.pitch_top(); time.sleep(0.7)
top = f.frame()
cv2.imwrite(str(s.screenshots_dir / f"{тег}_top.png"), top)
pad = f.pad_from_top(top)
h, w = top.shape[:2]
print("пад сверху:", None if not pad else "x=%.3f y=%.3f" % (pad[0] / w, pad[1] / h))
f.hand.pitch_normal(back=f.tuning.view_pitch_back, already_top=True); time.sleep(0.6)
for угол in (0, 90, 180, 270):
    if угол:
        f.hand.turn_degrees(90); time.sleep(0.8)
    fr = f.frame()
    c = nav.find_conveyor(fr)
    cv2.imwrite(str(s.screenshots_dir / f"{тег}_{угол}.png"), fr)
    print("поворот %3d: лента %s" % (угол, "x=%.2f y=%.2f" % (c.x / w, c.y / h) if c else "нет"))
f.hand.turn_degrees(90)
