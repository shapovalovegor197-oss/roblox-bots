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
# Секунд стоять у ленты за круг. 55 было слишком много: лок держится 80 с, а
# круг с локом, сбором и дорогой занимает больше — база оставалась открытой
# по полминуты, и за это время у нас УКРАЛИ уже купленную цель (07:09: в
# панели Chimpanzini Bananini горит, а Trulimero Trulicina снова тёмный).
# Для цепочки ребёрнов нужны оба ОДНОВРЕМЕННО, поэтому держим базу запертой
# плотнее, а у ленты стоим меньше.
BELT_STAY = 35.0
HOME_RESERVE = 40.0    # секунд лока, при которых пора домой запираться
# Обычные покупки снова разрешены: сбор заработал (проход по ряду даёт
# миллионы), значит доходный брейнрот опять окупается. Планку дохода держим
# высокой — слотов восемь, и место нужно под цели ребёрна.
ONLY_TARGETS = False
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
    "продано": 0,
    "ряд": 1,
    "продано_кого": [],
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
    # Ровно в сто раз больше — это потерянная десятичная точка, а не богатство:
    # «$1.47M» читается как «$147M» (замер 31.08 17:41). Такое отбрасываем
    # сразу, не тратя второе чтение.
    # Ровно в сто раз — это потерянная точка. Окно узкое НАМЕРЕННО: сбор
    # законно умножает деньги в разы и десятки раз (замер: 1.25M -> 93.44M за
    # проход по полной базе), и широкое окно зарубало правду. Проверено
    # кадром: 93.44M на HUD настоящие.
    if prev and 95 < now / max(prev, 1) < 105:
        say("чтение денег отброшено (похоже на потерянную точку): %s при %s" % (now, prev))
        return prev
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


def on_belt() -> bool:
    """Лента ПОД НОГАМИ: низ кадра залит синим полотном конвейера.

    Промпт «Purchase» появляется, только пока рядом едет брейнрот, — по нему
    нельзя понять, дошли ли мы. А полотно под ногами есть всегда. Замер по
    кадрам 31.08: стоя на ленте — 0.98, на траве у чужой базы — 0.00, на
    площади — 0.15, на своей дорожке внутри базы — 0.69. Порог 0.8 отделяет
    ленту от собственной дорожки с запасом.
    """
    fr = f.frame()
    h, w = fr.shape[:2]
    band = fr[int(h * 0.72):int(h * 0.95), int(w * 0.33):int(w * 0.67)]
    import cv2, numpy as np
    hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, np.array((100, 120, 90), np.uint8),
                    np.array((130, 255, 255), np.uint8))
    return float(m.mean()) / 255.0 > 0.8


def at_belt() -> bool:
    """Мы ВПЛОТНУЮ у ленты: в кадре сам промпт покупки, а не слово где угодно.

    Вхождение по всему кадру ловило и дальнюю вывеску, и подсказку чата: бот
    останавливался за десяток шагов до ленты и стоял там весь лок, ни разу не
    прочитав карточку (прогон 03:20–03:45, пять кругов, покупок ноль).
    Признак промпта тот же, что у read_card: короткая строка со словом.
    """
    return any("purchase" in t.lower() and len(t.strip()) <= 24
               for t, _, _ in ocr.lines(f.frame()))


def goto_belt(max_steps: int = 8, tries: int = 3) -> float | None:
    """Выйти из базы к ленте и ВСТАТЬ. Возвращает секунды пути.

    Раньше дорога считалась пройденной только когда в кадре появлялся промпт
    «Purchase». Но промпт есть лишь пока рядом едет брейнрот: лента движется,
    и ждать его надо СТОЯ у ленты, а не в пути. Бот же продолжал идти и
    уходил мимо ленты на площадь к Trade Plaza (кадр belt_miss_2 06:40).

    Теперь: встать в известный ноль, отвернуться от базы (проверяя кадром),
    пройти пять шагов наружу — и всё, дальше ждёт закуп.
    """
    t = time.time()
    f.reset_to_base()
    time.sleep(1.2)
    f.set_work_view()
    f.close_players_table()
    if f.face_base_from_top() is None:
        say("пад сверху не опознан — известного нуля нет")
    for _ in range(7):
        fr = f.frame()
        if not f.inside_base(fr) and f.looking_outside(fr):
            break
        f.hand.turn_degrees(45)
        time.sleep(0.35)
    for i in range(max_steps):
        f.hand.hold("w", 0.6)
        time.sleep(0.22)
        if at_belt() or on_belt():
            say("на ленте на %d-м шаге" % (i + 1))
            f.shot("belt_stand")
            return time.time() - t
    f.shot("belt_stand_miss")
    return None


