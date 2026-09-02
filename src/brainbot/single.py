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

from . import log

LOCKFILE = "var/farm_loop.lock"


def _процесс_жив(pid: int) -> bool:
    h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)   # QUERY_LIMITED
    if not h:
        return False
    код = ctypes.c_ulong()
    ок = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(код))
    ctypes.windll.kernel32.CloseHandle(h)
    return bool(ок) and код.value == 259                          # STILL_ACTIVE


def занять(имя: str = "скрипт") -> None:
    """Взять ввод в единоличное владение или выйти с объяснением."""
    прежний = 0
    if os.path.exists(LOCKFILE):
        try:
            with open(LOCKFILE, encoding="utf-8") as fh:
                прежний = int(fh.read().strip() or 0)
        except (ValueError, OSError):
            прежний = 0
    if прежний and прежний != os.getpid() and _процесс_жив(прежний):
        sys.exit("ввод уже занят (pid %d). Два хозяина на одном персонаже "
                 "оставляют базу открытой — останови прежний, потом запускай %s."
                 % (прежний, имя))
    with open(LOCKFILE, "w", encoding="utf-8") as fh:
        fh.write(str(os.getpid()))


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
