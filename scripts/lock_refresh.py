# -*- coding: utf-8 -*-
"""Обновляется ли таймер лока, если пройти по плите ПОКА ЛОК ЕЩЁ ДЕРЖИТ?

Зачем. Круг упирается в арифметику: лок 90 с, дорога к ленте 33-44 с, запас на
возврат 45 с — ленте достаётся 7-15 с, и цель ребёрна не поймать. Если плита
обновляет таймер досрочно, дверь не открывается вовсе и весь бюджет меняется.

Ответ даёт вспышка «You locked your base for N Seconds!»: она появляется только
в момент СРАБАТЫВАНИЯ плиты. Плюс сравниваем счётчик до и после прохода.
"""
import sys
import time

sys.path.insert(0, "src")
from brainbot import config, log                      # noqa: E402
from brainbot.window import enum_roblox_windows       # noqa: E402
from brainbot.inputs import Hand                      # noqa: E402
from brainbot.farm import Farmer, FarmTuning          # noqa: E402
from brainbot import single                          # noqa: E402

single.занять("опыт с локом")

WAIT = float(sys.argv[1]) if len(sys.argv) > 1 else 25.0

s = config.load()
log.setup(s.logs_dir)
w = enum_roblox_windows()[0]
f = Farmer(window=w, hand=Hand(w, s.input), tuning=FarmTuning(), screens_dir=s.screenshots_dir)

# Дорога к плите — та же, что в круге: респавн плюс вид сверху.
full = f.lock_with_retries(attempts=2)
print("лок взят: %s с" % full)
if not full:
    sys.exit("замок не сел — опыт бессмысленен")

time.sleep(WAIT)
before = f.read_lock_left()
print("через %.0f с счётчик показывает: %s" % (WAIT, before))

# Сойти с плиты и пройти по ней снова.
f.hand.hold("s", 1.0)
time.sleep(0.5)
f.step_onto_plate()

flash = None
for _ in range(8):
    flash = f.read_lock_flash()
    if flash:
        break
    time.sleep(0.4)
after = f.read_lock_left()
print("вспышка после повторного прохода: %s" % flash)
print("счётчик после прохода: %s" % after)

if flash:
    print("ВЫВОД: плита срабатывает повторно, таймер обновляется на %s с" % flash)
elif before and after and after > before + 10:
    print("ВЫВОД: вспышки не видел, но счётчик вырос %s -> %s — обновление есть" % (before, after))
else:
    print("ВЫВОД: обновления НЕТ — пока лок держит, плита не срабатывает")

# Опыт закончен — дверь оставляем ЗАКРЫТОЙ. Правило пользователя от 03.09:
# во время тестов база не должна стоять открытой.
single.запереть_базу(f)
