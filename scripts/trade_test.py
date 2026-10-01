# -*- coding: utf-8 -*-
"""Живой обмен SAB на уже открытом клиенте — модулем roblox-swap/agents/sab/trade.

    python scripts/trade_test.py send НИК [номер_карточки]   # позвать и отдать
    python scripts/trade_test.py accept НИК                  # принять приглашение от НИК

Успех — только объявление «Trade with @ник completed!» (см. trade.объявлено).
Вторая сторона — человек или другая машина: два клиента на одной не живут.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, str(Path(r"D:\claude projects\старпепс\roblox-swap")))
from brainbot import config, log, single                   # noqa: E402
from brainbot.window import enum_roblox_windows            # noqa: E402
from brainbot.inputs import Hand                           # noqa: E402
from brainbot.farm import Farmer, FarmTuning               # noqa: E402
from brainbot.vision import Templates                      # noqa: E402
from agents.sab import trade                               # noqa: E402

режим, ник = sys.argv[1], sys.argv[2]
карточка = int(sys.argv[3]) if len(sys.argv) > 3 else 0
single.занять("обмен")
s = config.load(); log.setup(s.logs_dir)
win = enum_roblox_windows()[0]
hand = Hand(win, s.input)
f = Farmer(window=win, hand=hand, tuning=FarmTuning(), screens_dir=s.screenshots_dir)
tpl = Templates(s.template_dir, s.vision["match_threshold"], s.vision.get("regions"))


def шаг(имя, ок):
    print("%s %-22s %s" % (time.strftime("%H:%M:%S"), имя, "OK" if ок else "НЕТ"), flush=True)
    f.shot("trade_" + имя)
    if not ок:
        sys.exit(1)


if режим == "send":
    шаг("окно_обмена", trade.открыть(f, hand, tpl))
    шаг("приглашение", trade.позвать(f, hand, ник))
    шаг("сессия", trade.ждать_сессию(f, 120))
elif режим == "accept":
    кто = None
    конец = time.time() + 180
    while time.time() < конец and not кто:
        кто = trade.принять_приглашение(f, hand, [ник])
        time.sleep(1.5)
    шаг("приглашение_принято", bool(кто))
    шаг("сессия", trade.ждать_сессию(f, 30))
else:
    sys.exit("режим: send или accept")
if режим == "send":
    точка = trade.карточки(f)[карточка]
    шаг("предмет_положен", trade.положить(f, hand, точка))
шаг("обмен_завершён", trade.довести(f, hand, 180))