def worth_buying(item, price) -> tuple[bool, str]:
    """Брать ли. Цели — всегда; остальных — только если быстро окупятся."""
    if item is None:
        return False, "неизвестный"
    want = {normalize(n) for n in state["цели"]}
    if normalize(item.name) in want:
        return True, "ЦЕЛЬ"
    # НЕ проедать порог перерождения. Цель прогона — цепочка ребёрнов, а для
    # каждого нужны деньги ($12.5M на первом). Доходные брейнроты стоят
    # миллионы: два подряд увели кэш с 35.4 до 24.9, и ещё пара оставила бы
    # бота без ребёрна. Держим требование плюс четверть сверху.
    # ТОЛЬКО ЦЕЛИ. Обычные покупки сейчас не дают ничего: доход с брейнротов
    # снимается сбором, а сбор сломан (пады видны, наступание не
    # засчитывается). Значит доходный брейнрот — это минус деньги и минус
    # слот, а слотов восемь, и забитые слоты мешают взять цель. Ребёрн базу
    # всё равно стирает. Вернуть обычный закуп, когда починится сбор.
    if ONLY_TARGETS:
        return False, "беру только цели"
    # Порог ребёрна бережём, ТОЛЬКО когда он уже взят. Иначе правило душит
    # само себя: 31.08 требование выросло до $35M, кэш был $1.32M, и условие
    # «после покупки должно остаться 35 миллионов» отменяло КАЖДУЮ покупку —
    # бот перестал зарабатывать вовсе, кэш стоял намертво четыре круга.
    #
    # Логика простая: денег меньше требования — растём покупками по
    # окупаемости, это единственный способ до него добраться. Денег больше —
    # не опускаемся обратно.
    need = state["нужно_денег"] or 0
    cash = state["кэш"] or 0
    if need and price and cash >= need * 1.25 and cash - price < need * 1.25:
        return False, "не проедаю порог ребёрна"
    income = item.base_income or 0
    # Планка дохода РАСТЁТ вместе с кэшем — лестница на уровне покупки.
    # Слотов восемь, и брейнрот за 3 доллара в секунду занимает место, которое
    # завтра стоило бы отдать под тысячи. Замер 31.08: с плоской планкой бот
    # за два круга купил двенадцать штук по 3-35/с (одного и того же трижды),
    # суммарный доход около сотни в секунду — против требования в 35 миллионов
    # это ничто.
    #
    # cash/20000: при 600 тысячах берём от 30/с, при 5 миллионах — от 250/с,
    # при 35 миллионах — от 1750/с.
    floor = max(MIN_INCOME, (state["кэш"] or 0) / 20000.0)
    if income < floor:
        return False, "доход %s меньше планки %.0f" % (income, floor)
    # Дубликаты не берём: тот же брейнрот занимает второй слот, а слотов
    # восемь. Лучше подождать лучшего.
    if item.name in state["куплено"]:
        return False, "такой уже есть"
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
    f.shot("belt_shop")          # что именно перед носом, когда начали закуп
    cash = sane_cash(state["кэш"]) or state["кэш"]
    note_cash(cash)
    if state["кэш_старт"] is None:
        state["кэш_старт"] = cash
    # Стоим у ленты ФИКСИРОВАННОЕ время, а не «пока горит лок».
    #
    # Расчёт был такой: лок 80 с, дорога домой и запирание 35 — значит уходить
    # надо заранее. Но лок сам занимает 31 с, дорога к ленте ещё 25-40, и на
    # закуп оставалось 3 секунды: за пять кругов бот не прочитал ни одной
    # карточки. А цель прогона — два именных брейнрота для ребёрна, и ребёрн
    # всё равно СТИРАЕТ базу: стеречь на ней нечего, а не купить цель — значит
    # не сделать ни одного перерождения.
    t_belt = time.time()
    last_seen = [time.time(), 0]        # когда последний раз видели карточку, сколько шагов сделали
    # Уходим, пока лок ЕЩЁ ДЕРЖИТ. Требование пользователя: дверь должна быть
    # закрыта, у нас воруют почти всё. Дорога домой и запирание занимают около
    # сорока секунд, поэтому при остатке меньше HOME_RESERVE закуп прекращаем,
    # сколько бы времени ни оставалось от BELT_STAY.
    while time.time() - t_belt < BELT_STAY and f.lock_left_now() > HOME_RESERVE:
        card = f.read_card()
        if not card["ready"]:
            # Промпта нет — либо между брейнротами на ленте, либо встали чуть
            # в стороне. Долго пусто — делаем шаг вперёд, но не больше трёх:
            # дальше начинается площадь, и оттуда лента уже не видна.
            time.sleep(0.2)
            if time.time() - last_seen[0] > 15 and last_seen[1] < 3:
                f.hand.hold("w", 0.5)
                time.sleep(0.3)
                last_seen[0] = time.time()
                last_seen[1] += 1
                say("у ленты пусто 15 с — шаг вперёд (%d из 3)" % last_seen[1])
            continue
        last_seen[0] = time.time()
        item = card.get("item")
        if item is None:
            say("карточка: имя не опознано, цена %s" % card.get("price"))
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
            # ЦЕЛЬ проверяем НЕ ПО ДЕНЬГАМ. HUD округляет до трёх значащих:
            # при кэше $25.44M покупка за $20K не меняет ни одной цифры, и
            # проверка «деньги упали» всегда отвечает «не взялась». Именно так
            # 04:45 бот решил, что не купил Trulimero Trulicina, и полез
            # освобождать слот. Верный признак — брейнрот на базе, а его
            # сверяет сам ребёрн.
            f.hand.interact(BUY_HOLD)
            time.sleep(0.7)
            f.hand.interact(BUY_HOLD)          # второй раз не повредит
            time.sleep(0.5)
            state["цели_куплены"].append(name)
            say("ЦЕЛЬ %s: удержание сделано, проверю ребёрном" % name)
            maybe_rebirth(after_target=True)
            cash = sane_cash(cash) or cash
            note_cash(cash)
            continue
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
            # Цель не купилась — самая вероятная причина не промах по клавише,
            # а ЗАБИТЫЕ СЛОТЫ: за круг бот берёт три-четыре штуки, и восьмой
            # слот кончается быстро. Освобождаем место худшим по доходу и
            # пробуем снова: цель важнее любого содержимого базы, потому что
            # ребёрн базу всё равно сотрёт.
            say("ЦЕЛЬ %s не взялась — освобождаю слот" % name)
            try:
                sold = f.sell_weakest()
                if sold:
                    say("продан %s ради цели" % sold)
            except Exception as exc:                        # noqa: BLE001
                say("продать не вышло: %s" % exc)
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


