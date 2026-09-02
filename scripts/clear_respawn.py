# -*- coding: utf-8 -*-
"""Убрать модалку «Are you sure you want to respawn your character?».

Она остаётся висеть, если процесс бота убили ровно во время респавна, и дальше
закрывает середину кадра — то есть ослепляет всё зрение. Координаты жёсткие:
модалка всегда по центру клиентской области 1280x720, «Don't Respawn» — справа.
OCR тут не зовём: он тянет dxcam/comtypes и в отдельном процессе падает на
инициализации COM.
"""
import sys
import time

sys.path.insert(0, "src")
from brainbot import config                                  # noqa: E402
from brainbot.window import enum_roblox_windows              # noqa: E402
from brainbot.inputs import Hand                             # noqa: E402

s = config.load()
wins = enum_roblox_windows()
if not wins:
    sys.exit("окон Roblox нет")
w = wins[0]
box = w.client_box()
hand = Hand(w, s.input)
x = int(box.width * 0.586)     # «Don't Respawn» @750 из 1280
y = int(box.height * 0.510)    # @367 из 720
print("жму «Don't Respawn» @%d,%d (окно %dx%d)" % (x, y, box.width, box.height))
hand.click(x, y, hold=0.2)
time.sleep(1.0)
print("готово")
