# -*- coding: utf-8 -*-
"""На сколько опускать камеру после вида сверху, чтобы вернуть её В ИСХОДНОЕ.

Исходное положение задаёт игра: после респавна камера встаёт в свой дефолт.
Снимаем его эталоном, потом уходим в верхний упор и опускаемся на разные B,
сравнивая кадр с эталоном. Сравниваем не корреляцией (она требует чистого
сдвига, а тут меняется вся картинка), а средним отличием уменьшенных кадров:
минимум и есть попадание.

Число нужно перемерять после КАЖДОЙ правки ввода: когда протяжка стала
плавной и начала доходить целиком, прежние 700 стали уводить камеру под
персонажа, в небо.
"""
import sys, time
import cv2
import numpy as np
sys.path.insert(0, "src")
from brainbot import config, log
from brainbot.window import enum_roblox_windows
from brainbot.inputs import Hand
from brainbot.farm import Farmer, FarmTuning
from brainbot.capture import grab

s = config.load(); log.setup(s.logs_dir)
w = enum_roblox_windows()[0]
f = Farmer(window=w, hand=Hand(w, s.input), tuning=FarmTuning(), screens_dir=s.screenshots_dir)
box = w.client_box()


def small():
    fr = grab(box, hwnd=w.hwnd)
    h, wd = fr.shape[:2]
    cut = fr[int(h * 0.10):int(h * 0.85), int(wd * 0.22):int(wd * 0.98)]
    g = cv2.cvtColor(cut, cv2.COLOR_BGR2GRAY)
    return cv2.resize(g, (64, 48)).astype(np.float32)


f.reset_to_base(); time.sleep(1.8)
f.close_players_table()
f.hand.scroll(60); time.sleep(0.4); f.hand.scroll(-f.tuning.work_zoom_out); time.sleep(0.5)
ref = small()
print("эталон снят", flush=True)

best = None
for back in (int(x) for x in (sys.argv[1:] or [300, 380, 450, 520, 600])):
    f.hand.pitch_top(); time.sleep(0.6)
    f.hand.look(0, -back); time.sleep(0.6)
    diff = float(np.abs(small() - ref).mean())
    print("возврат %4d: отличие от исходного %.1f" % (back, diff), flush=True)
    if best is None or diff < best[1]:
        best = (back, diff)
print("\nЛУЧШИЙ ВОЗВРАТ: %d (отличие %.1f)" % best, flush=True)
