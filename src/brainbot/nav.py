"""Навигация по ориентирам: как бот понимает, куда идти, и учится своему управлению.

Почему не глобальный оптический поток. Камера следует за персонажем: при ходьбе вперёд
картинка почти не меняется, потому что камера едет вместе с ним. Мерить сдвиг всего
кадра — значит мерить не то, и именно на этом мы получили ложный вывод «персонаж зажат,
w/a/s дают ноль».

Как правильно. Мерить смещение ОРИЕНТИРА относительно центра кадра. Спавн у базы всегда
один и тот же, ориентиры вокруг те же самые, и вопрос «куда идти» превращается в
«довести такую-то надпись до такой-то точки экрана» — а это замкнутый цикл, который
сам себя доводит и не требует точной калибровки.

Ориентиры:
    your_base   плашка YOUR BASE — крупная, ловится OCR увереннее всего
    collect     вывеска CASH MULTI / COLLECT ZONE — пад сбора кэша
    lock        промпт Lock Base
    conveyor    синяя лента конвейера — не текст, ловится по цвету
"""
from __future__ import annotations

import difflib
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from . import ocr
from .knowledge import Knowledge, signature
from .log import get

log = get("nav")

# Текстовые ориентиры: имя -> куски строк, по которым узнаём. Куски, а не целые
# слова: крупный стилизованный шрифт OCR корёжит (collect -> llect, multi -> lmylti).
# Для каждого — ещё и область кадра в долях (x0, y0, x1, y1), где он вообще может
# быть. Без этого «rebirth» ловится в заголовке лидерборда «Rebirths», а «shop» —
# в кнопке левого меню; оба раза это интерфейс, а не точка в мире, и идти туда некуда.
TEXT_LANDMARKS = {
    "your_base": (("your base", "yourbase"), (0.0, 0.0, 1.0, 1.0)),
    # ВАЖНО: это два РАЗНЫХ объекта, и мешать их в один ориентир нельзя.
    # Вывеска «CASH MULTI: xN» висит над базой крупно и целиком; надпись
    # «COLLECT ZONE» лежит на паду под ногами и наполовину закрыта персонажем.
    # Пока оба совпадали с именем `collect`, OCR отдавал то одну, то другую,
    # камера дёргалась между ними и наведение не сходилось за 12 шагов — при
    # том что каждая по отдельности видна прекрасно.
    # «CASH MULTI: xN» OCR разбирает не целиком: на живом кадре из всей вывески
    # прочиталось одно слово `cash`, а «MULTI» не распозналось вовсе. Поэтому
    # держим и голое `cash` — на экране больше нет ничего похожего (наличные в
    # HUD читаются как «$70,73m», без слова cash).
    "cash_multi": (("cash", "мульт"), (0.17, 0.10, 1.0, 0.75)),
    "collect": (("llect", "zone", "сбор", "зона"), (0.17, 0.20, 1.0, 0.90)),
    # NB: needle "lock" без "base" убрана сознательно. Когда база заперта, над головой
    # горит счётчик «Locked: 32s» — он всегда ровно в центре кадра, и ориентир
    # «lock» срабатывал на нём: наведение считало «уже смотрю на плиту» и шло вперёд
    # в пустоту. Замерено: четыре положения подряд lock@0.498 при повороте на 90°.
    "lock": (("lock base", "запереть"), (0.17, 0.20, 1.0, 0.85)),
    # интерфейс: кнопки левой колонки, кликаются, а не обходятся ногами
    "ui_rebirth": (("rebirth", "перерожд"), (0.0, 0.30, 0.18, 0.60)),
    "ui_shop": (("shop", "магазин"), (0.0, 0.30, 0.18, 0.60)),
    "ui_index": (("index", "индекс"), (0.0, 0.30, 0.18, 0.75)),
}

# Лента по цвету — НЕНАДЁЖНАЯ опора, и это знание дорого досталось.
#
# На событиях игра меняет оформление: дорожка и окружение перекрашиваются. В
# записях одного дня попадаются и зелёная трава с синей полосой ленты, и розовый
# пол, где лента занимает весь низ экрана другим оттенком. Любой фиксированный
# диапазон HSV на таком ломается, а сломается он молча — детектор просто вернёт
# «ленты нет», и бот будет стоять перед ней, как уже бывало.
#
# Устойчивая опора — ТЕКСТ. Имена брейнротов над товарами и слово Purchase не
# зависят от оформления вовсе. Поэтому цвет остаётся подсказкой (быстрой, когда
# совпал), а решение о том, где лента, принимается по подписям товаров.
CONVEYOR_HSV = ((100, 120, 60), (130, 255, 255))


@dataclass
class Spot:
    name: str
    x: int
    y: int
    weight: float = 0.0     # доля площади кадра; у текстовых ориентиров её нет


def _looks_like(text: str, needle: str, cutoff: float = 0.72) -> bool:
    """Похожа ли строка на эталон после нормализации кириллических двойников.

    Крупные игровые надписи OCR корёжит до неузнаваемости: «Lock Base» на живом
    кадре пришёл как «цое к в ase». Прямое вхождение подстроки такое не ловит —
    и бот стоял в трёх шагах от подписанной плиты, не видя её. После
    нормализации (той же, что чинит имена брейнротов) остаётся «oekbase» против
    «lockbase», и это уже 0.80 похожести.
    """
    from .brainrots import normalize
    a = normalize(text).replace(" ", "")
    b = normalize(needle).replace(" ", "")
    if len(a) < 4 or len(b) < 4:
        return False
    return difflib.SequenceMatcher(None, a, b).ratio() >= cutoff


def find_text_landmarks(frame: np.ndarray) -> dict[str, Spot]:
    h, w = frame.shape[:2]
    out: dict[str, Spot] = {}
    for text, xc, yc in ocr.lines(frame):
        for name, (needles, box) in TEXT_LANDMARKS.items():
            if name in out:
                continue
            hit = any(n in text for n in needles)
            if not hit:
                # Точного вхождения нет — пробуем нечётко, но только по длинным
                # эталонам: короткие вроде «lock» дают ложные совпадения.
                hit = any(_looks_like(text, n) for n in needles if len(n) >= 6)
            if not hit:
                continue
            x0, y0, x1, y1 = box
            if x0 * w <= xc <= x1 * w and y0 * h <= yc <= y1 * h:
                out[name] = Spot(name, xc, yc)
    return out


def world_landmarks(frame: np.ndarray, learned_hsv=None) -> dict[str, Spot]:
    """Только те, к которым можно подойти ногами — без кнопок интерфейса."""
    return {k: v for k, v in landmarks(frame, learned_hsv).items()
            if not k.startswith("ui_")}


