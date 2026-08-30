# -*- coding: utf-8 -*-
"""Шифт-лок: замкнутый разворот к ленте — крутим, пока не увидим внешний мир."""
import sys, time
sys.path.insert(0, "src")
from brainbot import config, log, ocr
from brainbot.window import enum_roblox_windows
from brainbot.inputs import Hand
from brainbot.farm import Farmer, FarmTuning

s = config.load(); log.setup(s.logs_dir)
w = enum_roblox_windows()[0]
f = Farmer(window=w, hand=Hand(w, s.input), tuning=FarmTuning(), screens_dir=s.screenshots_dir)

f.dismiss_modals(); f.reset_to_base(); time.sleep(1.6); f.close_players_table(); time.sleep(0.3)
# крутимся шагами ~30 градусов (по mouselook ~600 единиц), пока не увидим внешний мир
step = 600
turned = 0
for i in range(12):
    fr = f.frame()
    out = f.looking_outside(fr)
    print("поворот %d (~%d ед): внешний мир=%s" % (i, turned, out), flush=True)
    if out and turned > 900:   # отвернулись от базы достаточно
        print("вижу внешний мир, кадр:", f.shot("belt_out").name, flush=True); break
    f.hand.look(step, 0); time.sleep(0.25); turned += step
# идём к ленте
for i in range(12):
    f.hand.hold("w", 0.5); time.sleep(0.25)
    txt = " ".join(t.lower() for t,x,y in ocr.lines(f.frame()))
    if "purchase" in txt:
        print("ЛЕНТА на шаге", i, "кадр", f.shot("belt_ok").name, flush=True); break
    if i in (5,11):
        print("шаг %d: %s" % (i, txt[:60]), "кадр", f.shot("belt_w%d"%i).name, flush=True)
