# -*- coding: utf-8 -*-
"""Лок от респавна до подтверждения, с замером времени."""
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
runs = int(sys.argv[1]) if len(sys.argv) > 1 else 2
times = []
for i in range(runs):
    # Ждём снятия лока: иначе бот честно возвращается сразу («уже заперта»),
    # и замер показывает пять секунд вместо настоящего времени подхода.
    while True:
        still = f.read_lock_left(quick=True)
        if not still:
            break
        print("  жду снятия лока, осталось %d с" % still)
        time.sleep(min(still, 15))
    t0 = time.time()
    left = f.lock_with_retries(attempts=2)
    dt = time.time() - t0
    print("прогон %d: %s за %.1f с" % (i + 1, ("заперто, %d с" % left) if left else "НЕ вышло", dt))
    if left:
        times.append(dt)
    time.sleep(2)
if times:
    print("успешных %d из %d, среднее время %.1f с" % (len(times), runs, sum(times)/len(times)))
else:
    print("успешных 0 из %d" % runs)
