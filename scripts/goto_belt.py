# -*- coding: utf-8 -*-
"""Пункт 5: дойти до ленты и прочитать, что на ней едет."""
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


def aim_conveyor(tol=0.03, tries=8):
    """Довернуться на ленту, не двигаясь. Тот же приём, что и с плитой."""
    last = None
    for _ in range(tries):
        fr = f.frame()
        w = fr.shape[1]
        c = nav.find_conveyor(fr)
        if not c:
            return last
        off = (c.x - w / 2.0) / w
        last = off
        if abs(off) <= tol:
            return off
        f.hand.look(int(f.nav.units_for_pixels(off * w) * 0.8), 0)
        time.sleep(0.45)
    return last


f.reset_to_base(); time.sleep(1.6)
f.close_players_table()
away = f.face_belt_from_top()
print("развернулся от базы на %s" % (("%+.1f град" % away) if away is not None else "НЕ вышло"))
if away is None:
    sys.exit(1)

for i in range(12):
    f.hand.hold("w", 0.6)
    time.sleep(0.5)
    fr = f.frame()
    h, w = fr.shape[:2]
    offer = f.read_offer(fr)
    c = nav.find_conveyor(fr)
    names = [t.strip() for t, x, y in ocr.lines(fr[:int(h * 0.6)]) if len(t.strip()) > 4]
    print("шаг %2d: лента=%s | товар=%s | текст: %s"
          % (i + 1,
             ("%.3f,%.3f" % (c.x / w, c.y / h)) if c else "нет",
             offer.get("name") or "-",
             "; ".join(names[:3])[:70]))
    if offer.get("name"):
        cv2.imwrite(str(s.screenshots_dir / "belt_offer.png"), fr)
        print("=> товар прочитан: %s" % offer)
        break
    if f.inside_base(fr):
        print("   ушёл внутрь базы — разворачиваюсь обратно")
        f.face_belt_from_top()
    elif c and abs(c.x / w - 0.5) > 0.10:
        aim_conveyor()
cv2.imwrite(str(s.screenshots_dir / "belt_end.png"), f.frame())
