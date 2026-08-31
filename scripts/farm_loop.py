# -*- coding: utf-8 -*-
"""Автономный круг фермы: лок -> лента -> закуп -> домой -> лок.

Что здесь важного, помимо самого цикла:

* бюджет вылазки считается по НАШИМ часам (`lock_until`). Игра говорит
  длительность один раз вспышкой «You locked your base for 80 Seconds!»,
  дальше зрение в этом месте не участвует;
* цель закупа берётся из окна ребёрна, а не задаётся руками: требуемые
  брейнроты покупаются независимо от цены, остальные — только если окупаются
  быстрее порога;
* состояние пишется в `var/farm_status.json` после каждого круга, чтобы за
  прогоном можно было следить, не трогая мышь.

Запуск: python scripts/farm_loop.py [минут] [мин_доход]
"""
import json
import sys
import time
import traceback

sys.path.insert(0, "src")
from brainbot import config, log, ocr                      # noqa: E402
from brainbot.window import enum_roblox_windows            # noqa: E402
from brainbot.inputs import Hand                           # noqa: E402
from brainbot.farm import Farmer, FarmTuning               # noqa: E402
from brainbot.brainrots import normalize                   # noqa: E402

MINUTES = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0
MIN_INCOME = float(sys.argv[2]) if len(sys.argv) > 2 else 100.0
# Сколько перерождений сделать за прогон. Ребёрн СТИРАЕТ деньги и брейнротов —
# это прямое задание пользователя от 31.08: «нужно реберхов 10 сделать».
REBIRTHS_GOAL = int(sys.argv[3]) if len(sys.argv) > 3 else 10

# Секунд до конца лока, когда пора домой. Не константа: дорога домой плюс сам
# лок занимают около сорока секунд, и если уходить с ленты за восемь, база
# стоит открытой почти минуту каждый круг — а её именно в это время и
# обворовывают. Считаем по факту: медиана прошлых локов плюс запас.
RESERVE_MIN = 20.0
BUY_HOLD = 2.0         # промпт держать, иначе не засчитывается
PAYBACK_SEC = 400.0    # покупаем, если цена окупается быстрее этого

s = config.load()
log.setup(s.logs_dir)
wins = enum_roblox_windows()
if not wins:
    sys.exit("окон Roblox нет — клиент не запущен")
# Размер окна проверяем ДО первого кадра. Всё зрение считает долями кадра, и
# сжатое по высоте окно (замер 30.08: 1280x599 вместо 1280x720) сдвигает разом
# все области: наличные, промпты, пад в виде сверху. Ищется такое молча — по
# кривым пеленгам, а не по ошибке.
_box = wins[0].client_box()
if (_box.width, _box.height) != (int(s.window["width"]), int(s.window["height"])):
    print("окно %dx%d вместо %sx%s — привожу к рабочему"
          % (_box.width, _box.height, s.window["width"], s.window["height"]), flush=True)
    import ctypes
    from ctypes import wintypes as _wt
    _r = _wt.RECT()
    ctypes.windll.user32.GetWindowRect(wins[0].hwnd, ctypes.byref(_r))
    wins[0].move_resize(_r.left, _r.top, int(s.window["width"]), int(s.window["height"]))
    time.sleep(0.6)

f = Farmer(window=wins[0], hand=Hand(wins[0], s.input), tuning=FarmTuning(),
           screens_dir=s.screenshots_dir)
f.allow_wipe = True          # разрешено пользователем: цель — цепочка ребёрнов

STATUS = "var/farm_status.json"
state = {
    "начат": time.strftime("%H:%M:%S"),
    "кругов": 0,
    "локов": 0,
    "локи_секунд": [],
    "покупок": 0,
    "куплено": [],
    "цели": [],
    "цели_куплены": [],
    "нужно_денег": None,
    "кэш": None,
    "кэш_старт": None,
    "сбоев": 0,
    "доход_в_сек": None,
    "собрано_всего": 0.0,
    "цели_видел": 0,
    "ребёрнов": 0,
    "ребёрнов_цель": REBIRTHS_GOAL,
    "лента_видела": {},
    "последнее": "",
}
_money = []          # (время, кэш) — по ним считаем доход
_last_read = [time.time()]


