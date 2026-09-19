# -*- coding: utf-8 -*-
"""Два аккаунта MM2, каждый на своём столе, экран переключается между ними.

Схема, которую проверяем: столы работают ПО ОЧЕРЕДИ. Стол, который сейчас
показан, — единственный, где у бота есть зрение (замер 19.09: на показанном
столе кадр 1280x720 берётся, через полторы секунды после возврата экрана —
BitBlt отказывает). Значит клиент и поднимать надо при показанном столе, и
работать им, пока стол на экране.

Чего ждём от прогона и что здесь меряется:

* поднимется ли второй клиент, не убив первого (три прошлых замера говорят,
  что убьёт, и мьютекс не спасает) — это главный вопрос;
* берётся ли кадр у того стола, который сейчас показан.

Экран уходит на каждый стол по очереди и возвращается САМ. Пока он там, мышь и
клавиатуру трогать нельзя — ввод уйдёт в игру.

Запуск:  python scripts/two_desks_mm2.py [задержка] [показ]
"""
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, "src")

from brainbot import desktop, log                           # noqa: E402

MM2 = 142823291
корень = Path(__file__).resolve().parents[1]
задержка = float(sys.argv[1]) if len(sys.argv) > 1 else 25.0
показ = float(sys.argv[2]) if len(sys.argv) > 2 else 85.0

log.setup(корень / "var" / "logs")


def клиентов() -> int:
    вывод = subprocess.run(["tasklist", "/FI", "IMAGENAME eq RobloxPlayerBeta.exe"],
                           capture_output=True, text=True, errors="replace").stdout
    return вывод.lower().count("robloxplayerbeta.exe")


def поднять_на_столе(acc: str, стол: str) -> Path:
    """Показать стол и на нём поднять клиент — именно в таком порядке."""
    отчёт = корень / "var" / ("mm2_desk_%s.txt" % acc)
    desktop.ensure(стол)
    внутри = ('cd /d "%s" && set PYTHONIOENCODING=utf-8 && "%s" "%s" %s 600 %d > "%s" 2>&1'
              % (корень, sys.executable,
                 корень / "scripts" / "desk_switch_probe.py", acc, MM2, отчёт))
    поток = threading.Thread(target=desktop.show, args=(показ, стол), daemon=True)
    поток.start()
    time.sleep(2.0)
    desktop.spawn("cmd /c %s" % внутри, name=стол)
    print("[%s] стол %s показан, клиент поднимается" % (acc, стол), flush=True)
    поток.join()
    print("[%s] экран вернулся; клиентов Roblox сейчас: %d"
          % (acc, клиентов()), flush=True)
    return отчёт


print("клиентов Roblox до начала: %d" % клиентов(), flush=True)
print("через %.0f с экран начнёт уходить на столы ботов, по %.0f с на каждый — "
      "руки от мыши" % (задержка, показ), flush=True)
time.sleep(задержка)

первый = поднять_на_столе("raven", "brainbot-raven")
время_между = 10
print("пауза %d с, затем поднимаем второй аккаунт" % время_между, flush=True)
time.sleep(время_между)
второй = поднять_на_столе("mm2-1", "brainbot-mm2-1")

time.sleep(5)
print("\n=== ИТОГ ===", flush=True)
print("клиентов Roblox живо: %d (надо 2)" % клиентов(), flush=True)
for имя, путь in (("raven", первый), ("mm2-1", второй)):
    print("\n--- %s ---" % имя, flush=True)
    if путь.exists():
        строки = [s for s in путь.read_text(encoding="utf-8", errors="replace").splitlines()
                  if s.strip() and "WARNING" not in s]
        for s in строки[:4]:
            print("   ", s, flush=True)
        print("    ...", flush=True)
        for s in строки[-4:]:
            print("   ", s, flush=True)
    else:
        print("    отчёта нет", flush=True)

print("\nраскладка столов:", flush=True)
for стол, окна in desktop.layout(match="RobloxPlayerBeta"):
    if окна:
        пиды = sorted({p for _h, p, _t in окна})
        print("   %-20s pid %s" % (стол, пиды), flush=True)
