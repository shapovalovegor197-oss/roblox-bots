# -*- coding: utf-8 -*-
"""Поднять клиент одного аккаунта НА СВОЁМ столе и доложить, где он оказался.

Бежит уже на выделенном столе (его запускает `desk_login.py` через
`desktop.spawn`). Тем и отличается от запуска снаружи: дочерний процесс
наследует стол родителя, поэтому клиент открывается здесь же, а `EnumWindows`
в `wait_for_window` видит именно наше окно и ничьё больше.

Фермы тут нет намеренно — только вход в игру. Это ступенька: сперва убедиться,
что два клиента живут на двух столах одновременно, и лишь потом отдавать их
ферме.

Аргументы: <аккаунт> [место] [сколько_ждать_окно]
"""
import sys
import time

sys.path.insert(0, "src")

from brainbot import config, desktop, launcher, log        # noqa: E402
from brainbot.mutex import SingletonMutex                  # noqa: E402
from brainbot.window import wait_for_window                # noqa: E402

acc = sys.argv[1]
place = int(sys.argv[2]) if len(sys.argv) > 2 else 0
ждать = float(sys.argv[3]) if len(sys.argv) > 3 else 180.0

s = config.load()
log.setup(s.logs_dir)
place = place or s.place_id

мой_стол = desktop.current()
print("[%s] стол %r" % (acc, мой_стол), flush=True)

# Свой хэндл мьютекса в каждом процессе: оркестратор держит свой, но клиент
# переживает оркестратора, и пока жив хоть один хэндл, Roblox терпит соседей.
mutex = SingletonMutex()
mutex.acquire()

account = s.account(acc)
if not account.cookie:
    sys.exit("[%s] пустая кука" % acc)

pid = launcher.launch(account, place)
print("[%s] клиент pid=%s, place=%s" % (acc, pid, place), flush=True)

win = wait_for_window(pid, timeout=ждать)
if not win:
    столы = desktop.where(pid)
    if столы:
        print("[%s] ПРОВАЛ: клиент уехал на стол %s, а не остался на %r"
              % (acc, ", ".join(столы), мой_стол), flush=True)
    else:
        роблоксы = [(d, о) for d, о in desktop.layout(match="roblox") if о]
        print("[%s] ПРОВАЛ: окна нет нигде. Клиенты Roblox: %s"
              % (acc, "; ".join("%s: %d" % (d, len(о)) for d, о in роблоксы) or "нет"),
              flush=True)
    sys.exit(2)

# Размер клиентской области — часть калибровки зрения, а не косметика:
# сжатое окно молча сдвигает все доли кадра и врёт в пеленгах.
ш = int(s.window.get("width", 1280))
в = int(s.window.get("height", 720))
win.move_resize(0, 0, ш, в)
time.sleep(1.0)
рамка = win.client_box()
print("[%s] ОКНО hwnd=%s на столе %r, клиентская область %sx%s"
      % (acc, win.hwnd, мой_стол, рамка.width, рамка.height), flush=True)
print("[%s] ГОТОВ" % acc, flush=True)

# Держимся, пока жив клиент: так стол не останется без хозяина в момент, когда
# клиент перезапускается, и видно в логе, когда он умер.
пока_жив = 0
while win.alive():
    time.sleep(10)
    пока_жив += 10
    if пока_жив % 60 == 0:
        print("[%s] жив %d мин" % (acc, пока_жив // 60), flush=True)
print("[%s] окно закрылось, выхожу" % acc, flush=True)
