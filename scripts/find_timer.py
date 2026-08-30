# -*- coding: utf-8 -*-
"""Найти в записях кадры со счётчиком лока над головой."""
import sys, glob, os, cv2
sys.path.insert(0, "src")
from brainbot import config, log, ocr

s = config.load(); log.setup(s.logs_dir)
parts = sorted(glob.glob("var/screens/rec_1788036718_часть*.mp4"))
print("кусков: %d" % len(parts))
hits = 0
for path in parts:
    c = cv2.VideoCapture(path)
    n = int(c.get(cv2.CAP_PROP_FRAME_COUNT))
    if n <= 0:
        c.release(); continue
    i = 0
    while True:
        ok, fr = c.read()
        if not ok:
            break
        if i % 8 == 0:
            h, w = fr.shape[:2]
            roi = fr[int(h * 0.20): int(h * 0.60), int(w * 0.30): int(w * 0.72)]
            txt = " ".join(t for t, x, y in ocr.lines(roi)).lower()
            if "lock" in txt or "ocked" in txt:
                hits += 1
                out = "var/screens/timer_%s_%04d.png" % (
                    os.path.basename(path).split("часть")[1][:2], i)
                cv2.imwrite(out, fr)
                print("  %s кадр %4d: %r -> %s" % (
                    os.path.basename(path)[-12:], i, txt[:60], os.path.basename(out)))
                if hits >= 6:
                    break
        i += 1
    c.release()
    if hits >= 6:
        break
print("найдено кадров со счётчиком: %d" % hits)
