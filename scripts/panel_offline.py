# -*- coding: utf-8 -*-
"""Подобрать вырез и увеличение для требований ребёрна — на сохранённом кадре."""
import sys, cv2
sys.path.insert(0, "src")
from brainbot import ocr
from brainbot.brainrots import catalog

img = cv2.imread(sys.argv[1])
h, w = img.shape[:2]
print("кадр %dx%d" % (w, h))
for name, frac, scale in [
    ("прогресс", (0.28, 0.53, 0.73, 0.58), 3),
    ("иконки",   (0.42, 0.57, 0.58, 0.68), 4),
    ("иконки-ш", (0.28, 0.56, 0.73, 0.69), 4),
]:
    x0, y0, x1, y1 = frac
    crop = img[int(y0*h):int(y1*h), int(x0*w):int(x1*w)]
    big = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    got = [t for t, _, _ in ocr.lines(big)]
    print("--- %s x%d: %s" % (name, scale, got))
    cat = catalog()
    for t in got:
        it = cat.match(t)
        if it:
            print("      %-24s -> %s (%s, %s/с)" % (t, it.name, it.rarity, it.base_income))
    for a, b in zip(got, got[1:]):
        it = cat.match(a + " " + b)
        if it:
            print("      %-24s -> %s (пара)" % (a + " " + b, it.name))
