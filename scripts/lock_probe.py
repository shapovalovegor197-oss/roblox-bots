# -*- coding: utf-8 -*-
"""Что РЕАЛЬНО видно на кадре в момент лока: снять серию и вывести весь текст.

Нужен, потому что подтверждение лока не читается и бот сжигает лишнюю попытку.
"""
import sys, time
sys.path.insert(0, "src")
from brainbot import config, log, ocr
from brainbot.window import enum_roblox_windows
from brainbot.inputs import Hand
from brainbot.farm import Farmer, FarmTuning

s = config.load(); log.setup(s.logs_dir)
win = enum_roblox_windows()[0]
f = Farmer(window=win, hand=Hand(win, s.input), tuning=FarmTuning(),
           screens_dir=s.screenshots_dir)

def unlocked_for_sure(tries=4, pause=1.5):
    """Лок точно снят? Требуем несколько пустых чтений подряд."""
    for _ in range(tries):
        left = f.read_lock_left(quick=False)
        if left:
            return left
        time.sleep(pause)
    return None

while True:
    left = unlocked_for_sure()
    if not left:
        break
    print("жду снятия лока, осталось %d с" % left, flush=True)
    time.sleep(min(left, 20))

print("лок снят, иду запирать", flush=True)
t0 = time.time()
got = f.lock_with_retries(attempts=1)
dt = time.time() - t0
print("lock_via_top вернул: %s за %.1f с" % (got, dt), flush=True)

for i in range(8):
    fr = f.frame()
    p = f.shot("probe_lock_%d" % i)
    lines = ocr.lines(fr)
    h, w = fr.shape[:2]
    mid = [(t, round(x / w, 3), round(y / h, 3)) for t, x, y in lines]
    print("--- кадр %d (%.1f c) %s" % (i, time.time() - t0, p), flush=True)
    for t, x, y in mid:
        print("    %-28s x=%.3f y=%.3f" % (t[:28], x, y), flush=True)
    time.sleep(0.6)
