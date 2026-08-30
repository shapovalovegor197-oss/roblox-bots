# -*- coding: utf-8 -*-
"""Пункт 2: собирается ли валюта наступанием на COLLECT ZONE."""
import sys, time, cv2
sys.path.insert(0, "src")
from brainbot import config, log, ocr
from brainbot.window import enum_roblox_windows
from brainbot.inputs import Hand
from brainbot.farm import Farmer, FarmTuning

s = config.load(); log.setup(s.logs_dir)
win = enum_roblox_windows()[0]
f = Farmer(window=win, hand=Hand(win, s.input), tuning=FarmTuning(),
           screens_dir=s.screenshots_dir)

print("наличные до:", f.read_hud_cash())
f.reset_to_base(); time.sleep(1.6)
f.close_players_table()
print("после респавна:", f.read_hud_cash())

# Респавн ставит прямо на пад COLLECT ZONE — проверим, что он под ногами.
f.hand.pitch_top(); time.sleep(0.9)
top = f.frame()
cv2.imwrite(str(s.screenshots_dir / "collect_top.png"), top)
pad = f.pad_from_top(top)
h, w = top.shape[:2]
if pad:
    print("пад сверху: x=%.3f y=%.3f (%.0f%% кадра)" % (pad[0]/w, pad[1]/h, pad[2]*100))
f.hand.pitch_normal(); time.sleep(0.9)

for i in range(4):
    before = f.read_hud_cash()
    f.hand.hold("w", 0.3); time.sleep(1.2)
    after = f.read_hud_cash()
    print("шаг %d: %s -> %s" % (i + 1, before, after))
    fr = f.frame(); hh, ww = fr.shape[:2]
    labels = [t.strip() for t, x, y in ocr.lines(fr)
              if "collect" in t.lower() or "$" in t]
    if labels:
        print("   подписи рядом: %s" % "; ".join(labels[:4]))
cv2.imwrite(str(s.screenshots_dir / "collect_end.png"), f.frame())
