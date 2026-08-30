# -*- coding: utf-8 -*-
"""Период поворота накоплением: много мелких шагов от зафиксированной камеры.

Один шаг меряется с шумом (пад сверху опознаётся не идеально), поэтому копим
градусы по цепочке шагов и делим на суммарные единицы. Шаг мелкий, чтобы
протяжка доходила целиком и чтобы не перескочить оборот.
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

STEP = int(sys.argv[1]) if len(sys.argv) > 1 else 100
COUNT = int(sys.argv[2]) if len(sys.argv) > 2 else 6


def bearing():
    f.hand.pitch_top(); time.sleep(0.7)
    fr = f.frame(); h, wd = fr.shape[:2]
    pad = f.pad_from_top(fr)
    f.hand.pitch_normal(back=f.tuning.view_pitch_back, already_top=True); time.sleep(0.35)
    if not pad:
        return None
    px, py, _ = pad
    return math.degrees(math.atan2(px - wd / 2.0, -(py - h / 2.0)))


f.reset_to_base(); time.sleep(1.6)
f.close_players_table()
f.set_work_view()

total_deg, total_units, prev = 0.0, 0, bearing()
if prev is None:
    sys.exit("пад не опознан на старте")
print("старт: пеленг %+.1f" % prev, flush=True)
for i in range(COUNT):
    f.hand.look(STEP, 0); time.sleep(0.6)
    now = bearing()
    if now is None:
        print("шаг %d: пад не опознан, пропускаю" % i, flush=True); continue
    d = (now - prev + 540) % 360 - 180
    total_deg += abs(d); total_units += STEP
    print("шаг %d: %+.1f -> %+.1f, за шаг %+.1f град" % (i, prev, now, d), flush=True)
    prev = now
if total_units:
    rate = total_deg / total_units
    print("\nНАКОПЛЕНО: %.1f град за %d единиц; %.4f град/ед; полный оборот %.0f единиц"
          % (total_deg, total_units, rate, 360.0 / rate), flush=True)
