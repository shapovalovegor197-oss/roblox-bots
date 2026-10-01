# -*- coding: utf-8 -*-
"""Один хозяин ввода на машину — и дверь закрыта, когда его нет.

Зачем. 02.09 работали два farm_loop разом: двадцать две минуты два бота дрались
за одного персонажа, дверь простояла открытой 930 секунд подряд, и у нас унесли
Glorbo Fruttodrillo. Замок в farm_loop это закрыл, но только между прогонами
фермы: любой тестовый скрипт (lock_refresh, rebirth_read, замеры) брал мышь
свободно, и база всё это время стояла открытой.

Правило пользователя от 03.09: «во время тестов не забывай закрывать дверь».
Отсюда две вещи в одном месте:

* `занять(имя)` — общий замок var/farm_loop.lock. Кто угодно, кто трогает ввод,
  сперва спрашивает разрешения. Живость pid проверяется через WinAPI: os.kill на
  Windows не спрашивает, а убивает;
* `запереть_базу(f)` — закрыть дверь перед тем, как отпустить персонажа.
  Вызывать в конце любого опыта, а не «когда вспомню».

Замок за собой не убираем: мёртвый pid и так считается свободным.
"""
import ctypes
import os
import sys

from .log import get

log = get("single")

def _имя_стола() -> str:
    """Имя рабочего стола Windows, на котором крутится этот процесс.

    Замок ввода — на очередь ввода, а не на машину. У каждого рабочего стола
    (`CreateDesktop`) очередь СВОЯ: на столе `brainbot-1` бот шлёт `SendInput`
    в его очередь и не мешает боту на `brainbot-2`. Машинный замок их зря
    сталкивал. Ключуем замок по столу — на видимом столе это «Default», и всё
    работает как раньше; на выделенных столах флота — по замку на каждый.
    """
    try:
        h = ctypes.windll.user32.GetThreadDesktop(
            ctypes.windll.kernel32.GetCurrentThreadId())
        buf = ctypes.create_unicode_buffer(256)
        need = ctypes.c_ulong()
        # UOI_NAME = 2
        if ctypes.windll.user32.GetUserObjectInformationW(
                h, 2, buf, ctypes.sizeof(buf), ctypes.byref(need)):
            name = buf.value.strip()
            if name:
                return "".join(c if c.isalnum() or c in "-_" else "_"
                               for c in name)
    except Exception:                                          # noqa: BLE001
        pass
    return "default"


def _lockfile() -> str:
    return "var/farm_loop.%s.lock" % _имя_стола()


def _имя_процесса(pid: int) -> str:
    """Имя исполняемого файла живого процесса. Пусто — процесса нет или не видно."""
    h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)   # QUERY_LIMITED
    if not h:
        return ""
    буфер = ctypes.create_unicode_buffer(512)
    размер = ctypes.c_ulong(len(буфер))
    ок = ctypes.windll.kernel32.QueryFullProcessImageNameW(h, 0, буфер, ctypes.byref(размер))
    ctypes.windll.kernel32.CloseHandle(h)
    return os.path.basename(буфер.value) if ок else ""


def _процесс_жив(pid: int) -> bool:
    h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)   # QUERY_LIMITED
    if not h:
        return False
    код = ctypes.c_ulong()
    ок = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(код))
    ctypes.windll.kernel32.CloseHandle(h)
    return bool(ок) and код.value == 259                          # STILL_ACTIVE


def _наш_же(pid: int, имя_файла: str) -> bool:
    """Тот ли это процесс, что брал замок, — или номер уже переиспользован.

    Номера процессов Windows переиспользует, и довольно быстро. В ночь на 16.09
    это стоило пяти сорванных прогонов подряд: замок держал pid 23752, а под
    этим номером жил уже `browser.exe` — наш питон давно умер. Проверка «процесс
    жив» отвечала «жив», и дверь оставалась запертой навсегда.
    """
    if not имя_файла:
        return _процесс_жив(pid)          # старый замок без имени: судим как раньше
    return _имя_процесса(pid).lower() == имя_файла.lower()


def занять(имя: str = "скрипт") -> None:
    """Взять ввод в единоличное владение (на ЭТОМ столе) или выйти."""
    lockfile = _lockfile()
    прежний, чей = 0, ""
    if os.path.exists(lockfile):
        try:
            with open(lockfile, encoding="utf-8") as fh:
                части = fh.read().strip().split(maxsplit=1)
            прежний = int(части[0] or 0)
            чей = части[1] if len(части) > 1 else ""
        except (ValueError, OSError, IndexError):
            прежний, чей = 0, ""
    if прежний and прежний != os.getpid() and _наш_же(прежний, чей):
        sys.exit("ввод уже занят (pid %d) на столе %s. Два хозяина на одном "
                 "персонаже оставляют базу открытой — останови прежний, потом "
                 "запускай %s." % (прежний, _имя_стола(), имя))
    # Пишем номер И имя процесса: по одному номеру своего от чужого не отличить.
    with open(lockfile, "w", encoding="utf-8") as fh:
        fh.write(f"{os.getpid()} {os.path.basename(sys.executable)}")


def запереть_базу(f, попыток: int = 2) -> int | None:
    """Закрыть дверь и сказать числом, на сколько. None — не вышло.

    Отдельной функцией потому, что это последнее действие ЛЮБОГО опыта: пока
    база открыта, у нас уносят брейнротов, и цена этому уже измерена в двух
    потерянных легендарных за один вечер.
    """
    осталось = f.lock_left_now()
    if осталось > 5:
        log.info("дверь уже закрыта, осталось %d с", осталось)
        return осталось
    взято = f.lock_with_retries(attempts=попыток)
    if взято:
        log.info("дверь закрыта на %d с", взято)
    else:
        log.warning("ДВЕРЬ ОСТАЛАСЬ ОТКРЫТОЙ — запереть не вышло")
    return взято
