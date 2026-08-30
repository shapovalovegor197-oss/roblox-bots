"""Захват ROBLOX_singletonMutex — иначе второй клиент не запустится.

Держать нужно всё время жизни процесса-супервизора: как только хэндл закроется,
Roblox снова начнёт убивать лишние окна.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

from .log import get

log = get("mutex")

MUTEX_NAME = "ROBLOX_singletonMutex"
ERROR_ALREADY_EXISTS = 183

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.CreateMutexW.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL


class SingletonMutex:
    """Контекстный менеджер. Пока жив — можно поднимать сколько угодно клиентов."""

    def __init__(self, name: str = MUTEX_NAME) -> None:
        self.name = name
        self.handle: int | None = None

    def acquire(self) -> bool:
        if self.handle:
            return True
        handle = _kernel32.CreateMutexW(None, False, self.name)
        err = ctypes.get_last_error()
        if not handle:
            log.error("CreateMutexW не удался, код %s", err)
            return False
        self.handle = handle
        if err == ERROR_ALREADY_EXISTS:
            # Мьютекс уже кто-то держит: либо запущенный Roblox, либо второй наш
            # процесс. Хэндл всё равно наш и держит объект живым — этого достаточно.
            log.info("мьютекс %s уже существовал, подцепились к нему", self.name)
        else:
            log.info("мьютекс %s захвачен", self.name)
        return True

    def release(self) -> None:
        if self.handle:
            _kernel32.CloseHandle(self.handle)
            log.info("мьютекс отпущен")
            self.handle = None

    def __enter__(self) -> "SingletonMutex":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()
