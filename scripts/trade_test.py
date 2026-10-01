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
# Имя предмета OCR в окне обмена читает плохо (01.10: Strawberrelli Flamingelli
# пришёл как «ттвеш»), поэтому номер карточки надёжнее: сетка 3 в ряд,
# считая с нуля сверху слева.
карточка = sys.argv[3] if len(sys.argv) > 3 else "0"   # номер или имя предмета
single.занять("обмен")
s = config.load(); log.setup(s.logs_dir)
win = enum_roblox_windows()[0]
hand = Hand(win, s.input)
f = Farmer(window=win, hand=hand, tuning=FarmTuning(), screens_dir=s.screenshots_dir)
tpl = Templates(s.template_dir, s.vision["match_threshold"], s.vision.get("regions"))


def шаг(имя, ок):
    print("%s %-22s %s" % (time.strftime("%H:%M:%S"), имя, "OK" if ок else "НЕТ"), flush=True)
    # Имя кадра латиницей: OpenCV молча не пишет файлы с кириллицей в пути.
    f.shot("trade_step%02d" % шаг.n)
    шаг.n += 1
    if not ок:
        sys.exit(1)


шаг.n = 1
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
def точка_по_имени(имя: str):
    """Карточка нашей половины, ближайшая к подписи с этим именем (OCR сетки)."""
    from brainbot import ocr
    from brainbot.brainrots import normalize
    кадр = f.frame()
    цель = normalize(имя)
    точки = trade.карточки(f)
    for текст, x, y in ocr.lines(кадр):
        н = normalize(текст)
        if len(н) >= 5 and (н in цель or цель[:8] in н) and x < 680:
            print("подпись «%s» на (%d,%d)" % (текст, x, y))
            return min(точки, key=lambda p: (p[0] - x) ** 2 + (p[1] - y) ** 2)
    return None


if режим == "send":
    if карточка.isdigit():
        точка = trade.карточки(f)[int(карточка)]
    else:
        точка = точка_по_имени(карточка)
        шаг("предмет_найден", точка is not None)
    шаг("предмет_положен", trade.положить(f, hand, точка))
шаг("обмен_завершён", trade.довести(f, hand, 180))
