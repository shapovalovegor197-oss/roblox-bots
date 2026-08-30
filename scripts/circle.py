# -*- coding: utf-8 -*-
"""Круг целиком: запер -> вышел -> лента -> покупки по таймеру -> домой -> запер.

Бюджет вылазки считается по НАШИМ часам (`lock_until`), а не перечитыванием
счётчика: игра говорит длительность один раз вспышкой, дальше зрение не нужно.
"""
import sys, time
sys.path.insert(0, "src")
from brainbot import config, log, ocr
from brainbot.window import enum_roblox_windows
from brainbot.inputs import Hand
from brainbot.farm import Farmer, FarmTuning

s = config.load(); log.setup(s.logs_dir)
win = enum_roblox_windows()[0]
f = Farmer(window=win, hand=Hand(win, s.input), tuning=FarmTuning(),
           screens_dir=s.screenshots_dir)

ROUNDS = int(sys.argv[1]) if len(sys.argv) > 1 else 1
RESERVE = 8.0            # секунд до конца лока, когда пора домой
BUY_HOLD = 2.0           # промпт держать, иначе не засчитывается
MIN_INCOME = float(sys.argv[2]) if len(sys.argv) > 2 else 1000.0
# Адресная охота: эти нужны для перерождения, берём независимо от дохода.
from brainbot.brainrots import normalize
TARGETS = [t.strip() for t in (sys.argv[3].split(",") if len(sys.argv) > 3 else [])]
WANT = {normalize(t) for t in TARGETS if t}


def goto_belt(max_steps: int = 10) -> float | None:
    """Из базы к ленте. Возвращает секунды или None."""
    t = time.time()
    f.reset_to_base()
    time.sleep(1.2)
    f.close_players_table()
    if f.face_belt_from_top() is None:
        return None
    for i in range(max_steps):
        f.hand.hold("w", 0.6)
        time.sleep(0.25)
        txt = " ".join(x.lower() for x, _, _ in ocr.lines(f.frame()))
        if "purchase" in txt:
            return time.time() - t
    return None


for rnd in range(1, ROUNDS + 1):
    print("=== круг %d ===" % rnd, flush=True)
    while f.lock_left_now() > 0:
        time.sleep(min(f.lock_left_now(), 5))

    t0 = time.time()
    left = f.lock_with_retries(attempts=2)
    print("  лок: %s за %.1f с" % (("%d с" % left) if left else "НЕ ВЫШЕЛ", time.time() - t0),
          flush=True)
    if not left:
        continue

    t_walk = goto_belt()
    if t_walk is None:
        print("  до ленты не дошёл, осталось лока %d с" % f.lock_left_now(), flush=True)
        continue
    print("  у ленты за %.1f с, бюджет на закуп %d с" % (t_walk, f.lock_left_now() - RESERVE),
          flush=True)

    f.hand.move(13, 65)          # курсор в угол, чтобы не закрывал надписи
    cash0 = f.read_hud_cash()
    cash = cash0
    buys, skipped, misses = 0, 0, 0
    seen = []
    while f.lock_left_now() > RESERVE:
        card = f.read_card()
        if not card["ready"]:
            time.sleep(0.2)
            continue
        item = card.get("item")
        name = item.name if item else (card.get("name") or "?")
        income = (item.base_income if item else None) or 0
        seen.append((name, income, card.get("price")))
        hunted = bool(item) and normalize(item.name) in WANT
        if not hunted and income < MIN_INCOME:
            skipped += 1
            time.sleep(0.35)
            continue
        if hunted:
            print("    ЦЕЛЬ на ленте: %s, цена %s" % (name, card.get("price")), flush=True)
        f.hand.interact(BUY_HOLD)
        time.sleep(0.5)
        now = f.read_hud_cash()
        if now is not None and cash is not None and now < cash - 1:
            buys += 1
            misses = 0
            print("    куплено %s (доход %s/с): %.0f -> %.0f" % (name, income, cash, now),
                  flush=True)
        else:
            misses += 1
        if now is not None:
            cash = now
    print("  покупок %d, пропущено %d, промахов %d, кэш %s -> %s"
          % (buys, skipped, misses, cash0, cash), flush=True)
    if seen:
        print("  видел на ленте: %s" % ", ".join("%s(%s)" % (n, i) for n, i, _ in seen[:12]),
              flush=True)
    print("  к концу круга по часам осталось %d с" % f.lock_left_now(), flush=True)
