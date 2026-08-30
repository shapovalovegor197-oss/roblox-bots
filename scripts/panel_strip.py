# -*- coding: utf-8 -*-
"""Полоса требований целиком: имена вынимаем скользящим окном по словам."""
import sys, cv2
sys.path.insert(0, "src")
from brainbot import ocr
from brainbot.brainrots import catalog

def read_strip(img, frac=(0.40, 0.572, 0.61, 0.618)):
    h, w = img.shape[:2]
    x0, y0, x1, y1 = frac
    crop = img[int(y0*h):int(y1*h), int(x0*w):int(x1*w)]
    cat = catalog()
    found = []
    for scale, mode in ((6, "порог"), (6, "цвет"), (4, "порог"), (8, "цвет")):
        big = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_LANCZOS4)
        if mode == "порог":
            gray = cv2.cvtColor(big, cv2.COLOR_BGR2GRAY)
            im = cv2.cvtColor(cv2.threshold(gray, 160, 255, cv2.THRESH_BINARY)[1],
                              cv2.COLOR_GRAY2BGR)
        else:
            im = big
        words = []
        for t, x, y in ocr.lines(im):
            words.extend(t.split())
        # Имена в игре из двух слов; скользим окном 2 и 3.
        for n in (2, 3):
            for i in range(len(words) - n + 1):
                it = cat.match(" ".join(words[i:i+n]))
                if it and it.name not in found:
                    found.append(it.name)
        print("   %s x%d: %s -> %s" % (mode, scale, " ".join(words)[:60], found))
    return found

img = cv2.imread(sys.argv[1])
print("итог:", read_strip(img))