def find_conveyor(frame: np.ndarray, learned_hsv=None) -> Spot | None:
    """Синяя лента конвейера.

    Ловим не «самое синее пятно»: у персонажа синяя куртка и синие волосы, и когда
    камера зумится внутрь, аватар занимает пол-экрана. Бот тогда честно «приближался
    к конвейеру», подходя к самому себе — проверено, именно так он и застрял.

    Отличаем по форме и месту: лента ДЛИННАЯ И ПЛОСКАЯ (ширина много больше высоты)
    и не проходит через центр кадра, где стоит аватар.
    """
    h, w = frame.shape[:2]
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    lo, hi = learned_hsv or CONVEYOR_HSV
    mask = cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8))
    # Вырезаем зону аватара — но ТОЛЬКО его, а не полкадра.
    #
    # Было (0.30..0.70, 0.25..1.0): стиралась вся середина и весь низ. На живом
    # кадре лента шла ровно по центру, на x 500..750 и y 260..380 — целиком
    # внутри этой заплатки. Детектор честно возвращал None, и бот «не видел»
    # ленту, стоя прямо перед ней. Персонаж занимает куда меньше: примерно
    # восьмую часть ширины и треть высоты, ниже середины кадра.
    cv2.rectangle(mask, (int(w * 0.44), int(h * 0.42)), (int(w * 0.57), int(h * 0.80)),
                  0, -1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))

    n, _, stats, cents = cv2.connectedComponentsWithStats(mask, 8)
    best, best_area = None, 0
    for i in range(1, n):
        area = int(stats[i, cv2.CC_STAT_AREA])
        bw = int(stats[i, cv2.CC_STAT_WIDTH])
        bh = int(stats[i, cv2.CC_STAT_HEIGHT])
        if area < 1500 or bh == 0 or bw == 0:
            continue
        # Лента ДЛИННАЯ И ТОНКАЯ. Проверять «ширина больше высоты» нельзя: при виде
        # сверху лента идёт по диагонали, и её рамка почти квадратная — так деталь
        # и терялась. Смотрим на два признака: тянется через изрядную часть кадра
        # и заполняет свою рамку слабо (у диагональной полосы так и есть).
        long_enough = max(bw, bh) >= 0.35 * w
        thin = area / float(bw * bh) < 0.62
        if not (long_enough and thin) and bw / bh < 1.8:
            continue
        if area > best_area:
            best, best_area = i, area
    if best is None:
        return None
    cx, cy = cents[best]
    return Spot("conveyor", int(cx), int(cy), weight=best_area / (w * h))


def _same_scene(a: np.ndarray, b: np.ndarray) -> float:
    """Насколько два кадра — один и тот же вид. Для поиска полного оборота."""
    ga = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)[120:600, 160:1120].astype(np.float32)
    gb = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY)[120:600, 160:1120].astype(np.float32)
    if ga.shape != gb.shape:
        return 0.0
    (dx, _), response = cv2.phaseCorrelate(ga, gb)
    # Совпадение — это сильный отклик ПРИ нулевом сдвиге: вид вернулся на место.
    return float(response) if abs(dx) < 40 else 0.0


def _scene_shift(before: np.ndarray, after: np.ndarray) -> float | None:
    """На сколько пикселей уехала вся сцена по горизонтали. None — не понять.

    Годится только для ПОВОРОТА камеры: он двигает кадр целиком. Для ходьбы не
    годится — камера едет за персонажем, и картинка почти не меняется.
    """
    a = cv2.cvtColor(before, cv2.COLOR_BGR2GRAY)[120:600, 160:1120].astype(np.float32)
    b = cv2.cvtColor(after, cv2.COLOR_BGR2GRAY)[120:600, 160:1120].astype(np.float32)
    if a.shape != b.shape:
        return None
    (dx, dy), response = cv2.phaseCorrelate(a, b)
    # Слабый отклик или заметная вертикаль — это не поворот, а смена сцены.
    if response < 0.02 or abs(dy) > abs(dx) * 0.8 or abs(dx) < 3:
        log.debug("сдвиг отброшен: dx=%.1f dy=%.1f отклик=%.3f", dx, dy, response)
        return None
    return float(dx)


def path_direction(frame: np.ndarray, learned_hsv=None) -> tuple[int, int] | None:
    """Куда ведёт синяя дорожка: (смещение по x, «есть ли она под ногами»).

    Целиться в ЦЕНТР дорожки бессмысленно — он приводит на её середину, а плита
    лока в дальнем конце. Нужна не точка, а НАПРАВЛЕНИЕ: берём дорожку в верхней
    половине кадра (то, что впереди) и в нижней (то, что под ногами), и смотрим,
    куда её верхняя часть смещена относительно нижней. Это и есть поворот, на
    который надо довернуть, чтобы идти ВДОЛЬ, а не поперёк.

    Возвращает None, если дорожки не видно.
    """
    h, w = frame.shape[:2]
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    lo, hi = learned_hsv or CONVEYOR_HSV
    mask = cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8))
    cv2.rectangle(mask, (int(w * 0.44), int(h * 0.42)), (int(w * 0.57), int(h * 0.80)),
                  0, -1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))

    # Берём ТУ САМУЮ дорожку, на которой стоим, а не крупнейшее синее пятно.
    #
    # Синего в кадре несколько: дорожка под ногами, тёмные окна базы, лента
    # снаружи, базы соседей. Прежняя мера считала их все разом, и «направление»
    # прыгало между разными объектами — на соседних кадрах одного прохода центр
    # уезжал с x=1176 на 285 и обратно на 632. Отсюда и скачки знака, которые я
    # принимал за автоколебание наведения.
    #
    # Дорожка, по которой идёшь, отличается однозначно: она касается НИЗА кадра
    # рядом с центром. По ней и работаем.
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    foot = labels[int(h * 0.93):, int(w * 0.35):int(w * 0.65)]
    ids, counts = np.unique(foot[foot > 0], return_counts=True)
    if ids.size == 0:
        return None
    ours = int(ids[counts.argmax()])
    own = (labels == ours).astype(np.uint8)

    def centre(y0, y1):
        band = own[int(h * y0):int(h * y1)]
        xs = np.nonzero(band.any(axis=0))[0]
        if xs.size < 15:
            return None
        return int(xs.mean())

    far = centre(0.40, 0.62)        # куда дорожка уходит вдаль
    near = centre(0.80, 0.98)       # где она под ногами
    if far is None or near is None:
        return None
    # Направление — разница между дальней и ближней частью ОДНОЙ дорожки.
    return far - near, True


def sample_belt_hsv(frame: np.ndarray) -> list | None:
    """Подсмотреть цвет ленты по кадру, где мы стоим прямо на ней.

    Вызывается в момент, когда виден промпт покупки: значит товар рядом, а под
    ногами — сама дорожка. Берём преобладающий насыщенный оттенок в нижней
    полосе кадра и строим вокруг него диапазон. Так детектор переживает смену
    оформления на событиях, вместо того чтобы молча перестать видеть ленту.
    """
    h, w = frame.shape[:2]
    band = cv2.cvtColor(frame[int(h * 0.80):int(h * 0.97),
                              int(w * 0.15):int(w * 0.85)], cv2.COLOR_BGR2HSV)
    sat = band[:, :, 1] > 90        # только насыщенное: серый пол не считаем
    if sat.sum() < band[:, :, 0].size * 0.15:
        return None
    hues = band[:, :, 0][sat]
    hue = int(np.median(hues))
    lo = [max(0, hue - 12), 90, 50]
    hi = [min(179, hue + 12), 255, 255]
    return [lo, hi]


