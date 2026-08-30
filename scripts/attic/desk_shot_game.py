# -*- coding: utf-8 -*-
"""Снять кадр клиента Roblox. Запускать НА столе бота (WGC по hwnd)."""
import sys
sys.path.insert(0, "src")
import cv2
from brainbot import config
from brainbot.window import enum_roblox_windows
from brainbot.capture import grab
s = config.load()
wins = enum_roblox_windows()
if not wins:
    print("окна нет"); sys.exit(1)
win = wins[0]
img = grab(win.client_box(), hwnd=win.hwnd)
out = sys.argv[1] if len(sys.argv) > 1 else "var/screens/desk_game.bmp"
cv2.imwrite(out, img)
print("снят: %s (%dx%d)" % (out, img.shape[1], img.shape[0]))
