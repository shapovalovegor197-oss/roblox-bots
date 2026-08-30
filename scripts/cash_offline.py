# -*- coding: utf-8 -*-
"""Подобрать чтение наличных на кадрах, где сумма известна глазами."""
import sys, cv2, numpy as np
sys.path.insert(0, "src")
from brainbot import ocr

TRUTH = {
    "income_check_20260830-151705-633.png": 81860,
    "collect_probe_20260830-151814-267.png": 81860,
    "base_now_20260830-151346-223.png": 81860,
    "now_20260830-145902-606.png": 164360,
    "session_start_20260830-141542-543.png": 164360,
}

def crops(img):
    h, w = img.shape[:2]
    return {
        "широкий": img[int(h*0.87):int(h*0.98), 0:int(w*0.28)],
        "узкий":   img[int(h*0.90):int(h*0.965), 0:int(w*0.20)],
        "по-зелёному": None,   # вырез по самой зелёной надписи
    }

def green_box(img):
    h, w = img.shape[:2]
    band = img[int(h*0.86):, 0:int(w*0.30)]
    hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, np.array((35, 120, 150), np.uint8), np.array((85, 255, 255), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 25), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(m, 8)
    best, area = None, 0
    for i in range(1, n):
        a = st[i, cv2.CC_STAT_AREA]
        if a > area and st[i, cv2.CC_STAT_WIDTH] > st[i, cv2.CC_STAT_HEIGHT]:
            best, area = i, a
    if best is None:
        return None
    x, y, bw, bh = (st[best, cv2.CC_STAT_LEFT], st[best, cv2.CC_STAT_TOP],
                    st[best, cv2.CC_STAT_WIDTH], st[best, cv2.CC_STAT_HEIGHT])
    pad = 6
    return band[max(0, y-pad):y+bh+pad, max(0, x-pad):x+bw+pad]

def read(crop):
    if crop is None or crop.size == 0:
        return []
    big = cv2.resize(crop, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
    out = []
    for t, _, _ in ocr.lines(big):
        out.append(t)
    return out

import glob, os
for fn, truth in TRUTH.items():
    p = os.path.join("var/screens", fn)
    img = cv2.imread(p)
    if img is None:
        continue
    print("=== %s (правда %d)" % (fn[:28], truth))
    cc = crops(img)
    cc["по-зелёному"] = green_box(img)
    for name, crop in cc.items():
        print("   %-12s %s" % (name, read(crop)))
