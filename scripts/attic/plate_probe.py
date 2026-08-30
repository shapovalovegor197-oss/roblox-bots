# -*- coding: utf-8 -*-
"""Замерить, как выглядит плита лока при ШИФТ-ЛОКЕ по мере подхода.

Респавн -> взгляд на базу сверху -> вернуть камеру -> навестись на плиту ->
идти к ней короткими шагами, на каждом печатая площадь и y свечения и снимая
кадр. По этим числам перенастраиваются пороги прибытия.
"""
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
f.reset_to_base(); time.sleep(1.4)
f.close_players_table()
f.face_base_from_top(); time.sleep(0.4)
off = f.aim_at_plate()
print("наведение на плиту: промах", None if off is None else round(off, 3), flush=True)

for i in range(14):
    fr = f.frame()
    glow = f.plate_glow(fr)
    left = f.lock_confirmed()
    p = f.shot("plate_%02d" % i)
    if glow:
        gx, gy, area = glow
        wd, ht = fr.shape[1], fr.shape[0]
        print("шаг %2d: свечение x=%.3f y=%.3f площадь %5d  лок=%s  %s"
              % (i, gx/wd, gy/ht, area, left, p.name if p else ""), flush=True)
    else:
        print("шаг %2d: свечения нет  лок=%s  %s" % (i, left, p.name if p else ""), flush=True)
    if left:
        print("ЗАПЕРТО на шаге", i, flush=True)
        break
    f.hand.hold("w", 0.4)
    time.sleep(0.3)
