# -*- coding: utf-8 -*-
"""Стенд по кадрам окна ребёрна: что бот считает выполненным требованием.

Зачем: 01.09 в 07:37 окно показало Chef Crabracadabra ТЁМНЫМ силуэтом, а бот
прочитал «нужны предметы []», нажал Rebirth и получил молчаливый отказ игры.
Порог по насыщенности (<60 = не куплен) — единственное, что разводит эти два
состояния, и проверять его надо по сохранённым кадрам, а не в живом прогоне.

Запуск: python scripts/rebirth_offline.py [маска]
"""
import sys
import glob
import cv2

sys.path.insert(0, "src")
from brainbot.farm import Farmer                     # noqa: E402


class Offline(Farmer):
    def __init__(self, img):
        self._img = img

    def frame(self):
        return self._img


mask = sys.argv[1] if len(sys.argv) > 1 else "var/screens/rebirth_window_*.png"
for path in sorted(glob.glob(mask)):
    img = cv2.imread(path)
    if img is None:
        continue
    f = Offline(img)
    try:
        info = f.read_rebirth_window()
    except Exception as exc:                          # noqa: BLE001
        print("%-52s ОШИБКА %s" % (path.split("/")[-1], exc))
        continue
    print("%-52s $%s/%s  насыщенность=%s  не хватает=%s"
          % (path.split("/")[-1], info["have_cash"], info["need_cash"],
             info["item_saturation"], info["need_items"]))
