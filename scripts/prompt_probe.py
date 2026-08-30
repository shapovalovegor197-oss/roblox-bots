# -*- coding: utf-8 -*-
"""Засчитывается ли удержание E: снимаем кадры прямо во время нажатия."""
import sys, time, threading, cv2
sys.path.insert(0, "src")
from brainbot import config, log, ocr
from brainbot.window import enum_roblox_windows
from brainbot.inputs import Hand
from brainbot.farm import Farmer, FarmTuning
import pydirectinput

s = config.load(); log.setup(s.logs_dir)
win = enum_roblox_windows()[0]
f = Farmer(window=win, hand=Hand(win, s.input), tuning=FarmTuning(),
           screens_dir=s.screenshots_dir)
f.hand.move(13, 65)

# ждём промпт
for _ in range(40):
    fr = f.frame()
    txt = " ".join(t.lower() for t, x, y in ocr.lines(fr))
    if "purchase" in txt:
        break
    time.sleep(0.3)
else:
    print("промпт не появился"); sys.exit(1)

print("промпт есть, держу E 3 секунды и снимаю")
f.hand.ensure_focus()
shots = []
def grab():
    for i in range(6):
        time.sleep(0.45)
        shots.append((i, f.frame()))
t = threading.Thread(target=grab); t.start()
pydirectinput.keyDown("e")
time.sleep(3.0)
pydirectinput.keyUp("e")
t.join()
for i, fr in shots:
    cv2.imwrite(str(s.screenshots_dir / ("prompt_%d.png" % i)), fr)
    txt = [tt.strip() for tt, x, y in ocr.lines(fr) if len(tt.strip()) > 3]
    print("  кадр %d: %s" % (i, "; ".join(txt[:4])[:70]))
print("наличные после:", f.read_hud_cash())
