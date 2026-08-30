# -*- coding: utf-8 -*-
"""Честный замер лока: N настоящих подходов, с фазами.

Прежний стенд врал: `read_lock_left` изредка не читает счётчик, стенд решал,
что лок снят, шёл запирать — а `lock_via_top` видел живой таймер и возвращал
его же за 14 секунд. Три «успешных прогона» оказались одним локом.

Здесь снятие лока проверяется ТРЕМЯ независимыми признаками:
  * наши часы (`lock_until`) — игра сказала длительность вспышкой;
  * счётчик у входа не читается несколько раз подряд;
  * плита СВЕТИТСЯ (пока лок держится, свечения нет) — это независимо от OCR.
"""
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

RUNS = int(sys.argv[1]) if len(sys.argv) > 1 else 3


def wait_unlocked(limit: float = 180.0) -> None:
    """Дождаться, пока лок точно снят."""
    end = time.time() + limit
    while time.time() < end:
        mine = f.lock_left_now()
        if mine > 0:
            time.sleep(min(mine, 10))
            continue
        seen = [f.read_lock_left(quick=False) for _ in range(2)]
        if not any(seen):
            return
        left = max(x for x in seen if x)
        print("   лок ещё держится (%d с по счётчику)" % left, flush=True)
        time.sleep(min(left, 15))


rows = []
for i in range(1, RUNS + 1):
    wait_unlocked()
    t0 = time.time()
    got = f.lock_with_retries(attempts=2)
    dt = time.time() - t0
    rows.append((got, dt))
    print("прогон %d: %s за %.1f с" % (i, ("ЗАПЕРТО %d с" % got) if got else "НЕ ВЫШЛО", dt),
          flush=True)

ok = [dt for got, dt in rows if got]
print("--- итог: %d из %d, время %s, среднее %.1f, медиана %.1f" % (
    len(ok), RUNS, " ".join("%.1f" % x for x in ok),
    sum(ok) / len(ok) if ok else 0.0,
    sorted(ok)[len(ok) // 2] if ok else 0.0), flush=True)
