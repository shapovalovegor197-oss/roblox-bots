# -*- coding: utf-8 -*-
"""Покупка с того места, где бот уже стоит у ленты."""
import sys, time, cv2
sys.path.insert(0, "src")
from brainbot import config, log, ocr
from brainbot.window import enum_roblox_windows
from brainbot.inputs import Hand
from brainbot.farm import Farmer, FarmTuning

s = config.load(); log.setup(s.logs_dir)
win = enum_roblox_windows()[0]
f = Farmer(window=win, hand=Hand(win, s.input), tuning=FarmTuning(),
           screens_dir=s.screenshots_dir)

TARGETS = ("trippi troppi", "gangster footera")   # нужны для первого перерождения

f.hand.move(13, 65)          # курсор в угол, чтобы не закрывал надписи
before = f.read_hud_cash()
print("наличные до: %s" % before)
bought = 0
end = time.time() + 90
while time.time() < end:
    fr = f.frame()
    offer = f.read_offer(fr)
    name = (offer.get("name") or "").lower()
    if not name:
        time.sleep(0.3)
        continue
    hit = True if "--any" in sys.argv else any(t in name for t in TARGETS)
    txt = " ".join(t.lower() for t, x, y in ocr.lines(fr))
    prompt = "purchase" in txt or "buy" in txt
    print("вижу %-22s | нужен=%s | промпт=%s" % (offer["name"][:22], "ДА" if hit else "нет",
                                                 "есть" if prompt else "нет"))
    if hit and prompt:
        cv2.imwrite(str(s.screenshots_dir / "buy_before.png"), fr)
        f.hand.interact(3.0)          # ДЕРЖАТЬ: промпты требуют удержания
        time.sleep(1.0)
        after = f.read_hud_cash()
        print("   нажал покупку: %s -> %s" % (before, after))
        cv2.imwrite(str(s.screenshots_dir / "buy_after.png"), f.frame())
        if before and after and after < before:
            bought += 1
            print("   КУПЛЕНО, потрачено %s" % (before - after))
            before = after
            break
    time.sleep(0.4)
print("итого куплено: %d" % bought)
