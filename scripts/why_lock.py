# -*- coding: utf-8 -*-
"""Какая строка OCR выдаёт себя за ориентир lock."""
import sys
sys.path.insert(0, "src")
from brainbot import config, log, ocr, nav
from brainbot.window import enum_roblox_windows
from brainbot.inputs import Hand
from brainbot.farm import Farmer, FarmTuning

s = config.load(); log.setup(s.logs_dir)
win = enum_roblox_windows()[0]
f = Farmer(window=win, hand=Hand(win, s.input), tuning=FarmTuning(),
           screens_dir=s.screenshots_dir)
fr = f.frame()
h, w = fr.shape[:2]
for text, xc, yc in ocr.lines(fr):
    for name, (needles, box) in nav.TEXT_LANDMARKS.items():
        hit = any(n in text for n in needles)
        if not hit:
            hit = any(nav._looks_like(text, n) for n in needles if len(n) >= 6)
        if hit:
            x0, y0, x1, y1 = box
            inside = x0 * w <= xc <= x1 * w and y0 * h <= yc <= y1 * h
            print("%-12s <- %-24r x=%.3f y=%.3f %s" % (
                name, text.strip()[:24], xc / w, yc / h,
                "ЗАСЧИТАН" if inside else "вне рамки"))
