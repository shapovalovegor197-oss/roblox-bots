# -*- coding: utf-8 -*-
"""С скольких направлений видна плашка YOUR BASE? Полный оборот 12 шагами."""
import sys, time
sys.path.insert(0, "src")
from brainbot import config, log, nav
from brainbot.window import enum_roblox_windows
from brainbot.inputs import Hand
from brainbot.farm import Farmer, FarmTuning

s = config.load(); log.setup(s.logs_dir)
win = enum_roblox_windows()[0]
f = Farmer(window=win, hand=Hand(win, s.input), tuning=FarmTuning(),
           screens_dir=s.screenshots_dir)

turn = f.nav.full_turn
step = int(turn / 12)
print("полный оборот %d ед., шаг %d ед." % (turn, step))

f.close_players_table()
seen = 0
for i in range(12):
    fr = f.frame()
    h, w = fr.shape[:2]
    marks = nav.find_text_landmarks(fr)
    bits = []
    for name in ("your_base", "cash_multi", "lock"):
        sp = marks.get(name)
        if sp:
            bits.append("%s@%.3f" % (name, sp.x / w))
    if "your_base" in marks:
        seen += 1
    print("  %3d град: %s" % (i * 30, ", ".join(bits) if bits else "-"))
    f.hand.look(step, 0)
    time.sleep(0.7)

print("YOUR BASE видна с %d из 12 направлений" % seen)
