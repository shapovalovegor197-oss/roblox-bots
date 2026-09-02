# -*- coding: utf-8 -*-
"""Закрыть дверь прямо сейчас. Печатает, на сколько секунд заперто.

Нужен ровно для одного: закончить любой опыт с ЗАКРЫТОЙ базой. Пока она
открыта, у нас уносят брейнротов — за вечер 02.09 так потеряны два легендарных
(Glorbo Fruttodrillo в 20:00, Chef Crabracadabra около 21:45).

    python scripts/lock_now.py
"""
import sys

sys.path.insert(0, "src")
from brainbot import config, log, single                   # noqa: E402
from brainbot.window import enum_roblox_windows            # noqa: E402
from brainbot.inputs import Hand                           # noqa: E402
from brainbot.farm import Farmer, FarmTuning               # noqa: E402

single.занять("замок")

s = config.load()
log.setup(s.logs_dir)
окна = enum_roblox_windows()
if not окна:
    sys.exit("окон Roblox нет — клиент не запущен")

f = Farmer(window=окна[0], hand=Hand(окна[0], s.input), tuning=FarmTuning(),
           screens_dir=s.screenshots_dir)
взято = single.запереть_базу(f)
print("заперто на %s с" % взято if взято else "ЗАПЕРЕТЬ НЕ ВЫШЛО")
sys.exit(0 if взято else 1)
