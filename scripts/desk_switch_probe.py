# -*- coding: utf-8 -*-
"""Зрение на СВОЁМ столе, когда стол показан на экране, и когда убран.

Прошлый замер был нечестным: клиент поднимали на невидимом столе, и он, судя по
всему, не начинал рисовать вовсе — тогда и захватывать нечего, сколько потом
стол ни показывай. Здесь наоборот: стол СНАЧАЛА выводится на экран, клиент
поднимается при живом композиторе, и только потом экран возвращается человеку.

Так проверяется схема «столы по очереди»: активный стол — работающий бот,
остальные ждут. Если кадр берётся, пока стол показан, и пропадает, когда его
убрали, — схема рабочая, но только для одного бота за раз. Если кадра нет даже
на показанном столе — столы закрыты окончательно.

Бежит НА своём столе; пишет отчёт, по которому видно обе фазы.
"""
import sys
import time

sys.path.insert(0, "src")

import numpy as np                                          # noqa: E402

from brainbot import capture, config, desktop, launcher, log  # noqa: E402
from brainbot.mutex import SingletonMutex                   # noqa: E402
from brainbot.window import wait_for_window                 # noqa: E402

acc = sys.argv[1] if len(sys.argv) > 1 else "raven"
секунд = float(sys.argv[2]) if len(sys.argv) > 2 else 180.0
# Место передаём явно: настройки brainbot смотрят в Steal a Brainrot, а тестируем
# мы контур MM2 (142823291).
место = int(sys.argv[3]) if len(sys.argv) > 3 else 0

s = config.load()
log.setup(s.logs_dir)


def сказать(строка: str) -> None:
    print("%s  %s" % (time.strftime("%H:%M:%S"), строка), flush=True)


сказать("стол %r" % desktop.current())
mutex = SingletonMutex()
mutex.acquire()

account = s.account(acc)
place = место or s.place_id
pid = launcher.launch(account, place)
сказать("клиент pid=%s запущен в place=%s (стол сейчас должен быть НА ЭКРАНЕ)"
        % (pid, place))

win = wait_for_window(pid, timeout=180)
if not win:
    сказать("окно не появилось")
    raise SystemExit(3)
win.move_resize(0, 0, int(s.window.get("width", 1280)), int(s.window.get("height", 720)))
time.sleep(1.0)
сказать("ОКНО hwnd=%s %sx%s" % (win.hwnd, win.client_box().width, win.client_box().height))

конец = time.time() + секунд
взято = пусто = 0
while time.time() < конец and win.alive():
    try:
        кадр = capture.grab(win.client_box(), win.hwnd)
        взято += 1
        сказать("кадр есть: %sx%s, среднее %.1f"
                % (кадр.shape[1], кадр.shape[0], float(np.mean(кадр))))
    except Exception as беда:                               # noqa: BLE001
        пусто += 1
        сказать("КАДРА НЕТ: %s" % str(беда)[:80])
    time.sleep(3.0)

сказать("итог: кадров %d, отказов %d, клиент %s"
        % (взято, пусто, "жив" if win.alive() else "умер"))
сказать("ГОТОВО")
