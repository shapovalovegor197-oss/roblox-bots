# -*- coding: utf-8 -*-
"""Сколько градусов даёт единица мыши — по пеленгу пада сверху.

Пад — неподвижный объект, и вид сверху даёт его пеленг числом. Крутим на
известное число единиц и смотрим, на сколько градусов уехал пеленг. Способ
не зависит ни от совпадения кадров, ни от того, что творится в мире.
"""
import sys, time, math
sys.path.insert(0, "src")
from brainbot import config, log
from brainbot.window import enum_roblox_windows
from brainbot.inputs import Hand
from brainbot.farm import Farmer, FarmTuning

s = config.load(); log.setup(s.logs_dir)
w = enum_roblox_windows()[0]
f = Farmer(window=w, hand=Hand(w, s.input), tuning=FarmTuning(), screens_dir=s.screenshots_dir)


def bearing():
    f.hand.pitch_top(); time.sleep(0.6)
    fr = f.frame(); h, wd = fr.shape[:2]
    pad = f.pad_from_top(fr)
    f.hand.pitch_normal(back=f.tuning.view_pitch_back, already_top=True); time.sleep(0.3)
    if not pad:
        return None
    px, py, _ = pad
    return math.degrees(math.atan2(px - wd / 2.0, -(py - h / 2.0)))


f.reset_to_base(); time.sleep(1.6)
f.set_work_view()
f.close_players_table()
for units in (600, 1200, 2400):
    b0 = bearing()
    if b0 is None:
        print("пад не опознан, пропускаю", flush=True); continue
    f.hand.look(units, 0); time.sleep(0.8)
    b1 = bearing()
    if b1 is None:
        print("после поворота пад не опознан", flush=True); continue
    d = (b1 - b0 + 540) % 360 - 180        # знак и переход через 180
    deg = abs(d)
    print("%d единиц -> %+.1f град (пеленг %+.1f -> %+.1f); полный оборот ~%.0f единиц"
          % (units, d, b0, b1, units * 360.0 / deg if deg > 1 else 0), flush=True)
