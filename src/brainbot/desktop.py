"""Отдельный рабочий стол Windows: бот работает, машина свободна.

Зачем именно так. Ввод в окно без фокуса мы уже мерили (`postinput.py`,
29.08.2026): Roblox принимает клавиши и мышь, только пока считает себя
активным — из двадцати проб без фокуса дошла одна, и та оказалась выбросом.
Значит «тихого» ввода в фоне не будет, и вопрос не в способе доставки, а в том,
ЧЬЯ очередь ввода.

У каждого рабочего стола Windows (объект `Desktop` внутри оконной станции)
очередь своя. Процесс, запущенный на столе `brainbot`, шлёт `SendInput` в ЕГО
очередь: его окно там — активное, ввод доходит, а на твоём столе ничего не
дёргается. Ты работаешь, бот играет.

Что важно знать про этот путь:

* стол живёт, пока на нём есть процессы или открытый хэндл. Поэтому `up`
  держит хэндл и не завершается — закроешь консоль, стол исчезнет вместе с
  игрой;
* на новом столе НЕТ проводника: ни панели задач, ни рабочего стола, только те
  окна, что мы там запустили. Это нормально;
* переключение экрана — `SwitchDesktop`. Пока смотришь на стол бота, твоей
  консоли не видно, поэтому `show` возвращает экран обратно САМ по таймеру;
* окно нельзя перенести между столами. Клиент Roblox, поднятый на обычном
  столе, там и останется — игру надо запустить заново уже на столе бота.
"""
from __future__ import annotations

import subprocess
import time

import win32con
import win32process
import win32service

from .log import get

log = get("desktop")

NAME = "brainbot"

# Прав нужно много: создать стол, запускать на нём процессы, переключать экран.
DESKTOP_ACCESS = (win32con.DESKTOP_CREATEWINDOW | win32con.DESKTOP_CREATEMENU |
                  win32con.DESKTOP_HOOKCONTROL | win32con.DESKTOP_JOURNALRECORD |
                  win32con.DESKTOP_JOURNALPLAYBACK | win32con.DESKTOP_ENUMERATE |
                  win32con.DESKTOP_WRITEOBJECTS | win32con.DESKTOP_READOBJECTS |
                  win32con.DESKTOP_SWITCHDESKTOP)


def ensure(name: str = NAME):
    """Создать стол (или открыть уже существующий) и вернуть хэндл.

    Хэндл держать открытым, пока стол нужен: Windows сносит стол, у которого не
    осталось ни процессов, ни ссылок.
    """
    try:
        handle = win32service.OpenDesktop(name, 0, False, DESKTOP_ACCESS)
        log.info("стол %r уже есть", name)
        return handle
    except Exception:                                       # noqa: BLE001
        handle = win32service.CreateDesktop(name, 0, DESKTOP_ACCESS, None)
        log.info("стол %r создан", name)
        return handle


def spawn(command: str, name: str = NAME) -> int:
    """Запустить команду НА этом столе. Возвращает pid."""
    si = win32process.STARTUPINFO()
    si.lpDesktop = name
    handle, _thread, pid, _tid = win32process.CreateProcess(
        None, command, None, None, False,
        win32con.NORMAL_PRIORITY_CLASS, None, None, si)
    log.info("на столе %r запущено: %s (pid %s)", name, command, pid)
    return pid


def _switch(handle) -> None:
    """SwitchDesktop через ctypes: в этой сборке pywin32 его в win32service нет."""
    import ctypes
    if not ctypes.windll.user32.SwitchDesktop(int(handle)):
        raise ctypes.WinError()


def show(seconds: float, name: str = NAME) -> None:
    """Показать стол бота на экране и вернуться обратно по таймеру.

    Возврат обязательно по таймеру: пока экран на чужом столе, нашей консоли не
    видно и остановить нас нечем.
    """
    current = win32service.OpenInputDesktop(0, False, win32con.MAXIMUM_ALLOWED)
    target = ensure(name)
    log.info("переключаю экран на %r на %.0f с", name, seconds)
    _switch(target)
    try:
        time.sleep(seconds)
    finally:
        _switch(current)
        log.info("экран вернулся")


def processes(name: str = NAME) -> list[tuple[int, str]]:
    """Что сейчас крутится на столе бота: (pid, имя процесса)."""
    out = []
    try:
        raw = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
             "Get-Process | Where-Object { $_.SessionId -ge 0 } | "
             "Select-Object Id,ProcessName | ConvertTo-Csv -NoTypeInformation"],
            text=True, stderr=subprocess.DEVNULL, timeout=20)
    except Exception:                                       # noqa: BLE001
        return out
    for line in raw.splitlines()[1:]:
        parts = [p.strip('"') for p in line.split(",")]
        if len(parts) == 2 and parts[0].isdigit():
            out.append((int(parts[0]), parts[1]))
    return out
