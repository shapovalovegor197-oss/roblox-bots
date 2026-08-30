# -*- coding: utf-8 -*-
"""Честный замер сбора под шифт-лок: деньги из ПАНЕЛИ ребёрна (чистое число),
разные схемы прохода — какая собирает."""
import sys, time
sys.path.insert(0, "src")
from brainbot import config, log
from brainbot.window import enum_roblox_windows
from brainbot.inputs import Hand
from brainbot.farm import Farmer, FarmTuning

s = config.load(); log.setup(s.logs_dir)
w = enum_roblox_windows()[0]
f = Farmer(window=w, hand=Hand(w, s.input), tuning=FarmTuning(), screens_dir=s.screenshots_dir)

def panel_cash():
    f.dismiss_modals()
    if not f.open_menu_item("birth"): return None
    time.sleep(0.6)
    info = f.read_rebirth_window()
    f.dismiss_modals()
    return info["have_cash"]

def run(name, seq):
    f.dismiss_modals(); f.reset_to_base(); time.sleep(1.5); f.close_players_table(); time.sleep(0.3)
    before = panel_cash()
    f.dismiss_modals(); f.reset_to_base(); time.sleep(1.4); f.close_players_table(); time.sleep(0.3)
    for k,d in seq:
        f.hand.hold(k,d); time.sleep(0.2)
    after = panel_cash()
    g = None if (before is None or after is None) else after-before
    print("СХЕМА %s: %s -> %s  собрано=%s" % (name, before, after, g), flush=True)
    return g

# схема A: просто вперёд по дорожке (как работало на свободной камере)
run("вперёд-9", [("w",0.45)]*9)
# схема B: змейка по ширине на трёх глубинах
run("змейка", sum([[("w",0.5),("w",0.5),("a",1.3),("d",2.6),("a",1.3)] for _ in range(3)], []))
