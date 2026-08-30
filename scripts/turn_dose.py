# -*- coding: utf-8 -*-
"""Как слать поворот, чтобы игра его получила целиком: доза против дозы.

Замер по пеленгу пада сверху. Одно и то же число единиц отправляется разными
порциями; если игра теряет ввод, градусы будут разные.
"""
import sys, time, math
sys.path.insert(0, "src")
from brainbot import config, log
from brainbot.window import enum_roblox_windows
from brainbot.inputs import Hand
from brainbot.farm import Farmer, FarmTuning

s = config.load(); log.setup(s.logs_dir)
w = enum_roblox_windows()[0]
f = Farmer(window=w, hand=Hand(w, s.input), tuning=FarmTuning(), screens_dir=s.screenshots_dir)


def bearing():
    f.hand.pitch_top(); time.sleep(0.6)
    fr = f.frame(); h, wd = fr.shape[:2]
    pad = f.pad_from_top(fr)
    f.hand.pitch_normal(back=f.tuning.view_pitch_back, already_top=True); time.sleep(0.3)
    if not pad:
        return None
    px, py, _ = pad
    return math.degrees(math.atan2(px - wd / 2.0, -(py - h / 2.0)))


def turn(units, chunk, steps, pause):
    """Отправить units порциями по chunk, каждая — steps рывками."""
    sent = 0
    while sent < units:
        part = min(chunk, units - sent)
        f.hand.look(part, 0, steps=steps)
        sent += part
        time.sleep(pause)


f.reset_to_base(); time.sleep(1.6)
f.set_work_view(); f.close_players_table()

UNITS = 600
CASES = [("как сейчас: 1x600, 12 рывков", 600, 12, 0.0),
         ("2x300, 12 рывков",             300, 12, 0.15),
         ("6x100, 4 рывка",               100, 4,  0.10)]
for name, chunk, steps, pause in CASES:
    b0 = bearing()
    if b0 is None:
        print(name, "— пад не опознан", flush=True); continue
    turn(UNITS, chunk, steps, pause)
    time.sleep(0.6)
    b1 = bearing()
    if b1 is None:
        print(name, "— пад не опознан после", flush=True); continue
    d = abs((b1 - b0 + 540) % 360 - 180)
    print("%-32s %d ед -> %.1f град, оборот ~%.0f ед"
          % (name, UNITS, d, UNITS * 360.0 / d if d > 1 else 0), flush=True)
