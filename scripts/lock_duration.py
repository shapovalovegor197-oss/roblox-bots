# -*- coding: utf-8 -*-
"""Сколько секунд держится лок: запереть и вести счётчик до нуля."""
import sys, time
sys.path.insert(0, "src")
from brainbot import config, log
from brainbot.window import enum_roblox_windows
from brainbot.inputs import Hand
from brainbot.farm import Farmer, FarmTuning

s = config.load(); log.setup(s.logs_dir)
win = enum_roblox_windows()[0]
f = Farmer(window=win, hand=Hand(win, s.input), tuning=FarmTuning(),
           screens_dir=s.screenshots_dir)

if f.read_lock_left():
    print("лок ещё держится, жду окончания...")
    while f.read_lock_left():
        time.sleep(5)

left = f.lock_with_retries(attempts=2)
if not left:
    print("запереть не вышло"); sys.exit(1)
t0 = time.time()
print("заперто, счётчик показал %d с" % left)
last = left
while True:
    time.sleep(4)
    cur = f.read_lock_left()
    if cur is None:
        print("счётчик пропал через %.1f с после лока" % (time.time() - t0))
        break
    if cur != last:
        print("  %5.1f с: счётчик %d" % (time.time() - t0, cur))
        last = cur
    if time.time() - t0 > 200:
        print("  прервал по времени, счётчик %d" % cur)
        break
print("ПОЛНАЯ ДЛИТЕЛЬНОСТЬ ЛОКА ~ %d с" % (left + int(time.time() - t0) if False else left))