def sane_cash(prev):
    """Прочитать деньги с проверкой на правдоподобие.

    OCR путает разряды: «$121.86K» приходит как 6 139 360, то есть шесть
    миллионов вместо ста двадцати тысяч. Для порога перерождения в $12.5M это
    смертельно — бот решит, что накопил. Правило: деньги не могут вырасти
    быстрее, чем капает доход (берём щедрые $20 000/с), а падение проверяем
    вторым чтением.
    """
    now = f.read_hud_cash()
    if now is None or prev is None:
        return now
    dt = max(1.0, time.time() - _last_read[0])
    if now > prev + 20000 * dt + 50e6 or now < prev * 0.2:
        again = f.read_hud_cash()
        if again is None or again > prev + 20000 * dt or again < prev * 0.2:
            say("чтение денег отброшено: %s при прежних %s" % (now, prev))
            return prev
        now = again
    return now


def note_cash(value) -> None:
    """Запомнить деньги и пересчитать доход. Покупки его занижают, поэтому
    берём только участки, где деньги РОСЛИ."""
    if value is None:
        return
    state["кэш"] = value
    _last_read[0] = time.time()
    _money.append((time.time(), value))
    ups = [(t1 - t0, c1 - c0) for (t0, c0), (t1, c1) in zip(_money, _money[1:])
           if c1 > c0 and t1 - t0 > 5]
    if ups:
        state["доход_в_сек"] = round(sum(c for _, c in ups) / sum(t for t, _ in ups), 1)


def save() -> None:
    state["обновлено"] = time.strftime("%H:%M:%S")
    with open(STATUS, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1)


def say(msg: str) -> None:
    state["последнее"] = msg
    print(msg, flush=True)
    save()


KNOW = "var/knowledge.json"


def load_goals() -> None:
    """Цели из памяти бота — на случай, если окно ребёрна не открылось."""
    try:
        with open(KNOW, encoding="utf-8") as fh:
            k = json.load(fh)
        state["цели"] = k.get("цели_ребёрна") or []
        state["нужно_денег"] = k.get("нужно_денег")
        if state["цели"]:
            say("цели из памяти: %s" % ", ".join(state["цели"]))
    except Exception:                                       # noqa: BLE001
        pass


def remember_goals() -> None:
    try:
        with open(KNOW, encoding="utf-8") as fh:
            k = json.load(fh)
        k["цели_ребёрна"] = state["цели"]
        k["нужно_денег"] = state["нужно_денег"]
        with open(KNOW, "w", encoding="utf-8") as fh:
            json.dump(k, fh, ensure_ascii=False, indent=2)
    except Exception:                                       # noqa: BLE001
        pass


def read_goals() -> None:
    """Требования ребёрна: сумма и именные брейнроты."""
    try:
        f.close_players_table()
        if not f.open_menu_item("birth"):
            say("окно ребёрна не открылось — цели оставляю прежними")
            return
        time.sleep(0.6)
        info = f.read_rebirth_window()
        # Закрыть ОБЯЗАТЕЛЬНО и проверить. Незакрытое окно перекрывает середину
        # кадра, и дальше бот слеп: ни вида сверху, ни свечения плиты. Ровно так
        # 30.08 сгорели четыре лока подряд.
        if not f.dismiss_modals():
            f.shot("goals_modal_stuck")
            say("окно ребёрна не закрылось — снял кадр goals_modal_stuck")
        if info["need_items"]:
            state["цели"] = info["need_items"]
        if info["need_cash"]:
            state["нужно_денег"] = info["need_cash"]
        remember_goals()
        say("цели: %s, нужно $%s, накоплено $%s"
            % (", ".join(state["цели"]), info["need_cash"], info["have_cash"]))
    except Exception as exc:                                # noqa: BLE001
        say("цели прочитать не вышло: %s" % exc)


def at_belt() -> bool:
    return "purchase" in " ".join(x.lower() for x, _, _ in ocr.lines(f.frame()))


def goto_belt(max_steps: int = 12, tries: int = 4) -> float | None:
    """Из базы к ленте. Возвращает секунды пути либо None.

    Разворота на полкруга здесь НЕТ, и это главное. Замер 31.08: от точки
    респавна семь шагов ВПЕРЁД выводят прямо к ленте — в кадре промпт
    «Gangster Footera $4K — E Purchase» (near_rot_20260831-030631). Прежняя
    модель «респавн смотрит внутрь базы, лента строго позади» неверна, и
    из-за неё бот разворачивался на 175 градусов и уходил вдоль базы; за ночь
    это стоило всех кругов подряд с «до ленты не дошёл».

    Если за проход ленты нет — доворачиваем веером на 35 градусов и идём
    снова: так перебираются направления вокруг, не полагаясь на калибровку.
    """
    t = time.time()
    f.reset_to_base()
    time.sleep(1.2)
    f.set_work_view()
    f.close_players_table()
    for attempt in range(tries):
        if attempt:
            f.hand.turn_degrees(35)
            time.sleep(0.3)
        for _ in range(max_steps):
            f.hand.hold("w", 0.6)
            time.sleep(0.22)
            if at_belt():
                return time.time() - t
        f.shot("belt_miss_%d" % attempt)
    return None


