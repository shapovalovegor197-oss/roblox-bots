# -*- coding: utf-8 -*-
"""Три попытки лока с записью видео из глаз бота, с подписями фаз."""
import sys, time
sys.path.insert(0, "src")
from brainbot import config, log
from brainbot.window import enum_roblox_windows
from brainbot.inputs import Hand
from brainbot.farm import Farmer, FarmTuning
from brainbot.recorder import Recorder

s = config.load(); log.setup(s.logs_dir)
win = enum_roblox_windows()[0]
f = Farmer(window=win, hand=Hand(win, s.input), tuning=FarmTuning(),
           screens_dir=s.screenshots_dir)

out = s.screenshots_dir / "lockrun.mp4"
rec = Recorder(win, out, fps=6).start()
f.recorder = rec
try:
    for i in range(3):
        rec.note("попытка %d: иду к кнопке" % (i + 1))
        t0 = time.time()
        left = f.lock_with_retries(attempts=1)
        dt = time.time() - t0
        rec.note("попытка %d: %s за %.0f с" % (i + 1, "ЗАПЕРТО" if left else "мимо", dt))
        print("попытка %d: %s за %.1f с" % (i + 1, ("заперто %d с" % left) if left else "НЕ вышло", dt))
        time.sleep(1.5)
finally:
    rec.stop()
print("видео:", out)
