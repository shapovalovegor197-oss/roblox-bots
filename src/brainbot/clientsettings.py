# -*- coding: utf-8 -*-
"""Оконный режим клиента — настройка Roblox, а не наша.

Размер окна это калибровка, а не косметика: зрение адресуется долями кадра,
снятыми при 1280x720, а половина координат MM2 лежит прямо в пикселях. Вернуть
окно к рабочему размеру мы умели (`scripts/fix_window.py`, `window_keeper.py`),
а вот ЗАПРЕТИТЬ клиенту открываться неправильным — нет: свой размер он хранит у
себя, в `%LOCALAPPDATA%/Roblox/GlobalBasicSettings_13.xml`, и применяет, когда
игра догрузится, то есть уже после того, как лаунчер выставил рабочий.

Замер 21.09.2026 20:10. Клиент стоял 2560x1440 @(0,0); в настройках лежало
`Fullscreen=true`, `StartMaximized=true`, `StartScreenSize=2560x1440`. Кадр при
этом снимается честно и выглядит правдоподобно — просто весь интерфейс вдвое
мельче своих долей: окно Disconnected заняло 400x270 из 2560x1440 там, где
должно занимать те же 400x270 из 1280x720. Снаружи это «игра стала маленьким
окошком», изнутри — ложные совпадения шаблонов (замер 11.09: 0.871 на кнопке
Rebirth, то есть клик в «потеряешь весь кэш»).

Полей три, и нужны все три:

* `Fullscreen` — полный экран;
* `StartMaximized` — развернуть на весь монитор; работает и при Fullscreen=false,
  поэтому одного сброса полного экрана мало;
* `StartScreenSize` — собственно размер.

FFlag `FFlagHandleAltEnterFullscreenManually=False` из `optimize.py` сюда не
относится: он про обработку Alt+Enter, а не про сохранённый выбор пользователя.

Файл клиент читает при старте и переписывает при выходе — поэтому пишем перед
КАЖДЫМ запуском, ровно как FFlags.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from .log import get

log = get("clientsettings")

ФАЙЛ = "GlobalBasicSettings_13.xml"


def путь() -> Path:
    """Где лежат настройки клиента."""
    return Path(os.environ["LOCALAPPDATA"]) / "Roblox" / ФАЙЛ


def _булево(текст: str, имя: str) -> str | None:
    м = re.search(r'<bool name="%s">(.*?)</bool>' % имя, текст)
    return м.group(1) if м else None


def _вектор(текст: str, имя: str) -> tuple[str, str] | None:
    м = re.search(r'<Vector2 name="%s">\s*<X>(.*?)</X>\s*<Y>(.*?)</Y>' % имя, текст)
    return (м.group(1), м.group(2)) if м else None


def прочитать(path: Path | None = None) -> dict:
    """Что клиент запомнил про своё окно. Пустой словарь — файла нет."""
    p = path or путь()
    if not p.exists():
        return {}
    текст = p.read_text(encoding="utf-8", errors="ignore")
    размер = _вектор(текст, "StartScreenSize")
    return {
        "fullscreen": _булево(текст, "Fullscreen"),
        "maximized": _булево(текст, "StartMaximized"),
        "size": размер,
    }


def оконный_режим(width: int, height: int, path: Path | None = None) -> dict[str, str]:
    """Запретить клиенту открываться в полный экран. Что поменяли — то и вернём.

    Ничего не трогает, если и так всё правильно: файл общий с живым клиентом,
    лишняя запись тут никому не нужна. Правку живой клиент переживёт, но при
    выходе перепишет файл под себя — поэтому это не «настроил и забыл», а шаг
    перед запуском.
    """
    p = path or путь()
    if not p.exists():
        log.warning("настроек клиента нет: %s", p)
        return {}

    текст = p.read_text(encoding="utf-8", errors="ignore")
    было = текст
    менялось: dict[str, str] = {}

    for имя in ("Fullscreen", "StartMaximized"):
        текущее = _булево(текст, имя)
        if текущее is None or текущее == "false":
            continue
        текст = re.sub(r'(<bool name="%s">).*?(</bool>)' % имя,
                       r"\g<1>false\g<2>", текст, count=1)
        менялось[имя] = "%s -> false" % текущее

    размер = _вектор(текст, "StartScreenSize")
    if размер and размер != (str(width), str(height)):
        текст = re.sub(r'(<Vector2 name="StartScreenSize">\s*<X>).*?(</X>\s*<Y>).*?(</Y>)',
                       r"\g<1>%d\g<2>%d\g<3>" % (width, height), текст, count=1)
        менялось["StartScreenSize"] = "%sx%s -> %dx%d" % (*размер, width, height)

    if текст != было:
        p.write_text(текст, encoding="utf-8")
        log.info("настройки клиента поправлены: %s",
                 ", ".join("%s: %s" % кв for кв in менялось.items()))
    return менялось
