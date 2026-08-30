# -*- coding: utf-8 -*-
"""Перебрать предобработку, чтобы прочитать мелкие подписи под иконками."""
import sys, cv2, numpy as np
sys.path.insert(0, "src")
from brainbot import ocr
from brainbot.brainrots import catalog

img = cv2.imread(sys.argv[1]); h, w = img.shape[:2]
cat = catalog()
# Две коробки требований, координаты сняты с кадра глазами.
boxes = {"левая": (0.445, 0.578, 0.500, 0.615), "правая": (0.500, 0.578, 0.556, 0.615)}
for bname, (x0, y0, x1, y1) in boxes.items():
    crop = img[int(y0*h):int(y1*h), int(x0*w):int(x1*w)]
    for scale in (4, 6, 8):
        big = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_LANCZOS4)
        gray = cv2.cvtColor(big, cv2.COLOR_BGR2GRAY)
        variants = {
            "цвет": big,
            "серый": cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR),
            "otsu": cv2.cvtColor(cv2.threshold(gray, 0, 255,
                     cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1], cv2.COLOR_GRAY2BGR),
            "порог160": cv2.cvtColor(cv2.threshold(gray, 160, 255,
                     cv2.THRESH_BINARY)[1], cv2.COLOR_GRAY2BGR),
            "инверт": cv2.cvtColor(255 - gray, cv2.COLOR_GRAY2BGR),
        }
        for vname, im in variants.items():
            got = [t for t, _, _ in ocr.lines(im)]
            if not got:
                continue
            joined = " ".join(got)
            it = cat.match(joined) or cat.match_any(got)
            print("%-7s x%d %-9s -> %-38s %s" % (bname, scale, vname, joined[:38],
                                                 it.name if it else ""))
