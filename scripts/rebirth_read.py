# -*- coding: utf-8 -*-
"""Прочитать требования перерождения с экрана и сверить со справочником."""
import sys, time
sys.path.insert(0, "src")
from brainbot import config, log, ocr
from brainbot.window import enum_roblox_windows
from brainbot.inputs import Hand
from brainbot.farm import Farmer, FarmTuning
from brainbot.brainrots import catalog

s = config.load(); log.setup(s.logs_dir)
win = enum_roblox_windows()[0]
f = Farmer(window=win, hand=Hand(win, s.input), tuning=FarmTuning(),
           screens_dir=s.screenshots_dir)

f.close_players_table()
ok = f.open_menu_item("birth")
print("окно ребёрна открыто:", ok, flush=True)
time.sleep(0.8)
p = f.shot("rebirth_panel")
print("кадр:", p, flush=True)

info = f.read_rebirth_window()
print("read_rebirth_window ->", info, flush=True)

fr = f.frame()
h, w = fr.shape[:2]
print("--- весь текст окна ---", flush=True)
for t, x, y in ocr.lines(fr):
    print("   %-40s x=%.3f y=%.3f" % (t[:40], x / w, y / h), flush=True)

print("--- что стоит на базе ---", flush=True)
f.dismiss_modals()
time.sleep(0.5)
print(f.base_items(), flush=True)
