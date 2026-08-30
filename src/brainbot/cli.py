"""Командная строка. Всё, что нужно на этапе отладки, живёт здесь.

  python run.py doctor              проверить окружение
  python run.py windows             показать открытые окна Roblox
  python run.py shot --hwnd 12345   снять кадр окна
  python run.py cut var/screens/x.png   нарезать шаблоны из скрина
  python run.py find trade_button --hwnd 12345
  python run.py launch sklad-1
  python run.py up                  поднять супервизор
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import config, log, roblox_api
from .capture import grab, save
from .capture import grab as capture_grab
from .mutex import SingletonMutex
from .session import Session
from .vision import Templates, annotate
from .window import RobloxWindow, enum_roblox_windows


def _settings() -> config.Settings:
    s = config.load()
    log.setup(s.logs_dir)
    return s


def _pick_window(args, settings) -> RobloxWindow:
    windows = enum_roblox_windows()
    if not windows:
        sys.exit("Открытых окон Roblox нет. Запусти клиент или `python run.py launch <аккаунт>`")
    if args.hwnd:
        for w in windows:
            if w.hwnd == args.hwnd:
                return w
        sys.exit(f"Окно hwnd={args.hwnd} не найдено")
    if len(windows) > 1:
        print("Окон несколько, укажи --hwnd:")
        for w in windows:
            print(f"  {w.hwnd}  pid={w.pid}  {w.title}")
        sys.exit(1)
    return windows[0]


# --- команды ---

def cmd_doctor(args) -> None:
    s = _settings()
    ok = True

    print("=== окружение ===")
    print(f"python           {sys.version.split()[0]}")

    for mod in ("cv2", "numpy", "requests", "mss", "pydirectinput"):
        try:
            __import__(mod)
            print(f"{mod:16} ok")
        except ImportError:
            print(f"{mod:16} НЕТ — pip install -r requirements.txt")
            ok = False
    try:
        __import__("dxcam")
        print("dxcam            ok (быстрый захват)")
    except ImportError:
        print("dxcam            нет — будет mss, медленнее, но работает")

    print("\n=== Roblox ===")
    try:
        from .launcher import find_player_exe
        print(f"клиент           {find_player_exe()}")
    except Exception as e:  # noqa: BLE001
        print(f"клиент           НЕ НАЙДЕН: {e}")
        ok = False

    with SingletonMutex() as m:
        print(f"мьютекс          {'ok' if m.handle else 'НЕ ЗАХВАЧЕН'}")

    windows = enum_roblox_windows()
    print(f"открытых окон    {len(windows)}")
    for w in windows:
        box = w.client_box()
        print(f"  hwnd={w.hwnd} pid={w.pid} клиент={box.width}x{box.height}")

    print("\n=== конфиг ===")
    print(f"place_id         {s.place_id} ({s.game_name})")
    print(f"шаблонов         {len(Templates(s.template_dir).available())}")
    if not s.accounts:
        print("аккаунты         config/accounts.json нет — скопируй из accounts.example.json")
    for a in s.accounts:
        if not a.cookie:
            print(f"  {a.name:14} куки нет")
            continue
        user = roblox_api.whoami(a.cookie)
        if user:
            print(f"  {a.name:14} ok → {user.name} (id {user.id})")
        else:
            print(f"  {a.name:14} КУКА МЕРТВА — перелогинься и обнови")
            ok = False

    print("\n" + ("Всё на месте." if ok else "Есть проблемы — см. выше."))


def cmd_windows(args) -> None:
    _settings()
    windows = enum_roblox_windows()
    if not windows:
        print("Окон Roblox нет.")
        return
    for w in windows:
        box = w.client_box()
        print(f"hwnd={w.hwnd}  pid={w.pid}  клиент={box.width}x{box.height} "
              f"@({box.left},{box.top})  {w.title}")


def cmd_shot(args) -> None:
    import time
    s = _settings()
    win = _pick_window(args, s)
    if args.resize:
        win.move_resize(0, 0, s.window["width"], s.window["height"])
        time.sleep(0.5)

    if args.delay:
        # Даём время переключиться в игру и довести UI до нужного состояния.
        # Терминал при этом не должен перекрывать окно Roblox: захват берёт
        # пиксели с экрана, а не из процесса.
        win.focus()
        for left in range(args.delay, 0, -1):
            print(f"  снимаю через {left}...", end="\r", flush=True)
            time.sleep(1)
        print(" " * 30, end="\r")

    frame = grab(win.client_box(), hwnd=win.hwnd)
    path = save(frame, s.screenshots_dir, args.tag or f"hwnd{win.hwnd}")
    print(f"{path}  ({frame.shape[1]}x{frame.shape[0]})")


# Состояния, которые нужно отснять для сценария передачи. Порядок — как в игре.
SHOT_PLAN = [
    ("1_main", "обычный игровой экран, видна кнопка Trade рядом с Codes"),
    ("2_open", "окно трейда только открылось, видны вкладки"),
    ("3_search", "вкладка поиска, поле ввода ПУСТОЕ"),
    ("4_found", "ник введён, в списке появился результат"),
    ("5_joined", "вторая сторона вошла в трейд, слоты пустые, видна кнопка Ready"),
    ("6_ready", "предмет положен, наш Ready нажат, ждём вторую сторону"),
    ("7_confirm", "финальное окно подтверждения"),
    ("8_done", "трейд завершён"),
    ("9_base", "база с брейнротами — для чтения инвентаря"),
]


def cmd_shots(args) -> None:
    """Проводит по состояниям и снимает каждое под правильным именем."""
    import time
    s = _settings()
    win = _pick_window(args, s)

    w, h = s.window["width"], s.window["height"]
    win.move_resize(0, 0, w, h)
    time.sleep(0.5)
    box = win.client_box()
    print(f"Окно приведено к {box.width}x{box.height}.\n")
    if (box.width, box.height) != (w, h):
        print(f"  ВНИМАНИЕ: игра не дала точный размер, получилось {box.width}x{box.height}.")
        print("  Шаблоны будут привязаны к этому размеру — это нормально, но менять его\n"
              "  после съёмки нельзя.\n")

    plan = SHOT_PLAN if not args.only else [p for p in SHOT_PLAN if p[0] in args.only]
    print("Терминал может перекрывать окно игры — захват идёт по hwnd, это не мешает.\n")

    for tag, what in plan:
        print(f"[{tag}]  {what}")
        ans = input("      Enter — снять, s — пропустить, q — закончить: ").strip().lower()
        if ans == "q":
            break
        if ans == "s":
            print()
            continue
        for left in range(args.delay, 0, -1):
            print(f"      снимаю через {left}...", end="\r", flush=True)
            time.sleep(1)
        frame = capture_grab(win.client_box(), hwnd=win.hwnd)
        path = save(frame, s.screenshots_dir, tag)
        print(f"      → {path}                    \n")

    print(f"Готово. Файлы в {s.screenshots_dir}")


def cmd_cut(args) -> None:
    """Нарезка шаблонов из скрина: выделить рамку мышью, ввести имя, повторить."""
    import cv2
    s = _settings()
    img = cv2.imread(str(Path(args.image)))
    if img is None:
        sys.exit(f"Не читается: {args.image}")

    print("Выдели область мышью, Enter — принять, C — отмена. Пустое имя — выход.")
    while True:
        roi = cv2.selectROI("cut", img, showCrosshair=False)
        cv2.destroyAllWindows()
        x, y, w, h = (int(v) for v in roi)
        if w == 0 or h == 0:
            break
        name = input("имя шаблона (напр. trade_button): ").strip()
        if not name:
            break
        out = s.template_dir / f"{name}.png"
        cv2.imwrite(str(out), img[y:y + h, x:x + w])
        print(f"  → {out}  ({w}x{h}, позиция в кадре {x},{y})")


def cmd_templates(args) -> None:
    s = _settings()
    names = Templates(s.template_dir).available()
    if not names:
        print(f"Шаблонов нет. Положи PNG в {s.template_dir} или нарежь: python run.py cut <скрин>")
        return
    for n in names:
        print(n)


def cmd_find(args) -> None:
    import cv2
    s = _settings()
    win = _pick_window(args, s)
    # hwnd обязателен: без него берётся снимок ЭКРАНА, и если окно перекрыто
    # терминалом, в кадр попадут чужие пиксели, а шаблоны «не найдутся».
    frame = grab(win.client_box(), hwnd=win.hwnd)
    tpls = Templates(s.template_dir, s.vision["match_threshold"], s.vision.get("regions"))

    names = [args.template] if args.template else tpls.available()
    hits = []
    for name in names:
        m = tpls.find(name, frame, threshold=args.threshold)
        print(f"{name:26} {'найден  %.3f' % m.score if m else 'нет'}"
              + (f"  центр {m.center}" if m else ""))
        if m:
            hits.append(m)
    if hits:
        path = save(annotate(frame, hits), s.screenshots_dir, "found")
        print(f"\nразметка: {path}")


def cmd_launch(args) -> None:
    s = _settings()
    account = s.account(args.account)
    mutex = SingletonMutex()
    mutex.acquire()
    session = Session(account=account, settings=s)
    if session.launch():
        print(f"{account.name}: в игре, hwnd={session.window.hwnd}")
        print("Мьютекс отпускается вместе с процессом — для постоянной работы гоняй `up`.")
    else:
        sys.exit(f"{account.name}: не поднялся, см. лог")


def cmd_hold(args) -> None:
    """Держит мьютекс, пока не остановишь. Для ручного запуска второго клиента.

    Roblox не просто проверяет мьютекс при старте — он возвращается к этому
    и закрывает лишние окна через случайное время. Поэтому держать надо всё
    время, пока работают несколько клиентов, а не только в момент запуска.
    """
    import time
    _settings()
    mutex = SingletonMutex()
    if not mutex.acquire():
        sys.exit("Не удалось захватить мьютекс")
    print("Мьютекс захвачен. Теперь можно поднимать второй клиент вручную.")
    print("Окно не закрывать — пока держим, Roblox не будет прибивать лишние окна.")
    print("Ctrl+C — отпустить.\n")
    try:
        n = 0
        while True:
            time.sleep(5)
            n += 5
            print(f"  держу {n // 60} мин {n % 60} с", end="\r", flush=True)
    except KeyboardInterrupt:
        print("\nОтпускаю мьютекс.")
    finally:
        mutex.release()


def cmd_input_test(args) -> None:
    """Померить, доходит ли ввод до клиента. Отвечает числами, а не ощущениями."""
    from .inputs import Hand
    from .probe import Probe
    s = _settings()
    win = _pick_window(args, s)
    print("Окно будет захвачено в фокус, мышь и клавиатура — заняты на ~1 минуту.")
    print("Аварийный стоп: увести мышь в левый верхний угол экрана.")
    print()
    import time as _t

    import win32gui

    # Окно, с которого запускали (обычно терминал). Понадобится, чтобы увести
    # фокус с игры перед проходом без фокуса.
    launcher_hwnd = win32gui.GetForegroundWindow()

    backends = ["focus", "post"] if args.compare else [args.backend]
    reports, unfocused = {}, {}
    for backend in backends:
        if len(backends) > 1:
            print(f"\n{'=' * 62}\nканал доставки: {backend}\n{'=' * 62}")
        if backend == "post":
            # Без этого замер ничего не доказывает: окно остаётся активным после
            # прошлого прохода, а движок вполне может принимать сообщения только
            # пока считает себя в фокусе. Проверяем именно несфокусированное окно.
            try:
                win32gui.ShowWindow(launcher_hwnd, 9)     # SW_RESTORE
                win32gui.SetForegroundWindow(launcher_hwnd)
            except Exception as exc:                      # noqa: BLE001
                print(f"  не удалось увести фокус: {exc}")
            _t.sleep(0.8)
            fg = win32gui.GetForegroundWindow()
            unfocused[backend] = fg != win.hwnd
            print(f"  окно игры в фокусе: {'НЕТ — то, что надо' if fg != win.hwnd else 'ДА — замер недействителен'}")
        probe = Probe(window=win, hand=Hand(win, s.input, backend=backend),
                      screens_dir=s.screenshots_dir)
        probe.run(keep=args.keep)
        # Проверяем и ПОСЛЕ прохода: клик мог вернуть фокус игре по дороге.
        if backend == "post":
            still = win32gui.GetForegroundWindow() != win.hwnd
            unfocused[backend] = unfocused.get(backend, False) and still
            if not still:
                print("  ВНИМАНИЕ: к концу прохода игра снова оказалась в фокусе")
        print()
        print(probe.report())
        reports[backend] = probe

    if args.compare:
        # Ради этого сравнения всё и затевалось: если 'post' доходит, ввод
        # перестаёт быть общим ресурсом и клиенты смогут действовать разом.
        print(f"\n{'=' * 62}\nИТОГ")
        ok_focus = reports["focus"].delivered()
        ok_post = reports["post"].delivered()
        print(f"  через фокус доставлено проб: {ok_focus}")
        print(f"  без фокуса (PostMessage):    {ok_post}")
        if not unfocused.get("post"):
            print("  -> ВЫВОД НЕДЕЙСТВИТЕЛЕН: во время прохода 'post' окно игры "
                  "было активным, значит параллельность не проверена. Увести "
                  "фокус не удалось — попробуй запустить ещё раз, не трогая мышь.")
        elif ok_post >= max(1, ok_focus // 2):
            print("  -> PostMessage доходит. Можно вести несколько клиентов "
                  "одновременно: backend='post' в settings.json.")
        elif ok_post:
            print("  -> доходит частично. Годится гибрид: что дошло — без фокуса, "
                  "остальное по очереди.")
            print(f"     без фокуса прошли: {', '.join(reports['post'].passed())}")
        else:
            print("  -> не доходит. Клиент читает ввод мимо оконных сообщений; "
                  "параллельность придётся делать отдельными рабочими столами "
                  "или машинами.")
    if args.keep:
        print(f"\nкадры до/после: {s.screenshots_dir}")


def _farmer(args, s):
    """Фермер поверх уже открытого окна — для отладки без полного Session."""
    from .farm import Farmer, FarmTuning
    from .inputs import Hand
    win = _pick_window(args, s)
    nick = getattr(args, "nick", None) or s.raw.get("nick")
    if not nick and s.accounts:
        # ник нужен, чтобы найти свою строку в лидерборде — главном источнике
        # состояния (кэш, ребёрны). Спрашиваем Roblox по куке, это дёшево.
        user = roblox_api.whoami(s.accounts[0].cookie)
        nick = user.name if user else None
    return Farmer(window=win, hand=Hand(win, s.input), tuning=FarmTuning(),
                  screens_dir=s.screenshots_dir, nick=nick)


def cmd_op(args) -> None:
    """Одна операция из research/SCENARIOS.md, по имени."""
    import time as _time
    s = _settings()
    f = _farmer(args, s)

    rec = None
    if getattr(args, "record", False):
        from .recorder import Recorder
        out = s.screenshots_dir / f"op_{args.name}_{int(_time.time())}.mp4"
        rec = Recorder(f.window, out, fps=8).start()
        rec.note(f"операция {args.name}")
    try:
        _run_op(args, s, f, rec)
    finally:
        if rec:
            print(f"видео: {rec.stop()}")


def _run_op(args, s, f, rec=None) -> None:

    if args.name == "reference":
        ok = f.to_reference()
        print("опорное состояние взято" if ok else "не вышло, см. скрины и лог")
        side = {True: "справа", False: "слева", None: "НЕ ОПРЕДЕЛЕНА"}[f.plot_side_right]
        print(f"сторона базы: {side}")

    elif args.name == "collect":
        gain = f.collect_money()
        print(f"собрано: {gain:.0f}")

    elif args.name == "lock":
        sec = f.lock_base()
        print(f"база заперта на {sec} с" if sec else "подтверждения лока нет")

    elif args.name == "buy":
        targets = [x.strip() for x in args.want.split(",")] if args.want else None
        n = f.buy_at_conveyor(min_income=args.min_income, min_rarity=args.min_rarity,
                              seconds=args.seconds, max_price=args.max_price,
                              targets=targets)
        print(f"куплено: {n}")

    elif args.name == "rebirth":
        f.allow_wipe = args.allow_wipe
        print("ребёрн:", "сделан" if f.rebirth() else "рано, не подтвердился "
              "или остановлен ради содержимого базы — см. лог")

    elif args.name == "sell":
        sold = f.sell_weakest()
        print(f"продан: {sold}" if sold else "продавать нечего или не подтвердилось")

    elif args.name == "errand":
        targets = [x.strip() for x in args.want.split(",")] if args.want else None
        rep = f.errand(targets=targets, min_income=args.min_income)
        for k, v in rep.items():
            print(f"  {k}: {v}")

    elif args.name == "gear":
        print("снаряжение:", "куплено" if f.buy_gear(args.gear) else "не вышло")

    elif args.name == "turn":
        n = f.nav.calibrate_turn()
        if n:
            print(f"полный оборот: {n} единиц мыши; 90° = {n // 4}, 180° = {n // 2}")
        else:
            print("поймать полный оборот не вышло, см. лог")

    elif args.name == "mouse":
        k = f.nav.calibrate_mouse()
        print(f"чувствительность мыши: {k:.3f} px на единицу" if k
              else "измерить не вышло, см. лог")

    elif args.name == "aim":
        route = f.aim_belt()
        if route:
            f.save_belt_route(route)
            print(f"дорога найдена: поворот {route['поворот']}, "
                  f"вперёд {route['вперёд']:.1f} с — записана в var/route.json")
        else:
            print("ни один сектор камеры не вывел к ленте, см. лог")

    elif args.name == "shop":
        goods = f.read_shop(walk=not args.here)
        if not goods:
            print("прочитать ассортимент не вышло, см. скрины и лог")
        for text, price in goods:
            print(f"  {text:<40} {price or '—'}")

    elif args.name == "items":
        print("на базе:", f.base_items() or "пусто")

    elif args.name == "calibrate":
        if not f.to_reference():
            sys.exit("опорное состояние не взято — калибровать не от чего")
        axes = f.nav.calibrate()
        if not axes:
            print("НИ ОДНА клавиша не двигает ориентиры — смотри var/screens")
        for key, (dx, dy) in axes.items():
            print(f"  {key}: ориентиры едут на ({dx:+.0f}, {dy:+.0f}) px/с")

    elif args.name == "see":
        for name, spot in f.nav.see().items():
            print(f"  {name:12} x={spot.x:4} y={spot.y:4}")

    elif args.name == "approach":
        if not f.nav.axes:
            f.to_reference()
            f.nav.calibrate()
            f.to_reference()      # калибровка сдвигает персонажа — вернуться в точку
        ok = f.nav.approach(args.landmark)
        print(("подошёл к " if ok else "не подошёл к ") + args.landmark)

    elif args.name == "cash":
        print("наличные:", f.read_cash())

    elif args.name == "card":
        print("карточка у конвейера (редкость, доход, цена):", f.read_card())


def cmd_routine(args) -> None:
    """Суточный цикл: лок -> сбор -> покупка, с приоритетами и восстановлением."""
    from .routine import Routine, RoutineConfig
    s = _settings()
    f = _farmer(args, s)
    cfg = RoutineConfig(min_income=args.min_income, min_rarity=args.min_rarity)
    print("Цикл пошёл. Ctrl+C — остановить. Аварийный стоп ввода: мышь в угол экрана.")
    stats = Routine(farmer=f, cfg=cfg).run(seconds=args.seconds)
    print(f"итог: куплено {stats['bought']}, собрано {stats['collected']:.0f}, локов {stats['locks']}")


def cmd_survey(args) -> None:
    """Разведка маршрута к соседней базе: собрать тексты, которые показывает игра."""
    from .scenarios.steal import Transfer
    s = _settings()
    f = _farmer(args, s)
    out = Transfer(receiver=f).survey(bases=args.bases, to_right=not args.left)
    for where, texts in out.items():
        print(f"--- {where}")
        for t in texts[:20]:
            print("   ", t)


def cmd_join(args) -> None:
    """Запустить аккаунт в КОНКРЕТНОМ сервере — чтобы два наших были вместе."""
    from . import launcher
    s = _settings()
    account = s.account(args.account)

    job = args.job_id
    if not job:
        job = launcher.newest_job_id(place_id=s.place_id)
        if not job:
            sys.exit("не нашёл jobId в логах клиента — сначала запусти донора: run.py launch <аккаунт>")
        print(f"беру сервер из логов: {job}")

    mutex = SingletonMutex()
    mutex.acquire()
    session = Session(account=account, settings=s)
    opt = s.optimize
    pid = launcher.launch(account, s.place_id, apply_fflags=opt.get("apply_fflags", True),
                          target_fps=opt.get("target_fps"), job_id=job)
    print(f"{account.name}: клиент поднят (pid={pid}) в сервере {job}")
    print("Проверь по лидерборду (Tab), что оба ника в одном сервере.")


def cmd_servers(args) -> None:
    """Какие сервера заняли наши клиенты — по логам."""
    from . import launcher
    import datetime
    _settings()
    rows = launcher.recent_joins()
    if not rows:
        print("в логах клиента заходов не нашлось")
        return
    for mtime, job, place, path in rows:
        when = datetime.datetime.fromtimestamp(mtime).strftime("%d.%m %H:%M")
        print(f"{when}  place={place}  job={job}")


def cmd_record(args) -> None:
    """Записать mp4 из кадров зрения бота. --with-input пишет ещё и нажатия."""
    import time
    from .recorder import InputLog, Recorder
    s = _settings()
    win = _pick_window(args, s)
    out = Path(args.out) if args.out else s.screenshots_dir / f"rec_{int(time.time())}.mp4"

    # Пишем КУСКАМИ. Единственный надёжный способ не потерять запись целиком.
    #
    # OpenCV кладёт индекс mp4 (moov) в самый конец; пока идёт запись, его в
    # файле нет. Оборванный процесс оставляет данные, которые не открывает ни
    # плеер, ни ffmpeg — «moov atom not found». Так была потеряна запись показа
    # на 89 МБ. Обработчик сигнала не спасает: Windows не доставляет питону
    # SIGTERM при taskkill, проверено. А каждый кусок закрывается сам, и потеряться
    # может только последний, недописанный.
    if args.segment and args.segment < args.seconds:
        total, part, paths = int(args.seconds), 1, []
        piece = out.with_name(f"{out.stem}_часть{part:02d}{out.suffix}")
        rec = Recorder(win, piece, fps=args.fps, overlay=not args.raw)
        rec.note(f"{args.note or 'запись'} — часть {part}")
        rec.start()
        while total > 0:
            span = min(args.segment, total)
            for left in range(span, 0, -1):
                if left % 10 == 0 or left <= 3:
                    print(f"  часть {part}: осталось {left} с", flush=True)
                time.sleep(1)
            total -= span
            part += 1
            if total > 0:
                nxt = out.with_name(f"{out.stem}_часть{part:02d}{out.suffix}")
                rec.note(f"{args.note or 'запись'} — часть {part}")
                paths.append(rec.roll(nxt))
            else:
                paths.append(rec.stop())
            print(f"  готова: {paths[-1]}", flush=True)
        print(f"кусков записано: {len(paths)}")
        for x in paths:
            print(f"  {x}")
        return

    rec = Recorder(win, out, fps=args.fps, overlay=not args.raw)
    inp = InputLog().start() if args.with_input else None
    rec.note(args.note or "запись")
    rec.start()

    # Дописать файл при УБИЙСТВЕ процесса, а не только при Ctrl+C.
    #
    # OpenCV кладёт индекс mp4 (moov) в самый конец, при записи его в файле нет.
    # Оборванная запись превращается в 89 МБ данных, которые не открывает ни
    # плеер, ни ffmpeg: «moov atom not found», и восстановить нечем. Так была
    # потеряна запись показа целиком. SIGTERM приходит и при остановке фоновой
    # задачи, и при `taskkill` — ловим и закрываем писателя по-человечески.
    import signal

    def _finish(signum, frame):        # noqa: ARG001
        path = rec.stop()
        print()
        print(f"запись закрыта по сигналу: {path} (кадров {rec.frames})",
              flush=True)
        raise SystemExit(0)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _finish)
        except (ValueError, OSError):
            pass                        # не главный поток — переживём

    try:
        for left in range(int(args.seconds), 0, -1):
            if left % 10 == 0 or left <= 3:
                print(f"  пишу, осталось {left} с", flush=True)
            time.sleep(1)
    except KeyboardInterrupt:
        print()
    finally:
        path = rec.stop()
    print(f"видео: {path}  (кадров {rec.frames})")

    if inp:
        holds = inp.stop() and inp.holds()
        print(f"удержания клавиш ({len(holds)}):")
        for key, start, dur in holds:
            print(f"  {start:6.2f}с  {key:6} {dur:.2f}с")


def cmd_teach(args) -> None:
    """Записать проход человека, чтобы бот его повторил."""
    from . import lessons
    s = _settings()
    f = _farmer(args, s)
    lesson = lessons.teach(f, args.name, seconds=args.seconds,
                           countdown=args.countdown, setup=args.setup)
    path = lessons.save(lesson, s.screenshots_dir.parent)
    print(f"урок сохранён: {path}")
    print("повторить: python run.py replay --name " + args.name)


def cmd_lessons(args) -> None:
    """Что уже записано."""
    from . import lessons
    s = _settings()
    all_lessons = lessons.load_all(s.screenshots_dir.parent)
    if not all_lessons:
        print("уроков пока нет. Запиши: python run.py teach --name к-ленте")
        return
    for lesson in all_lessons:
        keys = " ".join(f"{'+'.join(st.keys)}{st.hold:.1f}" for st in lesson.steps[:12])
        mark = {True: "цель достигнута", False: "неудачный", None: "не подтверждён"}[lesson.ok]
        print(f"  {lesson.name:16} {len(lesson.steps):3} шагов  {lesson.duration():5.1f}с  "
              f"{mark}")
        print(f"      {keys}{' ...' if len(lesson.steps) > 12 else ''}")


def cmd_replay(args) -> None:
    """Повторить выученный проход."""
    from . import lessons
    s = _settings()
    f = _farmer(args, s)
    found = lessons.load_all(s.screenshots_dir.parent, name=args.name)
    if not found:
        sys.exit(f"уроков с именем {args.name!r} нет — сначала teach")
    lesson = lessons.merge(found)
    if lesson is None:
        sys.exit("удачных уроков нет")
    print(f"повторяю {args.name}: {len(lesson.steps)} шагов, {lesson.duration():.1f} с")

    rec = None
    if args.record:
        from .recorder import Recorder
        import time as _t
        rec = Recorder(f.window, s.screenshots_dir / f"replay_{args.name}_{int(_t.time())}.mp4",
                       fps=8).start()
        rec.note(f"повтор: {args.name}")
    try:
        # Признак цели зависит от того, чему учили. Раньше он был зашит на
        # промпт покупки, и урок про лок объявлялся неудачным всегда — что бы
        # бот ни сделал.
        goals = {
            "purchase": lambda: bool(f.sees("purchase")),
            "lock": lambda: bool(f._read_lock_seconds() or f.read_lock_left()),
        }
        rep = lessons.replay(f, lesson, check=goals[args.goal])
        for k, v in rep.items():
            print(f"  {k}: {v}")
        if rep.get("цель") and args.buy:
            print("  промпт есть — покупаю")
            f.hand.interact(1.8)
            print("  на базе:", f.base_items())
    finally:
        if rec:
            print(f"видео: {rec.stop()}")


def cmd_up(args) -> None:
    from .supervisor import Supervisor
    s = _settings()
    Supervisor(s).run()


def launcher_browser_path() -> str | None:
    """Где лежит браузер. Ищем по обычным местам, начиная с Яндекса."""
    import os
    from pathlib import Path as _P
    candidates = [
        _P(os.environ.get("LOCALAPPDATA", "")) / "Yandex/YandexBrowser/Application/browser.exe",
        _P(os.environ.get("PROGRAMFILES", "")) / "Yandex/YandexBrowser/Application/browser.exe",
        _P(os.environ.get("PROGRAMFILES(X86)", "")) / "Yandex/YandexBrowser/Application/browser.exe",
        _P(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
        _P(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft/Edge/Application/msedge.exe",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return None


def cmd_desk(args) -> None:
    """Отдельный рабочий стол: бот играет, машина остаётся твоей.

    Ввод в фоне, без фокуса, не работает — это замерено (см. `postinput.py`:
    одна проба из двадцати, и та выброс). Зато у каждого стола Windows своя
    очередь ввода: процесс, запущенный на столе `brainbot`, шлёт нажатия туда,
    и на твоём столе ничего не дёргается.
    """
    import json as _json
    import sys as _sys
    import time as _time
    from pathlib import Path as _Path

    from . import desktop
    s = _settings()
    root = _Path(__file__).resolve().parents[2]

    if args.action == "hold":
        handle = desktop.ensure()
        print(f"Стол {desktop.NAME} поднят. НЕ ЗАКРЫВАЙ это окно: закроешь — "
              f"стол исчезнет вместе со всем, что на нём запущено.")
        try:
            while True:
                _time.sleep(30)
        except KeyboardInterrupt:
            print("стол отпущен")
        finally:
            handle = None

    elif args.action == "browser":
        desktop.ensure()
        url = f"https://www.roblox.com/games/{s.place_id}/"
        # ФЛАГ --disable-gpu ОБЯЗАТЕЛЕН. Без него Chromium-браузер на
        # альтернативном рабочем столе не стартует вовсе: процесс умирает
        # молча, ноль процессов, пустой вывод. Проверено 30.08.2026 —
        # три захода без флага провалились, с флагом поднялось 14 процессов.
        # И запускать надо ПО ПОЛНОМУ ПУТИ: `start` на столе без оболочки
        # тоже не срабатывает.
        exe = args.browser or launcher_browser_path()
        if not exe:
            print("не нашёл браузер — укажи путь: run.py desk browser --browser \"<путь>\"")
            return
        desktop.spawn(f'"{exe}" --disable-gpu "{url}"')
        print(f"Браузер запущен на столе {desktop.NAME}: {url}")
        print("Дальше: run.py desk show --seconds 150 — посмотреть и нажать Play.")

    elif args.action == "show":
        desktop.show(args.seconds)
        print("Экран вернулся на твой стол.")

    elif args.action == "bot":
        desktop.ensure()
        loop = root / "scripts" / "farm_loop.py"
        inner = (f'cd /d "{root}" && set PYTHONIOENCODING=utf-8 && '
                 f'"{_sys.executable}" "{loop}" {args.minutes} {args.income} '
                 f'> var\\desk_loop.txt 2>&1')
        desktop.spawn(f'cmd /c {inner}')
        print(f"Цикл запущен на столе {desktop.NAME} на {args.minutes} мин.")
        print("Следить: var/desk_loop.txt и var/farm_status.json — файлы общие.")

    elif args.action == "game":
        # Клиент поднимается НАПРЯМУЮ, без браузера. Так пришлось сделать
        # потому, что браузер на альтернативном рабочем столе не стартует
        # вовсе: процесс умирает молча, без вывода — проверено тремя заходами
        # 30.08.2026, в том числе запуском по полному пути. А диплинк
        # `roblox://` без билета авторизации виснет и не входит в игру.
        from . import launcher
        desktop.ensure()
        account = s.account(args.account)
        if not account.cookie:
            print(f"У аккаунта {args.account} пустая кука. Вставь свежую "
                  f".ROBLOSECURITY в config/accounts.json (поле cookie) — "
                  f"как её взять, написано там же в _how_to_get_cookie.")
            return
        exe = launcher.find_player_exe()
        uri = launcher.build_launch_uri(launcher.auth_ticket(account), s.place_id,
                                        job_id=args.job_id)
        desktop.spawn(f'"{exe}" "{uri}"')
        print(f"Клиент {args.account} запущен на столе {desktop.NAME}.")
        print("Проверить, что окно там появилось: run.py desk check")

    elif args.action == "check":
        desktop.ensure()
        out = root / "var" / "desk_windows.txt"
        inner = (f'cd /d "{root}" && set PYTHONIOENCODING=utf-8 && '
                 f'"{_sys.executable}" run.py windows > var\\desk_windows.txt 2>&1')
        desktop.spawn(f'cmd /c {inner}')
        _time.sleep(6)
        print(out.read_text(encoding="utf-8", errors="replace").strip()
              if out.exists() else "проверка не отработала")

    elif args.action == "spawn":
        desktop.ensure()
        desktop.spawn(args.cmd)
        print(f"Запущено на столе {desktop.NAME}: {args.cmd}")

    elif args.action == "status":
        try:
            desktop.ensure()
            print(f"Стол {desktop.NAME}: есть.")
        except Exception as exc:                            # noqa: BLE001
            print(f"Стол {desktop.NAME}: нет ({exc})")
        st = root / "var" / "farm_status.json"
        if st.exists():
            d = _json.loads(st.read_text(encoding="utf-8"))
            print(f"  кругов {d.get('кругов')}, локов {d.get('локов')}, "
                  f"покупок {d.get('покупок')}, кэш {d.get('кэш')}")
            print(f"  последнее: {d.get('последнее')} ({d.get('обновлено')})")


def cmd_optimize(args) -> None:
    from . import optimize
    s = _settings()

    if args.clear:
        removed = optimize.clear_fflags()
        print(f"Настройки клиента убраны из {len(removed)} версий." if removed
              else "Убирать нечего.")
        return

    flags = dict(optimize.FFLAGS_BOT)
    fps = args.fps or s.optimize.get("target_fps", 20)
    flags["DFIntTaskSchedulerTargetFps"] = fps

    written = optimize.write_fflags(flags)
    print(f"Потолок кадров: {fps}")
    if written:
        for p in written:
            print(f"  записано: {p}")
    else:
        print("  уже было записано, менять нечего")

    print(f"\nЯдер в системе: {optimize.cpu_count()}")
    n = args.clients or max(1, len(s.enabled_accounts))
    plan = optimize.plan_affinity(n, s.optimize.get("reserve_threads", 4))
    print(f"Раскладка на {n} клиентов (первые {s.optimize.get('reserve_threads', 4)} "
          f"потока оставлены системе и боту):")
    for i, cores in enumerate(plan):
        print(f"  клиент {i + 1}: ядра {cores}")

    live = optimize.client_memory()
    if live and args.pin_live:
        # Осознанное действие: привязка режет клиенту ядра и опускает приоритет.
        # На клиенте, за которым сидит человек, это выливается в таймауты RakNet
        # и «Connection Failed 279». Поэтому только по явному флагу.
        print(f"\nПривязываю {len(live)} живых клиентов:")
        for i, (pid, gb) in enumerate(live):
            ok = optimize.pin_process(pid, plan[i % len(plan)],
                                      s.optimize.get("background_priority", "below"))
            print(f"  pid={pid} ({gb:.2f} ГБ): {'ok' if ok else 'не удалось'}")
    elif live:
        print(f"\nЖивых клиентов: {len(live)} — НЕ трогаю.")
        print("  Привязка к ядрам и пониженный приоритет ломают клиент, за которым")
        print("  играет человек. Ботам это ставит супервизор при запуске сам.")
        print("  Принудительно: --pin-live")

    print("\nFFlags подхватятся при следующем запуске клиента — перезапусти его.")


def cmd_unpin(args) -> None:
    """Вернуть клиентам все ядра и обычный приоритет."""
    from . import optimize
    _settings()
    live = optimize.client_memory()
    if not live:
        print("Живых клиентов Roblox нет.")
        return
    all_cores = list(range(optimize.cpu_count()))
    for pid, gb in live:
        ok = optimize.pin_process(pid, all_cores, "normal")
        print(f"  pid={pid} ({gb:.2f} ГБ): {'все ядра, приоритет обычный' if ok else 'не удалось'}")


def cmd_capacity(args) -> None:
    from . import optimize
    s = _settings()
    est = optimize.estimate_capacity(
        per_client_gb=args.per_client,
        headroom_gb=s.optimize.get("headroom_gb", 2.0),
    )

    print("=== память ===")
    print(f"всего            {est['total_gb']:.1f} ГБ")
    print(f"свободно сейчас  {est['avail_gb']:.1f} ГБ")
    print(f"клиентов живо    {est['live_clients']}")
    src = "замерено по живым" if est["measured"] else "оценка, живых клиентов нет"
    print(f"на клиент        {est['per_client_gb']:.2f} ГБ  ({src})")

    print("\n=== сколько влезет ===")
    print(f"сейчас, как есть            {est['fits_now']}")
    print(f"если закрыть лишнее         {est['fits_if_freed']}")

    per = est["per_client_gb"]
    head = s.optimize.get("headroom_gb", 2.0)
    for extra in (16, 32, 48):
        total = est["total_gb"] + extra
        print(f"если добить памяти +{extra} ГБ    "
              f"{max(0, int((total * 0.85 - head) / per))}")

    print("\n=== чем ограничены ===")
    cores = optimize.cpu_count()
    print(f"ядер/потоков  {cores} → по CPU потолок примерно "
          f"{max(1, (cores - s.optimize.get('reserve_threads', 4)))} клиентов")
    print("экран         не ограничивает: WGC снимает перекрытые окна")


def cmd_user(args) -> None:
    s = _settings()
    users = roblox_api.users_by_name([args.nickname])
    user = users.get(args.nickname) or next(iter(users.values()), None)
    if not user:
        sys.exit(f"Ник {args.nickname!r} не существует")
    print(f"{user.name} (display: {user.display_name})  id={user.id}")

    cookie = next((a.cookie for a in s.enabled_accounts), None)
    if not cookie:
        print("presence не проверить — нужен хотя бы один аккаунт с живой кукой")
        return
    pres = roblox_api.presence(cookie, [user.id]).get(user.id)
    if not pres:
        print("presence: нет данных")
        return
    state = {0: "офлайн", 1: "на сайте", 2: "в игре", 3: "в Studio"}.get(pres.type, "?")
    here = " (в нашей игре)" if pres.in_place(s.place_id) else ""
    print(f"presence: {state}{here}  {pres.last_location}")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="brainbot", description="Боты Steal a Brainrot")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="проверить окружение").set_defaults(fn=cmd_doctor)
    sub.add_parser("windows", help="открытые окна Roblox").set_defaults(fn=cmd_windows)
    sub.add_parser("templates", help="список шаблонов").set_defaults(fn=cmd_templates)
    sub.add_parser("up", help="супервизор: держать аккаунты в игре").set_defaults(fn=cmd_up)
    sub.add_parser("hold", help="держать мьютекс для ручного запуска второго клиента").set_defaults(fn=cmd_hold)

    sp = sub.add_parser("shot", help="снять кадр окна")
    sp.add_argument("--hwnd", type=int)
    sp.add_argument("--tag", help="имя файла, напр. trade_3_search")
    sp.add_argument("--resize", action="store_true", help="сначала привести окно к размеру из конфига")
    sp.add_argument("--delay", type=int, default=0,
                    help="снять через N секунд — успеть переключиться в игру")
    sp.set_defaults(fn=cmd_shot)

    sp = sub.add_parser("shots", help="провести по состояниям и отснять все нужные кадры")
    sp.add_argument("--hwnd", type=int)
    sp.add_argument("--delay", type=int, default=4, help="секунд на переключение в игру")
    sp.add_argument("--only", nargs="+", help="только эти состояния, напр. 1_main 2_open")
    sp.set_defaults(fn=cmd_shots)

    sp = sub.add_parser("cut", help="нарезать шаблоны из скрина")
    sp.add_argument("image")
    sp.set_defaults(fn=cmd_cut)

    sp = sub.add_parser("find", help="проверить шаблон на живом окне")
    sp.add_argument("template", nargs="?", help="без имени — проверит все")
    sp.add_argument("--hwnd", type=int)
    sp.add_argument("--threshold", type=float)
    sp.set_defaults(fn=cmd_find)

    sp = sub.add_parser("input-test", help="проверить, доходит ли ввод до клиента")
    sp.add_argument("--hwnd", type=int)
    sp.add_argument("--keep", action="store_true", help="сохранять кадры до/после")
    sp.add_argument("--backend", default="focus", choices=["focus", "post"],
                    help="focus — SendInput в окно в фокусе; "
                         "post — PostMessage прямо в окно, без фокуса")
    sp.add_argument("--compare", action="store_true",
                    help="прогнать обоими каналами и сравнить: дойдёт ли ввод "
                         "без фокуса, то есть можно ли вести клиенты параллельно")
    sp.set_defaults(fn=cmd_input_test)

    sp = sub.add_parser("op", help="одна операция сценария на живом окне")
    sp.add_argument("name", choices=["reference", "calibrate", "see", "approach",
                                     "collect", "lock", "buy", "rebirth", "sell",
                                     "items", "gear", "shop", "aim", "mouse", "turn", "errand", "cash",
                                     "card"])
    sp.add_argument("--gear", default="Speed Coil", help="что купить в магазине")
    sp.add_argument("--here", action="store_true",
                    help="для op shop: окно магазина уже открыто, никуда не идти")
    sp.add_argument("--allow-wipe", action="store_true",
                    help="для op rebirth: согласиться, что ребёрн сотрёт всё, "
                         "что стоит на базе")
    sp.add_argument("--landmark", default="collect", help="ориентир для approach")
    sp.add_argument("--record", action="store_true", help="писать видео прогона")
    sp.add_argument("--hwnd", type=int)
    sp.add_argument("--seconds", type=float, default=60.0)
    sp.add_argument("--min-income", type=float, default=100.0)
    sp.add_argument("--min-rarity", default=None)
    sp.add_argument("--max-price", type=float, default=None)
    sp.add_argument("--want", default=None,
                    help="охотиться только за этими именами, через запятую")
    sp.add_argument("--nick", default=None, help="наш ник в игре")
    sp.set_defaults(fn=cmd_op)

    sp = sub.add_parser("routine", help="цикл: лок, сбор, покупка")
    sp.add_argument("--hwnd", type=int)
    sp.add_argument("--seconds", type=float, default=None)
    sp.add_argument("--min-income", type=float, default=100.0)
    sp.add_argument("--min-rarity", default=None)
    sp.add_argument("--nick", default=None, help="наш ник в игре")
    sp.set_defaults(fn=cmd_routine)

    sp = sub.add_parser("survey", help="разведка маршрута к соседней базе")
    sp.add_argument("--hwnd", type=int)
    sp.add_argument("--bases", type=int, default=1, help="через сколько баз идти")
    sp.add_argument("--left", action="store_true", help="идти влево, а не вправо")
    sp.set_defaults(fn=cmd_survey)

    sp = sub.add_parser("join", help="поднять аккаунт в конкретном сервере")
    sp.add_argument("account")
    sp.add_argument("--job-id", default=None, help="по умолчанию — свежий из логов")
    sp.set_defaults(fn=cmd_join)

    sub.add_parser("servers", help="какие сервера заняли клиенты").set_defaults(fn=cmd_servers)

    sp = sub.add_parser("record", help="записать mp4 из кадров зрения бота")
    sp.add_argument("--hwnd", type=int)
    sp.add_argument("--seconds", type=float, default=20.0)
    sp.add_argument("--fps", type=int, default=10)
    sp.add_argument("--out", default=None)
    sp.add_argument("--note", default=None, help="подпись поверх кадра")
    sp.add_argument("--raw", action="store_true", help="без подписей")
    sp.add_argument("--with-input", action="store_true", help="писать и нажатия")
    sp.add_argument("--segment", type=int, default=0,
                    help="писать кусками по N секунд: оборванная запись тогда "
                         "теряет только последний кусок, а не всё")
    sp.set_defaults(fn=cmd_record)

    sp = sub.add_parser("teach", help="записать твой проход, чтобы бот его повторил")
    sp.add_argument("--name", default="к-ленте", help="как назвать маршрут")
    sp.add_argument("--seconds", type=float, default=60.0)
    sp.add_argument("--countdown", type=int, default=3)
    sp.add_argument("--setup", action="store_true",
                    help="дать боту выставить старт перед записью (по умолчанию нет)")
    sp.add_argument("--hwnd", type=int)
    sp.add_argument("--nick", default=None)
    sp.set_defaults(fn=cmd_teach)

    sp = sub.add_parser("lessons", help="какие проходы записаны")
    sp.set_defaults(fn=cmd_lessons)

    sp = sub.add_parser("replay", help="повторить выученный проход")
    sp.add_argument("--name", default="к-ленте")
    sp.add_argument("--hwnd", type=int)
    sp.add_argument("--nick", default=None)
    sp.add_argument("--record", action="store_true")
    sp.add_argument("--buy", action="store_true", help="в конце нажать покупку")
    sp.add_argument("--goal", default="purchase", choices=["purchase", "lock"],
                    help="по какому признаку считать урок удавшимся")
    sp.set_defaults(fn=cmd_replay)

    sp = sub.add_parser("launch", help="поднять клиент под аккаунтом")
    sp.add_argument("account")
    sp.set_defaults(fn=cmd_launch)

    sp = sub.add_parser("desk", help="отдельный рабочий стол: бот играет, "
                                     "машина остаётся твоей")
    sp.add_argument("action", choices=["hold", "browser", "game", "check", "show",
                                       "bot", "spawn", "status"])
    sp.add_argument("--account", default="raven", help="для game: какой аккаунт поднять")
    sp.add_argument("--job-id", default=None, help="для game: зайти в конкретный сервер")
    sp.add_argument("--seconds", type=float, default=150.0,
                    help="для show: на сколько показать стол бота")
    sp.add_argument("--minutes", type=float, default=120.0, help="для bot: сколько крутить")
    sp.add_argument("--income", type=float, default=3000.0, help="для bot: порог дохода")
    sp.add_argument("--cmd", default="", help="для spawn: что запустить")
    sp.add_argument("--browser", default="", help="для browser: путь к браузеру")
    sp.set_defaults(fn=cmd_desk)

    sp = sub.add_parser("optimize", help="настройки клиента, ядра, приоритеты")
    sp.add_argument("--fps", type=int, help="потолок кадров (по умолчанию из конфига)")
    sp.add_argument("--clients", type=int, help="под сколько клиентов раскладывать ядра")
    sp.add_argument("--clear", action="store_true", help="убрать наши настройки клиента")
    sp.add_argument("--pin-live", action="store_true",
                    help="привязать к ядрам уже запущенные клиенты (сломает тот, в котором играешь)")
    sp.set_defaults(fn=cmd_optimize)

    sub.add_parser("unpin", help="вернуть клиентам все ядра и обычный приоритет").set_defaults(fn=cmd_unpin)

    sp = sub.add_parser("capacity", help="сколько сессий влезет")
    sp.add_argument("--per-client", type=float, help="ГБ на клиента, если знаешь точнее")
    sp.set_defaults(fn=cmd_capacity)

    sp = sub.add_parser("user", help="ник → id и presence")
    sp.add_argument("nickname")
    sp.set_defaults(fn=cmd_user)

    args = p.parse_args(argv)
    args.fn(args)