def red_lasers(frame: np.ndarray) -> float:
    """Доля кадра, занятая красными решётками лока. Признак «база заперта».

    Игра пишет «You locked your base for N Seconds!», но эту надпись мы не
    поймали ни разу за день: она держится мгновение и стилизована. Зато лок
    видно — на входе загораются красные решётки, и это цвет, а не текст.

    Красный в HSV лежит на стыке круга, поэтому берём два куска: около 0 и
    около 180. Считаем только насыщенное и яркое, чтобы не поймать розовый пол
    оформления или кирпич.
    """
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    lo1 = cv2.inRange(hsv, np.array((0, 130, 110), np.uint8),
                      np.array((10, 255, 255), np.uint8))
    lo2 = cv2.inRange(hsv, np.array((170, 130, 110), np.uint8),
                      np.array((179, 255, 255), np.uint8))
    mask = cv2.bitwise_or(lo1, lo2)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    return float(mask.mean() / 255.0)


def find_goods(frame: np.ndarray) -> Spot | None:
    """Ближайший ТОВАР на ленте — по подписи с именем брейнрота.

    Лучший ориентир для похода за покупкой. Синюю ленту легко спутать с одеждой
    персонажа, а подпись над товаром — это имя из справочника, и его ни с чем не
    спутаешь. Видно её издалека: «noobini pizzanini», «talpa di fero», «trulimero
    trulicina» читаются с другого конца площадки.

    Из нескольких выбираем самый нижний в кадре — он ближе всех к нам.
    """
    from .brainrots import catalog
    cat = catalog()
    best = None
    for text, xc, yc in ocr.lines(frame):
        if len(text) < 5 or "$" == text.strip():
            continue
        if cat.match(text) is None:
            continue
        if best is None or yc > best.y:
            best = Spot("goods", xc, yc)
    return best


# Кнопка закрытия игровых окон: синий квадрат с белым «X» в правом верхнем углу
# панели. Одинакова у Shop, Index, Rebirth и прочих.
CLOSE_HSV = ((100, 120, 80), (130, 255, 255))


def find_close_button(frame: np.ndarray) -> Spot | None:
    """Крестик закрытия окна — по цвету и форме, а не по тексту.

    OCR белую «X» на синем не читает вовсе (проверено на окне REBIRTH: коротких
    строк в кадре не нашлось ни одной), поэтому ищем сам синий квадрат: примерно
    равные стороны, скромная площадь, верхняя половина кадра.
    """
    h, w = frame.shape[:2]
    hsv = cv2.cvtColor(frame[: int(h * 0.6)], cv2.COLOR_BGR2HSV)
    lo, hi = CLOSE_HSV
    mask = cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8))
    # Левая колонка — это меню игры (Shop, Rebirth, Index, Codes). Иконка Shop
    # синяя и квадратная, и без этой отсечки бот принимал её за крестик закрытия
    # и жал раз за разом, открывая магазин вместо того, чтобы идти.
    # Крестик закрытия всегда в правом верхнем углу панели. Всё остальное —
    # ложные срабатывания: слева синяя иконка Shop в меню игры, в центре —
    # синие элементы самой сцены (лента, лазеры баз).
    mask[:, : int(w * 0.55)] = 0
    mask[int(h * 0.45):, :] = 0
    n, _, stats, cents = cv2.connectedComponentsWithStats(mask, 8)
    best, best_white = None, 0.0
    for i in range(1, n):
        area = int(stats[i, cv2.CC_STAT_AREA])
        bw = int(stats[i, cv2.CC_STAT_WIDTH])
        bh = int(stats[i, cv2.CC_STAT_HEIGHT])
        if not (600 < area < 8000) or bh == 0:
            continue
        if not (0.7 < bw / bh < 1.4):
            continue
        # Синего квадрата МАЛО. Стены базы застеклены синими окнами, и они
        # проходят и по площади, и по пропорциям: 30.08 вечером бот принимал
        # окно за крестик, «закрывал» его щелчком в мир и отменял лок —
        # четыре круга подряд, ни одного успешного запирания.
        #
        # Отличает крестик БЕЛАЯ Х внутри. Замер по кадрам: у настоящего
        # крестика окна REBIRTH доля светлых точек в середине 0.17, у всех
        # ложных (окна базы, синие элементы сцены) — ровно 0.00.
        x0, y0 = int(stats[i, cv2.CC_STAT_LEFT]), int(stats[i, cv2.CC_STAT_TOP])
        box = frame[y0:y0 + bh, x0:x0 + bw]
        if box.size == 0:
            continue
        gray = cv2.cvtColor(box, cv2.COLOR_BGR2GRAY)
        inner = gray[int(bh * 0.2):int(bh * 0.8), int(bw * 0.2):int(bw * 0.8)]
        if inner.size == 0:
            continue
        white = float((inner > 190).mean())
        # Сверху — чтобы не принять за крестик белую плашку целиком.
        if not (0.05 <= white <= 0.60):
            continue
        # Белого мало — надо, чтобы он лежал КРЕСТОМ. Белый текст на синем
        # проходит проверку по доле: надпись «CASH MULTI: x2» на синей дорожке
        # базы принималась за крестик, бот «закрывал» её щелчком в мир и
        # отменял лок кругами (прогон 04:14, десяток попыток подряд).
        # У настоящей Х точки лежат близко к диагоналям квадрата.
        ys, xs = np.nonzero(inner > 190)
        if len(xs) < 20:
            continue
        ih, iw = inner.shape
        u = xs / max(1, iw - 1)
        v = ys / max(1, ih - 1)
        # расстояние до ближайшей из двух диагоналей единичного квадрата
        d = np.minimum(np.abs(u - v), np.abs(u + v - 1.0)) / 1.41421356
        if float(d.mean()) > 0.16:
            continue
        # У Х белое есть и В ЦЕНТРЕ — линии пересекаются. Оконные рамы базы
        # (белый кант на синем стекле) диагонали проходят, а середину нет:
        # именно они давали семь ложных срабатываний из 156 кадров стенда.
        ch, cw = inner.shape
        core = inner[int(ch * 0.30):int(ch * 0.70), int(cw * 0.30):int(cw * 0.70)]
        if core.size == 0 or float((core > 190).mean()) < 0.05:
            continue
        if white > best_white:
            cx, cy = cents[i]
            best = Spot("close", int(cx), int(cy), weight=area / (w * h))
            best_white = white
    return best


def landmarks(frame: np.ndarray, learned_hsv=None) -> dict[str, Spot]:
    out = find_text_landmarks(frame)
    conv = find_conveyor(frame, learned_hsv)
    if conv:
        out["conveyor"] = conv
    goods = find_goods(frame)
    if goods:
        out["goods"] = goods
    return out


