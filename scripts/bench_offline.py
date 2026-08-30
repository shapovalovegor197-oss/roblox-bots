# -*- coding: utf-8 -*-
"""Стенд по СОХРАНЁННЫМ кадрам: зрение проверяется без игры и без ввода.

Зачем: 30.08 вечером три правки подряд ушли в прод «на глаз», и каждая
принесла регрессию — деньги стали читаться то в десять раз меньше, то в десять
раз больше, а окна базы стали приниматься за крестик закрытия и отменять лок.
Кадры от тех прогонов лежат в var/screens, и всё это ловится за секунды.

Запуск: python scripts/bench_offline.py
"""
import sys
import glob
import cv2

sys.path.insert(0, "src")
from brainbot.farm import Farmer                     # noqa: E402
from brainbot.nav import find_close_button           # noqa: E402

SCR = "var/screens/"


class Offline(Farmer):
    """Farmer поверх файла: настоящие методы, кадр из png."""

    def __init__(self, img):
        self._img = img

    def frame(self):
        return self._img


# Кадры с суммой, известной глазами.
CASH_TRUTH = {
    # день, мелкие суммы
    "income_check_20260830-151705-633.png": 81860,
    "collect_probe_20260830-151814-267.png": 81860,
    "base_now_20260830-151346-223.png": 81860,
    "now_20260830-145902-606.png": 164360,
    "session_start_20260830-141542-543.png": 164360,
    "rebirth_panel_20260830-144204-895.png": 134360,
    # вечер, крупные — на них и врал тесный вырез
    "cash_now_20260830-215724-986.png": 35590000,
    "after_collect_20260830-220201-679.png": 35590000,
    "buy_at_belt_20260830-220322-025.png": 35590000,
    "rollback_check_20260830-221222-417.png": 35590000,
    "fail_modal_before_lock_20260830-221705-063.png": 35590000,
    "fail_modal_before_lock_20260830-222033-484.png": 35590000,
    "coll_12_20260830-213723-153.png": 35320000,
    "belt_final_20260830-214623-414.png": 35320000,
}

# Кадры с РЕАЛЬНО открытым окном игры: крестик обязан находиться.
MODAL_FRAMES = ["rebirth_panel_20260830-143824-564.png",
                "rebirth_panel_20260830-144204-895.png"]

# Чистый мир: крестика быть не должно ни одного.
CLEAN_GLOBS = ["fail_modal_before_lock_20260830-22*.png",
               "coll*_20260830-2137*.png", "belt*_2026083*.png",
               "plate_*_20260830-2114*.png", "day1_*.png",
               "probe_lock_*_20260830-14*.png"]


def bench_cash():
    ok = bad = miss = 0
    print("== ДЕНЬГИ (правда -> прочитано)")
    for fn, truth in CASH_TRUTH.items():
        img = cv2.imread(SCR + fn)
        if img is None:
            continue
        got = Offline(img)._read_hud_cash_once()
        mark = "ок" if got == truth else ("ПУСТО" if got is None else "ВРЁТ")
        if got == truth:
            ok += 1
        elif got is None:
            miss += 1
        else:
            bad += 1
        print("   %-46s %12s -> %-14s %s" % (fn[:46], truth, got, mark))
    print("   ИТОГО: верно %d, пусто %d, ВРЁТ %d" % (ok, miss, bad))
    return bad


def bench_close():
    print("== КРЕСТИК ЗАКРЫТИЯ")
    found = 0
    for fn in MODAL_FRAMES:
        img = cv2.imread(SCR + fn)
        if img is None:
            continue
        spot = find_close_button(img)
        print("   окно открыто: %-46s -> %s" % (fn[:46], spot and (spot.x, spot.y)))
        found += spot is not None
    files = sorted({p for g in CLEAN_GLOBS for p in glob.glob(SCR + g)})
    false = []
    for p in files:
        img = cv2.imread(p)
        if img is None:
            continue
        spot = find_close_button(img)
        if spot is not None:
            false.append((p.split("\\")[-1][:46], spot.x, spot.y))
    print("   чистых кадров: %d, ложных крестиков: %d" % (len(files), len(false)))
    for f in false[:10]:
        print("      ЛОЖНЫЙ", f)
    print("   ИТОГО: окон найдено %d из %d" % (found, len(MODAL_FRAMES)))
    return len(false) + (len(MODAL_FRAMES) - found)


if __name__ == "__main__":
    bad = bench_cash()
    bad += bench_close()
    print("\nСТЕНД: %s" % ("ЧИСТО" if bad == 0 else "ОШИБОК %d" % bad))
    sys.exit(1 if bad else 0)
