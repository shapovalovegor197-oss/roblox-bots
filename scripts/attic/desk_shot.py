# -*- coding: utf-8 -*-
"""Заглянуть на стол бота: переключить экран, снять кадр, вернуть назад."""
import sys, time, ctypes
sys.path.insert(0, "src")
import win32con, win32service, win32gui, win32ui
from ctypes import windll

out = sys.argv[1] if len(sys.argv) > 1 else "var/screens/desk_bot.bmp"

def switch(h):
    if not windll.user32.SwitchDesktop(int(h)):
        raise ctypes.WinError()

def grab_screen(path):
    hdesktop = win32gui.GetDesktopWindow()
    w = windll.user32.GetSystemMetrics(0); h = windll.user32.GetSystemMetrics(1)
    dc = win32gui.GetWindowDC(hdesktop)
    srcdc = win32ui.CreateDCFromHandle(dc); memdc = srcdc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap(); bmp.CreateCompatibleBitmap(srcdc, w, h)
    memdc.SelectObject(bmp)
    memdc.BitBlt((0, 0), (w, h), srcdc, (0, 0), win32con.SRCCOPY)
    bmp.SaveBitmapFile(memdc, path)
    srcdc.DeleteDC(); memdc.DeleteDC(); win32gui.ReleaseDC(hdesktop, dc)
    win32gui.DeleteObject(bmp.GetHandle())

current = win32service.OpenInputDesktop(0, False, win32con.MAXIMUM_ALLOWED)
target = win32service.OpenDesktop("brainbot", 0, False, win32con.MAXIMUM_ALLOWED)
try:
    switch(target); time.sleep(1.3); grab_screen(out); print("снят:", out)
finally:
    switch(current); print("экран возвращён")
