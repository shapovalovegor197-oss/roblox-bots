# -*- coding: utf-8 -*-
"""Поднять несколько клиентов, каждый на своём рабочем столе.

Что здесь важно и почему именно так:

* **мьютекс держит этот процесс** всё время. Roblox гасит лишние клиенты,
  пока `ROBLOX_singletonMutex` не занят кем-то ещё; отпустишь — и соседи
  начнут убивать друг друга;
* **стол держит модуль** `desktop` (см. `_HELD`): хэндл, отпущенный сразу
  после `ensure`, разрушает стол под запускаемым процессом, и тогда клиент
  уезжает на видимый стол — ровно та поломка, из-за которой «все аккаунты
  запускались на первом»;
* **клиент поднимает не этот процесс, а `desk_client.py` на своём столе**:
  дочерний процесс наследует стол родителя, значит запускать надо оттуда.

Запуск:  python scripts/desk_login.py raven,mm2-1
"""
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")

from brainbot import desktop, log                          # noqa: E402
from brainbot.mutex import SingletonMutex                  # noqa: E402

корень = Path(__file__).resolve().parents[1]
имена = [n.strip() for n in (sys.argv[1] if len(sys.argv) > 1 else "").split(",") if n.strip()]
if not имена:
    sys.exit("кого поднимать? пример: python scripts/desk_login.py raven,mm2-1")
пауза = float(sys.argv[2]) if len(sys.argv) > 2 else 20.0

log.setup(корень / "var" / "logs")

mutex = SingletonMutex()
mutex.acquire()
print("мьютекс мультиинстанса захвачен этим процессом — не закрывать", flush=True)

столы = {}
for имя in имена:
    стол = "brainbot-%s" % имя
    desktop.ensure(стол)
    отчёт = корень / "var" / ("desk_login_%s.txt" % имя)
    if отчёт.exists():
        отчёт.unlink()
    внутри = ('cd /d "%s" && set PYTHONIOENCODING=utf-8 && "%s" "%s" %s > "%s" 2>&1'
              % (корень, sys.executable, корень / "scripts" / "desk_client.py", имя, отчёт))
    desktop.spawn("cmd /c %s" % внутри, name=стол)
    столы[имя] = (стол, отчёт)
    print("[%s] стол %s поднят, запускаю клиент (отчёт %s)" % (имя, стол, отчёт.name), flush=True)
    time.sleep(пауза)          # враз два клиента Roblox не любит

# Ждём, пока каждый доложит исход
крайний = time.time() + 300
готовы = {}
while time.time() < крайний and len(готовы) < len(столы):
    time.sleep(5)
    for имя, (стол, отчёт) in столы.items():
        if имя in готовы or not отчёт.exists():
            continue
        текст = отчёт.read_text(encoding="utf-8", errors="replace")
        if "ГОТОВ" in текст:
            готовы[имя] = "ок"
        elif "ПРОВАЛ" in текст or "Traceback" in текст:
            готовы[имя] = "провал"

print("\n=== ИТОГ ===", flush=True)
for имя, (стол, отчёт) in столы.items():
    исход = готовы.get(имя, "не дождался")
    print("[%s] %s" % (имя, исход), flush=True)
    if отчёт.exists():
        for строка in отчёт.read_text(encoding="utf-8", errors="replace").splitlines():
            if строка.strip():
                print("    " + строка.strip(), flush=True)

print("\n=== РАСКЛАДКА СТОЛОВ ===", flush=True)
for стол, окна in desktop.layout(match="roblox"):
    if окна:
        print("%s: %d окон" % (стол, len(окна)), flush=True)
        for hwnd, pid, что in окна:
            print("    hwnd=%s pid=%s %s" % (hwnd, pid, что), flush=True)

живых = subprocess.run(["tasklist", "/FI", "IMAGENAME eq RobloxPlayerBeta.exe"],
                       capture_output=True, text=True, errors="replace").stdout
print("\nпроцессов RobloxPlayerBeta: %d"
      % живых.lower().count("robloxplayerbeta.exe"), flush=True)
print("\nДержу мьютекс. Ctrl+C — отпустить (клиенты и столы останутся жить).", flush=True)
try:
    while True:
        time.sleep(30)
except KeyboardInterrupt:
    mutex.release()
    print("мьютекс отпущен")
