# -*- coding: utf-8 -*-
"""Замер: доходит ли процесс до НАЗВАННОГО стола и видно ли там его окно.

Зачем отдельно от игры. Симптом «все аккаунты запускались на первом столе»
имеет ровно две разные причины, и лечатся они по-разному:

* стол не наследуется дочерним процессом — тогда ломается любой запуск, и это
  чинится в нашем коде (явный `lpDesktop` при создании процесса);
* стол наследуется, но именно клиент Roblox уезжает на видимый стол — тогда
  дело в самом клиенте (переадресация URI уже запущенному экземпляру), и наш
  код тут бессилен.

Проверять это клиентом дорого: он тянет куку, сервер и минуты ожидания. Здесь
та же цепочка «родитель → cmd → питон → окно», но вместо игры `notepad`, и
весь замер занимает секунды. Окна открываются на невидимом столе, ввод не
трогается, на рабочем экране не мелькает ничего.

Запуск (с обычного, видимого стола):  python scripts/desk_probe.py
"""
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")

from brainbot import desktop                               # noqa: E402

DESK = "brainbot-probe"
OUT = Path("var") / "desk_probe.txt"


def ребёнок() -> None:
    """Эта половина бежит уже НА столе замера."""
    сюда = desktop.current()
    print("воркер: стол %r, pid %d" % (сюда, os.getpid()), flush=True)

    # Дочерний процесс без всяких ухищрений — наследует ли он стол?
    внук = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, 'src');"
         " from brainbot import desktop; print(desktop.current())"],
        capture_output=True, text=True, timeout=60)
    print("внук через Popen: стол %r" % внук.stdout.strip(), flush=True)

    # Окно. Проверяем не «запустилось ли», а НА КАКОМ СТОЛЕ оно оказалось.
    #
    # Окно рисуем сами через PowerShell, а не берём готовое приложение: у
    # Notepad в Windows 11 один экземпляр на пользователя, и второй запуск
    # отдаёт работу первому — окно появляется там, где живёт ПЕРВЫЙ, и замер
    # меряет не наследование стола, а чужую однооконность.
    окно = subprocess.Popen([
        "powershell", "-NoProfile", "-Command",
        "Add-Type -AssemblyName System.Windows.Forms;"
        " $f = New-Object System.Windows.Forms.Form;"
        " $f.Text = 'DESK-TEST'; $f.Show(); Start-Sleep -Seconds 12"])
    окно_нашлось = False
    for _ in range(24):
        time.sleep(0.5)
        столы = desktop.where(окно.pid)
        if столы:
            print("моё окно (pid %d) на столах: %s"
                  % (окно.pid, ", ".join(столы)), flush=True)
            окно_нашлось = True
            break
    if not окно_нашлось:
        print("моё окно (pid %d) не нашлось НИ НА ОДНОМ столе"
              % окно.pid, flush=True)

    свои = desktop.windows(сюда)
    print("окон на своём столе %r: %d" % (сюда, len(свои)), flush=True)
    for hwnd, pid, title in свои:
        print("   hwnd=%s pid=%s %s" % (hwnd, pid, title), flush=True)

    окно.terminate()
    print("ГОТОВО", flush=True)


def родитель() -> None:
    корень = Path(__file__).resolve().parents[1]
    os.chdir(корень)
    OUT.parent.mkdir(exist_ok=True)
    if OUT.exists():
        OUT.unlink()

    print("мой стол: %r" % desktop.current())
    desktop.ensure(DESK)
    внутри = ('cd /d "%s" && set PYTHONIOENCODING=utf-8 && "%s" "%s" --child > "%s" 2>&1'
              % (корень, sys.executable, Path(__file__).resolve(), OUT))
    pid = desktop.spawn("cmd /c %s" % внутри, name=DESK)
    print("запустил воркер на столе %r (cmd pid %s), жду..." % (DESK, pid))

    for _ in range(60):
        time.sleep(1)
        if OUT.exists() and "ГОТОВО" in OUT.read_text(encoding="utf-8", errors="replace"):
            break

    print("\n--- что сказал воркер (%s) ---" % OUT)
    print(OUT.read_text(encoding="utf-8", errors="replace")
          if OUT.exists() else "(воркер не написал ничего)")

    print("--- раскладка столов (все окна) ---")
    for стол, окна in desktop.layout(match=""):
        print("%-20s %d окон" % (стол, len(окна)))


if __name__ == "__main__":
    if "--child" in sys.argv:
        ребёнок()
    else:
        родитель()