@dataclass
class Navigator:
    """Умеет мерить своё управление и подводить ориентир в нужную точку кадра."""

    farmer: object                      # Farmer, но без импорта — циклическая зависимость
    axes: dict = field(default_factory=dict)   # клавиша -> (dx, dy) на секунду удержания
    _last_key: str = ""
    # сколько пикселей проезжает ориентир на единицу сдвига мыши;
    # стартовое значение грубое, дальше уточняется по факту
    px_per_mouse: float = 0.6
    _kb: Knowledge | None = None

    @property
    def kb(self) -> Knowledge:
        """Память между запусками. Подгружается один раз и сеет собой таблицу.

        Без этого бот на каждом старте заново мерил, куда едет `w`, — а замер
        стоит десятка удержаний и всё равно врёт первые шаги.
        """
        if self._kb is None:
            self._kb = Knowledge.load()
            if self._kb.axes and not self.axes:
                self.axes = {k: tuple(v) for k, v in self._kb.axes.items()}
                log.info("таблица управления взята из памяти: %s", self.axes)
            if self._kb.px_per_mouse:
                self.px_per_mouse = self._kb.px_per_mouse
                log.info("чувствительность из памяти: %.3f", self.px_per_mouse)
        return self._kb

    # --- восприятие ---

    def see(self, with_ui: bool = False) -> dict[str, Spot]:
        frame = self.farmer.frame()
        hsv = self.kb.belt_hsv
        return landmarks(frame, hsv) if with_ui else world_landmarks(frame, hsv)

    def snapshot(self) -> tuple[np.ndarray, dict[str, Spot]]:
        """Кадр и разобранные по нему ориентиры за один захват.

        Отдельно от `see`, потому что в подходе кадр нужен дважды: по нему же
        меряется, сдвинулась ли вообще картинка. Раньше цикл брал захват трижды
        за шаг — на разных кадрах, то есть сравнивал несравнимое.
        """
        frame = self.farmer.frame()
        return frame, world_landmarks(frame, self.kb.belt_hsv)

    def where(self, name: str) -> Spot | None:
        return self.see().get(name)

    # --- калибровка ---

    def calibrate(self, hold: float = 0.5, tries: int = 2) -> dict:
        """Выучить, на сколько пикселей уезжают ориентиры от каждой клавиши.

        Меряем по ВСЕМ видимым ориентирам сразу и берём медиану — один ориентир может
        уйти за кадр или быть перекрыт, но все сразу вряд ли.
        """
        if not self._ensure_free("калибровка"):
            return {}
        hand = self.farmer.hand
        axes: dict[str, tuple[float, float]] = {}
        for key, back in (("w", "s"), ("s", "w"), ("a", "d"), ("d", "a")):
            samples: list[tuple[float, float]] = []
            for _ in range(tries):
                before = self.see()
                if not before:
                    log.warning("клавиша %s: ориентиров не видно, пропускаю", key)
                    break
                hand.hold(key, hold)
                time.sleep(0.45)
                after = self.see()
                shifts = [(after[n].x - before[n].x, after[n].y - before[n].y)
                          for n in before if n in after]
                hand.hold(back, hold)
                time.sleep(0.45)
                if shifts:
                    dx = float(np.median([s[0] for s in shifts]))
                    dy = float(np.median([s[1] for s in shifts]))
                    samples.append((dx, dy))
            if samples:
                dx = float(np.median([s[0] for s in samples])) / hold
                dy = float(np.median([s[1] for s in samples])) / hold
                if (dx * dx + dy * dy) ** 0.5 >= 8.0:      # px/с, ниже — шум
                    axes[key] = (dx, dy)
                    log.info("клавиша %s: ориентиры едут на (%.0f, %.0f) px/с", key, dx, dy)
                else:
                    log.warning("клавиша %s: ориентиры почти не двигаются (%.0f, %.0f) px/с",
                                key, dx, dy)
        self.axes = axes
        return axes

    # --- локализация: где я и куда смотрю ---

    # Окна, которые перехватывают мышь. Поворот камеры делается зажатой ПКМ с
    # протяжкой, и пока поверх висит диалог, протяжка уходит в интерфейс, а не в
    # мир. Камера при этом не двигается ВООБЩЕ — а замер этого не замечает и
    # честно пишет числа.
    BLOCKERS = ("respawn your character", "don't respawn", "are you sure")

    def blocked(self) -> str | None:
        """Что мешает мерить прямо сейчас. None — экран чист."""
        return self.farmer.sees(*self.BLOCKERS)

    def _ensure_free(self, what: str) -> bool:
        """Убрать перехватчик мыши перед замером. False — мерить нельзя.

        Появилось не от аккуратности. Ночные `sweep` и «три респавна» сняты с
        открытым диалогом подтверждения респавна: за восемь шагов «полного
        оборота» конвейер простоял на x=90..120, то есть камера не повернулась
        ни разу. На этих числах стоял вывод «разворот каждый раз разный» — и он
        оказался ни на чём не основан. Молчаливо неверный замер хуже отказа.
        """
        blocker = self.blocked()
        if blocker is None:
            return True
        log.warning("%s: поверх висит %r — мышь до мира не дойдёт, закрываю",
                    what, blocker)
        self.farmer.dismiss_modals(force=True)
        time.sleep(0.6)
        blocker = self.blocked()
        if blocker is None:
            return True
        log.error("%s ОТМЕНЁН: на экране %r, замер был бы враньём", what, blocker)
        self.farmer.shot("fail_blocked_measure")
        return False

    def sweep(self, steps: int = 8, dx: int | None = None) -> dict:
        """Обвести камерой вокруг и записать, что где видно.

        Точка спавна одна и та же в пределах ОДНОГО захода на сервер; при новом
        заходе игра выдаёт другую базу из ряда, и всё, что привязано к её месту,
        перестаёт быть верным. Разворот после респавна тоже не гарантирован.
        Поэтому «где я» решается не предположением, а осмотром.
        """
        if not self._ensure_free("осмотр"):
            return {}
        dx = dx or max(1, self.full_turn // steps)
        found: dict[str, tuple[int, int, int]] = {}     # имя -> (шаг, x, y)
        for i in range(steps):
            for name, spot in self.see().items():
                if name not in found:
                    found[name] = (i, spot.x, spot.y)
            self.farmer.hand.look(dx, 0)
            time.sleep(0.5)
        log.info("осмотр: нашёл %s", {k: (v[1], v[2]) for k, v in found.items()})
        return found

    # Поле зрения камеры по горизонтали, градусов. Нужно, чтобы перевести
    # промах В ПИКСЕЛЯХ в угол, а угол — в единицы мыши через полный оборот.
    FOV_DEGREES = 70.0

    # Пропорции, при которых подобрано FOV_DEGREES. Roblox задаёт поле зрения
    # ПО ВЕРТИКАЛИ, поэтому горизонтальный обзор зависит от ширины окна: сузил
    # окно — сузился и обзор. Замер сделан на 1280x720.
    BASE_ASPECT = 1280.0 / 720.0

    def effective_fov(self, width: int, height: int) -> float:
        """Горизонтальный обзор для ТЕКУЩИХ пропорций окна.

        Без этой поправки смена разрешения молча ломает наведение: после
        переподключения мониторов окно стало 1078x720 вместо 1280x720, обзор
        сузился примерно на десятую часть, и бот начал переворачивать на
        столько же. Величина в единицах мыши на полный оборот от разрешения не
        зависит и переизмерения не требует — а вот эта требует.
        """
        if not height:
            return self.FOV_DEGREES
        return self.FOV_DEGREES * (width / float(height)) / self.BASE_ASPECT

    def units_for_pixels(self, px: float) -> int:
        """Сколько единиц мыши нужно, чтобы цель сместилась на `px` пикселей.

        Раньше это считалось через «пикселей на единицу», и оно врало: величина
        ЗАВИСИТ ОТ СЦЕНЫ. Замеры в двух местах одной базы дали 0.354 и 0.185 —
        близкие объекты при повороте смещаются сильнее далёких, и корреляция
        честно отвечает по-разному. Строить на этом наведение нельзя.

        Угол же от сцены не зависит: доля кадра -> доля поля зрения -> доля
        полного оборота, который измерен прямо (`op turn`).
        """
        box = self.farmer.window.client_box()
        width = box.width or 1280
        degrees = (px / width) * self.effective_fov(width, box.height or 720)
        return int(degrees / 360.0 * self.full_turn)

    @property
    def full_turn(self) -> int:
        """Единиц мыши на полный оборот. Измеряется `op turn`, лежит в памяти.

        Раньше по коду было зашито 5600 (восемь шагов по 700). Замер дал 7000 —
        то есть осмотр «на полный круг» покрывал лишь 80% окружности, и цель
        могла остаться в непросмотренной четверти. Отсюда и «ориентир не нашёлся
        за полный оборот» при том, что он был.
        """
        return int(self.kb.units_per_turn or 7000)

    def find(self, name: str, sweeps: int = 8) -> bool:
        """Просто НАЙТИ ориентир: крутить камеру, пока он не окажется в кадре.

        Центровать не нужно. Подход и так работает замкнутым циклом от любого
        положения ориентира в кадре, а точная центровка мышью капризна:
        чувствительность плавает, и наведение то недокручивает, то проскакивает.
        Требовать от камеры меньше — значит меньше и ломаться.
        """
        step = max(1, self.full_turn // sweeps)
        for i in range(sweeps):
            if self.where(name) is not None:
                if i:
                    log.info("ориентир %r нашёлся за %s поворотов", name, i)
                return True
            self.farmer.hand.look(step, 0)
            time.sleep(0.45)
        log.warning("ориентир %r не нашёлся за полный оборот", name)
        return False

    def face(self, name: str, tol: int = 70, max_iter: int = 12) -> bool:
        """Развернуть камеру на ориентир: подвести его к центру по горизонтали.

        Чувствительность мыши в игре нам неизвестна (в настройках стоит 0.2, но во
        что это превращается на экране — вопрос), поэтому коэффициент не задаём, а
        ВЫУЧИВАЕМ: после каждого поворота смотрим, на сколько пикселей уехал
        ориентир на единицу сдвига мыши, и правим оценку. Первый шаг осторожный.
        """
        cx = self.farmer.window.client_box().width // 2
        sweeps = 0
        for step in range(max_iter):
            spot = self.where(name)
            if spot is None:
                if sweeps >= 8:
                    log.warning("ориентир %r не нашёлся за полный оборот", name)
                    return False
                sweeps += 1
                self.farmer.hand.look(self.full_turn // 8, 0)
                time.sleep(0.45)
                continue
            err = spot.x - cx
            if abs(err) <= tol:
                log.info("камера смотрит на %r (x=%s, центр %s, за %s шагов)",
                         name, spot.x, cx, step)
                return True
            # сколько мыши нужно на нужное число пикселей, по текущей оценке
            move = int(-err / self.px_per_mouse)
            move = max(-1400, min(1400, move))
            if abs(move) < 40:
                move = 40 if move >= 0 else -40
            self.farmer.hand.look(move, 0)
            time.sleep(0.45)
            after = self.where(name)
            if after is not None:
                got = after.x - spot.x
                if abs(got) > 8 and abs(move) > 0:
                    k = got / move
                    # оценка держится скользящим средним и не даёт себя обнулить
                    self.px_per_mouse = max(0.02, min(5.0,
                                                      self.px_per_mouse * 0.5 + abs(k) * 0.5))
                    log.info("наведение на %r: промах %+d px, мышь %+d -> ушло %+d "
                             "(оценка %.2f px на единицу)", name, err, move, got,
                             self.px_per_mouse)
        log.warning("не навёлся на %r за %s шагов", name, max_iter)
        return False

    def calibrate_turn(self, step: int = 350, max_units: int = 12000) -> int | None:
        """Сколько единиц мыши в ПОЛНОМ ОБОРОТЕ. Отсюда любой угол — доля.

        Подсказка пользователя, и она точнее прежнего замера. Пиксели на единицу
        мыши — величина косвенная: зависит от поля зрения, меняется от кадра к
        кадру, за вечер гуляла между 0.31 и 0.57. А полный оборот — абсолютная
        величина, и поймать его можно без всяких ориентиров: крутим камеру
        шагами и сравниваем картинку с исходной. На 360° сцена совпадает сама с
        собой, и совпадение видно по отклику корреляции.

        После этого «повернуться на 90°» — это просто четверть найденного числа,
        и повороты из уроков становятся воспроизводимыми.
        """
        self.farmer.close_panels()
        time.sleep(0.4)
        start = self.farmer.frame()
        best, best_score, spent = None, 0.0, 0
        seen: list[tuple[int, float]] = []
        while spent < max_units:
            self.farmer.hand.look(step, 0)
            time.sleep(0.35)
            spent += step
            score = _same_scene(start, self.farmer.frame())
            seen.append((spent, score))
            # Ищем возврат к исходному виду, но не в самом начале: первые шаги
            # ещё похожи на старт просто потому, что мы недалеко ушли.
            if spent > max_units * 0.4 and score > best_score:
                best, best_score = spent, score
        if best is None or best_score < 0.05:
            log.warning("полный оборот не пойман, лучшее совпадение %.3f", best_score)
            log.info("замеры: %s", [(u, round(v, 3)) for u, v in seen])
            return None
        self.kb.units_per_turn = best
        self.kb.touch()
        self.kb.save(force=True)
        log.info("полный оборот: %s единиц мыши (совпадение %.3f). "
                 "90° = %s, 180° = %s", best, best_score, best // 4, best // 2)
        return best

    def calibrate_mouse(self, amount: int = 500, tries: int = 3) -> float | None:
        """Замерить чувствительность мыши напрямую — по сдвигу всей сцены.

        Косвенный замер по ориентиру давал разброс 0.31..0.57 между заходами:
        надпись бывает распознана криво, перекрыта или вовсе принадлежит соседней
        базе. Здесь ориентиры не нужны вовсе. Поворачиваем камеру на известное
        число единиц и меряем, на сколько пикселей уехала КАРТИНКА — фазовой
        корреляцией, той же, которой достаём повороты из записи прохода.

        Каждый замер делается туда и обратно: так камера остаётся примерно там
        же, где была, а два измерения одного угла ловят случайный промах.
        """
        # Перед замером — на спавн и убрать панели.
        #
        # Мерить можно только там, где есть что мерить. Зажатый в углу персонаж
        # упирает камеру в стену, кадр забит одной текстурой, и поворот её не
        # меняет — замер честно отвечает «сцена не сдвинулась», хотя ввод дошёл.
        # Открытая таблица игроков даёт то же самое, плюс перехватывает мышь.
        self.farmer.to_reference(aim=False)
        self.farmer.close_panels()
        time.sleep(0.4)

        samples: list[float] = []
        for i in range(tries):
            for direction in (1, -1):
                before = self.farmer.frame()
                self.farmer.hand.look(amount * direction, 0)
                time.sleep(0.45)
                after = self.farmer.frame()
                dx = _scene_shift(before, after)
                if dx is None:
                    # Показать, ЧТО именно не понравилось: иначе «сцена не
                    # сдвинулась» ничего не объясняет.
                    import cv2 as _cv
                    import numpy as _np
                    ga = _cv.cvtColor(before, _cv.COLOR_BGR2GRAY)[120:600, 160:1120]
                    gb = _cv.cvtColor(after, _cv.COLOR_BGR2GRAY)[120:600, 160:1120]
                    (rx, ry), resp = _cv.phaseCorrelate(ga.astype(_np.float32),
                                                        gb.astype(_np.float32))
                    log.info("замер %s (%+d): не засчитан — dx=%.1f dy=%.1f отклик=%.3f",
                             i + 1, amount * direction, rx, ry, resp)
                    continue
                k = abs(dx) / amount
                if 0.02 < k < 5.0:
                    samples.append(k)
                    log.info("замер %s: поворот %+d -> сцена уехала на %+.0f px "
                             "(%.3f px на единицу)", i + 1, amount * direction, dx, k)
        if not samples:
            log.warning("чувствительность измерить не вышло: сцена не сдвинулась")
            return None
        samples.sort()
        value = samples[len(samples) // 2]
        self.px_per_mouse = value
        self.kb.remember_mouse(value, direct=True)
        self.kb.save(force=True)
        log.info("чувствительность мыши: %.3f px на единицу (по %s замерам)",
                 value, len(samples))
        return value

    def head_to(self, name, stop_when=None, legs: int = 10,
                leg: float = 1.0, tol: int = 90) -> bool:
        """Идти к ориентиру, ЦЕЛЯСЬ КАМЕРОЙ, а не подбирая клавишу.

        Модель движения в Roblox: направление задаёт камера, а не мир. При
        включённом шифт-локе персонаж всегда развёрнут туда же, куда смотрит
        камера, и `w` означает ровно «вперёд по взгляду». Значит дорога к любой
        цели — это «навести камеру и держать w», и никакая таблица «клавиша →
        направление на экране» для этого не нужна.

        Почему так лучше прежнего подхода. Та таблица описывала связь, которой
        не существует: она зависит от угла камеры, а он меняется. Отсюда и
        вечная нестабильность замеров, и вид сверху с дальним зумом — подпорка,
        которая делала направления «стабильными на экране». С камерой за спиной
        подпорка не нужна.

        Цикл: навёлся -> прошёл короткий отрезок -> проверил, не пришёл ли, и
        не упёрся ли -> навёлся заново (за отрезок цель успевает сместиться).
        """
        # Целей может быть несколько, по убыванию дальности видимости. Лента
        # ловится по цвету и видна через полкарты; подпись над товаром читается
        # только вблизи. Замерено: с базы `goods` не виден вовсе, и подход по
        # нему сдавался на первом же отрезке, хотя лента была прямо по курсу.
        names = (name,) if isinstance(name, str) else tuple(name)
        cx = self.farmer.window.client_box().width // 2
        lost = False
        stuck = 0
        for i in range(legs):
            if stop_when is not None and stop_when():
                log.info("пришли: признак цели появился на отрезке %s", i)
                return True
            seen = self.see()
            target = next((n for n in names if n in seen), None)
            spot = seen.get(target) if target else None
            if spot is None:
                # Потерять цель у самого носа — нормально: подпись над товаром
                # уходит за верх кадра, когда подходишь вплотную. Замерено: два
                # отрезка шли верно, на третьем ориентир пропал, бот крутил
                # полный оборот и сдавался — а до ленты оставалось несколько
                # шагов ПРЯМО. Поэтому сначала проходим ещё отрезок вперёд и
                # только потом оглядываемся.
                if not lost:
                    lost = True
                    log.info("отрезок %s: цели %s пропали — иду вперёд вслепую",
                             i + 1, list(names))
                    self.farmer.trail_hold("w", leg)
                    time.sleep(0.35)
                    continue
                if not any(self.find(n, sweeps=8) for n in names):
                    log.warning("ни один из ориентиров %s не нашёлся", list(names))
                    return bool(stop_when and stop_when())
                seen = self.see()
                target = next((n for n in names if n in seen), None)
                spot = seen.get(target) if target else None
                if spot is None:
                    return bool(stop_when and stop_when())
            else:
                lost = False
            # Один доворот за отрезок — и всё. Замкнутый цикл здесь не нужен:
            # цикл и есть сама ходьба, следующий отрезок померит заново.
            #
            # Раньше тут вызывался `face`, и это была главная трата времени:
            # 24 попытки навестись за поход, каждая до 12 итераций с OCR, по
            # 25-30 секунд на отрезок — шесть минут вместо полутора. Причём ни
            # одна не сходилась, и не могла: лента это длинная диагональная
            # полоса, её центроид гуляет по всей длине, центрировать такое
            # бессмысленно. Достаточно повернуться В СТОРОНУ цели.
            err = spot.x - cx
            if abs(err) > tol:
                # Доворачиваем на ПОЛОВИНУ промаха, а не на весь.
                #
                # Полный доворот верен для точки, но цели у нас крупные: синяя
                # дорожка и лента тянутся через полкадра, и при повороте в вид
                # входит другой их участок — центр снова оказывается сбоку.
                # Замерено: промах +337 -> доворот -933 -> промах +430 -> -1190,
                # знак скачет каждый отрезок, и бот перелетает цель раз за разом.
                # Половина шага гасит качание: за два-три отрезка сходится, а
                # перелететь не даёт.
                move = -self.units_for_pixels(err * 0.5)
                move = max(-1800, min(1800, move))
                if abs(move) < 40:
                    move = 40 if move >= 0 else -40
                self.farmer.hand.look(move, 0)
                time.sleep(0.35)
                # Уточнять чувствительность НА ХОДУ можно только пока нет
                # прямого замера. Иначе бот сам портит точное число: оценка по
                # смещению ориентира накапливается скользящим средним и
                # разгоняется. В логе это выглядело так — при калиброванных
                # 0.354 доворот на промах +451 составил -89 вместо -637, то есть
                # в ходу значение уехало до ~2.5. Прямой замер (`op mouse`,
                # `op turn`) даёт одно и то же в шести пробах, ему и верим.
                if self.kb.units_per_turn:
                    continue_update = False
                else:
                    continue_update = True
                after = self.see().get(target) if continue_update else None
                if after is not None and abs(move) > 0:
                    got = after.x - spot.x
                    if abs(got) > 8:
                        self.px_per_mouse = max(0.02, min(5.0,
                                                self.px_per_mouse * 0.5 + abs(got / move) * 0.5))
                        self.kb.remember_mouse(self.px_per_mouse)
                        self.kb.save()
                log.info("отрезок %s: доворот на %+d (промах был %+d)", i + 1, move, err)

            before = self.farmer.frame()
            self.farmer.trail_hold("w", leg)
            time.sleep(0.35)
            after = self.farmer.frame()

            moved = self.farmer._moved(before, after)
            if moved < 0.004:
                stuck += 1
                log.info("отрезок %s: картинка не изменилась (%.4f) — упёрлись (%s раз)",
                         i + 1, moved, stuck)
                if stuck >= 2:
                    # Записываем упор ЗДЕСЬ, с подписью текущего места: пока
                    # что-то видно, запись применима. Без этого бот повторял
                    # один и тот же неудачный шаг на каждом заходе.
                    sig = signature(seen)
                    if target:
                        self.kb.note_wall(sig, "w")
                        self.kb.save(force=True)
                    # Второй упор подряд — значит обходы не помогают. Дальше
                    # продавливать бессмысленно: перерождаемся и отдаём заход
                    # наверх, пусть начнёт из чистой точки.
                    self.farmer.unstick(f"дважды подряд у цели {list(names)}")
                    return bool(stop_when and stop_when())
                # Первая ступень: прыжок снимает мелкие бортики, шаг вбок
                # обходит угол. Целимся заново на следующем витке.
                self.farmer.hand.jump()
                time.sleep(0.5)
                side = "d" if i % 2 == 0 else "a"
                self.farmer.trail_hold(side, 0.8)
                time.sleep(0.3)
            else:
                stuck = 0
                log.info("отрезок %s: прошёл %.1f с, картинка %.3f", i + 1, leg, moved)
        ok = bool(stop_when and stop_when())
        log.info("к %s: %s за %s отрезков", list(names),
                 "дошёл" if ok else "не дошёл", legs)
        return ok

    def goto(self, name: str, tol: int = 45) -> bool:
        """Развернуться на ориентир и подойти к нему."""
        return self.face(name) and self.approach(name, tol=tol)

    # --- движение ---

    def key_for(self, want_dx: float, want_dy: float,
                exclude: set | None = None,
                min_hold: float = 0.0,
                min_score: float = 0.3) -> tuple[str, float] | None:
        """Клавиша, двигающая ориентир в нужную сторону, и её «скорость» в px/с.

        Хотим, чтобы ориентир поехал в (want_dx, want_dy) — берём клавишу, чей
        замеренный вектор смотрит туда же.

        `min_hold` — самое короткое удержание, которое персонаж вообще
        отрабатывает (замерено: 0.3 с даёт ровно нулевое смещение). Клавиша, чей
        МИНИМАЛЬНЫЙ шаг перелетает остаток по её оси, отбрасывается: по этой оси
        мы уже на месте, а нажатие только качнёт обратно. Без этого подход в
        конце вилял `a`/`d`/`a`/`d`, каждый раз проскакивая цель.
        """
        if not self.axes:
            return None
        norm = (want_dx ** 2 + want_dy ** 2) ** 0.5 or 1.0
        wx, wy = want_dx / norm, want_dy / norm
        best, best_score, best_speed = None, 0.0, 0.0
        for key, (dx, dy) in self.axes.items():
            if exclude and key in exclude:
                continue
            speed = (dx * dx + dy * dy) ** 0.5
            if speed < 1:
                continue
            if min_hold:
                need = (dx * want_dx + dy * want_dy) / speed
                if need < speed * min_hold * 0.5:
                    continue
            score = (dx * wx + dy * wy) / speed
            if score > best_score:
                best, best_score, best_speed = key, score, speed
        if best is None or best_score < min_score:
            return None
        return best, best_speed

    def _update_axis(self, key: str, dx: float, dy: float, hold: float,
                     alpha: float = 0.5) -> None:
        """Подправить таблицу по факту сделанного шага (скользящее среднее).

        Начальная калибровка может врать: камера доворачивается, персонаж скользит,
        часть ориентиров уходит за кадр. Поэтому таблица не догма, а гипотеза —
        каждый реальный шаг её уточняет.
        """
        if hold <= 0:
            return
        nx, ny = dx / hold, dy / hold
        if key in self.axes:
            ox, oy = self.axes[key]
            self.axes[key] = (ox * (1 - alpha) + nx * alpha, oy * (1 - alpha) + ny * alpha)
        else:
            self.axes[key] = (nx, ny)
        # Выученное сразу уходит в память: следующий запуск начнёт не с нуля.
        # Запись дросселируется внутри save(), на каждый шаг файл не пишется.
        self.kb.axes = {k: list(v) for k, v in self.axes.items()}
        self.kb.px_per_mouse = self.px_per_mouse
        self.kb.touch()
        self.kb.save()

    def approach(self, name: str, target: tuple[int, int] | None = None,
                 tol: int = 45, max_steps: int = 22,
                 step_hold: float = 0.55, stop_when=None) -> bool:
        """Подвести ориентир `name` в точку `target`. Замкнутый цикл с самообучением.

        Шаг короткий и одинаковый, а не рассчитанный по таблице: так ошибка таблицы
        не превращается в промах через полкарты. Но и не слишком короткий: удержание
        меньше полусекунды персонаж отрабатывает как топтание на месте — замерено,
        0.3 с давали ровно нулевое смещение ориентира. После каждого шага смотрим, куда
        ориентир поехал НА САМОМ ДЕЛЕ, и правим таблицу. Если стало хуже — эта
        клавиша для этого направления не годится, пробуем следующую.

        target=None — низ центра кадра: там ориентир оказывается, когда персонаж
        стоит вплотную к нему.
        """
        b = self.farmer.window.client_box()
        target = target or (b.width // 2, int(b.height * 0.72))
        tried_bad: set[str] = set()
        prev_dist = None
        best_dist = None
        regained = False
        jumped = False

        for step in range(max_steps):
            if stop_when is not None and stop_when():
                log.info("цель достигнута по внешнему признаку на шаге %s", step)
                return True
            frame, spots = self.snapshot()
            sig = signature(spots)
            spot = spots.get(name)
            if spot is None:
                # Ориентир ушёл из кадра. Возвращаться в опорное состояние НЕЛЬЗЯ:
                # оно делает респавн, и бот вместо подхода ходит кругами — ровно
                # это и выглядело как «зачем ты кружишь». Правильно — отступить
                # назад тем же шагом, которым потеряли, и осмотреться.
                back = {"w": "s", "s": "w", "a": "d", "d": "a"}.get(self._last_key)
                if back:
                    log.info("ориентир %r пропал — отступаю назад (%s)", name, back)
                    self.farmer.hand.hold(back, step_hold)
                    time.sleep(0.4)
                    if self.where(name) is not None:
                        continue
                if not regained:
                    regained = True
                    log.info("ориентир %r не вижу — оглядываюсь", name)
                    if self.find(name, sweeps=6):
                        continue
                log.warning("ориентир %r не виден на шаге %s", name, step + 1)
                return False
            ex, ey = target[0] - spot.x, target[1] - spot.y
            dist = (ex * ex + ey * ey) ** 0.5
            if dist <= tol:
                log.info("ориентир %r на месте (промах %.0f px за %s шагов)",
                         name, dist, step)
                self.kb.save(force=True)
                return True
            # Клавиша плоха, если ЭТОТ шаг не приблизил. Порог «хуже прошлого в
            # 1.25 раза» был слишком добрым: на подставном прогоне бот двадцать
            # шагов держал одну клавишу и уехал с y=300 на y=6150, потому что
            # каждый отдельный шаг ухудшал меньше чем на четверть. Требование
            # «каждый шаг должен приближать» такого не допускает. Небольшой люфт
            # оставлен на дрожание OCR: подпись гуляет на несколько пикселей.
            if prev_dist is not None and dist > prev_dist * 1.02 + 5:
                tried_bad.add(self._last_key)
                log.info("шаг %s: не приблизило (%.0f -> %.0f), клавиша %s отброшена",
                         step + 1, prev_dist, dist, self._last_key)
            # Лучший результат за подход — повод снять отбраковку: мы в новом
            # месте, и клавиша, не годившаяся раньше, здесь может быть верной.
            if best_dist is None or dist < best_dist:
                if best_dist is not None and tried_bad:
                    log.info("шаг %s: новый лучший промах %.0f, снимаю отбраковку %s",
                             step + 1, dist, sorted(tried_bad))
                    tried_bad.clear()
                best_dist = dist
            prev_dist = dist

            # Клавиши, которыми отсюда уже упирались в прошлых запусках, даже не
            # пробуем: именно на переборе стен и уходили заходы, где до ленты так
            # и не дошли. Память привязана к состоянию, так что запрет узкий.
            walls = {k for k in ("w", "a", "s", "d") if self.kb.is_wall(sig, k)}
            if walls:
                log.info("шаг %s: помню упоры отсюда — %s", step + 1, sorted(walls))
            skip = tried_bad | walls

            # Если памятью отброшено ВСЁ, память надо перепроверить, а не сдаваться.
            # Иначе запись о стене становится вечной: исключённую клавишу никто
            # больше не пробует, значит и опровергнуть её нечем. Стены в этой игре
            # не навсегда — на плот встают брейнроты, проходы меняются.
            if not [k for k in ("w", "a", "s", "d") if k not in skip] and walls:
                log.info("шаг %s: памятью отброшены все клавиши — перепроверяю стены",
                         step + 1)
                skip = set(tried_bad)

            pick = self.key_for(ex, ey, exclude=skip, min_hold=0.35)
            if pick is None:
                # Хорошо подходящей клавиши нет. Сначала пробуем НЕИЗМЕРЕННУЮ —
                # замер полезен сам по себе. И только если измерены все, берём
                # лучшую из известных, пусть и с плохим совпадением.
                # Слепой перебор по алфавиту тут был вредителем: он раз за разом
                # выбирал 's' с полным удержанием, игнорируя уже выученное, и
                # уносил ориентир далеко за цель.
                rest = [k for k in ("w", "a", "s", "d") if k not in skip]
                if not rest:
                    log.warning("все клавиши отброшены, к %r не подойти", name)
                    self.kb.save(force=True)
                    return False
                unknown = [k for k in rest if k not in self.axes]
                if unknown:
                    key = unknown[0]
                    log.info("шаг %s: %s ещё не мерена, пробую её", step + 1, key)
                else:
                    weak = self.key_for(ex, ey, exclude=skip, min_hold=0.35,
                                        min_score=0.0)
                    if weak is None:
                        log.info("шаг %s: по всем осям уже на месте, промах %.0f",
                                 step + 1, dist)
                        self.kb.save(force=True)
                        return dist <= tol * 1.5
                    key = weak[0]
                    log.info("шаг %s: точного направления нет, беру лучшее — %s",
                             step + 1, key)
            else:
                key = pick[0]

            self._last_key = key
            before = spot
            # Длина шага по расстоянию: пока скорость этой клавиши неизвестна,
            # шагаем осторожно; как только замерена — идём столько, сколько нужно,
            # но с недолётом (0.7), чтобы не проскочить мимо.
            hold = step_hold
            if key in self.axes:
                dx, dy = self.axes[key]
                speed = (dx * dx + dy * dy) ** 0.5
                if speed > 5:
                    # Считаем по ПРОЕКЦИИ ошибки на ось этой клавиши, а не по
                    # полному расстоянию. Клавиша едет вдоль одной оси, и если
                    # промах набран в основном по другой, длительность от полного
                    # расстояния даёт перелёт в разы: на подставном прогоне 's'
                    # уносило на 360 px там, где нужно было 128.
                    need = abs(ex * dx + ey * dy) / speed
                    hold = max(0.35, min(2.0, need / speed * 0.7))
            self.farmer.hand.hold(key, hold)
            time.sleep(0.4)
            frame2, spots2 = self.snapshot()
            after = spots2.get(name)

            # Упёрлись или прошли — решает картинка, а не ориентир. При виде
            # сверху ориентиров в кадре почти нет, и «подпись не сдвинулась»
            # ничего не значит; доля изменившихся пикселей значит. Порог тот же,
            # что в farm.local_search, где он замерен на живой игре.
            moved = self.farmer._moved(frame, frame2)
            if moved < 0.004:
                hits = self.kb.note_wall(sig, key)
                log.info("шаг %s: картинка не изменилась (%.4f) — упор в %s, раз %s",
                         step + 1, moved, key, hits)
                tried_bad.add(key)
                if not jumped:
                    # Прыжок снимает мелкие бортики — тот же приём, что уже
                    # выручал в farm. Одного раза за подход достаточно: если не
                    # помог, дело не в бортике.
                    jumped = True
                    log.info("пробую перепрыгнуть препятствие")
                    self.farmer.hand.jump()
                    time.sleep(0.6)
                self.kb.save()
                continue
            self.kb.note_free(sig, key)

            if after is not None:
                self._update_axis(key, after.x - before.x, after.y - before.y, hold)
                log.info("шаг %s: %r промах %.0f px, держал %s %.2f с -> уехал на "
                         "(%+d, %+d), картинка %.3f",
                         step + 1, name, dist, key, hold,
                         after.x - before.x, after.y - before.y, moved)
        self.kb.save(force=True)
        log.warning("не подвёл %r за %s шагов", name, max_steps)
        return False
