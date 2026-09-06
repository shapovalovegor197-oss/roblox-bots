# -*- coding: utf-8 -*-
"""Держать окно Roblox рабочим ВСЮ ночь, а не только на старте прогона.

Зачем отдельный сторож. Размер окна — это калибровка: всё зрение считает долями
кадра, снятыми при 1280x720. `farm_loop` проверяет размер один раз, перед
первым кадром, и этого мало: 06.09 в 20:00:24 лаунчер выставил 1280x720, в
20:00:34 farm_loop увидел рабочее окно и промолчал, а к 20:04 клиент стоял уже
800x599 — Roblox применяет СВОЙ сохранённый размер, когда игра догрузится.
Кадры при этом уходят в 4:3, и доли показывают мимо: имя карточки не читается,
полоса кассы ребёрна меряется не там, где нарисована.

Сторож ничего не нажимает и ввод не занимает — только SetWindowPos, поэтому
может работать рядом с фермой.

Запуск: python scripts/window_keeper.py [секунд_между_проверками]
"""
import ctypes
import sys
import time
from ctypes import wintypes

sys.path.insert(0, "src")
from brainbot import config                                  # noqa: E402
from brainbot.window import enum_roblox_windows              # noqa: E402

ПАУЗА = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0

s = config.load()
НУЖНО = (int(s.window["width"]), int(s.window["height"]))


def сказать(текст: str) -> None:
    print("%s %s" % (time.strftime("%H:%M:%S"), текст), flush=True)


сказать("сторож окна пошёл: держу %dx%d, проверка раз в %.0f с" % (*НУЖНО, ПАУЗА))
правок = 0
while True:
    try:
        for win in enum_roblox_windows():
            box = win.client_box()
            if (box.width, box.height) == НУЖНО:
                continue
            r = wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(win.hwnd, ctypes.byref(r))
            win.move_resize(r.left, r.top, *НУЖНО)
            новое = win.client_box()
            правок += 1
            сказать("окно съехало на %dx%d — вернул %dx%d%s (правка №%d)"
                    % (box.width, box.height, новое.width, новое.height,
                       "" if (новое.width, новое.height) == НУЖНО else " — НЕ ПРИВЕЛОСЬ",
                       правок))
    except Exception as exc:                                  # noqa: BLE001
        сказать("проверка не удалась: %s" % exc)
    time.sleep(ПАУЗА)
