# -*- coding: utf-8 -*-
"""Один склад на ОТДЕЛЬНОМ рабочем столе: поднять клиент и гонять ферму.

Запускается оркестратором `run.py fleet` через `desktop.spawn(...)` — то есть
уже НА своём столе. Это важно: `EnumWindows` видит только окна текущего стола,
поэтому и клиент, и ферму поднимает один процесс здесь, а не снаружи. У каждого
стола своя очередь ввода (см. `single._имя_стола`), значит фермы на разных
столах не дерутся за курсор — это и есть параллельные сессии на одном ПК.

Аргументы: <аккаунт> <минут> <мин_доход> <ребёрнов> <тихо:0|1>
"""
import runpy
import sys
import time

sys.path.insert(0, "src")

from brainbot import config, desktop, launcher, log   # noqa: E402
from brainbot.mutex import SingletonMutex             # noqa: E402
from brainbot.window import wait_for_window           # noqa: E402

acc = sys.argv[1]
minutes = sys.argv[2] if len(sys.argv) > 2 else "400"
income = sys.argv[3] if len(sys.argv) > 3 else "100"
rebirths = sys.argv[4] if len(sys.argv) > 4 else "40"
quiet = (sys.argv[5] if len(sys.argv) > 5 else "1") != "0"

s = config.load()
log.setup(s.logs_dir)

# Мьютекс держим и здесь: пока жив хоть один хэндл, Roblox пускает второй
# клиент. Оркестратор держит свой, но подстрахуемся — хэндл в этом процессе.
_mutex = SingletonMutex()
_mutex.acquire()

мой_стол = desktop.current()
print("[%s] я на столе %r" % (acc, мой_стол), flush=True)
if мой_стол.lower() == "default":
    # Значит нас запустили мимо стола: работать можно, но это не флот, а
    # второй бот на видимом столе — он будет драться с человеком за курсор.
    print("[%s] ВНИМАНИЕ: воркер оказался на видимом столе, а не на своём"
          % acc, flush=True)

account = s.account(acc)
if not account.cookie:
    sys.exit("[%s] пустая кука — вставь .ROBLOSECURITY в accounts.json" % acc)

# Склад — в самый пустой сервер: воровство главный потолок фермы.
job = launcher.quietest_job_id(s.place_id) if quiet else None
pid = launcher.launch(account, s.place_id, job_id=job)
print("[%s] клиент pid=%s, сервер=%s" % (acc, pid, job or "любой"), flush=True)

win = wait_for_window(pid, timeout=150)
if not win:
    # `wait_for_window` зовёт EnumWindows, а тот показывает только СВОЙ стол.
    # Поэтому «окно не появилось» здесь значит «его нет на моём столе» — и
    # раньше на этом всё обрывалось. Спросим про остальные столы и скажем
    # прямо, куда уехал клиент: чаще всего его перехватывает уже запущенный
    # экземпляр Roblox на видимом столе.
    чужие = desktop.where(pid)
    if чужие:
        sys.exit("[%s] клиент pid=%s поднялся НЕ на моём столе %r, а на %s — "
                 "флот так не работает" % (acc, pid, мой_стол, ", ".join(чужие)))
    роблоксы = [(d, окна) for d, окна in desktop.layout(match="roblox") if окна]
    if роблоксы:
        куда = "; ".join("%s: %d окон" % (d, len(о)) for d, о in роблоксы)
        sys.exit("[%s] моего окна нет, но клиенты Roblox есть тут: %s. Похоже, "
                 "запуск перехватил уже работающий клиент" % (acc, куда))
    sys.exit("[%s] окно не появилось за 150 с ни на одном столе" % acc)
print("[%s] окно hwnd=%s — отдаю ферме" % (acc, win.hwnd), flush=True)
time.sleep(3)

# farm_loop сам приведёт окно к 1280x720, возьмёт единственное окно этого стола
# и запишет var/farm_status.json. Замок ввода у него теперь пер-стол.
sys.argv = ["farm_loop.py", str(minutes), str(income), str(rebirths)]
runpy.run_path("scripts/farm_loop.py", run_name="__main__")
