# -*- coding: utf-8 -*-
"""Прогон схемы «столы по очереди»: показать стол, поднять на нём клиент, убрать.

Порядок ровно такой и никакой другой: клиент должен стартовать при ПОКАЗАННОМ
столе, иначе он не начинает рисовать, и замер меряет не схему, а наш прежний
промах.

Экран уходит на стол бота на время подъёма клиента и возвращается САМ по
таймеру — пока он там, человеку мышь и клавиатуру трогать нельзя: ввод уйдёт
в игру.
"""
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")

from brainbot import desktop, log                           # noqa: E402

СТОЛ = "brainbot-switch"
корень = Path(__file__).resolve().parents[1]
acc = sys.argv[1] if len(sys.argv) > 1 else "raven"
задержка = float(sys.argv[2]) if len(sys.argv) > 2 else 25.0
показ = float(sys.argv[3]) if len(sys.argv) > 3 else 80.0

log.setup(корень / "var" / "logs")
отчёт = корень / "var" / "desk_switch.txt"

# Ни одного клиента до начала: второй убивает первого, и замер станет мусором.
живые = subprocess.run(["tasklist", "/FI", "IMAGENAME eq RobloxPlayerBeta.exe"],
                       capture_output=True, text=True, errors="replace").stdout
print("клиентов Roblox до начала: %d"
      % живые.lower().count("robloxplayerbeta.exe"), flush=True)

desktop.ensure(СТОЛ)
внутри = ('cd /d "%s" && set PYTHONIOENCODING=utf-8 && "%s" "%s" %s 200 > "%s" 2>&1'
          % (корень, sys.executable, корень / "scripts" / "desk_switch_probe.py",
             acc, отчёт))

print("через %.0f с экран уйдёт на стол %s примерно на %.0f с — руки от мыши"
      % (задержка, СТОЛ, показ), flush=True)
time.sleep(задержка)

# Сперва показываем стол, потом на нём запускаем клиент: показ идёт в отдельном
# потоке, потому что desktop.show держит экран у себя и возвращает его сам.
import threading                                            # noqa: E402

поток = threading.Thread(target=desktop.show, args=(показ, СТОЛ), daemon=True)
поток.start()
time.sleep(2.0)
desktop.spawn("cmd /c %s" % внутри, name=СТОЛ)
print("наблюдатель запущен на столе, клиент поднимается при показанном столе",
      flush=True)

поток.join()
print("экран вернулся, стол ушёл в фон — дальше мерим слепую фазу", flush=True)
time.sleep(60)

print("\n=== отчёт ===", flush=True)
print(отчёт.read_text(encoding="utf-8", errors="replace") if отчёт.exists()
      else "(наблюдатель ничего не написал)", flush=True)
