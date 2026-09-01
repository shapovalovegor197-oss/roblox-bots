# -*- coding: utf-8 -*-
"""Сколько коробок требований бот находит и какая у каждой насыщенность.

Отдельно от имени: имя на тёмной коробке OCR читает через раз, и когда не
прочитал — требование ИСЧЕЗАЕТ из списка. Здесь видно и коробку, и её цвет.
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
    boxes = f._requirement_boxes(img)
    parts = []
    for b in boxes:
        name, sat = f._read_requirement_box(img, b)
        parts.append("x=%d w=%d sat=%.1f имя=%s" % (b[0], b[2], sat, name))
    print("%-52s коробок=%d | %s"
          % (path.replace("\\", "/").split("/")[-1], len(boxes), " ; ".join(parts)))
