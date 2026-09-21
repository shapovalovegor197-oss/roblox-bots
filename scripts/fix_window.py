# -*- coding: utf-8 -*-
"""Привести окно Roblox к размеру из настроек. Печатает было/стало.

Размер окна — не косметика. Всё зрение бота считает ДОЛЯМИ кадра: где искать
наличные, где ждать промпт, где лежит пад в виде сверху. Доли снимались при
1280x720, и стоит окну съехать по высоте, как те же доли показывают на другое
место, а вид сверху вдобавок растягивается по горизонтали и врёт пеленгом.

Замер 30.08, 23:30: окно было 1280x599 — на 121 пиксель ниже рабочего.
"""
import ctypes
import sys
from ctypes import wintypes
sys.path.insert(0, "src")
from brainbot import clientsettings, config
from brainbot.window import enum_roblox_windows

s = config.load()
want_w, want_h = int(s.window["width"]), int(s.window["height"])

# Сперва причина, потом следствие. Размер окна клиент хранит У СЕБЯ и применяет,
# когда игра догрузится: пока в его настройках лежит полный экран, любое наше
# SetWindowPos — это разовая правка, которая не переживёт следующего запуска.
запомнено = clientsettings.прочитать()
if запомнено:
    print("клиент помнит: fullscreen=%s, maximized=%s, размер=%s" % (
        запомнено["fullscreen"], запомнено["maximized"],
        "x".join(запомнено["size"]) if запомнено["size"] else "?"))
    поправлено = clientsettings.оконный_режим(want_w, want_h)
    for поле, что in поправлено.items():
        print("  поправил %s: %s" % (поле, что))
    if поправлено:
        print("  живой клиент перепишет это при выходе — рабочим оно станет на "
              "следующем запуске через лаунчер")
wins = enum_roblox_windows()
if not wins:
    sys.exit("окон Roblox нет — клиент не запущен")

for win in wins:
    box = win.client_box()
    if (box.width, box.height) == (want_w, want_h):
        print("окно уже рабочее: %dx%d @(%d,%d)" % (box.width, box.height, box.left, box.top))
        continue
    print("было: %dx%d @(%d,%d)" % (box.width, box.height, box.left, box.top))
    # Позицию не трогаем — окно может стоять на любом мониторе, это выбор
    # пользователя. Правим только размер клиентской области, а угол берём у
    # САМОГО окна: move_resize ждёт координаты окна, а не клиентской области,
    # и на разнице в рамку окно уползает при каждом вызове.
    r = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(win.hwnd, ctypes.byref(r))
    win.move_resize(r.left, r.top, want_w, want_h)
    new = win.client_box()
    print("стало: %dx%d @(%d,%d)%s" % (new.width, new.height, new.left, new.top,
                                       "" if (new.width, new.height) == (want_w, want_h)
                                       else "  — НЕ ПРИВЕЛОСЬ"))
