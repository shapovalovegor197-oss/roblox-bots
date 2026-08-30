"""Работа с окнами Roblox: найти, привязать к аккаунту, сфокусировать, разложить.

Все координаты сценариев — относительно клиентской области окна, а не экрана.
Это то, что позволяет двигать окна и не переснимать шаблоны.
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from dataclasses import dataclass

from .log import get

log = get("window")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

ROBLOX_CLASS = "WINDOWSCLIENT"
ROBLOX_TITLE = "Roblox"

SW_RESTORE = 9
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010


class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(POINT)]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.SetFocus.argtypes = [wintypes.HWND]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD


@dataclass
class Box:
    """Клиентская область окна в экранных координатах."""
    left: int
    top: int
    width: int
    height: int

    @property
    def region(self) -> tuple[int, int, int, int]:
        """(left, top, right, bottom) — как любит mss/dxcam."""
        return (self.left, self.top, self.left + self.width, self.top + self.height)

    def to_screen(self, x: int, y: int) -> tuple[int, int]:
        return self.left + x, self.top + y


@dataclass
class RobloxWindow:
    hwnd: int
    pid: int
    title: str

    def alive(self) -> bool:
        return bool(user32.IsWindow(self.hwnd))

    def client_box(self) -> Box:
        rect = RECT()
        user32.GetClientRect(self.hwnd, ctypes.byref(rect))
        origin = POINT(0, 0)
        user32.ClientToScreen(self.hwnd, ctypes.byref(origin))
        return Box(origin.x, origin.y, rect.right - rect.left, rect.bottom - rect.top)

    def focus(self) -> None:
        """Перевести окно в передний план — надёжно, с обходом запрета Windows.

        Обычный SetForegroundWindow из фонового процесса Windows часто игнорирует
        (окно только мигает в таскбаре). Для мыши это неважно — клик позиционный,
        но клавиатура идёт строго в окно с фокусом, и WASD тогда уходят в пустоту.
        Трюк: на время присоединить свой поток ввода к потоку целевого окна через
        AttachThreadInput — тогда SetForegroundWindow срабатывает.
        """
        user32.ShowWindow(self.hwnd, SW_RESTORE)
        if user32.SetForegroundWindow(self.hwnd):
            return

        target_tid = user32.GetWindowThreadProcessId(self.hwnd, None)
        our_tid = kernel32.GetCurrentThreadId()
        if target_tid and target_tid != our_tid:
            user32.AttachThreadInput(our_tid, target_tid, True)
            try:
                user32.BringWindowToTop(self.hwnd)
                user32.SetForegroundWindow(self.hwnd)
                user32.SetFocus(self.hwnd)
            finally:
                user32.AttachThreadInput(our_tid, target_tid, False)

    def is_foreground(self) -> bool:
        return user32.GetForegroundWindow() == self.hwnd

    def move_resize(self, x: int, y: int, client_w: int, client_h: int) -> None:
        """Задаёт размер КЛИЕНТСКОЙ области — рамку считаем и добавляем сами."""
        win, cli = RECT(), RECT()
        user32.GetWindowRect(self.hwnd, ctypes.byref(win))
        user32.GetClientRect(self.hwnd, ctypes.byref(cli))
        chrome_w = (win.right - win.left) - (cli.right - cli.left)
        chrome_h = (win.bottom - win.top) - (cli.bottom - cli.top)
        user32.SetWindowPos(self.hwnd, 0, x, y,
                            client_w + chrome_w, client_h + chrome_h,
                            SWP_NOZORDER | SWP_NOACTIVATE)


def _text(fn, hwnd, size=512) -> str:
    buf = ctypes.create_unicode_buffer(size)
    fn(hwnd, buf, size)
    return buf.value


def enum_roblox_windows() -> list[RobloxWindow]:
    """Все живые окна клиента Roblox. Roblox Studio сюда не попадает."""
    found: list[RobloxWindow] = []

    def cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        if _text(user32.GetClassNameW, hwnd, 256) != ROBLOX_CLASS:
            return True
        title = _text(user32.GetWindowTextW, hwnd)
        if ROBLOX_TITLE not in title:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        found.append(RobloxWindow(hwnd=hwnd, pid=pid.value, title=title))
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return found


def wait_for_window(pid: int, timeout: float = 120.0, poll: float = 1.0) -> RobloxWindow | None:
    """Ждёт окно конкретного процесса.

    Клиент Roblox перезапускает себя при обновлении, поэтому PID может смениться.
    Если по PID не нашли, но появилось ровно одно НОВОЕ окно — берём его.
    """
    before = {w.hwnd for w in enum_roblox_windows()}
    deadline = time.time() + timeout
    while time.time() < deadline:
        windows = enum_roblox_windows()
        for w in windows:
            if w.pid == pid:
                log.info("окно найдено по pid=%s hwnd=%s", pid, w.hwnd)
                return w
        fresh = [w for w in windows if w.hwnd not in before]
        if len(fresh) == 1:
            log.info("окно найдено как единственное новое: hwnd=%s pid=%s "
                     "(pid не совпал — клиент перезапустился)", fresh[0].hwnd, fresh[0].pid)
            return fresh[0]
        time.sleep(poll)
    log.warning("окно для pid=%s не появилось за %.0f с", pid, timeout)
    return None


def tile(windows: list[RobloxWindow], w: int, h: int,
         origin: tuple[int, int] = (0, 0), cols: int = 2) -> None:
    """Раскладывает окна сеткой, чтобы они не перекрывали друг друга.

    Перекрытие — это не косметика: закрытый пиксель ломает matchTemplate.
    """
    ox, oy = origin
    for i, win in enumerate(windows):
        win.move_resize(ox + (i % cols) * w, oy + (i // cols) * h, w, h)
