# -*- coding: utf-8 -*-
"""Найти коробки требований контурами и прочитать подпись в каждой."""
import sys, cv2
sys.path.insert(0, "src")
from brainbot import ocr
from brainbot.brainrots import catalog

def find_boxes(img):
    h, w = img.shape[:2]
    y0, y1 = int(h * 0.545), int(h * 0.66)
    x0, x1 = int(w * 0.28), int(w * 0.73)
    band = img[y0:y1, x0:x1]
    gray = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)
    dark = cv2.threshold(gray, 80, 255, cv2.THRESH_BINARY_INV)[1]
    cnts, _ = cv2.findContours(dark, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for c in cnts:
        x, y, bw, bh = cv2.boundingRect(c)
        if bw < 35 or bh < 35 or bw > 150 or bh > 150:
            continue
        if not 0.6 < bw / bh < 1.6:
            continue
        boxes.append((x0 + x, y0 + y, bw, bh))
    boxes.sort()
    return boxes

def read_caption(img, box):
    x, y, bw, bh = box
    # Подпись — верхняя треть коробки (имя написано над силуэтом).
    crop = img[y:y + int(bh * 0.42), x:x + bw]
    cat = catalog()
    for scale, mode in ((6, "порог"), (6, "цвет"), (4, "порог"), (8, "цвет")):
        big = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_LANCZOS4)
        if mode == "порог":
            g = cv2.cvtColor(big, cv2.COLOR_BGR2GRAY)
            im = cv2.cvtColor(cv2.threshold(g, 160, 255, cv2.THRESH_BINARY)[1],
                              cv2.COLOR_GRAY2BGR)
        else:
            im = big
        got = [t for t, _, _ in ocr.lines(im)]
        if not got:
            continue
        it = cat.match(" ".join(got)) or cat.match_any(got)
        if it:
            return it.name, " ".join(got), "%s x%d" % (mode, scale)
    return None, "", ""

img = cv2.imread(sys.argv[1]); h, w = img.shape[:2]
boxes = find_boxes(img)
print("коробок найдено: %d" % len(boxes))
for b in boxes:
    name, raw, how = read_caption(img, b)
    print("   x=%.3f y=%.3f %dx%d -> %-24s [%s] %s"
          % (b[0]/w, b[1]/h, b[2], b[3], name, raw[:30], how))
