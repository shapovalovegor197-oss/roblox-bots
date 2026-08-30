# -*- coding: utf-8 -*-
"""Полный круг: запер базу -> к ленте -> покупки -> назад к концу таймера -> снова."""
import sys, time
sys.path.insert(0, "src")
from brainbot import config, log, nav, ocr
from brainbot.window import enum_roblox_windows
from brainbot.inputs import Hand
from brainbot.farm import Farmer, FarmTuning

s = config.load(); log.setup(s.logs_dir)
win = enum_roblox_windows()[0]
f = Farmer(window=win, hand=Hand(win, s.input), tuning=FarmTuning(),
           screens_dir=s.screenshots_dir)

ROUNDS = int(sys.argv[1]) if len(sys.argv) > 1 else 2
RESERVE = 10.0          # возврат мгновенный (смертью), резерв нужен только на реакцию
BUY_HOLD = 2.0

for rnd in range(1, ROUNDS + 1):
    print("=== круг %d ===" % rnd)
    t0 = time.time()
    left = f.lock_with_retries(attempts=2)
    t_lock = time.time() - t0
    if not left:
        print("  лок не вышел за %.1f с — круг пропущен" % t_lock)
        continue
    print("  заперто за %.1f с, таймер %d с" % (t_lock, left))

    # Выходим из базы РЕСПАВНОМ. Изнутри, от плиты лока, пад сверху не виден
    # вовсе — «сверху пад не опознан», и разворот к ленте отменяется. Респавн
    # ставит ровно на пад, откуда работает и локализация, и разворот наружу.
    # Приём подсказан пользователем: смерть — это штатный быстрый возврат домой.
    t1 = time.time()
    f.reset_to_base(); time.sleep(1.5)
    f.close_players_table()
    away = f.face_belt_from_top()
    if away is None:
        print("  к ленте развернуться не вышло")
        continue
    # Идём до САМОГО промпта покупки, а не до первой прочитанной вывески.
    # Имя товара читается издалека, а `E — Purchase` появляется только вплотную:
    # круг с бюджетом 17 с не купил ничего именно потому, что встал рано.
    for i in range(8):
        f.hand.hold("w", 0.6)
        time.sleep(0.3)
        fr = f.frame()
        txt = " ".join(t.lower() for t, x, y in ocr.lines(fr))
        if "purchase" in txt:
            break
    t_walk = time.time() - t1
    print("  у ленты за %.1f с (шагов %d)" % (t_walk, i + 1))

    budget = left - (time.time() - t0) - RESERVE
    print("  бюджет на покупки: %.1f с" % budget)
    cash0 = f.read_hud_cash()
    buys, cash = 0, cash0
    while time.time() - t0 < left - RESERVE:
        f.hand.interact(BUY_HOLD)
        time.sleep(0.2)
        now = f.read_hud_cash()
        if now is not None and cash is not None and now < cash - 1:
            buys += 1
            print("    покупка: %s -> %s" % (cash, now))
        if now is not None:
            cash = now
    print("  покупок %d, деньги %s -> %s" % (buys, cash0, cash))

    осталось = f.read_lock_left()
    print("  к концу круга таймер: %s" % осталось)
