# -*- coding: utf-8 -*-
"""Почему не покупается под шифт-лок. Встаём у ленты, читаем карточку, жмём E."""
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

f.dismiss_modals(); f.reset_to_base(); time.sleep(1.5); f.close_players_table()
for _ in range(6):
    f.hand.look(3250//6,0); time.sleep(0.12)
time.sleep(0.4)
for _ in range(12):
    f.hand.hold("w",0.5); time.sleep(0.22)
    if at_belt(): break
print("у ленты:", at_belt(), "кадр:", f.shot("buy_at_belt").name, flush=True)
f.hand.move(13,65); time.sleep(0.3)
for i in range(6):
    card=f.read_card()
    txt=" ".join(t.lower() for t,x,y in ocr.lines(f.frame()))
    has_prompt="purchase" in txt
    c=f.read_hud_cash()
    print("проба %d: ready=%s name=%s price=%s | prompt=%s кэш=%s"
          % (i, card["ready"], card.get("name"), card.get("price"), has_prompt, c), flush=True)
    # жмём E независимо от read_card, раз промпт есть
    if has_prompt:
        f.hand.interact(2.2); time.sleep(0.6)
        c2=f.read_hud_cash()
        print("   после E: кэш %s -> %s %s" % (c, c2, "КУПЛЕНО" if (c and c2 and c2<c-1) else "нет"), flush=True)
    time.sleep(0.4)
