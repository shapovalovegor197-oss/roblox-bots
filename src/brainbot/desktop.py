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


# Открытые нами хэндлы столов. Держим их ЗДЕСЬ, а не на совести вызывающего:
# стол живёт, пока на него есть хэндл или процессы, и `ensure(...)` без
# присвоения результата (`desktop.ensure(desk)` одной строкой) отдавал хэндл
# сборщику мусора в ту же секунду. Стол при этом разрушается под только что
# запущенным процессом, и выглядит это дико: процесс стартует, но любая DLL,
# которой нужен USER32, не инициализируется — питон падает на `import ctypes`,
# а клиент Roblox уезжает на видимый стол. Замер 19.09.2026: четыре пробы на
# свежих столах, с удержанным хэндлом 2 из 2 грузятся, без него 0 из 2.
_HELD: dict[str, object] = {}


def ensure(name: str = NAME):
    """Создать стол (или открыть уже существующий) и держать его живым.

    Хэндл остаётся у модуля до конца процесса или до `release(name)` — именно
    поэтому вызов можно писать одной строкой, не заботясь о возвращённом
    значении.
    """
    held = _HELD.get(name)
    if held is not None:
        return held
    try:
        handle = win32service.OpenDesktop(name, 0, False, DESKTOP_ACCESS)
        log.info("стол %r уже есть", name)
    except Exception:                                       # noqa: BLE001
        handle = win32service.CreateDesktop(name, 0, DESKTOP_ACCESS, None)
        log.info("стол %r создан", name)
    _HELD[name] = handle
    return handle


def release(name: str = NAME) -> None:
    """Отпустить свой хэндл стола. Стол уйдёт, когда на нём кончатся процессы."""
    handle = _HELD.pop(name, None)
    if handle is not None:
        # У PyHDESK метод зовётся CloseDesktop (не Close): в win32service
        # свободной функции нет вовсе, а у объекта метод есть.
        handle.CloseDesktop()
        log.info("хэндл стола %r отпущен", name)


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


# --------------------------------------------------------------------------
# Кто на каком столе. Без этого флот падает молча
# --------------------------------------------------------------------------
#
# `EnumWindows` перечисляет окна ТОЛЬКО своего стола. Поэтому воркер, поднявший
# клиент, который почему-то открылся на чужом столе, видит пустоту и говорит
# «окно не появилось за 150 с» — а окно есть, просто не здесь. Ровно так флот и
# выглядел снаружи: аккаунты запускались, все окна оказывались на первом столе.
#
# `EnumDesktopWindows` берёт хэндл стола и отвечает про НЕГО, поэтому им можно
# спросить про любой стол и получить факт, а не догадку.

_ENUM_ACCESS = win32con.DESKTOP_ENUMERATE | win32con.DESKTOP_READOBJECTS


def current() -> str:
    """Имя стола, на котором крутится ЭТОТ процесс."""
    import ctypes
    try:
        h = ctypes.windll.user32.GetThreadDesktop(
            ctypes.windll.kernel32.GetCurrentThreadId())
        buf = ctypes.create_unicode_buffer(256)
        need = ctypes.c_ulong()
        if ctypes.windll.user32.GetUserObjectInformationW(   # UOI_NAME = 2
                h, 2, buf, ctypes.sizeof(buf), ctypes.byref(need)):
            return buf.value.strip() or "Default"
    except Exception:                                       # noqa: BLE001
        pass
    return "Default"


def desks() -> list[str]:
    """Все столы нашей оконной станции — и наши, и системные."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.LPWSTR, wintypes.LPARAM)
    found: list[str] = []

    def callback(name, _lparam):
        if name:
            found.append(name)
        return True

    hwinsta = user32.GetProcessWindowStation()
    user32.EnumDesktopsW(hwinsta, proc_type(callback), 0)
    return found


def windows(name: str = NAME, only_visible: bool = True) -> list[tuple[int, int, str]]:
    """Окна, которые живут на названном столе: (hwnd, pid, заголовок).

    Спрашивать можно про любой стол, в том числе не свой: в этом вся польза.
    Пустой список у существующего стола — это «там правда нет окон», а не
    «отсюда не видно».
    """
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    out: list[tuple[int, int, str]] = []

    # Через ctypes, а не pywin32: в этой сборке в win32service нет ни
    # CloseDesktop, ни SwitchDesktop, и хэндл пришлось бы оставлять на совести
    # сборщика мусора — а стол живёт, пока открыт хоть один хэндл.
    hdesk = user32.OpenDesktopW(name, 0, False, _ENUM_ACCESS)
    if not hdesk:
        log.debug("стола %r нет (или нет доступа)", name)
        return out

    def callback(hwnd, _lparam):
        if only_visible and not user32.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, buf, len(buf))
        title = buf.value.strip()
        if only_visible and not title:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        out.append((int(hwnd), int(pid.value), title))
        return True

    try:
        user32.EnumDesktopWindows(hdesk, proc_type(callback), 0)
    finally:
        user32.CloseDesktop(hdesk)
    return out


def where(pid: int) -> list[str]:
    """На каких столах есть окна этого процесса. Пусто — окон нет нигде."""
    return [d for d in desks() if any(p == pid for _hwnd, p, _t in windows(d))]


def layout(match: str = "roblox",
           only_visible: bool = False) -> list[tuple[str, list[tuple[int, int, str]]]]:
    """Раскладка «стол → его окна», для глаз и для протокола замера.

    `match` фильтрует по имени процесса окна, чтобы в выводе не тонуть: пустая
    строка — показать все окна.

    `only_visible=False` по умолчанию намеренно: клиент Roblox, пока грузится,
    держит окна без заголовка и без флага видимости. Отфильтровать их — значит
    ответить «клиентов нет ни на одном столе» ровно в ту минуту, когда вопрос
    «куда он поехал» и задают.
    """
    import subprocess as _sp
    names: dict[int, str] = {}
    try:
        raw = _sp.check_output(
            ["powershell", "-NoProfile", "-Command",
             "Get-Process | Select-Object Id,ProcessName | ConvertTo-Csv -NoTypeInformation"],
            text=True, stderr=_sp.DEVNULL, timeout=20)
        for line in raw.splitlines()[1:]:
            parts = [p.strip('"') for p in line.split(",")]
            if len(parts) == 2 and parts[0].isdigit():
                names[int(parts[0])] = parts[1]
    except Exception:                                       # noqa: BLE001
        pass

    out = []
    for desk in desks():
        rows = []
        for hwnd, pid, title in windows(desk, only_visible=only_visible):
            proc = names.get(pid, "?")
            if match and match.lower() not in proc.lower():
                continue
            rows.append((hwnd, pid, f"{proc}: {title or '(без заголовка)'}"))
        out.append((desk, rows))
    return out
