# -*- coding: utf-8 -*-
"""Перечислить окна СТОЛА, на котором запущен этот процесс."""
import sys
import win32gui
def cb(hwnd, acc):
    if win32gui.IsWindowVisible(hwnd):
        t = win32gui.GetWindowText(hwnd); c = win32gui.GetClassName(hwnd)
        if t or "Chrome" in c or "Roblox" in c:
            acc.append("%s\t%s\t%r" % (hwnd, c, t[:50]))
acc = []
win32gui.EnumWindows(cb, acc)
print("\n".join(acc) if acc else "окон нет")
