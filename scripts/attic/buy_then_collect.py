# -*- coding: utf-8 -*-
"""Купить несколько брейнротов, вернуться, замерить сбор честным чтением."""
import sys, time
sys.path.insert(0, "src")
from brainbot import config, log, ocr
from brainbot.window import enum_roblox_windows
from brainbot.inputs import Hand
from brainbot.farm import Farmer, FarmTuning

s = config.load(); log.setup(s.logs_dir)
w = enum_roblox_windows()[0]
f = Farmer(window=w, hand=Hand(w, s.input), tuning=FarmTuning(), screens_dir=s.screenshots_dir)

def at_belt():
    return "purchase" in " ".join(t.lower() for t,x,y in ocr.lines(f.frame()))

# на ленту (шифт-лок разворот)
f.dismiss_modals(); f.reset_to_base(); time.sleep(1.5); f.close_players_table()
for _ in range(6):
    f.hand.look(3250//6,0); time.sleep(0.12)
time.sleep(0.4)
got_belt=False
for _ in range(12):
    f.hand.hold("w",0.5); time.sleep(0.22)
    if at_belt(): got_belt=True; break
print("у ленты:", got_belt, flush=True)
if got_belt:
    f.hand.move(13,65)
    cash0=f.read_hud_cash(); print("кэш до закупа:", cash0, flush=True)
    bought=0
    end=time.time()+40
    while time.time()<end and bought<5:
        card=f.read_card()
        if not card["ready"]:
            time.sleep(0.2); continue
        c=f.read_hud_cash()
        f.hand.interact(2.0); time.sleep(0.5)
        c2=f.read_hud_cash()
        if c and c2 and c2<c-1:
            bought+=1
            print("  куплен %s: %s -> %s" % (card.get("name"), c, c2), flush=True)
        time.sleep(0.3)
    print("куплено штук:", bought, flush=True)

# теперь сбор, честным чтением через тесный вырез
time.sleep(1.0)
before=f.read_hud_cash()
gain=f.collect_money(attempts=1)
after=f.read_hud_cash()
print("СБОР: было %s, метод вернул %s, стало %s" % (before, gain, after), flush=True)
print("кадр базы:", f.shot("after_collect").name, flush=True)
