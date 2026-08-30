# -*- coding: utf-8 -*-
"""Круговой ход камеры: горизонт -> вид сверху -> горизонт. С заведомо известного старта."""
import sys, time, cv2
sys.path.insert(0, "src")
from brainbot import config, log, nav, ocr
from brainbot.window import enum_roblox_windows
from brainbot.inputs import Hand
from brainbot.farm import Farmer, FarmTuning

s = config.load(); log.setup(s.logs_dir)
win = enum_roblox_windows()[0]
f = Farmer(window=win, hand=Hand(win, s.input), tuning=FarmTuning(),
           screens_dir=s.screenshots_dir)
f.close_players_table()

# Принудительно в горизонт: тянем вверх с запасом, дальше упора камера не пойдёт.
f.hand.pitch_up(1.6); time.sleep(1.0)
before = f.frame()
cv2.imwrite(str(s.screenshots_dir / "rt_1_before.png"), before)

f.hand.pitch_down(1.0); time.sleep(1.0)
top = f.frame()
cv2.imwrite(str(s.screenshots_dir / "rt_2_top.png"), top)
h, w = top.shape[:2]
found = []
for text, xc, yc in ocr.lines(top):
    t = text.strip().lower()
    if len(t) >= 3:
        found.append("%s@%.2f,%.2f" % (t[:14], xc / w, yc / h))
print("сверху видно: %s" % ("; ".join(found[:8]) if found else "ничего"))

f.hand.pitch_up(1.0); time.sleep(1.0)
after = f.frame()
cv2.imwrite(str(s.screenshots_dir / "rt_3_after.png"), after)

print("горизонт до/после: %.3f  (1.0 = вернулись точно)" % nav._same_scene(before, after))
print("горизонт/сверху:   %.3f  (низкая = наклон сработал)" % nav._same_scene(before, top))
