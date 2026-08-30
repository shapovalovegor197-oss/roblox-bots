# -*- coding: utf-8 -*-
"""Сколько градусов даёт единица мыши. Замер ОТ ЗАФИКСИРОВАННОЙ камеры.

Порядок жёсткий, иначе числа скачут:
  1. респавн — известное место;
  2. `set_work_view` — известный зум и известный наклон, ТОТ ЖЕ перед каждым
     шагом; без этого камера уезжает от предыдущего действия, и один и тот же
     поворот меряется по-разному;
  3. пеленг пада сверху до и после поворота.

Шаги мелкие: при чувствительности 1.0 поворот на 600 единиц перескакивает
полный оборот, и замер врёт молча.

Запуск: python scripts/turn_measure.py [шаг ...]
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
    f.hand.pitch_top(); time.sleep(0.7)
    fr = f.frame(); h, wd = fr.shape[:2]
    pad = f.pad_from_top(fr)
    f.hand.pitch_normal(back=f.tuning.view_pitch_back, already_top=True); time.sleep(0.35)
    if not pad:
        return None
    px, py, _ = pad
    return math.degrees(math.atan2(px - wd / 2.0, -(py - h / 2.0)))


STEPS = [int(x) for x in sys.argv[1:]] or [50, 100, 100, 200]
f.reset_to_base(); time.sleep(1.6)
f.close_players_table()
rates = []
for units in STEPS:
    f.set_work_view()                 # исходное положение камеры — каждый раз одно
    b0 = bearing()
    if b0 is None:
        print("%4d: пад не опознан до поворота" % units, flush=True); continue
    f.hand.look(units, 0); time.sleep(0.7)
    b1 = bearing()
    if b1 is None:
        print("%4d: пад не опознан после поворота" % units, flush=True); continue
    d = (b1 - b0 + 540) % 360 - 180
    if abs(d) < 2:
        print("%4d ед -> %+.1f град (шум, пропускаю)" % (units, d), flush=True); continue
    rate = abs(d) / units
    rates.append(rate)
    print("%4d ед -> %+.1f град; %.4f град/ед; полный оборот %.0f ед"
          % (units, d, rate, 360.0 / rate), flush=True)

if rates:
    rates.sort()
    mid = rates[len(rates) // 2]
    print("\nМЕДИАНА: %.4f град/ед, полный оборот %.0f единиц" % (mid, 360.0 / mid), flush=True)