ACCOUNT = "raven"


def client_alive() -> bool:
    """Окно клиента ещё живо? Пустой список — Roblox закрыт или выбит."""
    try:
        return bool(enum_roblox_windows())
    except Exception:                                       # noqa: BLE001
        return False


def revive_client() -> bool:
    """Поднять клиент заново и пересобрать фермера на новое окно.

    Без этой проверки бот не замечает пропажи окна и продолжает водить мышью
    по рабочему столу: 31.08 клиент выбило (в игру вошли тем же аккаунтом с
    ноутбука), а цикл ещё минуту слепо тыкал курсором, пока не сработала
    защита pydirectinput от угла экрана. Ввод при этом уходил КУДА УГОДНО.
    """
    global f
    say("окна клиента нет — поднимаю заново")
    try:
        from brainbot.session import Session                # noqa: PLC0415
        from brainbot.mutex import SingletonMutex           # noqa: PLC0415
        SingletonMutex().acquire()
        session = Session(account=s.account(ACCOUNT), settings=s)
        if not session.launch():
            say("клиент не поднялся")
            return False
        win = session.window
    except Exception as exc:                                # noqa: BLE001
        say("поднять клиент не вышло: %s" % exc)
        return False
    f = Farmer(window=win, hand=Hand(win, s.input), tuning=FarmTuning(),
               screens_dir=s.screenshots_dir)
    f.allow_wipe = True
    say("клиент поднят заново, hwnd=%s" % win.hwnd)
    return True


def wait_at_plate() -> None:
    """Дождаться истечения лока СТОЯ У ПЛИТЫ и запереть сразу же.

    Требование пользователя: дверь должна быть закрыта, воруют почти всё.
    Раньше бот ждал истечения где придётся, а потом шёл к плите — и дверь
    стояла открытой всю дорогу (замер: 13 секунд). Запереть заранее нельзя,
    игра отвечает «Your base is already locked!», поэтому единственный способ
    сократить окно — быть на месте к моменту истечения.

    Плита при действующем локе НЕ СВЕТИТСЯ, наводиться не на что: идём вслепую
    по короткому маршруту от респавна, он же используется в локе.
    """
    left = f.lock_left_now()
    say("жду у плиты, до истечения %d с" % left)
    f.reset_to_base()
    time.sleep(1.2)
    f.set_work_view()
    f.close_players_table()
    f.face_base_from_top()
    for _ in range(4):
        f.hand.hold("w", 0.6)
        time.sleep(0.2)
    while f.lock_left_now() > 1:
        time.sleep(0.4)
    for _ in range(4):
        f.hand.hold("w", 0.5)
        time.sleep(0.25)
        if f.lock_confirmed():
            say("заперто сразу по истечении — дверь почти не открывалась")
            return


