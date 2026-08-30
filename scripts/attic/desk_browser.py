# -*- coding: utf-8 -*-
"""Помощник, работающий НА СТОЛЕ БОТА: снимок окна браузера и клики в него.

Запускается через desktop.spawn, поэтому его поток привязан к столу бота:
  * WGC-захват по hwnd берёт пиксели окна даже когда стол не виден на мониторе;
  * SendInput/SetCursorPos из этого процесса попадают в очередь ввода СТОЛА
    БОТА, а не твоего — на твоём столе мышь не дёргается.

Режимы:
  shot                 — найти окно браузера, снять, сохранить png, вывести рамку
  click X Y            — клик по координате внутри клиентской области окна
  key ENTER            — нажать клавишу
"""
import sys, time
sys.path.insert(0, "src")
import cv2
import win32gui, win32con, win32api
from brainbot.capture import grab

TITLE_HINTS = ("roblox", "brainrot", "украд", "атроф", "play on")

def find_browser():
    found = []
    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        cls = win32gui.GetClassName(hwnd)
        if not title:
            return
        r = win32gui.GetClientRect(hwnd)
        area = r[2] * r[3]
        score = 10 if any(h in title.lower() for h in TITLE_HINTS) else (1 if "Chrome_WidgetWin" in cls else 0)
        if score:
            found.append((score, area, hwnd, title, cls))
    win32gui.EnumWindows(cb, None)
    found.sort(reverse=True)
    return found[0][1:] if found else None

def client_box(hwnd):
    l, t = win32gui.ClientToScreen(hwnd, (0, 0))
    r = win32gui.GetClientRect(hwnd)
    return l, t, r[2], r[3]

mode = sys.argv[1] if len(sys.argv) > 1 else "shot"
b = find_browser()
if not b:
    print("окно браузера не найдено"); sys.exit(1)
area, hwnd, title, cls = b
print("окно:", hwnd, repr(title), cls)
win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
time.sleep(0.3)
try:
    win32gui.SetForegroundWindow(hwnd)
except Exception:
    pass
time.sleep(0.5)
l, t, w, h = client_box(hwnd)
print("клиент: %dx%d @(%d,%d)" % (w, h, l, t))

if mode == "shot":
    from brainbot.window import Box
    img = grab(Box(l, t, w, h), hwnd=hwnd)
    out = sys.argv[2] if len(sys.argv) > 2 else "var/screens/desk_browser.bmp"
    cv2.imwrite(out, img)
    print("снят:", out)
elif mode == "click":
    x, y = int(sys.argv[2]), int(sys.argv[3])
    win32api.SetCursorPos((l + x, t + y))
    time.sleep(0.2)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.08)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    print("клик в %d,%d (экран %d,%d)" % (x, y, l + x, t + y))
elif mode == "key":
    import pydirectinput
    pydirectinput.press(sys.argv[2].lower())
    print("нажато:", sys.argv[2])
