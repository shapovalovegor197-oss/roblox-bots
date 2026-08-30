# -*- coding: utf-8 -*-
"""Шифт-лок: дойти до ленты. Респавн смотрит ВНУТРЬ базы, лента снаружи —
развернуться на ~180 и идти. Меряем, на каком шаге появляется 'Purchase'."""
import sys, time
sys.path.insert(0, "src")
from brainbot import config, log, ocr
from brainbot.window import enum_roblox_windows
from brainbot.inputs import Hand
from brainbot.farm import Farmer, FarmTuning

s = config.load(); log.setup(s.logs_dir)
w = enum_roblox_windows()[0]
f = Farmer(window=w, hand=Hand(w, s.input), tuning=FarmTuning(), screens_dir=s.screenshots_dir)

def seen():
    txt = " ".join(t.lower() for t,x,y in ocr.lines(f.frame()))
    return ("purchase" in txt), txt

f.dismiss_modals(); f.reset_to_base(); time.sleep(1.6); f.close_players_table(); time.sleep(0.3)
full = int(f.nav.full_turn)
print("полный оборот =", full, "кадр старт:", f.shot("belt0").name, flush=True)
# разворот на 180 дроблением (длинную протяжку игра теряет)
half = full//2
for _ in range(5):
    f.hand.look(half//5, 0); time.sleep(0.15)
time.sleep(0.5)
print("после разворота 180:", f.shot("belt_turned").name, flush=True)
for i in range(12):
    f.hand.hold("w", 0.5); time.sleep(0.25)
    ok, txt = seen()
    print("шаг %2d: purchase=%s | %s" % (i, ok, txt[:60]), flush=True)
    if ok:
        print("ЛЕНТА достигнута на шаге", i, "кадр", f.shot("belt_reached").name, flush=True)
        break
