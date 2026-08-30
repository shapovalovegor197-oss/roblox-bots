# -*- coding: utf-8 -*-
"""Шифт-лок: как собрать деньги. Респавн смотрит внутрь базы; идём вперёд и
пробуем стрейфы, на каждом шаге печатаем кэш и снимаем кадр."""
import sys, time
sys.path.insert(0, "src")
from brainbot import config, log, ocr
from brainbot.window import enum_roblox_windows
from brainbot.inputs import Hand
from brainbot.farm import Farmer, FarmTuning

s = config.load(); log.setup(s.logs_dir)
w = enum_roblox_windows()[0]
f = Farmer(window=w, hand=Hand(w, s.input), tuning=FarmTuning(), screens_dir=s.screenshots_dir)

f.dismiss_modals(); f.reset_to_base(); time.sleep(1.5); f.close_players_table(); time.sleep(0.3)
c0 = f.read_hud_cash()
print("кэш старт:", c0, flush=True)
p = f.shot("coll0"); print("кадр старт:", p.name, flush=True)

# план: вперёд по дорожке, на каждой глубине — стрейф влево до края и вправо
def cash(): 
    v = f.read_hud_cash()
    return v
seq = [("w",0.5),("w",0.5),("a",1.2),("d",2.4),("a",1.2),
       ("w",0.5),("w",0.5),("a",1.2),("d",2.4),("a",1.2),
       ("w",0.5),("a",1.2),("d",2.4)]
for i,(k,d) in enumerate(seq):
    f.hand.hold(k,d); time.sleep(0.25)
    v = cash()
    print("шаг %2d %s%.1f -> кэш %s" % (i,k,d,v), flush=True)
    if i in (4,9,12):
        print("  кадр:", f.shot("coll_%d"%i).name, flush=True)
