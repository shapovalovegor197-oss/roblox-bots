# -*- coding: utf-8 -*-
"""Покупка без раздумий: стоим у ленты и жмём E подряд.

Разбор карточки между нажатиями стоит секунду, а лента за это время увозит
товар — промпт гаснет ровно к моменту нажатия. Поэтому решение «брать или нет»
выносим ЗА цикл нажатий: сначала научиться покупать вообще.
"""
import sys, time
sys.path.insert(0, "src")
from brainbot import config, log
from brainbot.window import enum_roblox_windows
from brainbot.inputs import Hand
from brainbot.farm import Farmer, FarmTuning

s = config.load(); log.setup(s.logs_dir)
win = enum_roblox_windows()[0]
f = Farmer(window=win, hand=Hand(win, s.input), tuning=FarmTuning(),
           screens_dir=s.screenshots_dir)
f.hand.move(13, 65)

hold = float(sys.argv[1]) if len(sys.argv) > 1 else 2.0
start = f.read_hud_cash()
print("наличные до: %s, удержание %.1f с" % (start, hold))
cash = start
for i in range(10):
    f.hand.interact(hold)
    time.sleep(0.3)
    now = f.read_hud_cash()
    if now is not None and cash is not None and now != cash:
        print("  нажатие %2d: %s -> %s  (потрачено %s)" % (i + 1, cash, now, cash - now))
        cash = now
    else:
        print("  нажатие %2d: без изменений (%s)" % (i + 1, now))
print("итого: %s -> %s" % (start, cash))
