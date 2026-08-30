# -*- coding: utf-8 -*-
"""От упора вниз поднимаем камеру разными долями — ищем обычный вид из-за плеча."""
import sys, time, cv2, numpy as np
sys.path.insert(0, "src")
from brainbot import config, log, ocr
from brainbot.window import enum_roblox_windows
from brainbot.inputs import Hand
from brainbot.farm import Farmer, FarmTuning

s = config.load(); log.setup(s.logs_dir)
win = enum_roblox_windows()[0]
f = Farmer(window=win, hand=Hand(win, s.input), tuning=FarmTuning(),
           screens_dir=s.screenshots_dir)
f.close_players_table()

def sky_share(fr):
    """Доля кадра выше линии горизонта — грубый признак, куда смотрит камера."""
    top = fr[: fr.shape[0] // 3]
    hsv = cv2.cvtColor(top, cv2.COLOR_BGR2HSV)
    return float((hsv[:, :, 2] > 120).mean())

for up in (0.0, 0.30, 0.45, 0.60, 0.75):
    f.hand.pitch_down(2.0); time.sleep(0.8)      # упор вниз — общий старт
    if up:
        f.hand.pitch_up(up); time.sleep(0.8)
    fr = f.frame()
    cv2.imwrite(str(s.screenshots_dir / ("pitch_up%02d.png" % int(up * 100))), fr)
    texts = [t.strip() for t, x, y in ocr.lines(fr) if len(t.strip()) >= 4][:6]
    print("подъём %.2f: верх кадра светлый на %.2f | %s"
          % (up, sky_share(fr), "; ".join(texts)))
