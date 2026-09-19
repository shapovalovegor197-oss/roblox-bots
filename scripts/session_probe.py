# -*- coding: utf-8 -*-
"""Живут ли два клиента Roblox в ДВУХ сессиях Windows — и видит ли бот фоновую.

Зачем сессии, когда столы уже проверены и не годятся. `ROBLOX_singletonMutex`
создаётся без префикса, а значит лежит в пространстве имён своей сессии: из
второй сессии его не видно вовсе, и клиенты друг о друге не узнают. Плюс у
каждой сессии свой рабочий стол и свой композитор — в отличие от выделенного
стола, где композитора нет и бот слеп.

Проверять надо ровно две вещи, и обе — числами:

* **живут ли оба клиента одновременно** (в консольной сессии и во второй);
* **берётся ли кадр в той сессии, что сейчас НЕ на экране.** Если фоновая
  сессия не рендерит, мы получим ту же слепоту, что и на столе, и тогда путь
  тоже мёртв.

Скрипт пишет отчёт в общую папку, которую видно из обеих сессий, и держится,
пока жив клиент, подписывая кадр раз в несколько секунд.

Запуск:  python scripts/session_probe.py <аккаунт> [минут]
"""
import ctypes
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")

import numpy as np                                          # noqa: E402

from brainbot import capture, config, desktop, launcher, log  # noqa: E402
from brainbot.mutex import SingletonMutex                   # noqa: E402
from brainbot.window import wait_for_window                 # noqa: E402

ОБЩАЯ = Path(r"C:\Users\Public\bots-test")
acc = sys.argv[1] if len(sys.argv) > 1 else "mm2-1"
минут = float(sys.argv[2]) if len(sys.argv) > 2 else 20.0

kernel32 = ctypes.windll.kernel32
сессия = ctypes.c_ulong()
kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(сессия))
номер = int(сессия.value)

ОБЩАЯ.mkdir(parents=True, exist_ok=True)
отчёт = ОБЩАЯ / ("session_%d_%s.txt" % (номер, acc))


def сказать(строка: str) -> None:
    строка = "%s  %s" % (time.strftime("%H:%M:%S"), строка)
    print(строка, flush=True)
    with open(отчёт, "a", encoding="utf-8") as fh:
        fh.write(строка + "\n")


сказать("сессия Windows №%d, пользователь %s, стол %r"
        % (номер, os.environ.get("USERNAME", "?"), desktop.current()))

s = config.load()
log.setup(s.logs_dir)

mutex = SingletonMutex()
mutex.acquire()

account = s.account(acc)
if not account.cookie:
    сказать("пустая кука у %s — нечем входить" % acc)
    raise SystemExit(2)

def поднять():
    """Поднять клиент и вернуть окно. None — не дождались.

    Отдельной функцией, потому что клиент приходится поднимать не раз: 19.09 он
    умирал сам через две минуты после того, как магазинная версия перевела
    установку на свою. Для замера сессий важно, чтобы в момент переключения
    пользователя клиент был жив, а не чтобы прогон был красивым.
    """
    pid = launcher.launch(account, s.place_id)
    сказать("клиент pid=%s запущен" % pid)
    окно = wait_for_window(pid, timeout=180)
    if not окно:
        сказать("окно не появилось за 180 с")
        return None
    окно.move_resize(0, 0, int(s.window.get("width", 1280)),
                     int(s.window.get("height", 720)))
    time.sleep(1.0)
    сказать("ОКНО hwnd=%s, клиентская область %sx%s"
            % (окно.hwnd, окно.client_box().width, окно.client_box().height))
    return окно


win = поднять()
if win is None:
    raise SystemExit(3)
подъёмов = 1

# Дальше самое важное: подписывать, берётся ли кадр. Когда человек переключится
# на другую сессию, эта станет фоновой — и в отчёте будет видно, ослеп бот или
# нет, без единого предположения.
конец = time.time() + минут * 60
пусто = взято = 0
while time.time() < конец:
    if not win.alive():
        сказать("клиент умер — поднимаю заново (подъём %d)" % (подъёмов + 1))
        новое = поднять()
        if новое is None:
            break
        win = новое
        подъёмов += 1
        continue
    try:
        кадр = capture.grab(win.client_box(), win.hwnd)
        взято += 1
        сказать("кадр есть: %sx%s, среднее %.1f"
                % (кадр.shape[1], кадр.shape[0], float(np.mean(кадр))))
    except Exception as беда:                               # noqa: BLE001
        пусто += 1
        сказать("КАДРА НЕТ: %s" % str(беда)[:90])
    time.sleep(5.0)

сказать("итог: кадров %d, отказов %d, подъёмов клиента %d, сейчас %s"
        % (взято, пусто, подъёмов, "жив" if win.alive() else "умер"))
сказать("ГОТОВО")