def worth_buying(item, price) -> tuple[bool, str]:
    """Брать ли. Цели — всегда; остальных — только если быстро окупятся."""
    if item is None:
        return False, "неизвестный"
    want = {normalize(n) for n in state["цели"]}
    if normalize(item.name) in want:
        return True, "ЦЕЛЬ"
    income = item.base_income or 0
    if income < MIN_INCOME:
        return False, "доход %s" % income
    if price and income and price / income > PAYBACK_SEC:
        return False, "окупается %.0f с" % (price / income)
    return True, "доход %s/с" % income


def reserve_now() -> float:
    """Сколько секунд оставить на дорогу домой и запирание.

    Берём медиану замеренных локов и добавляем шесть секунд. Пока замеров нет,
    считаем по последнему известному времени — 45 секунд.
    """
    times = sorted(state["локи_секунд"][-5:])
    if not times:
        return 45.0
    mid = times[len(times) // 2]
    return max(RESERVE_MIN, mid + 6.0)


def shopping(deadline_left) -> None:
    """Стоять у ленты и покупать, пока горит лок."""
    f.hand.move(13, 65)          # курсор в угол, чтобы не закрывал надписи
    cash = f.read_hud_cash()
    note_cash(cash)
    if state["кэш_старт"] is None:
        state["кэш_старт"] = cash
    while deadline_left() > reserve_now():
        card = f.read_card()
        if not card["ready"]:
            time.sleep(0.2)
            continue
        item = card.get("item")
        if item is not None:
            state["лента_видела"][item.name] = state["лента_видела"].get(item.name, 0) + 1
        take, why = worth_buying(item, card.get("price"))
        if not take:
            time.sleep(0.3)
            continue
        name = item.name
        if why == "ЦЕЛЬ":
            state["цели_видел"] += 1
            say("ЦЕЛЬ НА ЛЕНТЕ: %s, цена %s — беру" % (name, card.get("price")))
        f.hand.interact(BUY_HOLD)
        time.sleep(0.5)
        now = sane_cash(cash)
        if now is not None and cash is not None and now < cash - 1:
            state["покупок"] += 1
            state["куплено"].append(name)
            if normalize(name) in {normalize(n) for n in state["цели"]}:
                state["цели_куплены"].append(name)
                say("!!! КУПЛЕНА ЦЕЛЬ %s за %.0f" % (name, cash - now))
            else:
                say("куплено %s (%s): %.0f -> %.0f" % (name, why, cash, now))
        elif why == "ЦЕЛЬ":
            say("ЦЕЛЬ %s: удержание E не засчиталось, пробую ещё" % name)
            f.hand.interact(BUY_HOLD + 0.8)
            time.sleep(0.6)
            now = sane_cash(cash)
            if now is not None and cash is not None and now < cash - 1:
                state["покупок"] += 1
                state["куплено"].append(name)
                state["цели_куплены"].append(name)
                say("!!! КУПЛЕНА ЦЕЛЬ %s со второго раза" % name)
        if now is not None:
            cash = now
            note_cash(now)
    save()


def maybe_rebirth(after_target: bool = False) -> None:
    """Попробовать переродиться, если похоже, что требования выполнены.

    Проверку «хватает ли» делает сам `Farmer.rebirth`: он читает окно, сверяет
    сумму и ИМЕННЫЕ требования с тем, что стоит на базе, и отказывается, если
    чего-то нет. Поэтому здесь достаточно грубого условия — иначе мы просто
    зря откроем и закроем окно.

    После удачного ребёрна требования МЕНЯЮТСЯ (следующий уровень дороже и
    просит других брейнротов), поэтому цели перечитываем сразу.
    """
    if state["ребёрнов"] >= REBIRTHS_GOAL:
        return
    need = state["нужно_денег"] or 0
    cash = state["кэш"] or 0
    if cash < need and not after_target:
        return
    say("пробую ребёрн: кэш %s, нужно %s" % (cash, need))
    try:
        ok = f.rebirth()
    except Exception as exc:                                # noqa: BLE001
        state["сбоев"] += 1
        say("ребёрн сорвался: %s" % exc)
        return
    if not ok:
        return
    state["ребёрнов"] += 1
    state["цели_куплены"] = []
    say("!!! РЕБЁРН %d из %d сделан" % (state["ребёрнов"], REBIRTHS_GOAL))
    # Деньги и брейнроты стёрты — перечитываем и то, и другое.
    state["кэш"] = f.read_hud_cash()
    state["кэш_старт"] = state["кэш"]
    _money.clear()
    read_goals()


def circle() -> None:
    if not f.ensure_connected():
        state["сбоев"] += 1
        say("клиент не вернулся в игру")
        time.sleep(30)
        return

    if f.lock_left_now() <= 0:
        t0 = time.time()
        before = sane_cash(state["кэш"]) or state["кэш"]
        left = f.lock_with_retries(attempts=2)
        if not left:
            state["сбоев"] += 1
            say("лок не вышел за %.1f с" % (time.time() - t0))
            return
        state["локов"] += 1
        state["локи_секунд"].append(round(time.time() - t0, 1))
        # Дорога к плите идёт мимо брейнротов, а деньги забираются проходом —
        # значит лок ЗАОДНО и собирает. Проверяем это числом, а не верой: если
        # прирост нулевой, делаем отдельный проход.
        # Сбор — отдельным проходом, ВСЕГДА. Дорога к плите идёт по центру и
        # брейнротов не задевает: замерено, проход по центру дал ноль, а проход
        # от пада внутрь — +$703 000.
        # Сбор — через круг. Он стоит 40-45 секунд из 80-секундного окна лока,
        # и если делать его каждый раз, у ленты остаётся полминуты — мало,
        # чтобы дождаться нужного брейнрота. Деньги за пропущенный круг никуда
        # не денутся: они копятся на самих брейнротах.
        collected = f.collect_money(attempts=1) if state["кругов"] % 2 == 0 else 0.0
        # После сбора деньги ПРЫГАЮТ на миллионы — это законно, и фильтр
        # правдоподобия тут не нужен: он отбрасывал верное «3 000 000 при
        # прежних 1 300 000» как невозможный рост.
        after = f.read_hud_cash() or before
        if collected > 0:
            state["собрано_всего"] += collected
        note_cash(after)
        say("заперто на %d с (за %.1f с), собрано %.0f, кэш %s"
            % (left, time.time() - t0, collected, after))

    walked = goto_belt()
    if walked is None:
        state["сбоев"] += 1
        f.shot("fail_belt")
        say("до ленты не дошёл")
        return
    bought_target = len(state["цели_куплены"])
    shopping(f.lock_left_now)
    # Купили цель — идём на ребёрн НЕМЕДЛЕННО: база открыта между локами, и
    # соседи воруют. Именно из-за краж база и пустеет.
    if len(state["цели_куплены"]) > bought_target:
        maybe_rebirth(after_target=True)
    elif state["кругов"] % 3 == 0:
        maybe_rebirth()
    state["кругов"] += 1
    say("круг %d закрыт: покупок всего %d, кэш %s"
        % (state["кругов"], state["покупок"], state["кэш"]))


# Мера поворота — своим замером, а не числом из кода: ползунок
# чувствительности в клиенте двигает человек, и он переживает перезапуск.
try:
    f.set_work_view()
    _rate = f.calibrate_turn()
    say("мера поворота: %s град/ед" % (round(_rate, 4) if _rate else "не измерена"))
except Exception as _exc:                                   # noqa: BLE001
    say("замер поворота сорвался: %s" % _exc)

load_goals()
if not state["цели"]:
    read_goals()
state["кэш"] = f.read_hud_cash()
state["кэш_старт"] = state["кэш"]
save()
end = time.time() + MINUTES * 60
while time.time() < end:
    try:
        circle()
    except KeyboardInterrupt:
        break
    except Exception:                                       # noqa: BLE001
        state["сбоев"] += 1
        say("сбой круга: %s" % traceback.format_exc().splitlines()[-1])
        try:
            f.hand.release_all()
        except Exception:                                   # noqa: BLE001
            pass
        time.sleep(3)
    if state["кругов"] and state["кругов"] % 25 == 0:
        read_goals()

say("прогон окончен: кругов %d, локов %d, покупок %d, ребёрнов %d, кэш %s, сбоев %d"
    % (state["кругов"], state["локов"], state["покупок"],
       state["ребёрнов"], state["кэш"], state["сбоев"]))
