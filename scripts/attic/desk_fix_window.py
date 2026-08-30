# -*- coding: utf-8 -*-
"""Привести окно Roblox к рабочему размеру. Запускать НА столе бота."""
import sys, time
sys.path.insert(0, "src")
from brainbot import config
from brainbot.window import enum_roblox_windows

s = config.load()
w, h = s.window["width"], s.window["height"]
for _ in range(20):
    wins = enum_roblox_windows()
    if wins:
        win = wins[0]
        win.move_resize(0, 0, w, h)
        time.sleep(0.5)
        box = win.client_box()
        print("окно приведено: %dx%d @(%d,%d) hwnd=%s"
              % (box.width, box.height, box.left, box.top, win.hwnd))
        break
    time.sleep(1)
else:
    print("окно Roblox не найдено")
