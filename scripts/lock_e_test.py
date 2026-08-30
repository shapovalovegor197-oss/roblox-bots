# -*- coding: utf-8 -*-
"""Лок срабатывает наступанием или нажатием E? Бот уже стоит у плиты."""
import sys, time, cv2
sys.path.insert(0, "src")
from brainbot import config, log, ocr, nav
from brainbot.window import enum_roblox_windows
from brainbot.inputs import Hand
from brainbot.farm import Farmer, FarmTuning

s = config.load(); log.setup(s.logs_dir)
win = enum_roblox_windows()[0]
f = Farmer(window=win, hand=Hand(win, s.input), tuning=FarmTuning(),
           screens_dir=s.screenshots_dir)


def look_at(tag):
    fr = f.frame()
    cv2.imwrite(str(s.screenshots_dir / ("etest_%s.png" % tag)), fr)
    h, w = fr.shape[:2]
    marks = nav.find_text_landmarks(fr)
    texts = [t.strip().lower() for t, x, y in ocr.lines(fr) if x / w > 0.18]
    joined = " ".join(texts)
    print("  [%s] lock=%s | locked=%s | allow=%s | текст: %s"
          % (tag, "да" if "lock" in marks else "нет",
             "ДА" if "locked" in joined else "нет",
             "ДА" if ("allow" in joined or "friend" in joined and "boost" not in joined) else "нет",
             "; ".join(t[:18] for t in texts if len(t) > 3)[:90]))
    return marks, joined


marks, before = look_at("1_before")
if "locked" in before:
    print("=> лок уже стоял"); sys.exit(0)

# Гейт по СВЕЧЕНИЮ, а не по подписи: OCR читает её как попало — то
# «—'>ck base», то просто «base», то никак. Свечение видно всегда.
glow = f.plate_glow(f.frame())
print("  свечение: %s" % ("площадь %d" % glow[2] if glow else "нет"))
# Годится ЛЮБОЙ из двух признаков. Свечение пропадает ровно когда мы на плите:
# оно оказывается под фигурой, а фигуру я из маски вырезаю. Подпись при этом
# читается. И наоборот — издали видно свечение, а подпись мелкая.
if not ((glow and glow[2] >= 800) or "lock" in marks):
    print("=> плиты рядом не видно — не жму"); sys.exit(1)
# Опасность одна: рядом промпт Allow Friends, он откроет базу друзьям.
if "allow" in before or "friends" in before:
    print("=> рядом Allow Friends — не жму"); sys.exit(1)

print("жму E...")
f.hand.press("e")
time.sleep(1.5)
marks2, after = look_at("2_after_E")
print("=> лок от E: %s" % ("ЕСТЬ" if "locked" in after else "НЕТ"))