def circle() -> None:
    if not client_alive():
        if not revive_client():
            state["сбоев"] += 1
            time.sleep(30)
            return
    if not f.ensure_connected():
        state["сбоев"] += 1
        say("клиент не вернулся в игру")
        time.sleep(30)
        return

    # Осталось меньше полуминуты — идём ждать к плите, чтобы запереть в ту же
    # секунду, как лок спадёт.
    if 0 < f.lock_left_now() <= 30:
        try:
            wait_at_plate()
        except Exception as exc:                            # noqa: BLE001
            say("ожидание у плиты сорвалось: %s" % exc)

    if f.lock_left_now() <= 0:
        t0 = time.time()
        # Сколько дверь простояла открытой: от истечения прошлого лока до
        # начала этого. Число важнее всех прочих — пока база открыта, соседи
        # уносят брейнротов, и именно из-за этого база пустеет.
        opened_at = state.get("_лок_истёк") or t0
        state["дверь_открыта_с"] = max(0.0, round(t0 - opened_at, 1))
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
        # Проход по ряду: он и деньги собирает, и слоты чистит.
        #
        # Раскрутка баланса, как её описал пользователь: пока денег мало —
        # берём дешёвых, а как накопили — освобождаем под них слоты и ставим
        # дороже. Слотов восемь, и дешёвый брейнрот со временем становится
        # тормозом: занимает место, которое стоит отдать под тысячи в секунду.
        # Планка растёт вместе с кэшем, чтобы это шло само.
        # Ряд не чередуем вслепую, а ЗАПОМИНАЕМ, какой платит. После
        # перерождения брейнроты встали справа, и замер это показал прямо:
        # левый проход дал 0, правый — 584 820 (605K -> 1.19M). При слепом
        # чередовании половина проходов уходила впустую, а в логе это
        # выглядело как «сбор не работает».
        side = state.get("ряд", -1)
        before_pass = f.read_hud_cash()
        # Продажа — инструмент БОГАТОЙ фазы. Пока денег мало, слоты не узкое
        # место: занять их нечем, и продавать только что купленную мелочь
        # значит топтаться на месте. Порог 5 миллионов взят по факту: дорогие
        # брейнроты (Gorillo $3M, Girafa $7.5M) начинаются примерно оттуда.
        if state["кругов"] % 3 == 2 and (state["кэш"] or 0) >= 5e6:
            bar = max(MIN_INCOME, (state["кэш"] or 0) / 4000.0)
            say("проход с продажей: планка дохода %.0f/с" % bar)
            sold = f.sell_below(bar, side=side)
            if sold:
                state["продано"] = state.get("продано", 0) + len(sold)
                state.setdefault("продано_кого", []).extend(sold)
            after_pass = f.read_hud_cash()
            collected = (after_pass - before_pass
                         if before_pass and after_pass and after_pass > before_pass
                         else 0.0)
        else:
            collected = f.collect_rows(side=side)
        # Ничего не принёс — в следующий раз идём в другую сторону.
        if not collected:
            state["ряд"] = -side
            say("ряд %s пуст — в следующий круг иду в другую сторону"
                % ("левый" if side < 0 else "правый"))
        else:
            state["ряд"] = side
        # После сбора деньги ПРЫГАЮТ на миллионы — это законно, и фильтр
        # правдоподобия тут не нужен: он отбрасывал верное «3 000 000 при
        # прежних 1 300 000» как невозможный рост.
        # Через фильтр: сырое чтение здесь дало «92 670 000» при полутора
        # миллионах на счету (вечер 31.08) и утащило в статус ложное богатство.
        after = sane_cash(before) or before
        if collected > 0:
            state["собрано_всего"] += collected
        note_cash(after)
        state["_лок_истёк"] = time.time() + left
        state.setdefault("дверь_открыта_история", []).append(state["дверь_открыта_с"])
        say("заперто на %d с (за %.1f с), дверь была открыта %.1f с, собрано %.0f, кэш %s"
            % (left, time.time() - t0, state["дверь_открыта_с"], collected, after))

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

# Цели читаем С ПАНЕЛИ на каждом старте, а память — только запасной путь.
# После перерождения требования МЕНЯЮТСЯ (следующий уровень дороже и просит
# других брейнротов), и старт со старым списком — это круги вхолостую: бот
# караулит на ленте то, что уже не нужно. 31.08 пользователь сделал ребёрн
# руками, а бот после перезапуска пошёл искать прежних двух.
load_goals()
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
    # Требования перечитываем ЧАСТО: цель могут украсть с базы между кругами,
    # и тогда список недостающих меняется на ходу.
    if state["кругов"] and state["кругов"] % 5 == 0:
        read_goals()

say("прогон окончен: кругов %d, локов %d, покупок %d, ребёрнов %d, кэш %s, сбоев %d"
    % (state["кругов"], state["локов"], state["покупок"],
       state["ребёрнов"], state["кэш"], state["сбоев"]))
