"""Следить за ЖИВЫМ вводом пользователя, отличая его от ввода бота.

Зачем отдельный модуль, а не `GetLastInputInfo`. Тот считает последним вводом
любой — в том числе SendInput самого бота. Для бота, который сам всё время жмёт
клавиши, он всегда показывал бы «пользователь только что был активен», и уступка
не наступала бы никогда.

Поэтому ставим низкоуровневые хуки WH_MOUSE_LL и WH_KEYBOARD_LL и смотрим флаг
инъекции: события от SendInput/pydirectinput помечены LLMHF_INJECTED /
LLKHF_INJECTED, живые движения мыши и нажатия — нет. Считаем только НЕ
помеченные — это и есть настоящий пользователь за машиной.

Хук требует своего цикла сообщений, поэтому крутим его в отдельном потоке.
Колбэк держим в глобальной ссылке: если его соберёт сборщик мусора, Windows
уронит процесс при первом же событии.
"""
from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes

from .log import get

log = get("useractivity")

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
LLKHF_INJECTED = 0x00000010
LLMHF_INJECTED = 0x00000001

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# Типы ОБЯЗАТЕЛЬНЫ. На 64-битной Windows хэндлы и LRESULT — 64-битные, а ctypes
# по умолчанию считает возврат 32-битным c_int и УСЕКАЕТ его: SetWindowsHookExW
# отдавал битый хэндл, и хук молча не вставал. Из-за этого весь монитор
# показывал «пользователя нет» при любом вводе.
LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE,
                                     wintypes.DWORD]
user32.CallNextHookEx.restype = LRESULT
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM,
                                  wintypes.LPARAM]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                               ctypes.c_uint, ctypes.c_uint]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]


class _MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]


class UserActivity:
    """Монитор живого ввода. `seconds_since()` — сколько прошло с последнего
    ДЕЙСТВИЯ ПОЛЬЗОВАТЕЛЯ (не бота)."""

    def __init__(self) -> None:
        self._last = 0.0                 # время последнего живого ввода
        self._running = False
        self._thread: threading.Thread | None = None
        # ссылки, которые нельзя дать собрать сборщику мусора
        self._kb_proc = None
        self._ms_proc = None
        self._kb_hook = None
        self._ms_hook = None

    def seconds_since(self) -> float:
        """Секунд с последнего живого ввода. Большое число — пользователя нет."""
        if not self._last:
            return 1e9
        return time.time() - self._last

    def start(self) -> "UserActivity":
        if self._running:
            return self
        self._running = True
        self._thread = threading.Thread(target=self._run, name="useractivity",
                                        daemon=True)
        self._thread.start()
        # Дать потоку поставить хуки, иначе первые действия бота пройдут без
        # присмотра.
        time.sleep(0.2)
        return self

    def _mark(self, injected: bool) -> None:
        # Считаем ТОЛЬКО живой ввод. Инъекции бота игнорируем — иначе уступка
        # не наступит никогда.
        if not injected:
            self._last = time.time()

    def _run(self) -> None:
        def kb(nCode, wParam, lParam):
            if nCode >= 0:
                st = ctypes.cast(lParam, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
                self._mark(bool(st.flags & LLKHF_INJECTED))
            return user32.CallNextHookEx(None, nCode, wParam, lParam)

        def ms(nCode, wParam, lParam):
            if nCode >= 0:
                st = ctypes.cast(lParam, ctypes.POINTER(_MSLLHOOKSTRUCT)).contents
                self._mark(bool(st.flags & LLMHF_INJECTED))
            return user32.CallNextHookEx(None, nCode, wParam, lParam)

        self._kb_proc = HOOKPROC(kb)
        self._ms_proc = HOOKPROC(ms)
        hmod = kernel32.GetModuleHandleW(None)
        self._kb_hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._kb_proc, hmod, 0)
        self._ms_hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._ms_proc, hmod, 0)
        if not self._kb_hook or not self._ms_hook:
            log.warning("не удалось поставить хуки ввода — уступка работать не будет")
            return
        log.info("монитор живого ввода запущен")
        msg = wintypes.MSG()
        while self._running and user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
