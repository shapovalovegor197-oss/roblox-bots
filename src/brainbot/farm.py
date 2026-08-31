"""Операции в мире игры: опорное состояние, сбор, лок, покупка, ребёрн.

Спецификация — `research/SCENARIOS.md`. Здесь её реализация, и главное отличие от
прошлой версии: у каждой операции есть ПРОВЕРКА по кадру, а не только слепой тайминг.

  ОС (опорное состояние)  reset -> зум наружу циклом -> база по центру -> камера вниз
  collect_money()         проверка: кэш вырос
  lock_base()             проверка: игра написала «You locked your base for N Seconds»
  buy_at_conveyor()       проверка: кэш упал примерно на цену с карточки
  rebirth()               проверка: требования прочитаны, подтверждение нажато

Архитектура заимствована у внешнего Python-макроса Namesnipes/Steal-A-Brainrot-Macro
(тот же класс решения — зрение+ввод снаружи, без инъекции) и переложена на наш код.
Оттуда же взяты два его известных заусенца, которые мы здесь чиним:
  * зум колесом нестабилен (его issue #2) — поэтому зум замкнутым циклом по картинке,
    а не «ровно N кликов»;
  * Enter в диалоге reset сломан обновлением игры (его issue #5) — поэтому подтверждаем
    кликом, Enter шлём только на всякий случай.

Тайминги ходьбы зависят от конкретной базы и требуют калибровки на живой игре —
вынесены в FarmTuning.
"""
from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from . import ocr
from .brainrots import catalog
from .capture import grab, save
from .inputs import Hand
from .log import get
from .nav import _looks_like as _nav_looks_like
from .window import RobloxWindow

log = get("farm")

# Редкости по возрастанию — как в игре. Для фильтра «покупать от такой-то».
RARITIES = ["common", "rare", "epic", "legendary", "mythic", "brainrot", "secret"]

# Ответ игры на запирание базы. Из него же берём длительность лока.
LOCK_MSG = re.compile(r"lock\w*\s+your\s+base\s+for\s+(\d+)", re.I)
# Русская локаль на случай, если клиент поднимут не с en_us.
LOCK_MSG_RU = re.compile(r"баз\w*\s+заперт\w*\s+на\s+(\d+)", re.I)

# Фрагменты вывески своей базы. Крупный стилизованный шрифт OCR корёжит
# (collect -> llect, multi -> lmylti), поэтому матчим по стойким кускам.
SIGN_FRAGMENTS = ("cash", "ulti", "llect", "zone", "мульт", "сбор", "зона")


def _plausible_cash(now, before) -> bool:
    """Правдоподобно ли новое значение денег относительно прежнего.

    Сбор законно умножает деньги в разы (замер $723K -> $7.38M, десятикратно),
    поэтому верхняя граница КРАТНАЯ, а не абсолютная. Но не в тысячу: разовый
    сбой OCR читает суффикс B вместо M и завышает в 1000 раз.
    """
    return (now is not None and before is not None
            and before * 1.02 < now < before * 10 + 1e6)


@dataclass
class FarmTuning:
    """Тайминги и области. ВСЁ, что зависит от конкретной базы — здесь, для калибровки."""
    # сколько идти от базы (в секундах, key hold)
    to_conveyor_sec: float = 1.7
    to_lock_sec: float = 1.8
    # зум: порция кликов наружу за шаг и потолок числа шагов
    zoom_step: int = 6
    zoom_max_steps: int = 8
    # обратный подъезд после центровки: на обзорном зуме вывеска базы слишком
    # мелкая для OCR, а тайминги ходьбы всё равно нужны от одного и того же зума
    work_zoom_in: int = 4
    # Рабочий зум: столько щелчков наружу от упора внутрь.
    #
    # Замерено кадрами 30.08 (var/screens/cam*_z*_b*.png), шаг зума нелинейный:
    # 0 — вид от первого лица, 4 — затылок во весь кадр, 8 — из-за плеча,
    # 10 и дальше — камера улетает над площадью, персонаж с ноготь и промптов
    # не прочесть. Рабочих значений всего одно, и это 8.
    work_zoom_out: int = 8
    # Наклон рабочего вида: столько единиц мыши ВВЕРХ от упора сверху. Меньше
    # число — камера выше и смотрит вниз, больше — ближе к горизонту.
    #
    # Подобрано замером `scripts/pitch_home.py` 31.08: за эталон берётся
    # положение камеры СРАЗУ ПОСЛЕ РЕСПАВНА (его ставит сама игра), дальше
    # перебираются величины возврата от верхнего упора. Отличие от эталона:
    # 60 -> 50.9, 120 -> 41.2, 150 -> 35.7, 170 -> 19.6, 190 -> 27.2,
    # 240 -> 53.5, 300 -> 74.7, 700 -> камера уходит ПОД персонажа и смотрит
    # в небо.
    #
    # Прежние 700 были подобраны, когда протяжка шла двенадцатью рывками и
    # игра брала не всё. Как только протяжка стала плавной и начала доходить
    # целиком, та же цифра стала перелётом. Правишь ввод — перемеряй это.
    view_pitch_back: int = 170
    # область OCR с карточкой брейнрота у конвейера (доли окна, множим на размер)
    npc_box: tuple[float, float, float, float] = (0.11, 0.13, 0.48, 0.71)
    # область OCR с наличными (левый низ)
    cash_box: tuple[float, float, float, float] = (0.0, 0.86, 0.32, 1.0)
    # область, где игра пишет системные сообщения (низ по центру)
    msg_box: tuple[float, float, float, float] = (0.15, 0.78, 0.85, 0.95)
    # область для поиска вывески базы. Вывеска может быть слева ИЛИ справа, поэтому
    # по горизонтали берём всё, КРОМЕ левой колонки меню (Shop/Rebirth, x<0.17).
    # По вертикали — ниже чата (y>0.36) и выше нижнего HUD: там только вывеска базы.
    side_box: tuple[float, float, float, float] = (0.17, 0.36, 1.0, 0.72)
    # левая колонка меню: Shop / Rebirth / Index / Codes
    menu_box: tuple[float, float, float, float] = (0.0, 0.30, 0.18, 0.60)
    # панель лидерборда (Tab): ники и колонки Steals / Rebirths / Cash
    lb_box: tuple[float, float, float, float] = (0.66, 0.13, 1.0, 0.55)
    # кнопка подтверждения reset-меню (доли окна)
    reset_confirm: tuple[float, float] = (0.23, 0.50)


@dataclass
class Farmer:
    window: RobloxWindow
    hand: Hand
    tuning: FarmTuning = field(default_factory=FarmTuning)
    screens_dir: Path | None = None
    nick: str | None = None               # наш ник в игре — чтобы найти себя в лидерборде
    plot_side_right: bool | None = None   # наследие референса; после face_base не значимо
    # Разрешение на необратимое: ребёрн обнуляет базу. По умолчанию выключено,
    # включается явным флагом на запуске.
    allow_wipe: bool = False
    # Свелась ли камера на якорь в опорном состоянии. От этого зависит,
    # можно ли вообще доверять записанному маршруту: ходьба камерная.
    reference_aimed: bool = False
    axes: dict = field(default_factory=dict)   # клавиша -> (dx, dy, уверенность)
    # Когда истекает лок по НАШИМ часам. Игра говорит длительность один раз —
    # вспышкой «You locked your base for 80 Seconds!», — и дальше считать
    # секунды дешевле и надёжнее, чем перечитывать счётчик у входа: он висит в
    # мире, а не на HUD, и пропадает из кадра, стоит отвернуть камеру. Замерено
    # пробой 30.08: сразу после лока счётчик виден (78s, x=0.58 y=0.31), а через
    # две секунды, когда камера ушла, его нет — и «подтверждения нет» при
    # фактически запертой базе.
    lock_until: float = 0.0
    lock_seconds: int = 0

    def __post_init__(self) -> None:
        # OCR обязан подняться раньше первого WGC-захвата — иначе COM-конфликт с zbl.
        ocr._get_reader()

    # ------------------------------------------------------------------
    # служебное
    # ------------------------------------------------------------------

    def _box(self, frac: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
        b = self.window.client_box()
        x0, y0, x1, y1 = frac
        return int(x0 * b.width), int(y0 * b.height), int(x1 * b.width), int(y1 * b.height)

    def frame(self):
        return grab(self.window.client_box(), hwnd=self.window.hwnd)

    def shot(self, tag: str) -> Path | None:
        """Скрин на память — вызывать при каждом отказе, иначе разбирать нечего."""
        if not self.screens_dir:
            return None
        return save(self.frame(), self.screens_dir, tag)

    @property
    def nav(self):
        """Навигатор по ориентирам — он же хранит выученную таблицу управления."""
        if getattr(self, "_nav", None) is None:
            from .nav import Navigator
            self._nav = Navigator(farmer=self)
        return self._nav

    def center(self) -> tuple[int, int]:
        b = self.window.client_box()
        return b.width // 2, b.height // 2

    # ------------------------------------------------------------------
    # чтение состояния
    # ------------------------------------------------------------------

    def read_hud_cash(self, agree: int = 4) -> float | None:
        """Наличные из HUD. Ответ принимается, только если ПОВТОРИЛСЯ на разных кадрах.

        Одиночное чтение врёт не из-за шрифта, а из-за соседей по кадру: над
        суммой всплывают начисления «+$960», «+$3», «+$160» и попадают в ту же
        область. Замерено в прогоне сбора: 110000 -> 5110000 -> 11 -> 110000 при
        неизменных деньгах. На статичных кадрах цветной способ читает те же
        $110K верно все три раза — значит дело именно во всплывашках.

        Всплывашки живут доли секунды, наличные стоят. Поэтому читаем несколько
        кадров и берём значение, которое встретилось не меньше двух раз.
        """
        from collections import Counter
        seen = []
        for i in range(agree):
            v = self._read_hud_cash_once()
            if v is not None:
                seen.append(v)
            if i < agree - 1:
                time.sleep(0.25)
        if not seen:
            return None
        value, count = Counter(seen).most_common(1)[0]
        if count >= 2:
            return value
        log.info("наличные не сошлись между кадрами: %s", seen)
        return None

    def _cash_crop(self, frame):
        """Вырез с суммой: ищем саму зелёную надпись, а не берём область наугад.

        Широкий вырез (x до 0.28, y от 0.87) захватывает соседей — иконку слотов
        с числом и строку Friend Boost. OCR склеивает их с суммой, и $81.86K
        приходит как 681860 или 48186000: ошибка не в разряде, а в порядке.
        Замерено 30.08 — четыре разных значения подряд при неподвижном HUD, и на
        них бот «подтверждал» покупки, которых не было.
        """
        h, w = frame.shape[:2]
        band = frame[int(h * 0.86):, 0:int(w * 0.30)]
        if not band.size:
            return None
        hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
        m = cv2.inRange(hsv, np.array((35, 120, 150), np.uint8),
                        np.array((85, 255, 255), np.uint8))
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 25), np.uint8))
        n, _lab, st, _c = cv2.connectedComponentsWithStats(m, 8)
        best, area = None, 0
        for i in range(1, n):
            a = int(st[i, cv2.CC_STAT_AREA])
            # Сумма — широкая строка, а не квадратная иконка.
            if a > area and st[i, cv2.CC_STAT_WIDTH] > st[i, cv2.CC_STAT_HEIGHT]:
                best, area = i, a
        if best is None:
            return band[int(band.shape[0] * 0.28):int(band.shape[0] * 0.60),
                        0:int(band.shape[1] * 0.68)]
        x, y = int(st[best, cv2.CC_STAT_LEFT]), int(st[best, cv2.CC_STAT_TOP])
        bw, bh = int(st[best, cv2.CC_STAT_WIDTH]), int(st[best, cv2.CC_STAT_HEIGHT])
        # Справа запас БОЛЬШОЙ: суффикс «K»/«M» стоит последним и по цвету
        # сливается с фоном хуже цифр, поэтому в зелёное пятно попадает не
        # всегда. Обрезанный суффикс — это не мелкая ошибка, а тысячекратная:
        # «$846.76K» читается как 846.
        pad = 6
        return band[max(0, y - pad):y + bh + pad,
                    max(0, x - pad):min(band.shape[1], x + bw + 34)]

    # Сумма из HUD. Каждое требование куплено ошибкой:
    #   * знак «$» ОБЯЗАТЕЛЕН. Без него «$35.59M», где «$» прочитан как «3»,
    #     проходит как 335.59M — ровно это стояло в статусе вечером 30.08;
    #   * суффикс K/M/B ОБЯЗАТЕЛЕН: без него «$846.76K» читается как 846 —
    #     ошибка в тысячу раз;
    #   * мантисса меньше 1000. HUD пишет не больше трёх цифр до точки, а
    #     потерянная точка даёт «$35059m» = 35 млрд вместо 35 млн (замер 22:16).
    # `re.search`, а не `match`: перед знаком бывает мусор («,$35.59m»).
    _CASH_RX = re.compile(r"\$\s*([\d.,]+)\s*([kmb])")

    def _parse_cash(self, text: str) -> float | None:
        """Строка OCR -> сумма. None, если строка не похожа на деньги HUD."""
        t = text.strip().lower().translate(self._DIGIT_GLYPHS)
        m = self._CASH_RX.search(t)
        if not m:
            return None
        num = m.group(1).replace(",", ".").strip(".")
        # Несколько точек — это разряды тысяч; десятичная только последняя.
        if num.count(".") > 1:
            head, _, tail = num.rpartition(".")
            num = head.replace(".", "") + "." + tail
        try:
            base = float(num)
        except ValueError:
            return None
        if not 0 < base < 1000:
            return None
        value = base * {"k": 1e3, "m": 1e6, "b": 1e9}[m.group(2)]
        return value if 1000 < value < 1e12 else None

    def _read_hud_cash_once(self) -> float | None:
        """Одно чтение наличных из HUD — ГОЛОСОВАНИЕМ четырёх способов.

        Вырезов два, и у каждого своя болезнь:

        * по самой зелёной надписи (`_cash_crop`) — подстраивается под текст и
          не режет его, но иногда цепляет зелень мира: в кадре у Trade Plaza
          самым большим зелёным пятном оказалась трава, и чтение вышло пустым;
        * тесный нижний-левый — всегда на месте, но на ходу режет и добавляет:
          за вечер 30.08 он выдал 5.59M и 335.59M при настоящих 35.59M.

        Каждый вырез читается двумя способами (цветной кадр и инвертированная
        зелёная маска), итого четыре голоса. Берём значение, за которое подано
        не меньше двух; если голос всего один — принимаем, только когда других
        чисел в кадре не нашлось.

        Врать в разы этот набор больше не может: разбор требует знака «$» и
        мантиссы меньше 1000, а такие ошибки — всегда либо потерянный знак,
        либо потерянная точка.
        """
        frame = self.frame()
        h, w = frame.shape[:2]
        crops = [self._cash_crop(frame),
                 frame[int(h * 0.90):int(h * 0.99), 0:int(w * 0.22)]]

        from collections import Counter
        votes = Counter()
        for crop in crops:
            if crop is None or not crop.size:
                continue
            big = cv2.resize(crop, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
            hsv = cv2.cvtColor(big, cv2.COLOR_BGR2HSV)
            green = cv2.inRange(hsv, np.array((35, 120, 150), np.uint8),
                                np.array((85, 255, 255), np.uint8))
            for view in (big, cv2.cvtColor(cv2.bitwise_not(green), cv2.COLOR_GRAY2BGR)):
                vals = set()
                for text, _, _ in ocr.lines(view):
                    v = self._parse_cash(text)
                    if v is not None:
                        vals.add(v)
                # Один голос от способа: два числа в одном виде — сам себе
                # не верит, пусть решают остальные.
                if len(vals) == 1:
                    votes[vals.pop()] += 1
        if not votes:
            return None
        value, count = votes.most_common(1)[0]
        if count >= 2:
            return value
        if len(votes) == 1:
            return value
        log.info("наличные не сошлись между способами: %s", dict(votes))
        return None

    def read_leaderboard(self, frame=None) -> dict[str, dict]:
        """Лидерборд (Tab): {ник: {steals, rebirths, cash}}.

        Надёжнее HUD: обычный мелкий шрифт, точное число вместо округлённого
        «100.1K», и сразу три величины — включая счётчик ребёрнов, по которому
        проверяется операция 5.

        Колонки определяем по ЗАГОЛОВКАМ, а не по порядку чисел в строке: нули
        OCR иногда не видит вовсе, и тогда порядковая раскладка сдвигает всё.
        """
        words = ocr.read(frame if frame is not None else self.frame(),
                         self._box(self.tuning.lb_box))
        if not words:
            return {}

        # где какая колонка — по словам-заголовкам
        cols: dict[str, int] = {}
        for w in words:
            key = w.text.strip().lower()
            if key in ("steals", "rebirths", "cash"):
                cols[key] = w.x
        if "cash" not in cols:
            return {}

        rows: list[list] = []
        for w in sorted(words, key=lambda w: (w.y, w.x)):
            if rows and abs(rows[-1][0].y - w.y) <= 12:
                rows[-1].append(w)
            else:
                rows.append([w])

        skip = {"people", "here", "friends", "global", "steals", "rebirths", "cash"}
        out: dict[str, dict] = {}
        for row in rows:
            row.sort(key=lambda w: w.x)
            head = row[0].text.strip().lower()
            if head in skip or not row[1:]:
                continue
            vals: dict[str, float] = {}
            for w in row[1:]:
                v = ocr.parse_amount(w.text)
                if v is None:
                    continue
                col = min(cols, key=lambda c: abs(cols[c] - w.x))
                vals.setdefault(col, v)
            if vals:
                out[head] = {"steals": vals.get("steals"),
                             "rebirths": vals.get("rebirths"),
                             "cash": vals.get("cash")}
        return out

    def open_leaderboard(self) -> bool:
        """Открыть лидерборд, если он закрыт. True — панель видна."""
        if self.read_leaderboard():
            return True
        self.hand.press("tab")
        time.sleep(0.7)
        return bool(self.read_leaderboard())

    @staticmethod
    def _norm_nick(s: str) -> str:
        """Ник под сравнение: OCR путает 0/o, 1/l/i, 5/s — приводим к одному виду."""
        table = str.maketrans({"0": "o", "1": "l", "5": "s", "8": "b", "|": "l"})
        return "".join(ch for ch in s.lower().translate(table) if ch.isalnum())

    def me(self) -> dict | None:
        """Наша строка лидерборда. Ник сравниваем нечётко: OCR его корёжит."""
        rows = self.read_leaderboard()
        if not rows or not self.nick:
            return None
        import difflib
        want = self._norm_nick(self.nick)
        best, score = None, 0.0
        for name, vals in rows.items():
            r = difflib.SequenceMatcher(None, want, self._norm_nick(name)).ratio()
            if r > score:
                best, score = vals, r
        return best if score >= 0.8 else None

    def read_cash(self, toggle: bool = True, samples: int = 2) -> float | None:
        """Наличные. Лидерборд даёт точное число, HUD — как получится.

        Читаем ДВАЖДЫ и требуем совпадения. Одиночное чтение врёт: на живой игре
        лидерборд однажды отдал «100» вместо «100,100», и покупка была засчитана
        по несуществующему падению кэша на сто тысяч. Сверка двух чтений такие
        выбросы убирает почти бесплатно.
        """
        def once() -> float | None:
            mine = self.me()
            return mine.get("cash") if mine else None

        vals = []
        if not toggle:
            v = once()
            return v
        self.hand.press("tab")
        time.sleep(0.7)
        for _ in range(samples):
            vals.append(once())
            time.sleep(0.25)
        self.hand.press("tab")
        time.sleep(0.3)

        good = [v for v in vals if v is not None]
        if len(good) >= 2 and good[0] == good[1]:
            return good[0]
        if good:
            # чтения разошлись — доверяем тому, что ближе к HUD
            hud = self.read_hud_cash()
            if hud:
                return min(good, key=lambda v: abs(v - hud))
            log.warning("кэш прочитан неустойчиво: %s", vals)
        return self.read_hud_cash()

    def read_message(self) -> str:
        """Системная строка внизу по центру — ответы игры на наши действия."""
        return ocr.all_text(self.frame(), self._box(self.tuning.msg_box))

    def find_your_base_x(self) -> int | None:
        """Экранный X плашки 'YOUR BASE'. None — не видно.

        Как якорь ненадёжна: её регулярно перекрывает текст задания. Оставлена
        для отладки, опорное состояние держится на вывеске базы.
        """
        for text, xc, _ in ocr.lines(self.frame()):
            if "your base" in text or ("base" in text and "your" in text):
                return xc
        return None

    def alive_in_world(self) -> bool:
        """Мы в игре: виден интерфейс базы и нет диалога разрыва связи.

        Через кэш проверять нельзя: он читается из лидерборда (нужен ник) или из
        HUD (стилизованный шрифт). Левая колонка меню Shop/Rebirth/Index/Codes
        есть на экране всегда, пока мы в игре, — вот она и есть признак.
        """
        frame = self.frame()
        text = ocr.all_text(frame)
        if "disconnect" in text or "reconnect" in text:
            return False
        if any(m in text for m in self.MODALS):   # игровое окно поверх мира
            self.dismiss_modals()
            frame = self.frame()
        from .nav import landmarks
        seen = landmarks(frame)
        return any(k.startswith("ui_") for k in seen) or "collect" in seen

    def ensure_connected(self, wait: float = 120.0) -> bool:
        """Если клиент выкинуло — нажать Reconnect и дождаться мира.

        Обязательно для работы без присмотра: Roblox выбрасывает за 20 минут
        простоя (Error 278), и без этого весь прогон встаёт на первом же вылете.
        Кнопку жмём С УДЕРЖАНИЕМ: мгновенный клик интерфейс Roblox теряет —
        проверено, именно так Reconnect и не срабатывал.
        """
        frame = self.frame()
        text = ocr.all_text(frame)
        if "disconnect" not in text and "reconnect" not in text:
            return True
        log.warning("клиент отвалился — жму Reconnect")
        self.shot("disconnected")
        b = self.window.client_box()
        for t, xc, yc in ocr.lines(frame):
            if "reconnect" in t:
                self.hand.click(xc, yc, hold=0.12)
                break
        else:
            self.hand.click(int(b.width * 0.572), int(b.height * 0.62), hold=0.12)
        end = time.time() + wait
        while time.time() < end:
            time.sleep(4)
            if self.alive_in_world():
                log.info("вернулись в игру")
                return True
        log.error("вернуться в игру не удалось за %.0f с", wait)
        return False

    # ------------------------------------------------------------------
    # Операция 0: опорное состояние
    # ------------------------------------------------------------------

    def close_panels(self) -> None:
        """Закрыть чат и лидерборд: они забивают кадр и топят OCR.

        Чат сыплет подсказками («[TIP] Your brainrots earn cash offline...»), а
        лидерборд занимает правую четверть — а ровно там висит промпт Lock Base.
        Пока панели открыты, ориентиры в мире не разглядеть.
        """
        # Таблица игроков — первой: её крестик серый на тёмном, обычный поиск
        # синего крестика её не берёт, и она провисела в кадре весь вечер.
        self.close_players_table()
        self.dismiss_modals()
        text = ocr.all_text(self.frame())
        if "steals" in text and "rebirths" in text:
            self.hand.press("tab")          # лидерборд — переключатель
            time.sleep(0.4)
        # Чат сыплет подсказками и занимает левую треть. Кнопка — переключатель,
        # поэтому проверяем результат: лишний клик открыл бы его обратно.
        for _ in range(2):
            if not any(k in ocr.all_text(self.frame())
                       for k in ("[tip]", "invite friends", "send quick words")):
                return
            b = self.window.client_box()
            self.hand.click(int(b.width * 0.109), int(b.height * 0.046))
            time.sleep(0.6)

    # Заголовки окон, которые игра открывает при подходе к объекту или по кнопке.
    # Пока такое окно открыто, мира не видно и ходить бессмысленно.
    MODALS = ("buy tools to protect", "shop", "index", "codes", "birth",
              "purchase items", "магазин", "индекс")

    def close_players_table(self) -> bool:
        """Закрыть таблицу игроков. Она висит справа и режет четверть кадра.

        Открывается сама (по Tab или по клику) и не уходит: `find_close_button`
        её крестик не видит, потому что ищет СИНИЙ квадрат, а тут он серый на
        тёмном. Весь вечер она провисела в кадре, обрезая правую часть картинки
        всем детекторам — и я это заметил только на видео пробы.

        Координата крестика взята с живого кадра и переведена в доли окна.
        """
        text = ocr.all_text(self.frame())
        if "steals" not in text and "rebirths" not in text:
            return True
        b = self.window.client_box()
        self.hand.click(int(b.width * 0.695), int(b.height * 0.101))
        time.sleep(0.5)
        text = ocr.all_text(self.frame())
        gone = "steals" not in text and "rebirths" not in text
        log.info("таблица игроков %s", "закрыта" if gone else "НЕ закрылась")
        return gone

    def dismiss_modals(self, tries: int = 2, force: bool = False) -> bool:
        """Закрыть открытое игровое окно крестиком. True — экран чист.

        Главный признак окна — САМ КРЕСТИК, а не заголовок. Заголовки
        стилизованные, и OCR их корёжит: окно REBIRTH приходит как «nebirth»,
        в списке `MODALS` стоит «rebirth», совпадения нет — и проверка отвечала
        «экран чист», не закрыв ничего. Окно висело на экране, перекрывая
        середину кадра, а бот сорок минут искал за ним плиту лока и честно
        сообщал «свечения не видно даже осмотром». Замерено 30.08: четыре лока
        подряд провалены ровно по этой причине.

        Крестик ищется по цвету и форме (`find_close_button`), поэтому не
        зависит ни от OCR, ни от того, какое именно окно открылось.
        """
        for _ in range(tries):
            frame = self.frame()
            lines = ocr.lines(frame)
            menu_x = self.window.client_box().width * 0.20
            text = " ".join(t for t, x, _ in lines if x > menu_x)
            # Системное меню Roblox (Resume / Leave / Respawn) закрывается только
            # клавишей Esc. Тыкать в него «крестиком» нельзя: так мы попадали в
            # его вкладки и открывали Gallery вместо того, чтобы вернуться в игру.
            # Системное меню Roblox (People / Settings / Leave / Respawn /
            # Resume) закрывается ТОЛЬКО клавишей Esc; крестика у него нет, и
            # тыкать в него нельзя — так мы открывали Gallery вместо возврата.
            # Признаков берём несколько и любого ДВУХ достаточно: подписи мелкие
            # и OCR их читает через раз. Замер 30.08: меню провисело на экране
            # три круга, бот всё это время работал вслепую и объявлял «окно
            # ребёрна не закрылось».
            marks = sum(w in text for w in
                        ("resume", "leave", "respawn", "invite friends",
                         "in this server", "gallery", "report"))
            if marks >= 2:
                log.info("системное меню Roblox — закрываю по Esc")
                self.hand.press("esc")
                time.sleep(1.0)
                continue
            from .nav import find_close_button
            spot = find_close_button(frame)
            if spot is None:
                cross = [(cx, cy) for tx, cx, cy in lines
                         if tx.strip() in ("x", "х", "×")
                         and cy < self.window.client_box().height * 0.6
                         and cx > self.window.client_box().width * 0.5]
                if cross:
                    spot = type("S", (), {"x": cross[0][0], "y": cross[0][1]})()
            if spot is None:
                # Крестика нет — экран чист, и точка. По ТЕКСТУ решать нельзя:
                # в списке `MODALS` стоит «shop», а на площади висит вывеска
                # магазина Trade Plaza — и лок отменялся посреди чистого мира
                # («окно открыто по тексту, а крестик не нашёлся», 23:10).
                # Крестик после проверки на белую X врать перестал: 0 ложных
                # на 42 кадрах стенда, оба настоящих окна найдены. Заголовки же
                # OCR корёжит («nebirth»), из-за них и городили поиск крестика.
                if any(m in text for m in self.MODALS):
                    log.info("похоже на окно по тексту, но крестика нет — иду дальше")
                return True
            log.info("закрываю окно игры (крестик @%s,%s)", spot.x, spot.y)
            self.hand.click(spot.x, spot.y, hold=0.1)
            time.sleep(0.8)
            if find_close_button(self.frame()) is None:
                return True
        # Крестик всё ещё «виден» — но это НЕ повод отменять действие.
        # Детектор ошибается на четырёх кадрах из 156 (оконные рамы базы), и
        # раньше такая ошибка стоила круга целиком: лок отменялся, бот клал
        # десяток попыток подряд и вставал намертво (прогон 04:14). Настоящее
        # окно и так провалит наведение — там ничего не видно, — и попытка
        # честно закончится респавном. Ложное же больше не мешает.
        log.info("крестик не исчез после кликов — считаю экран чистым и иду дальше")
        return True

    def calibrate_turn(self, step: int = 50, tries: int = 8) -> float | None:
        """Измерить градусы на единицу мыши. ВЕРТИКАЛЬ КАМЕРЫ НЕ ТРОГАЕТСЯ.

        Зачем самозамер: поворот зависит от ползунка Mouse Sensitivity в самом
        клиенте, а его двигает человек и переживает перезапуск. За ночь 31.08
        мера менялась трижды — 0.102, 0.070 и 0.217 градуса на единицу, и
        каждый раз бот терял и плиту, и ленту, а в логах это выглядело как
        поломка навигации. Числу в коде тут не место.

        Способ: крутим мелкими шагами и меряем горизонтальный сдвиг сцены
        фазовой корреляцией; пиксели в градусы — через угол обзора (у Roblox
        по умолчанию 70 по вертикали). Берём медиану, потому что отдельные
        кадры не совпадают (мимо бегают игроки, крутится RNG Machine).

        Заодно пересчитывает шаг разворота: длинную протяжку игра берёт не
        целиком, замеренная доля — примерно две трети.
        """
        box = self.window.client_box()
        deg_per_px = (2 * math.degrees(math.atan(
            math.tan(math.radians(35.0)) * box.width / box.height))) / box.width

        def scene():
            fr = self.frame()
            h, w = fr.shape[:2]
            cut = fr[int(h * 0.12):int(h * 0.55), int(w * 0.25):int(w * 0.95)]
            return cv2.cvtColor(cut, cv2.COLOR_BGR2GRAY).astype(np.float32)

        prev = scene()
        rates = []
        for _ in range(tries):
            self.hand.look(step, 0)
            time.sleep(0.45)
            now = scene()
            (dx, _dy), resp = cv2.phaseCorrelate(prev, now)
            prev = now
            if resp < 0.05 or abs(dx) < 5:
                continue
            rates.append(abs(dx) * deg_per_px / step)
        if len(rates) < 3:
            log.warning("поворот замерить не вышло (%d годных из %d) — "
                        "оставляю прежнюю меру %.4f град/ед",
                        len(rates), tries, self.hand.SMALL_DEG_PER_UNIT)
            return None
        rates.sort()
        rate = rates[len(rates) // 2]
        self.hand.SMALL_DEG_PER_UNIT = rate
        self.hand.TURN_STEP_DEG = self.hand.TURN_STEP_UNITS * rate * 0.65
        log.info("поворот: %.4f град/ед (полный оборот %.0f единиц), "
                 "шаг %d единиц = %.1f град; замеров %d",
                 rate, 360.0 / rate, self.hand.TURN_STEP_UNITS,
                 self.hand.TURN_STEP_DEG, len(rates))
        return rate

    def set_work_view(self) -> None:
        """Привести камеру к РАБОЧЕМУ виду: известный зум и известный наклон.

        Зум держится не «как получилось», а от упора: до упора внутрь, затем
        фиксированное число щелчков наружу. Иначе за прогон камера уезжает, и
        одни и те же пиксели значат каждый раз другое расстояние.

        Раньше вид выставлялся только в опорном состоянии и на подъезде, а лок
        и дорога к ленте работали с той камерой, какая осталась от прошлого
        действия. Отсюда и «плита не видна» на ровном месте.
        """
        self.hand.scroll(60)
        time.sleep(0.4)
        self.hand.scroll(-self.tuning.work_zoom_out)
        time.sleep(0.4)
        self.hand.pitch_normal(back=self.tuning.view_pitch_back)
        time.sleep(0.4)

    def ensure_world_focus(self) -> None:
        """Клик в центр кадра: увести фокус ввода в мир, а не в элементы интерфейса."""
        self.hand.click(*self.center())
        time.sleep(0.3)

    def _menu_click(self, needle: str, timeout: float = 4.0,
                    at_x: int | None = None, exact: bool = False) -> bool:
        """Найти строку меню по тексту и кликнуть. at_x — кликнуть правее (значение).

        exact=True обязателен для КНОПОК. На этом мы уже обожглись: диалог
        подтверждения респавна содержит и вопрос «Are you sure you want to respawn
        your character?», и кнопки «Respawn» / «Don't respawn». Поиск по вхождению
        кликал в текст вопроса, диалог оставался висеть, и дальше всё вставало —
        камера не крутится, ориентиры не находятся, операции валятся.
        """
        end = time.time() + timeout
        while time.time() < end:
            for text, xc, yc in ocr.lines(self.frame()):
                t = text.strip()
                if (t == needle) if exact else (needle in t):
                    self.hand.click(at_x if at_x is not None else xc, yc, hold=0.1)
                    time.sleep(0.8)
                    return True
            time.sleep(0.3)
        return False

    def _wait_gone(self, needle: str, timeout: float = 12.0) -> bool:
        """Дождаться, пока текст пропадёт с экрана. True — пропал."""
        end = time.time() + timeout
        while time.time() < end:
            if needle not in ocr.all_text(self.frame()):
                return True
            time.sleep(0.5)
        return False

    def apply_game_settings(self) -> dict:
        """Выставить настройки клиента, от которых зависит навигация.

        Главное — **Camera Mode: Classic**. По умолчанию камера доворачивается за
        персонажем при ходьбе, и «вперёд» уезжает вместе с ней: первый шаг верный,
        дальше направления плывут. В классическом режиме камера сама не крутится,
        и связка «клавиша -> направление на экране» держится.

        Второе — **Shift Lock Switch**: даёт режим, где персонаж жёстко смотрит туда
        же, куда камера. Пригодится для точных подходов.

        HUD Roblox спрятать нельзя (это CoreGui, снаружи не выключается), но чат и
        лидерборд мы закрываем сами — см. close_panels().
        """
        done = {}
        self.hand.ensure_focus()
        self.hand.press("esc"); time.sleep(1.2)
        done["settings"] = self._menu_click("settings")
        self.hand.move(640, 420)
        self.hand.scroll(-3); time.sleep(0.9)      # долистать до «View & Controls»
        done["camera_mode"] = self._menu_click("camera mode", at_x=795)
        self.hand.scroll(-3); time.sleep(0.9)
        done["shift_lock"] = self._menu_click("shift lock", at_x=795)
        self.hand.press("esc"); time.sleep(1.0)
        log.info("настройки клиента: %s", done)
        return done

    def reset_to_base(self) -> None:
        """Вернуть персонажа на спавн базы — кнопкой Respawn в меню Esc.

        Прежняя связка Esc/R/Enter ненадёжна: обновление игры сломало подтверждение
        по Enter (issue #5 референса), а сочетание R работает не во всех состояниях.
        В самом меню Esc внизу есть кнопка Respawn — её и жмём, найдя по тексту.
        Дальше игра просит подтверждение, его тоже находим по тексту.
        """
        self.hand.ensure_focus()
        self.hand.press("esc"); time.sleep(1.1)
        if not self._menu_click("respawn", timeout=3.0, exact=True):
            self.hand.press("r"); time.sleep(0.4)      # запасной путь
        # Подтверждение: в диалоге ровно две кнопки, «Respawn» и «Don't respawn».
        # Совпадение только точное, иначе попадём в текст вопроса.
        for _ in range(3):
            if not self._menu_click("respawn", timeout=3.0, exact=True):
                break
            # Диалог гаснет НЕ мгновенно: клик срабатывает, а надпись держится
            # ещё несколько секунд. Ждём именно исчезновения, а не спим наугад.
            if self._wait_gone("respawn", timeout=12.0):
                break
        else:
            log.warning("диалог подтверждения респавна не ушёл")
            self.shot("fail_respawn_confirm")
        # Ждём не наугад, а пока картинка не перестанет меняться: анимация
        # возрождения короче четырёх секунд, а четыре стояли с запасом.
        prev = None
        for _ in range(8):
            time.sleep(0.35)
            cur = cv2.cvtColor(cv2.resize(self.frame(), (160, 90)), cv2.COLOR_BGR2GRAY)
            if prev is not None and float(np.abs(cur.astype(int) - prev.astype(int)).mean()) < 3.0:
                break
            prev = cur

    def zoom_out_until_overview(self) -> bool:
        """Отъезжать порциями, пока в кадре не появится плашка 'YOUR BASE'.

        Замкнутый цикл вместо «ровно N кликов»: колесо доезжает не всегда, у автора
        референса это отдельный баг (issue #2). Считать вслепую нельзя.
        """
        self.hand.scroll(60)              # обнулить зум внутрь — известная точка
        time.sleep(0.4)
        for i in range(self.tuning.zoom_max_steps):
            self.hand.scroll(-self.tuning.zoom_step)
            time.sleep(0.45)
            if self.find_your_base_x() is not None:
                log.info("обзор поймали за %s шагов зума", i + 1)
                return True
        log.warning("зум: 'YOUR BASE' так и не появилась за %s шагов",
                    self.tuning.zoom_max_steps)
        return False

    def face_base(self, tol: int = 60, max_iter: int = 8) -> bool:
        """Довернуть камеру так, чтобы 'YOUR BASE' была по центру = смотрим на базу.

        Замкнутый цикл: не требует точной калибровки скорости поворота, сам подводит
        подпись к центру. Знак: look вправо двигает подпись влево. Работает при ЛЮБОМ
        спавне — оттого и надёжно.
        """
        cx = self.center()[0]
        for _ in range(max_iter):
            x = self.find_your_base_x()
            if x is None:
                log.info("YOUR BASE не видно, доворачиваю")
                self.hand.look(900, 0)
                time.sleep(0.5)
                continue
            err = x - cx
            if abs(err) <= tol:
                log.info("камера смотрит на базу (YOUR BASE @x=%s, центр %s)", x, cx)
                return True
            # err>0 (подпись справа) -> крутить вправо, она поедет к центру
            self.hand.look(int(err * 15), 0)
            time.sleep(0.5)
        log.warning("не удалось центрировать базу за %s шагов", max_iter)
        return False

    def detect_side(self, tries: int = 5) -> bool:
        """Сторона базы по вывеске COLLECT ZONE / CASH MULTI. Задаёт plot_side_right."""
        cx = self.center()[0]
        for _ in range(tries):
            x = self.find_sign_x()
            if x is not None:
                self.plot_side_right = x > cx
                log.info("база справа: %s (вывеска @x=%s)", self.plot_side_right, x)
                self.nav.kb.plot_side_right = self.plot_side_right
                self.nav.kb.save(force=True)
                return True
            time.sleep(0.25)
        # Замерить не вышло — но сторона могла быть определена в прошлый раз.
        # Это не догадка: слот базы в пределах одного захода не меняется.
        if self.plot_side_right is None and self.nav.kb.plot_side_right is not None:
            self.plot_side_right = self.nav.kb.plot_side_right
            log.info("сторону базы взял из памяти: справа=%s", self.plot_side_right)
            return True
        log.warning("вывеску базы не нашёл — сторона не определена")
        return False

    def to_reference(self, attempts: int = 2, anchor: str = "cash_multi",
                     aim: bool = True) -> bool:
        """Опорное состояние: спавн базы, известный зум, камера наведена на якорь.

        Раньше якорем была плашка YOUR BASE. На живой игре она регулярно перекрыта
        текстом задания («Go buy a Noobini Pizzanini» ложится ровно поверх неё), и
        центровка падала. Якорь надёжнее — вывеска собственной базы CASH MULTI /
        COLLECT ZONE: она в мире, не мигает и не перекрывается.

        Разворот после респавна каждый раз РАЗНЫЙ (замерено: конвейер оказывается
        то на x=112, то на x=1147), поэтому камеру не «восстанавливаем», а наводим
        осмотром.
        """
        for attempt in range(1, attempts + 1):
            if not self.ensure_connected():
                return False
            self.ensure_world_focus()
            self.close_panels()
            self.reset_to_base()
            if not self.alive_in_world():
                # Мира не видно — скорее всего поверх открыто игровое окно, чей
                # заголовок OCR не разобрал. Пробуем закрыть по кнопке-крестику.
                self.dismiss_modals(force=True)
                if not self.alive_in_world():
                    log.warning("после респавна интерфейс не виден (попытка %s)", attempt)
                    self.shot("fail_reset")
                    continue
            # известный зум: до упора внутрь, затем фиксированное число щелчков наружу
            self.hand.scroll(60); time.sleep(0.4)
            self.hand.scroll(-self.tuning.work_zoom_out); time.sleep(0.5)
            # Якорь мало ВИДЕТЬ — на него надо НАВЕСТИСЬ.
            #
            # Раньше здесь стоял `find`: достаточно, мол, чтобы вывеска попала в
            # кадр, а подход и так работает от любого её положения. Для подхода
            # верно, а для памяти маршрутов — нет, и это выяснилось замером.
            # Движение в игре камерное: `w` означает «вперёд по взгляду». Если
            # камера каждый старт смотрит чуть иначе, одно и то же «держать w
            # 6 секунд» ведёт каждый раз в другое место. Замерено: маршрут
            # `w 6.0с`, приведший к ленте, на следующем заходе не сработал, а
            # перебор сторон прошёл тем же `w` 15 секунд и упёрся в стену.
            #
            # Наведение капризно (чувствительность мыши плавает), поэтому оно с
            # допуском и с откатом: не свелось — принимаем по факту видимости,
            # как раньше, но пишем в лог, что старт ненормализован.
            if not self.nav.find(anchor):
                self.shot("fail_find_anchor")
                continue
            if not aim:
                self.reference_aimed = False
                log.info("опорное состояние без наведения: камера как после респавна")
            elif self.nav.face(anchor, tol=60):
                self.reference_aimed = True
            else:
                self.reference_aimed = False
                log.warning("на якорь %r не навёлся — старт ненормализован, "
                            "маршрут по памяти будет ненадёжен", anchor)
            # Камеру НЕ опускаем в вид сверху. Раньше опускали: считалось, что
            # так w/a/s/d стабильно совпадают с направлениями на экране. Это
            # была подпорка под неверную модель — движение в игре камерное, `w`
            # значит «вперёд по взгляду», и совпадение с экраном не нужно.
            #
            # А вреда от наклона оказалось много: он выполнялся ПОСЛЕДНИМ
            # действием опорного состояния и уводил из кадра сам якорь. В логе
            # это выглядело абсурдно — «камера смотрит на collect» и следующей
            # строкой «ориентир collect не нашёлся за полный оборот». Калибровка
            # угла от вывески после такого невозможна в принципе.
            log.info("опорное состояние взято (попытка %s, якорь %r, камера за спиной)",
                     attempt, anchor)
            return True
        log.error("опорное состояние не взято за %s попытки", attempts)
        return False

    def _side_key(self, toward_base: bool) -> str:
        """Клавиша к базе / от базы с учётом стороны плота."""
        right = bool(self.plot_side_right)
        if toward_base:
            return "d" if right else "a"
        return "a" if right else "d"

    # ------------------------------------------------------------------
    # Локальный поиск: то, что видно только вблизи
    # ------------------------------------------------------------------

    def sees(self, *needles: str) -> str | None:
        """Есть ли на экране одна из строк. Возвращает найденную."""
        text = ocr.all_text(self.frame())
        for n in needles:
            if n in text:
                return n
        return None

    def local_search(self, *needles: str, step: float = 0.6,
                     rounds: int = 3) -> bool:
        """Обойти окрестность короткими шагами, пока не покажется нужный промпт.

        Часть объектов видна только вблизи: надпись «Lock Base» мелкая, издалека
        OCR её не берёт, а значит навестись на неё как на ориентир нельзя. Зато
        подойдя, её видно уверенно. Поэтому не «идти к точке», а обшарить округу —
        и это же честно отвечает «здесь такого нет», если обошли и не нашли.
        """
        found = self.sees(*needles)
        if found:
            log.info("вижу %r сразу", found)
            return True
        # Разворачивающаяся спираль: с каждым кругом шаг длиннее, охват шире.
        pattern = ["d", "w", "a", "a", "s", "s", "d", "d", "w", "w"]
        for r in range(rounds):
            for key in pattern:
                self.trail_hold(key, step)
                time.sleep(0.35)
                found = self.sees(*needles)
                if found:
                    log.info("нашёл %r за %s шагов", found, r * len(pattern) + pattern.index(key) + 1)
                    return True
            step *= 1.4
        log.warning("в окрестности не нашёл: %s", ", ".join(needles))
        return False

    def walk_until(self, *needles: str, landmark: str | None = None,
                   steps: int = 20, step: float = 0.7) -> bool:
        """Идти к ориентиру, пока на экране не появится нужный промпт.

        Навигация по ЦЕЛИ, а не по геометрии. Раньше подход считался успешным,
        когда ориентир вставал в заданную точку кадра, — но конвейер это лента
        через полэкрана, и её «центр» не значит «я в зоне действия». Настоящий
        признак один: игра показала промпт. По нему и останавливаемся.

        Направление выбираем восхождением: пробуем каждую клавишу по короткому
        шагу и оставляем ту, от которой ориентир становится БЛИЖЕ. Близость меряем
        по видимой площади (для ленты) или по смещению вниз по экрану — приближаясь
        к объекту, видишь его ниже и крупнее. Это работает без знания геометрии
        карты и не требует, чтобы камера была куда-то повёрнута.
        """
        if self.sees(*needles):
            return True
        # Зум за время ходьбы уезжает, а на максимальном приближении в кадре одна
        # спина персонажа — ни ориентиров, ни промптов. Приводим к рабочему.
        self.hand.scroll(60); time.sleep(0.3)
        self.hand.scroll(-self.tuning.work_zoom_out); time.sleep(0.4)
        if landmark and not self.nav.find(landmark):
            log.warning("ориентир %r не виден", landmark)
            return False

        def closeness() -> float | None:
            spot = self.nav.where(landmark) if landmark else None
            if spot is None:
                return None
            # Приближаясь, видишь объект НИЖЕ и КРУПНЕЕ. У текстовой подписи
            # площади нет, поэтому решает высота в кадре; у ленты площадь есть, и
            # она весомее — центр большого объекта может стоять на месте, пока сам
            # объект растёт.
            return spot.y + spot.weight * 5000

        best_key, tried = None, {}
        for i in range(steps):
            if self.sees(*needles):
                log.info("промпт появился за %s шагов", i)
                return True
            before = closeness()
            key = best_key
            if key is None:
                # ещё не знаем, куда идти — пробуем очередную клавишу
                key = next((k for k in ("w", "a", "s", "d") if k not in tried), None)
                if key is None:
                    best_key = max(tried, key=lambda k: tried[k])
                    if tried[best_key] <= 0:
                        # Плато: дальше по прямой не подойти — мешает край базы или
                        # бортик. Прыжок снимает мелкие преграды, а если и он не
                        # помог, обходим окрестность спиралью: этим способом бот
                        # однажды уже добрался до ленты.
                        log.info("плато — прыгаю и обхожу окрестность")
                        self.hand.jump()
                        time.sleep(0.5)
                        return self.local_search(*needles, step=1.2, rounds=4)
                    log.info("иду к %r клавишей %s (прирост %.1f)",
                             landmark, best_key, tried[best_key])
                    tried = {}
                    continue
            self.hand.hold(key, step)
            time.sleep(0.35)
            after = closeness()
            gain = (after - before) if (before is not None and after is not None) else 0.0
            if best_key is None:
                tried[key] = gain
                self.hand.hold({"w": "s", "s": "w", "a": "d", "d": "a"}[key], step)
                time.sleep(0.3)
            elif gain <= 0:
                log.info("клавиша %s перестала приближать — ищу заново", best_key)
                best_key, tried = None, {}
        log.warning("промпт так и не появился: %s", ", ".join(needles))
        return False

    # ------------------------------------------------------------------
    # Поход за покупкой: одна цель, прямая линия, без кругов
    # ------------------------------------------------------------------

    ROUTE_FILE = "route.json"

    def _route_path(self):
        return (self.screens_dir.parent / self.ROUTE_FILE) if self.screens_dir else None

    def load_route(self) -> list[tuple[str, float]] | None:
        """Выученный маршрут от спавна до ленты: список удержаний по порядку.

        Раньше маршрут был одной прямой — «держать d 4.2 с». Этого мало: если до
        ленты нужно два колена, такой маршрут не найдётся и не запишется вообще.
        Теперь это последовательность, а старый формат читается как маршрут из
        одного шага — чтобы уже накопленное не пропало.
        """
        path = self._route_path()
        if not path or not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if isinstance(data, dict) and "вперёд" in data:
            return data                      # новый формат: угол от якоря + время
        if isinstance(data, dict) and "шаги" in data:
            if data.get("старт") != "нормализован":
                # Записан до того, как старт стали доводить до якоря — значит от
                # неизвестного угла камеры. Такой маршрут бесполезен.
                log.info("маршрут из памяти пропущен: записан от ненормализованного старта")
                return None
            return [(s["клавиша"], float(s["секунд"])) for s in data["шаги"]]
        if isinstance(data, dict) and "клавиша" in data:       # старый формат
            log.info("маршрут в старом формате пропущен: угол камеры при записи неизвестен")
            return None
        return None

    def save_belt_route(self, route: dict) -> None:
        """Записать откалиброванную дорогу: поворот от якоря и время хода."""
        path = self._route_path()
        if not path:
            return
        data = dict(route)
        data["старт"] = "нормализован"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        log.info("запомнил дорогу до ленты: поворот %s, вперёд %.1f с",
                 route["поворот"], route["вперёд"])

    def save_route(self, legs: list[tuple[str, float]]) -> None:
        """Записать путь до ленты — тот, который только что сработал.

        Это и есть обучение показом, где показывает бот сам себе: что бы его до
        ленты ни довело — прямая, два колена или обход спиралью, — записывается
        ровно та последовательность удержаний, после которой появился промпт.
        Дальше её достаточно повторить, а зрением только проверить, что пришли.

        Шаги одной клавиши подряд склеиваются: поиск идёт короткими шажками, и
        без склейки маршрут получается из двадцати кусочков по 0.7 с, каждый со
        своей паузой — повтор выходит дольше и дёрганее оригинала.
        """
        path = self._route_path()
        if not path or not legs:
            return
        glued: list[list] = []
        for key, sec in legs:
            if glued and glued[-1][0] == key:
                glued[-1][1] += sec
            else:
                glued.append([key, sec])
        if not self.reference_aimed:
            # Записывать маршрут от ненормализованного старта нельзя: он ведёт
            # не туда, куда вёл при записи, и следующий заход честно потратит на
            # него время. Лучше не помнить ничего, чем помнить неправду.
            log.info("маршрут не записан: старт был ненормализован")
            return
        data = {"старт": "нормализован",
                "шаги": [{"клавиша": k, "секунд": round(s, 2)} for k, s in glued]}
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        log.info("запомнил маршрут до ленты: %s",
                 " -> ".join(f"{k} {s:.1f}с" for k, s in glued))

    # Журнал удержаний текущего похода. Нужен, чтобы записать в маршрут не
    # догадку, а ровно то, что бот проделал до появления промпта.
    def trail_start(self) -> None:
        self._trail = []

    def trail_hold(self, key: str, seconds: float) -> None:
        """Удержание с записью в журнал. Всё, что должно попасть в маршрут."""
        self.hand.hold(key, seconds)
        if getattr(self, "_trail", None) is not None:
            self._trail.append((key, seconds))

    def trail(self) -> list[tuple[str, float]]:
        return list(getattr(self, "_trail", []) or [])

    def _moved(self, before, after) -> float:
        """Доля изменившихся пикселей между кадрами: двигались мы или упёрлись.

        Считаем по самой картинке, а не по ориентирам. При виде сверху ориентиров
        в кадре почти нет, и мера «сместились ли подписи» давала ноль на каждом
        шаге — бот решал, что упёрся в стену, хотя спокойно шёл.
        """
        import numpy as np
        a = before[100:620, 200:1100, :3].mean(axis=2)
        b = after[100:620, 200:1100, :3].mean(axis=2)
        return float((np.abs(a - b) > 25).mean())

    # Полный оборот камеры в единицах мыши. Значение по умолчанию — на случай,
    # если калибровка `op turn` ещё не делалась; измеренное лежит в памяти.
    # Прежнее зашитое 5600 было занижено на четверть: замер дал 7000.
    FULL_TURN = 7000

    def unstick(self, why: str, anchor: str = "cash_multi") -> bool:
        """Выбраться из тупика респавном, а не упорством.

        Правило простое: если бот упёрся — не долбиться в одну точку, а
        переродиться. Респавн возвращает в спавн базы точно и стоит несколько
        секунд, тогда как попытки протиснуться могут не кончиться никогда. Все
        обходы (прыжок, шаг вбок, спираль) остаются, но как первая ступень, а не
        как единственная.
        """
        log.info("застрял (%s) — перерождаюсь вместо того, чтобы продавливать", why)
        self.shot("stuck_respawn")
        if not self.to_reference(anchor=anchor):
            log.warning("после респавна опорное состояние не взято")
            return False
        return True

    def aim_belt(self, sector: int = 700, forward: float = 9.0,
                 step: float = 1.5, anchor: str = "cash_multi") -> dict | None:
        """Разовая калибровка дороги до ленты: сначала УГОЛ, потом ВРЕМЯ.

        Порядок именно такой, и это принципиально. Движение камерное: `w` значит
        «вперёд по взгляду». Значит сперва надо выставить направление, а уже
        потом мерить, сколько по нему идти. Обратный порядок — искать дорогу,
        гоняясь за целью на каждом шаге, — и приводил к тому, что бот бесконечно
        доворачивался: лента это длинная диагональ, её центроид всегда сбоку,
        сколько на неё ни наводись.

        Перебираем СЕКТОРЫ КАМЕРЫ, а не четыре клавиши. Поворот покрывает все
        направления, WASD — только четыре, и ни одно может не смотреть на ленту.

        Каждый сектор: повернулись, прошли вперёд отрезками, следя за промптом и
        за тем, что картинка вообще меняется. Не нашли — вернулись ровно на
        столько же и пробуем следующий. Возврат равен пройденному, поэтому все
        сектора проверяются из одной и той же точки.

        Возвращает {"поворот": единицы мыши от якоря, "вперёд": секунды}.
        """
        # Калибровка обязана начинаться СО СПАВНА, всегда. Угол и время меряются
        # от базы, и только оттуда они что-то значат.
        #
        # Сперва тут стояла проверка «а вдруг вывеска и так видна» — и она
        # обманывала: одиночный кадр её ловил, а `face` за полный оборот терял.
        # Хуже того, замер получался от случайной точки, где персонаж остался
        # стоять после прошлого прогона, и записывать такое в маршрут нельзя.
        if not self.to_reference(anchor=anchor):
            log.warning("калибровка: опорное состояние не взято")
            return None
        # Наведение на якорь — ЖЕЛАТЕЛЬНО, но не обязательно.
        #
        # Раньше калибровка тут прекращалась. И зря: вывеска «CASH MULTI» есть у
        # КАЖДОЙ базы в ряду, при повороте камеры в кадр входят соседские, и
        # детектор берёт первую попавшуюся — цель прыгает между базами, а
        # наведение не сходится за 12 шагов. Но для прямого пути якорь не нужен
        # вовсе: ленту видно саму по себе, и угол считается от неё.
        # Камеру наводим НА ЛЕНТУ, а не на вывеску базы.
        #
        # Прежний порядок был самоубийственным: сначала нормализуем старт
        # наведением на вывеску — а она висит НАД базой, то есть камера после
        # этого смотрит в сторону, противоположную ленте. Проверка «видна ли
        # лента» всегда давала «нет», прямой путь не запускался ни разу, и
        # калибровка каждый раз уходила в дорогой перебор восьми секторов.
        # Ищем сперва ПОДПИСЬ ТОВАРА, потом ленту по цвету. Подпись — текст, она
        # переживает смену оформления на событиях; цвет ленты не переживает.
        if not (self.nav.find("goods", sweeps=8) or self.nav.find("conveyor", sweeps=8)):
            log.info("ни товара, ни ленты не видно — остаётся перебор секторов")

        # Сначала — прямой путь: если лента видна, угол не ищут перебором, его
        # ВЫЧИСЛЯЮТ из её положения в кадре. Перебор восьми секторов стоит по
        # респавну на каждый, три минуты на четыре сектора; а лента с базы, как
        # правило, видна — она ловится по цвету и тянется через полкадра.
        cx = self.window.client_box().width // 2
        seen = self.nav.see()
        belt = seen.get("goods") or seen.get("conveyor")
        log.info("цель %s", f"{'товар' if 'goods' in seen else 'лента'} на x={belt.x}"
                 if belt else "не видна")
        if belt is not None:
            err = belt.x - cx
            turn = int(-err / max(self.nav.px_per_mouse, 0.05))
            turn = max(-2000, min(2000, turn))
            log.info("лента видна на x=%s (центр %s) — доворачиваю на %+d",
                     belt.x, cx, turn)
            if turn:
                self.hand.look(turn, 0)
                time.sleep(0.5)
            gone, stuck = 0.0, 0
            while gone < forward + 4:
                before = self.frame()
                self.hand.hold("w", step)
                time.sleep(0.4)
                gone += step
                if self.sees("purchase"):
                    log.info("лента найдена по прямому наведению: "
                             "поворот %s, вперёд %.1f с", turn, gone)
                    return {"поворот": turn, "вперёд": round(gone, 2)}
                if self._moved(before, self.frame()) < 0.004:
                    stuck += 1
                    if stuck >= 2:
                        log.info("прямое наведение: упёрлись через %.1f с", gone)
                        break
                else:
                    stuck = 0
            log.info("прямое наведение не вывело, перехожу к перебору секторов")
            if not self.unstick("прямое наведение к ленте", anchor=anchor):
                return None
            self.nav.face(anchor, tol=60)      # если получится — хорошо, нет — идём так

        full = int(self.nav.kb.units_per_turn or self.FULL_TURN)
        sectors = list(range(0, full, sector))
        log.info("калибровка дороги: %s секторов по %s единиц", len(sectors), sector)
        turned = 0
        for turn in sectors:
            if turn != turned:
                self.hand.look(turn - turned, 0)
                time.sleep(0.5)
                turned = turn
            gone, stuck = 0.0, 0
            while gone < forward:
                before = self.frame()
                self.hand.hold("w", step)
                time.sleep(0.4)
                gone += step
                if self.sees("purchase"):
                    log.info("лента найдена: поворот %s, вперёд %.1f с", turn, gone)
                    return {"поворот": turn, "вперёд": round(gone, 2)}
                moved = self._moved(before, self.frame())
                if moved < 0.004:
                    stuck += 1
                    if stuck >= 2:
                        log.info("сектор %s: упёрлись через %.1f с", turn, gone)
                        break
                else:
                    stuck = 0
            if gone:
                # Возврат РЕСПАВНОМ, а не ходьбой назад.
                #
                # Сперва откатывались тем же временем в обратную сторону, и это
                # выглядело честно: «возврат равен пройденному». На деле назад
                # можно упереться, зацепиться за рельеф, пройти меньше — и
                # каждый следующий сектор стартовал уже из другой точки. За
                # восемь секторов набегало столько, что перебор терял смысл:
                # прогон прошёл все восемь по 9 секунд, нигде не упёрся и ничего
                # не нашёл. Респавн возвращает в спавн базы ТОЧНО.
                log.info("сектор %s не подошёл (%.1f с), возвращаюсь респавном",
                         turn, gone)
                if not self.to_reference(anchor=anchor):
                    log.warning("после сектора %s опорное состояние не взято", turn)
                    return None
                if not self.nav.face(anchor, tol=60):
                    log.warning("после сектора %s на якорь не навёлся", turn)
                    return None
                turned = 0      # камера снова смотрит на якорь, угол обнулился
        log.warning("ни один сектор камеры не вывел к ленте")
        return None

    def walk_belt_route(self, route: dict, anchor: str = "cash_multi") -> bool:
        """Повторить выученную дорогу: навестись на якорь, довернуть, идти."""
        if not self.nav.face(anchor, tol=60):
            log.info("повтор маршрута: на якорь не навёлся, угол будет неверным")
            return False
        if route.get("поворот"):
            self.hand.look(int(route["поворот"]), 0)
            time.sleep(0.5)
        self.hand.hold("w", float(route["вперёд"]))
        time.sleep(0.6)
        return bool(self.sees("purchase"))

    def find_belt(self, max_seconds: float = 15.0, step: float = 1.5) -> tuple[str, float] | None:
        """Найти дорогу до ленты перебором направлений. Возвращает (клавиша, секунды).

        Прямой и честный способ вместо угадывания: пробуем сторону, идём по ней
        шагами по секунде и после каждого смотрим, не появился ли промпт покупки.
        Не появился за отведённое время — возвращаемся ровно на столько же назад и
        пробуем следующую сторону. Никаких кругов: каждый заход начинается из одной
        и той же точки, потому что откат равен пройденному.

        Если сторона вообще не двигает картинку — упёрлись в стену, сразу следующая.
        """
        from .knowledge import signature
        kb = self.nav.kb
        sig = signature(self.nav.see())
        opposite = {"w": "s", "s": "w", "a": "d", "d": "a"}

        # Стороны, которыми отсюда уже упирались в прошлые разы, не пробуем.
        # Каждая такая попытка стоит полутора десятков секунд ходьбы и отката.
        order = [k for k in ("w", "s", "a", "d") if not kb.is_wall(sig, k)]
        skipped = [k for k in ("w", "s", "a", "d") if k not in order]
        if skipped:
            log.info("пропускаю стороны, которыми уже упирались отсюда: %s", skipped)
        if not order:
            log.info("памятью отброшены все стороны — перепроверяю их заново")
            order = ["w", "s", "a", "d"]

        for key in order:
            gone, stuck = 0.0, 0
            log.info("пробую сторону %s", key)
            while gone < max_seconds:
                before = self.frame()
                self.trail_hold(key, step)
                time.sleep(0.4)
                gone += step
                if self.sees("purchase"):
                    log.info("лента найдена: %s за %.1f с", key, gone)
                    kb.note_free(sig, key)
                    kb.save(force=True)
                    return key, gone
                after = self.frame()
                moved = self._moved(before, after)
                log.info("сторона %s: прошёл %.1f с, картинка изменилась на %.4f",
                         key, gone, moved)
                # Один слабый замер ничего не значит: над однотонной зелёной базой
                # ходьба меняет доли процента кадра. Обрываем только если подряд
                # два раза почти ничего — вот это уже стена.
                if moved < 0.004:
                    stuck += 1
                    if stuck >= 2:
                        log.info("сторона %s: упёрлись", key)
                        kb.note_wall(sig, key)
                        break
                else:
                    stuck = 0
            if gone:
                log.info("сторона %s не подошла, откатываюсь на %.1f с", key, gone)
                # Откат тоже в журнал: если лента найдётся следующей стороной,
                # маршрут обязан содержать и возврат, иначе повтор уедет не туда.
                self.trail_hold(opposite[key], gone)
                time.sleep(0.5)
        kb.save(force=True)
        return None

    def errand(self, targets: list[str] | None = None, min_income: float = 1.0,
               bursts: int = 14, burst: float = 1.1) -> dict:
        """Сходить до ленты, купить брейнрота, вернуться и запереть базу.

        Одна цель, прямая линия. Устройство простое:

        1. Респавн — точка старта всегда одна.
        2. Если маршрут уже выучен, просто повторяем его: держим клавишу столько,
           сколько записано. Не сработало — ищем заново и перезаписываем.
        3. Покупка: промпт надо ДЕРЖАТЬ, и проверяется она по деньгам.
        4. Возврат — тем же респавном, он и есть дорога домой.

        Лок идёт ПЕРВЫМ, а не последним: его окно 60 секунд, и поход должен
        уложиться внутрь. Запирать после возвращения бессмысленно — база стояла
        открытой всё время, пока нас не было.
        """
        report = {"дошёл": False, "куплено": None, "вернулся": False, "заперта": None}

        if not self.to_reference():
            report["ошибка"] = "опорное состояние не взято"
            return report

        # Что стоит на базе ДО похода. Это и есть настоящая проверка покупки:
        # купленный брейнрот встаёт на плот, и его видно глазами. Кэшем такое не
        # проверить — HUD показывает четыре значащих цифры ($70.73M), покупка за
        # 20 тысяч меняет их в последнем разряде, а OCR ошибается как раз в
        # разрядах. Сигнал тоньше шума.
        report["на базе до"] = self.base_items()
        log.info("на базе до похода: %s", report["на базе до"] or "пусто")

        # ЛОК — ПЕРВЫМ ДЕЙСТВИЕМ, до всякой ходьбы.
        #
        # Так это делается руками, и так же записано в нашем ресерче: окно лока
        # на нуле ребёрнов всего 60 секунд, и весь поход обязан уложиться в него.
        # У нас же лок стоял ПОСЛЕДНИМ, после возвращения — то есть всё время
        # похода база оставалась открытой, и запирали её тогда, когда красть уже
        # было поздно. Порядок правильный такой: запер, вышел, дошёл, купил,
        # вернулся.
        report["заперта"] = self.lock_base()
        log.info("база заперта на %s с" if report["заперта"] else
                 "запереть базу не вышло — иду всё равно", report["заперта"])

        # 1. Дорога: сперва по памяти, потом калибровкой
        self.trail_start()
        route = self.load_route()
        if isinstance(route, dict) and "вперёд" in route:
            log.info("иду по выученной дороге: поворот %s, вперёд %.1f с",
                     route["поворот"], route["вперёд"])
            report["дошёл"] = self.walk_belt_route(route)
            report["маршрут"] = route
            report["способ"] = "память"
            if not report["дошёл"]:
                log.info("по памяти не вышло — калибрую заново")
        if not report["дошёл"]:
            found = self.aim_belt()
            if found:
                self.save_belt_route(found)
                report["маршрут"] = found
                report["дошёл"] = True
                report["способ"] = "калибровка по секторам"
        route = None if isinstance(route, dict) else route
        if route and not report["дошёл"]:
            log.info("иду по выученному маршруту: %s",
                     " -> ".join(f"{k} {sec:.1f}с" for k, sec in route))
            for key, sec in route:
                self.hand.hold(key, sec)
                time.sleep(0.5)
                # Проверяем промпт после КАЖДОГО колена, а не только в конце:
                # маршрут мог сработать раньше, и лишние шаги уведут мимо.
                if self.sees("purchase"):
                    break
            report["дошёл"] = bool(self.sees("purchase"))
            report["маршрут"] = [{"клавиша": k, "секунд": sec} for k, sec in route]
            if not report["дошёл"]:
                log.info("по памяти не вышло — ищу заново")
        if not report["дошёл"]:
            # Основной способ: навести камеру на товар и идти вперёд. Работает
            # без таблицы направлений — при шифт-локе `w` это «куда смотрит
            # камера». Перебор сторон остаётся запасным путём: он дороже
            # (каждая неудачная сторона это ходьба туда и откат обратно).
            log.info("иду к ленте по камере")
            self.trail_start()
            if self.nav.head_to(("goods", "conveyor"),
                                stop_when=lambda: bool(self.sees("purchase")),
                                legs=14):
                legs = self.trail()
                self.save_route(legs)
                report["маршрут"] = [{"клавиша": k, "секунд": sec} for k, sec in legs]
                report["дошёл"] = True
                report["способ"] = "камера"
        if not report["дошёл"]:
            log.info("по камере не вышло — перебираю стороны")
            self.trail_start()
            found = self.find_belt()
            if not found:
                self.shot("errand_no_belt")
                report["ошибка"] = "ленту не нашёл ни камерой, ни перебором сторон"
                return report
            # В маршрут пишем не найденную прямую, а ВЕСЬ путь из журнала:
            # вместе с тупиковыми сторонами и откатами. Только он и приводит
            # к ленте из той же стартовой точки.
            legs = self.trail()
            self.save_route(legs)
            report["маршрут"] = [{"клавиша": k, "секунд": sec} for k, sec in legs]
            report["дошёл"] = True
            report["способ"] = "перебор сторон"

        # 2. Купить.
        #
        # Промпт МИГАЕТ: по записи прохода руками видно, что он держится около
        # секунды, пока товар проезжает мимо, потом гаснет. Прежний цикл опрашивал
        # раз в полсекунды и приходил, когда окно уже закрылось — отсюда «промпт
        # есть, а купить не успел». Поэтому опрашиваем часто и жмём сразу, как
        # увидели, а решение принимаем по уже прочитанной вывеске.
        cash_before = self.read_hud_cash()
        report["кэш до"] = cash_before
        self.shot("errand_before_buy")

        deadline = time.time() + 45
        tries = 0
        while time.time() < deadline and tries < 8:
            frame = self.frame()
            lines = ocr.lines(frame)
            in_range = any("purchase" in t for t, _, _ in lines)
            if not in_range:
                time.sleep(0.15)
                continue
            # Стоим у ленты — самое время подсмотреть её цвет под текущее
            # оформление. На событиях дорожку перекрашивают, и зашитый диапазон
            # перестаёт её находить; выученный переживает смену скина.
            if self.nav.kb.belt_hsv is None:
                from .nav import sample_belt_hsv
                hsv = sample_belt_hsv(frame)
                if hsv:
                    self.nav.kb.belt_hsv = hsv
                    self.nav.kb.touch()
                    self.nav.kb.save(force=True)
                    log.info("выучил цвет ленты для этого оформления: %s", hsv)
            offer = self.read_offer(lines=lines)
            if not offer.get("ready"):
                time.sleep(0.15)
                continue
            if targets or min_income > 1:
                card = {"name": offer["name"], "rarity": offer["rarity"],
                        "income": offer["income"], "price": offer["price"]}
                if not self.want(card, min_income, None, None, targets):
                    time.sleep(0.2)
                    continue
            tries += 1
            log.info("беру %s%s (%s, доход %s/с, цена %s) — попытка %s",
                     offer["name"],
                     f" [{offer['mutation']}]" if offer.get("mutation") else "",
                     offer["rarity"], offer["income"], offer["price"], tries)
            self.hand.interact(1.4)
            time.sleep(0.8)

            cash_now = self.read_hud_cash()
            # При нынешнем капитале это снова рабочая проверка. Она не годилась
            # при $70M — покупка за $20k была ниже точности HUD, — но на тысячах
            # долларов трата в $25 видна ясно. Кэш сам по себе только растёт.
            if cash_before and cash_now and cash_now < cash_before:
                report["куплено"] = offer["name"]
                report["кэш после"] = cash_now
                log.info("сделка прошла: кэш %s -> %s", cash_before, cash_now)
                break
            log.info("не подтвердилось (кэш %s -> %s), жду следующий товар",
                     cash_before, cash_now)
            cash_before = cash_now or cash_before
        self.shot("errand_after_buy")

        # 3. Домой. Запирать уже не нужно — заперли перед выходом.
        self.reset_to_base()
        report["вернулся"] = self.alive_in_world()
        report["на базе"] = self.base_items()
        # Окончательная проверка: появилось ли на базе то, чего не было.
        before = set(report.get("на базе до") or [])
        appeared = [n for n in report["на базе"] if n not in before]
        report["появилось на базе"] = appeared
        if appeared:
            report["куплено"] = appeared[0]
            log.info("покупка подтверждена базой: появилось %s", appeared)
        elif report.get("жали"):
            log.info("жали %s, но на базе ничего не прибавилось", report["жали"])
        # Отчёт — словарь, и передавать его в log единственным аргументом
        # нельзя: logging принимает такой словарь за отображение для %(...)s
        # и падает на форматировании, теряя всю запись.
        log.info("поход завершён: %s", json.dumps(report, ensure_ascii=False,
                                                  default=str))
        return report

    # ------------------------------------------------------------------
    # Операция 1: сбор кэша
    # ------------------------------------------------------------------

    def collect_labels(self, frame=None) -> list[tuple[float, float]]:
        """Подписи «Collect $N» над брейнротами: доли кадра по x и y.

        Берём только середину кадра: такие же подписи висят над чужими базами
        в том же ряду, и наведение на соседскую уводит с базы.
        """
        fr = self.frame() if frame is None else frame
        h, w = fr.shape[:2]
        # Читаем не полный кадр, а середину С УВЕЛИЧЕНИЕМ. Подписи над
        # брейнротами мелкие и полупрозрачные: на кадре 31.08 глазами видны
        # «Collect $937K» и «Collect $8M», а полнокадровый OCR не находит ни
        # одной — и сбор честно отвечал «собирать нечего» при восьми миллионах
        # на базе.
        band = fr[int(h * 0.10):int(h * 0.80), int(w * 0.18):int(w * 0.85)]
        if not band.size:
            return []
        big = cv2.resize(band, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        bh, bw = big.shape[:2]
        out = []
        for text, x, y in ocr.lines(big):
            t = text.lower()
            if "collect" not in t or "zone" in t:
                continue
            # обратно в доли ПОЛНОГО кадра
            fx = (0.18 + (x / bw) * 0.67)
            fy = (0.10 + (y / bh) * 0.70)
            out.append((fx, fy))
        return out

    # Зелень пада: тот же диапазон, что у надписи наличных, но по площади это
    # крупное пятно, а не строка.
    PAD_HSV = ((35, 120, 120), (85, 255, 255))

    def green_pads(self, frame=None) -> list[tuple[float, float, int]]:
        """Зелёные пады сбора в кадре: (x, y, площадь) в долях кадра.

        Пады — плоские прямоугольники у каждого брейнрота, деньги берутся
        НАСТУПАНИЕМ. Слепой проход по рядам их не задевает: замер 31.08 —
        четыре шага вглубь и стрейфы по 2.5 и 5 секунд дали ноль, зато вынесли
        персонажа из базы в щель между стенами.

        Отсекаем верх кадра (там трава соседних баз и мира) и берём пятна
        шире, чем выше: пад в перспективе всегда вытянут поперёк.
        """
        fr = self.frame() if frame is None else frame
        h, w = fr.shape[:2]
        hsv = cv2.cvtColor(fr, cv2.COLOR_BGR2HSV)
        lo, hi = self.PAD_HSV
        mask = cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8))
        # Верх режем скупо: дальние пады с деньгами лежат высоко в кадре
        # (замер 31.08: «Collect $10.9M» на y около 0.28), и прежняя отсечка по
        # 0.35 выбрасывала ровно их, оставляя пустые пады под ногами.
        mask[: int(h * 0.20), :] = 0
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 15), np.uint8))
        n, _lab, st, cen = cv2.connectedComponentsWithStats(mask, 8)
        out = []
        for i in range(1, n):
            area = int(st[i, cv2.CC_STAT_AREA])
            bw, bh = int(st[i, cv2.CC_STAT_WIDTH]), int(st[i, cv2.CC_STAT_HEIGHT])
            if area < 1200 or bh == 0 or bw < bh:
                continue
            cx, cy = cen[i]
            out.append((cx / w, cy / h, area))
        out.sort(key=lambda p: p[1])           # ДАЛЬНИЕ (выше в кадре) первыми
        return out

    def collect_by_pads(self, budget: float = 40.0) -> float:
        """Собрать деньги, НАВОДЯСЬ на каждый зелёный пад и наступая на него.

        Слепые проходы по рядам не работают (см. green_pads), а подписи
        «Collect $N» мелкие и читаются не всегда. Зато сам пад — крупное
        зелёное пятно, его видно всегда, и приём тот же, что у плиты лока:
        довернуть по горизонтали, шагнуть, повторить.

        Возвращает прирост наличных за отведённые секунды.
        """
        before = self.read_hud_cash()
        end = time.time() + budget
        seen_none = 0
        while time.time() < end:
            fr = self.frame()
            pads = self.green_pads(fr)
            if not pads:
                seen_none += 1
                if seen_none > 3:
                    log.info("падов в кадре нет — осматриваюсь")
                    self.hand.turn_degrees(45)
                    time.sleep(0.4)
                    seen_none = 0
                    continue
                self.hand.hold("w", 0.4)
                time.sleep(0.2)
                continue
            seen_none = 0
            # Цель — САМЫЙ ДАЛЬНИЙ пад: деньги лежат на брейнротах в глубине
            # базы, а под ногами пады пустые. Дойдя до дальнего, мы проходим
            # по всем промежуточным.
            x, y, area = pads[0]
            off = x - 0.5
            # Пад под ногами — он занимает низ кадра и уже не цель.
            if y > 0.88 and area > 40000:
                self.hand.hold("w", 0.45)
                time.sleep(0.2)
                continue
            if abs(off) > 0.06:
                # доли кадра -> градусы через горизонтальный угол обзора
                self.hand.turn_degrees(off * 102.0)
                time.sleep(0.25)
                continue
            self.hand.hold("w", 0.5)
            time.sleep(0.25)
        after = self.read_hud_cash()
        if before is not None and after is not None and after > before:
            gain = after - before
            log.info("собрано падами %.0f (стало %.0f)", gain, after)
            return gain
        log.info("падами собрать не вышло (было %s, стало %s)", before, after)
        return 0.0

    def collect_money(self, rows: int = 3, attempts: int = 2) -> float:
        """Собрать накопленное: пройти ПОПЕРЁК рядов зелёных падов.

        Механика видна на кадре и подтверждена замерами 30.08: у каждого
        брейнрота свой зелёный пад с подписью «Collect $1.6M», и деньги
        забираются, когда НАСТУПАЕШЬ на этот пад. Пады стоят рядами по бокам от
        синей дорожки, поэтому проход по центру не задевает ни одного — три
        круга подряд собирали ноль при трёх миллионах, висевших в кадре.

        Рабочий проход: войти в базу, затем ходить ПОПЕРЁК — стрейфом влево до
        конца ряда и вправо до конца, спускаясь вглубь. Замер: $10 790 000 ->
        $12 890 000 за один стрейф влево, потом до $13 020 000.

        Возвращает прирост наличных.
        """
        for attempt in range(attempts):
            self.reset_to_base()
            time.sleep(1.3)
            self.close_players_table()
            # Пустая база — собирать нечего. Без этой проверки разовый глитч
            # чтения денег («$35.59M» как $5.59M) давал ложный «собрано 60 млн»:
            # `before` выходил заниженным, и следующий верный кадр проходил
            # порог правдоподобия как «прирост». Нет подписей Collect в кадре —
            # выходим сразу, не тратя проход и не рискуя ложным сбором.
            # Подписи — подсказка, а не приговор. Раньше их отсутствие
            # прекращало сбор, и при живых «Collect $8M» на базе бот уходил ни
            # с чем: OCR просто не видел мелкий текст. Судить о сборе надо по
            # ДЕНЬГАМ — чтение наличных теперь этому под стать.
            if not self.collect_labels():
                for _ in range(2):
                    self.hand.hold("w", 0.4)
                    time.sleep(0.2)
                if not self.collect_labels():
                    log.info("подписей Collect не вижу — прохожу вслепую, "
                             "судить буду по деньгам")
            # Вид сверху — только БЕЗ шифт-лока. С ним камера держится сама и
            # наклон вниз уводит в небо; а респавн и так ставит лицом внутрь
            # базы, вдоль рядов падов, что и нужно для сбора.
            if not getattr(self.hand, "shift_lock", False):
                self.face_base_from_top()
            time.sleep(0.4)
            before = self.read_hud_cash()
            log.info("собираю кэш поперёк рядов (было %s)", before)
            best = before
            for row in range(rows):
                for _ in range(4 if row == 0 else 3):
                    self.hand.hold("w", 0.5)
                    time.sleep(0.22)
                # Поперёк: влево через ряд, потом вправо через оба ряда, потом
                # обратно в середину — так пады обеих сторон оказываются под
                # ногами.
                for key, dur in (("a", 1.3), ("d", 2.6), ("a", 1.3)):
                    self.hand.hold(key, dur)
                    time.sleep(0.25)
                now = self.read_hud_cash()
                if before is not None and _plausible_cash(now, before):
                    best = now if best is None else max(best, now)
            after = self.read_hud_cash()
            # Финальное чтение проверяем ТАК ЖЕ, как промежуточные. Без этого
            # разовый сбой («$35.59M» прочитан как 35 млрд — суффикс B вместо M)
            # уходил в прирост и давал «собрано 35 000 000 000». Порог не
            # абсолютный, а кратный: сбор законно умножает деньги в разы (замер
            # $723K -> $7.38M, десятикратно), но не в тысячу.
            cands = [x for x in (after, best)
                     if before is not None and _plausible_cash(x, before)]
            end_value = max(cands) if cands else None
            if before is not None and end_value is not None and end_value > before:
                gain = end_value - before
                log.info("собрано %.0f (стало %.0f)", gain, end_value)
                return gain
            log.info("проход не собрал ничего (заход %d)", attempt + 1)
        return 0.0

    def _read_lock_seconds(self, timeout: float = 4.0) -> int | None:
        """Дождаться строки «You locked your base for N Seconds» и вынуть N."""
        end = time.time() + timeout
        while time.time() < end:
            text = ocr.all_text(self.frame())
            for rx in (LOCK_MSG, LOCK_MSG_RU):
                m = rx.search(text)
                if m:
                    return int(m.group(1))
            time.sleep(0.4)
        return None

    def read_lock_flash(self) -> int | None:
        """Вспышка «You locked your base for N Seconds!» — внизу по центру.

        Замерено пробой 30.08: строка приходит как `уои locked your base for 80`
        на y=0.867, x=0.498 и живёт несколько секунд. Это САМОЕ надёжное
        подтверждение: оно на HUD, а не в мире, и не зависит от того, куда
        смотрит камера. Счётчик у входа исчезает при первом же довороте.

        Читаем узкую полосу, а не кадр целиком: полный OCR 1280x720 стоит
        полсекунды, а вызывать проверку надо после каждого шага.
        """
        frame = self.frame()
        h, w = frame.shape[:2]
        band = frame[int(h * 0.80):int(h * 0.95), int(w * 0.25):int(w * 0.80)]
        if not band.size:
            return None
        text = " ".join(t.lower() for t, _, _ in ocr.lines(band))
        for rx in (LOCK_MSG, LOCK_MSG_RU):
            m = rx.search(text)
            if m:
                value = int(m.group(1))
                if 0 < value <= 300:
                    return value
        return None

    def note_locked(self, seconds: int) -> int:
        """Запомнить лок по своим часам. Возвращает те же секунды."""
        self.lock_seconds = seconds
        self.lock_until = time.time() + seconds
        return seconds

    def lock_left_now(self) -> int:
        """Сколько секунд лока осталось ПО НАШИМ ЧАСАМ, без зрения."""
        return max(0, int(round(self.lock_until - time.time())))

    def lock_confirmed(self) -> int | None:
        """Заперта ли база: сперва вспышка, потом счётчик у входа.

        Порядок важен. Вспышка даёт полную длительность (80), счётчик — остаток
        (78, 77...). Для планирования круга нужна именно длительность.
        """
        got = self.read_lock_flash()
        if got:
            return self.note_locked(got)
        got = self.read_lock_left(quick=True)
        if got:
            return self.note_locked(got)
        return None

    def read_lock_left(self, quick: bool = False) -> int | None:
        """Сколько секунд лока осталось — по счётчику «Locked: Ns» над головой.

        НЕ требуем слова «Locked:»: надпись синяя, и когда она ложится на синюю
        дорожку базы, OCR не видит её вовсе — а сам счётчик «23s» при этом
        читается. Замерено на кадре, где база была заперта, а чтение возвращало
        None и цикл считал лок сорванным.

        Ловим счётчик по форме: одна-три цифры и `s`, В УЗКОЙ центральной
        колонке. Колонка обязательна: подписи дохода на ленте выглядят так же
        («$525/s»), и без неё чтение однажды выдало «заперто, 525 с».
        Величину тоже ограничиваем: лок даёт около 80 секунд, не сотни.

        `quick` — одно быстрое чтение без увеличения кадра. Нужно потому, что
        проверка идёт после КАЖДОГО шага: тщательный вариант с тремя попытками
        и увеличением стоил по несколько секунд на вызов, и круг раздувался со
        сорока секунд до ста семидесяти.
        """
        import re
        counter = re.compile(r"^(\d{1,3})\s*[sс]$")

        def pick(lines, width, lo=0.40, hi=0.62):
            for text, x, y in lines:
                m = counter.match(text.strip().lower())
                if m and lo < x / width < hi:
                    value = int(m.group(1))
                    if 0 < value <= 100:
                        return value
            return None

        attempts = 1 if quick else 3
        for attempt in range(attempts):
            frame = self.frame()
            h, w = frame.shape[:2]
            if quick:
                # Быстрый режим читает ТОЛЬКО полосу над головой, а не весь кадр.
                # Замер по фазам: дошаг занимал 32.4 с из 68.7 — потому что
                # проверка лока на каждой итерации гоняла OCR по всему кадру
                # 1280x720. Счётчик всегда в центральной колонке, и полоса
                # впятеро меньше по площади.
                x0, x1 = int(w * 0.34), int(w * 0.68)
                band = frame[int(h * 0.28):int(h * 0.92), x0:x1]
                if band.size:
                    got = pick(ocr.lines(band), band.shape[1], 0.10, 0.90)
                    if got is not None:
                        return got
                continue
            got = pick(ocr.lines(frame), w)
            if got is not None:
                return got
            if not quick:
                crop = frame[int(h * 0.25):int(h * 0.95), int(w * 0.25):int(w * 0.78)]
                if crop.size:
                    big = cv2.resize(crop, None, fx=2, fy=2,
                                     interpolation=cv2.INTER_CUBIC)
                    got = pick(ocr.lines(big), big.shape[1], 0.28, 0.72)
                    if got is not None:
                        return got
            if attempt < attempts - 1:
                time.sleep(0.35)
        return None

    def base_locked_visually(self, turn_to_check: bool = True) -> bool:
        """Заперта ли база — по красным решёткам на входе.

        Решётки загораются позади, у входа, поэтому если прямо перед носом их
        нет, разворачиваемся и смотрим ещё раз. Порог подобран так, чтобы не
        принять за них розовый пол праздничного оформления: решётки дают
        сплошную яркую красную область, фон — нет.
        """
        from .nav import red_lasers
        share = red_lasers(self.frame())
        if share > 0.02:
            log.info("красные решётки в кадре: %.3f", share)
            return True
        if not turn_to_check:
            return False
        # Развернуться и посмотреть назад: половина оборота — четыре шага по 700.
        for _ in range(4):
            self.hand.look(700, 0)
            time.sleep(0.35)
        share = red_lasers(self.frame())
        log.info("после разворота красного в кадре: %.3f", share)
        return share > 0.02

    # --- локализация видом сверху ---------------------------------------
    #
    # Смысл связки: СВЕРХУ понимаем, где мы и куда идти, ВНИЗУ идём. Вид сверху
    # даёт карту — персонаж ровно в центре кадра, вокруг видна вся база; вид
    # из-за плеча даёт понятное движение (w — вперёд по взгляду). Наклон камеры
    # строго вертикальный, рысканье он не меняет, поэтому пеленг, снятый
    # сверху, остаётся верным после возврата. Это и есть опора, которой не было:
    # ни один ориентир на уровне глаз не годился — вывеска CASH MULTI есть у
    # каждой базы в ряду, YOUR BASE уходит за верхний край, а подпись Lock Base
    # с дальнего конца комнаты слишком мелкая для OCR.

    def pad_from_top(self, frame) -> tuple[float, float, float] | None:
        """Пад под ногами в виде сверху: крупное зелёное пятно разумного размера.

        Проверка ДОЛИ кадра обязательна. На «зелёном» событии игра перекрасила
        землю, маска поймала 66% кадра, и центром пада оказался центр лужайки —
        молча, без признаков ошибки. Пад занимает от 3% до 40%, всё остальное
        отбраковываем.
        """
        h, w = frame.shape[:2]
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        m = cv2.inRange(hsv, np.array((35, 90, 90), np.uint8),
                        np.array((90, 255, 255), np.uint8))
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((11, 11), np.uint8))
        n, _lab, st, cent = cv2.connectedComponentsWithStats(m, 8)
        best, best_area = None, 0
        for i in range(1, n):
            a = int(st[i, cv2.CC_STAT_AREA])
            share = a / float(h * w)
            if 0.03 <= share <= 0.40 and a > best_area:
                best, best_area = (float(cent[i][0]), float(cent[i][1]), share), a
        return best

    def plate_glow(self, frame) -> tuple[float, float, int] | None:
        """Светящаяся плита лока: самое крупное яркое голубое пятно.

        Подпись «Lock Base» с дальнего конца комнаты OCR не читает — мелкая. А
        свечение видно всегда, и по его площади понятно, приближаемся ли мы.

        Кадр НЕ обрезаем сверху, а вычёркиваем помехи по месту. Обрезка по
        `y < 0.38` уже стоила прогона: пока плита далеко, она в верхней части
        кадра, но по мере подхода съезжает вниз и вываливается из рамки —
        на третьем шаге детектор потерял её и вцепился в иконку левого меню
        (x=0.016, площадь 234, неподвижная четыре шага подряд).

        Вычёркиваем: колонку интерфейса слева, правый край с часами и
        собственную фигуру — у персонажа синие волосы, они дают второе яркое
        голубое пятно ровно по центру.
        """
        h, w = frame.shape[:2]
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        # Порог по ЯРКОСТИ и узкому тону, замерено прямо на кадре у плиты:
        # свечение H=90 V=255, волосы персонажа H=107 V=164, синяя дорожка
        # H=119 V=206. То есть свечение — единственное, что одновременно
        # голубее 100 и ярче 240. С таким порогом вырезать фигуру не нужно, а
        # это важно: вблизи свечение оказывается ПОД персонажем и прежний вырез
        # (x 0.42-0.60) стирал его целиком — детектор слеп ровно там, где
        # решается дело.
        m = cv2.inRange(hsv, np.array((80, 120, 240), np.uint8),
                        np.array((100, 255, 255), np.uint8))
        # HUD выключаем целиком, а не по краям: на прошлом прогоне иконка
        # самоцветов в правом нижнем углу (x=0.934, y=0.951, площадь ~950)
        # шесть шагов подряд выдавала себя за плиту, потому что рамка обрезала
        # только x > 0.95, а она чуть левее.
        m[: int(h * 0.06), :] = 0                       # верхняя панель Roblox
        m[int(h * 0.88):, :] = 0                        # нижний HUD: деньги, часы, самоцветы
        m[:, : int(w * 0.20)] = 0                       # левое меню
        m[:, int(w * 0.95):] = 0                        # правый край
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))

        # Плита лежит НА СИНЕЙ ДОРОЖКЕ, а купленные брейнроты стоят на боковых
        # падах. Это единственное устойчивое различие: сами брейнроты тоже
        # голубые и яркие, и когда база перестала быть пустой, их пятна начали
        # выигрывать по размеру. Замерено на кадре с двумя купленными: пятно
        # брейнрота 902 px против 210 px у настоящей плиты — детектор «самого
        # крупного» уверенно брал не то, и лок перестал получаться совсем.
        walk = cv2.inRange(hsv, np.array((105, 80, 90), np.uint8),
                           np.array((135, 255, 255), np.uint8))
        wn, wlab, wst, _wc = cv2.connectedComponentsWithStats(
            cv2.morphologyEx(walk, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8)), 8)
        road = None
        for i in range(1, wn):
            if road is None or wst[i, cv2.CC_STAT_AREA] > wst[road, cv2.CC_STAT_AREA]:
                road = i
        road_mask = (wlab == road) if road is not None else None
        if road_mask is not None:
            road_mask = cv2.dilate(road_mask.astype(np.uint8),
                                   np.ones((41, 41), np.uint8)) > 0

        n, _lab, st, cent = cv2.connectedComponentsWithStats(m, 8)
        best, best_area, fallback, fb_area = None, 0, None, 0
        for i in range(1, n):
            a = int(st[i, cv2.CC_STAT_AREA])
            if a <= 120:
                continue
            cx, cy = float(cent[i][0]), float(cent[i][1])
            if a > fb_area:
                fallback, fb_area = (cx, cy, a), a
            if road_mask is not None and not road_mask[int(cy), int(cx)]:
                continue
            if a > best_area:
                best, best_area = (cx, cy, a), a
        # Если дорожки в кадре нет (например, смотрим снаружи), работаем как
        # раньше — по самому крупному пятну.
        return best if best is not None else fallback

    def face_base_from_top(self) -> float | None:
        """Развернуться лицом внутрь базы, определившись по виду сверху.

        Возвращает снятый пеленг в градусах либо None, если пад не опознан.
        """
        self.hand.pitch_top()
        time.sleep(0.7)
        top = self.frame()
        h, w = top.shape[:2]
        pad = self.pad_from_top(top)
        if not pad:
            log.warning("сверху пад не опознан — разворот отменён")
            self.hand.pitch_normal()
            return None
        px, py, share = pad
        bearing = math.degrees(math.atan2(px - w / 2.0, -(py - h / 2.0)))
        log.info("сверху: пад x=%.3f y=%.3f (%.0f%% кадра), пеленг %+.1f град",
                 px / w, py / h, share * 100, bearing)
        self.hand.pitch_normal(back=self.tuning.view_pitch_back, already_top=True)
        time.sleep(0.5)
        self.hand.turn_degrees(bearing)
        time.sleep(0.6)

        # ЗДЕСЬ БЫЛА ДОВОРОТКА НА 180 по подписям внешнего мира — снята 30.08
        # вечером как вредная. Замер: за 46 дневных локов она не понадобилась ни
        # разу, а вечером срабатывала на КАЖДОМ (подписи соседних баз и Trade
        # Plaza видны и из своей базы). Ценой был не только сам разворот: после
        # него бот честно смотрел наружу, и `lock_via_top` шёл в полный осмотр.
        # Наведение выросло с 0.5 до 16.9 с, лок с 20 до 60-90 с.
        # Неоднозначность пеленга разбирает не текст, а свечение плиты: его
        # ищет `aim_at_plate`, и оно врать не умеет.
        return bearing

    def aim_at_plate(self, tol: float = 0.03, tries: int = 8) -> float | None:
        """Довернуться на плиту, НЕ двигаясь с места. Возвращает остаточный промах.

        Наведение и ходьба обязаны быть раздельными. Когда они шли вместе,
        доворот выходил крошечным (промах 0.15 кадра — это 5 градусов, 52
        единицы мыши), а шаг вперёд большим, и цель уезжала от центра ХОДЬБОЙ:
        идёшь мимо того, что стоит слева, — оно смещается ещё левее. В логе это
        выглядело как «наведение работает наоборот», хотя знак был верный.
        """
        last = None
        misses = 0
        # Масштаб доворота подстраивается ПО ХОДУ. Перевод «пиксели -> единицы
        # мыши» верен только для той дистанции, на которой его мерили: близкие
        # объекты при повороте смещаются сильнее далёких, замеры на одной базе
        # дали 0.354 и 0.185. С одной зашитой цифрой наведение перелетает через
        # цель, и знак промаха скачет: −0.218 -> −0.190 -> +0.211, восемнадцать
        # секунд на то, что делается за полторы.
        # Правило простое: промах сменил знак, а меньше не стал — шаг вдвое
        # короче. Не уменьшается вовсе — шаг в полтора раза длиннее.
        scale = 1.0
        for _ in range(tries):
            fr = self.frame()
            w = fr.shape[1]
            glow = self.plate_glow(fr)
            if not glow:
                # НЕ сдаваться с первого кадра. Сразу после крупного разворота
                # (а он бывает и на 126 градусов) камера ещё едет, и первый
                # кадр приходит смазанным — свечения в нём нет. Из-за этого
                # круг падал с «свечения не видно после разворота», хотя через
                # полсекунды оно находилось: x=0.385, площадь 568.
                misses += 1
                if misses >= 3:
                    return last
                time.sleep(0.4)
                continue
            misses = 0
            off = (glow[0] - w / 2.0) / w
            if last is not None:
                if off * last < 0 and abs(off) > abs(last) * 0.6:
                    scale = max(0.25, scale * 0.5)      # перелёт
                elif off * last > 0 and abs(off) > abs(last) * 0.85:
                    scale = min(2.5, scale * 1.5)       # недолёт
            last = off
            if abs(off) <= tol:
                return off
            # Вблизи доворачиваем СИЛЬНЕЕ, чем говорит поле зрения.
            #
            # Перевод «пиксели -> градусы» верен для оси КАМЕРЫ, а камера стоит
            # позади и выше персонажа. На расстоянии это одно и то же, вблизи —
            # нет: плита в 10 градусах от камеры находится под заметно большим
            # углом от самого персонажа. Замерено по трём одинаковым прогонам:
            # промах 0.043 не добирался порогом, за один шаг разрастался до
            # 0.136, и дошаг терял цель. Близость определяем по площади свечения.
            # Доворачиваем СРАЗУ на весь угол, а не подкрадываемся.
            #
            # Гашение 0.8 с повторами было защитой от автоколебания, но цель
            # компактная (светящийся круг), и колебаться тут не на чем. Зато
            # каждая лишняя итерация — это кадр, поворот и пауза: наведение
            # раздувалось до трёх секунд там, где хватает полусекунды.
            # Вблизи угол от КАМЕРЫ меньше угла от персонажа (камера позади и
            # выше), поэтому там усиливаем.
            near = glow[2] > 8000
            gain = (1.8 if near else 1.0) * scale
            self.hand.look(int(self.nav.units_for_pixels(off * w) * gain), 0)
            time.sleep(0.25)
        return last

    def lock_with_retries(self, attempts: int = 3) -> int | None:
        """Запереть базу, перерождаясь между попытками.

        Правило пользователя: застрял — перерождайся, не долби одну точку.
        Респавн заодно возвращает в известное место у пада, откуда локализация
        сверху работает; из угла за комнатой пад не виден вовсе и разворот
        отменяется.
        """
        shift = getattr(self.hand, "shift_lock", False)
        for i in range(attempts):
            log.info("попытка лока %d из %d", i + 1, attempts)
            self.reset_to_base()
            self.set_work_view()
            left = self.lock_forward() if shift else self.lock_via_top()
            if left:
                return left
        return None

    def lock_forward(self, legs: int = 14, leg: float = 0.4) -> int | None:
        """Лок при ШИФТ-ЛОКЕ: респавн смотрит на плиту, идём прямо вперёд.

        Вид сверху с шифт-локом не работает: камеру держит сам шифт-лок, и
        наклон вниз уводит взгляд в небо и в спину персонажа (замерено 30.08 —
        `face_base_from_top` оставлял камеру задранной, свечение не находилось).

        Зато после респавна персонаж стоит на COLLECT ZONE ЛИЦОМ внутрь базы, и
        плита лока — прямо по курсу в конце синей дорожки. Замер подхода:
        плита стартует по центру (x≈0.50, площадь 146, y≈0.38), растёт и уезжает
        вниз, лок срабатывает при площади ~10000 и y≈0.82 — за пять шагов.
        Наведение с шифт-локом точное (промах 0.014), поэтому доворот не нужен —
        хватает мягкой поправки вбок стрейфом.
        """
        self.dismiss_modals()
        self.close_players_table()
        time.sleep(0.3)
        lost = 0
        for i in range(legs):
            left = self.lock_confirmed()
            if left:
                log.info("база заперта на %d с (шифт-лок, шаг %d)", left, i)
                return left
            fr = self.frame()
            w = fr.shape[1]
            glow = self.plate_glow(fr)
            if not glow:
                lost += 1
                # Пропала — шагаем вперёд вслепую пару раз, плиту часто закрывает
                # порог базы, с шага она появляется. Совсем потеряли — респавн.
                if lost >= 4:
                    log.info("плита не видна %d шагов — на респавн", lost)
                    return None
                self.hand.hold("w", leg)
                time.sleep(0.25)
                continue
            lost = 0
            gx, gy, area = glow
            log.info("шаг %d: плита x=%.3f y=%.3f площадь %d",
                     i, gx / w, gy / w, area)
            # Поправка вбок стрейфом, если плита заметно в стороне.
            off = gx / w - 0.5
            if abs(off) > 0.06:
                key = "d" if off > 0 else "a"
                self.hand.hold(key, min(0.25, abs(off) / self.STRAFE_PER_SEC))
                time.sleep(0.1)
            self.hand.hold("w", leg)
            time.sleep(0.25)
        return self.confirm_lock(tries=2)

    def face_belt_from_top(self) -> float | None:
        """Развернуться от базы наружу, к ленте. Пеленг на пад плюс 180 градусов.

        Наводиться на ленту по её синему цвету НЕЛЬЗЯ: дорожка ВНУТРИ базы
        такая же синяя, и `find_conveyor` уверенно берёт её. Проверено прогоном —
        бот развернулся и ушёл внутрь базы, в кадре появились `lock base` и
        `allow friends`, а «товаром» оказался ложный матч по справочнику.

        Надёжнее геометрия: сверху видно пад, он всегда со стороны базы, значит
        лента — в противоположную сторону.
        """
        self.hand.pitch_top()
        time.sleep(0.7)
        top = self.frame()
        h, w = top.shape[:2]
        pad = self.pad_from_top(top)
        if not pad:
            log.warning("сверху пад не опознан — к ленте не разворачиваюсь")
            self.hand.pitch_normal()
            return None
        px, py, share = pad
        bearing = math.degrees(math.atan2(px - w / 2.0, -(py - h / 2.0)))
        away = bearing + 180.0
        if away > 180.0:
            away -= 360.0
        log.info("сверху: пад x=%.3f y=%.3f, на базу %+.1f, на ленту %+.1f",
                 px / w, py / h, bearing, away)
        self.hand.pitch_normal(back=self.tuning.view_pitch_back, already_top=True)
        time.sleep(0.5)
        self.hand.turn_degrees(away)
        time.sleep(0.8)
        return away

    def inside_base(self, frame=None) -> bool:
        """Мы внутри базы? По подписям, которые есть только там."""
        fr = self.frame() if frame is None else frame
        w = fr.shape[1]
        txt = " ".join(t.lower() for t, x, y in ocr.lines(fr) if x / w > 0.18)
        return "allow" in txt or any(_nav_looks_like(p, "lock base")
                                     for p in (txt, txt.replace(" ", "")))

    # Подписи, которые видны только СНАРУЖИ базы. Если они в кадре, мы смотрим
    # в мир, а не внутрь своей базы.
    OUTSIDE_MARKERS = ("trade plaza", "rng machine", "admin machine", "robux shop",
                       "honey bee", "crystal", "live sp", "'s base", "s base")

    def looking_outside(self, frame) -> bool:
        w = frame.shape[1]
        txt = " ".join(t.lower() for t, x, y in ocr.lines(frame) if x / w > 0.18)
        return any(m in txt for m in self.OUTSIDE_MARKERS)

    def sweep_for_glow(self, steps: int = 12) -> bool:
        """Обойти полный круг и встать на ЛУЧШЕЕ свечение. True, если нашли.

        Раньше осмотр останавливался на первом же пятне выше порога — и брал
        мелкое случайное. Замерено: «плита найдена на шаге 12, площадь 254»,
        после чего наведение не сходилось (промах 0.223) и попытка сгорала.
        Настоящая плита с той же точки даёт 600 и больше.

        Поэтому обходим круг ЦЕЛИКОМ, запоминаем лучшее по площади и
        возвращаемся к нему. Один лишний оборот дешевле, чем сгоревшая попытка:
        оборот стоит секунды три, попытка — двадцать пять.

        Одного свечения мало и по другой причине: снаружи базы в мире полно
        ярких голубых объектов (статуя у Trade Plaza, RNG Machine). Кандидат
        принимается, только если в кадре нет подписей внешнего мира.
        """
        step = int(self.nav.full_turn / steps)
        best_i, best_area = None, 0
        for i in range(steps):
            fr = self.frame()
            glow = self.plate_glow(fr)
            # `looking_outside` гоняет OCR по всему кадру, поэтому зовём её
            # ТОЛЬКО когда есть кандидат. Иначе осмотр стоил 21 секунду вместо
            # пяти: двенадцать шагов, и на каждом полное распознавание текста.
            if glow and glow[2] >= 200 and glow[2] > best_area:
                if not self.looking_outside(fr):
                    best_i, best_area = i, glow[2]
            self.hand.look(step, 0)
            time.sleep(0.25)
        if best_i is None:
            return False
        # Круг пройден целиком, значит мы снова в исходном положении.
        log.info("осмотр: лучшая плита на шаге %d (площадь %d), возвращаюсь",
                 best_i + 1, best_area)
        for _ in range(best_i):
            self.hand.look(step, 0)
            time.sleep(0.12)
        time.sleep(0.3)
        return True

    def lock_via_top(self, walk_legs: int = 8, leg: float = 0.6) -> int | None:
        """Запереть базу: определиться сверху, довернуться, дойти по свечению.

        Порядок ровно тот, что показал пользователь: сперва закрыть базу, потом
        выходить наружу. Подтверждение — счётчик «Locked: Ns» над головой.
        """
        # Уже заперта — идти незачем. Заодно объясняет провал повторного
        # прогона: пока лок держится, плита НЕ светится, и наведение честно
        # отвечает «свечения не видно».
        # Быстрая проверка: тщательная читает кадр трижды с увеличением и стоит
        # секунд шесть-девять — а здесь она вызывается ПЕРЕД каждой попыткой,
        # то есть чаще всего впустую, когда база не заперта.
        # Сперва свои часы: они не врут и ничего не стоят.
        if self.lock_left_now() > 3:
            log.info("база заперта по нашим часам, осталось %d с — иду мимо",
                     self.lock_left_now())
            return self.lock_left_now()
        left = self.read_lock_left(quick=True)
        if left:
            log.info("база уже заперта, осталось %d с — иду мимо", left)
            return self.note_locked(left)

        _t = time.time()
        _phase = {}

        def _mark(name):
            nonlocal _t
            _phase[name] = time.time() - _t
            _t = time.time()

        # Экран должен быть чист ДО всякой навигации. Открытое окно игры
        # перекрывает середину кадра — то есть ровно то место, где живут и вид
        # сверху, и свечение плиты. Проверять это надо здесь, а не полагаться,
        # что кто-то закрыл окно раньше.
        if not self.dismiss_modals():
            log.warning("на экране висит окно игры — лок отменён")
            self.shot("fail_modal_before_lock")
            return None
        self.close_players_table()
        _mark("таблица")
        self.face_base_from_top()
        _mark("разворот")

        # Пеленг сверху даёт грубое направление, дальше работает свечение.
        # Раньше здесь был только осмотр — и этого мало.
        #
        # Пеленг сверху отвалился по той самой причине, о которой уже знали:
        # сменилось оформление, земля стала зелёной травой, и маска пада ловит
        # её целиком — доля кадра больше 40%, детектор отвечает «пада нет».
        # Замерено: четыре попытки подряд «сверху пад не опознан».
        # Плюс пеленг был неоднозначен сам по себе — он указывает на ЦЕНТР пада,
        # а стоя на краю получаешь разворот в противоположную сторону.
        #
        # Свечение плиты от оформления не зависит: это яркий голубой источник
        # (H≈90, V=255), ничего похожего вокруг нет.
        if self.looking_outside(self.frame()):
            # Смотрим наружу — сперва найти базу, потом наводиться.
            if not self.sweep_for_glow():
                log.warning("плиты не видно осмотром")
                return None
        off = self.aim_at_plate()
        if off is None:
            # Дешёвая попытка перед дорогим осмотром: шагнуть вперёд. Плита
            # часто не видна с самого пада — её закрывает порог базы, — а с
            # пары шагов внутрь появляется. Шаг стоит секунду, осмотр — пять.
            self.hand.hold("w", 0.7)
            time.sleep(0.35)
            off = self.aim_at_plate()
        if off is None:
            # Пеленг на пад НЕОДНОЗНАЧЕН: он указывает на центр пада, а мы можем
            # стоять на его дальнем краю — тогда «на базу» выходит ~180 градусов
            # и разворот отворачивает от базы. Замерено: провалы шли ровно при
            # паде под персонажем (y≈0.75, пеленг +153..+175), удача — когда пад
            # сбоку (y≈0.46, пеленг +79).
            if not self.sweep_for_glow():
                log.warning("свечения плиты не видно даже осмотром")
                return None
            off = self.aim_at_plate()
            if off is None:
                return None

        # Идти можно ТОЛЬКО с сошедшимся наведением.
        #
        # Различие резкое и воспроизводимое: промах 0.018 — лок за 32 секунды;
        # промах 0.158 или 0.325 — свечение теряется на первом же шаге, попытка
        # сгорает. Раньше `aim_at_plate` возвращал остаточный промах молча, а
        # вызывающий шёл вперёд при любом значении.
        # Порог мягкий: 0.12, а не 0.06. Точность на этом этапе не нужна —
        # плита срабатывает и от края круга, а остаток промаха доберёт стрейф
        # при проходе. Жёсткий порог заставлял сжигать попытку целиком там, где
        # до цели оставалось семь сотых кадра.
        for _ in range(2):
            if abs(off) <= 0.12:
                break
            log.info("наведение не сошлось (%.3f) — повторяю, не иду", off)
            again = self.aim_at_plate()
            if again is None:
                return None
            off = again
        if abs(off) > 0.12:
            log.warning("наведение так и не сошлось (%.3f) — попытка отменена", off)
            return None
        _mark("наведение")
        log.info("навёлся на плиту, промах %.3f кадра", off)

        lost = 0
        for i in range(walk_legs):
            self.hand.hold("w", leg)
            time.sleep(0.2)
            # Проверяем лок не после каждого отрезка: полное чтение кадра стоит
            # полсекунды, а на подходе базе всё равно неоткуда запереться.
            left = self.lock_confirmed() if i % 2 else None
            if left:
                log.info("база заперта, осталось %d с", left)
                return left
            fr = self.frame()
            glow = self.plate_glow(fr)
            if not glow:
                lost += 1
                log.info("шаг %d: свечения не видно (%d раз подряд)", i + 1, lost)
                # Потеряли цель дважды — значит прошли мимо или упёрлись.
                # Долбиться в одну точку нельзя, переопределяемся сверху.
                if lost >= 2:
                    if self.face_base_from_top() is None:
                        return None
                    self.aim_at_plate()
                    lost = 0
                continue
            lost = 0
            gx, gy, area = glow
            w, h = fr.shape[1], fr.shape[0]
            log.info("шаг %d: плита x=%.3f y=%.3f площадь %d",
                     i + 1, gx / w, gy / h, area)
            # Признак прибытия. Без него бот проходил плиту НАСКВОЗЬ: на прошлом
            # прогоне свечение было уже под ногами (площадь 18499, y=0.66), а он
            # прошагал ещё шесть отрезков и застрял в углу за комнатой.
            # Свечение растёт по мере подхода и уезжает вниз кадра — этого хватает.
            if area > self.ARRIVED_GLOW or gy / h > 0.62:
                _mark("подход")
                log.info("плита рядом — дошагиваю")
                got = self.step_onto_plate()
                _mark("дошаг")
                log.info("ФАЗЫ: %s", ", ".join("%s %.1f" % kv for kv in _phase.items()))
                return got
            # Порог перенаведения тугой: 0.06 пропускал остаточные 0.043, и за
            # один шаг они разрастались втрое — бот проходил мимо плиты.
            # Правим вбок стрейфом, а не доворотом: доворот меняет всю картинку
            # и сбивает уже набранное направление, а сместиться надо чуть-чуть.
            off_now = gx / w - 0.5
            if abs(off_now) > 0.06:
                key = "d" if off_now > 0 else "a"
                self.hand.hold(key, min(0.25, abs(off_now) / self.STRAFE_PER_SEC))
                time.sleep(0.15)
        return self.confirm_lock()

    # Площадь свечения, при которой считаем, что стоим на плите.
    # Пересчитано после ужесточения маски по яркости: дальний конец комнаты даёт
    # ~500, вплотную у плиты — 3400..10800.
    ARRIVED_GLOW = 2500

    # Стрейф: сколько долей кадра проходит свечение за секунду удержания клавиши.
    # Замерено у самой плиты: 0.22 с давали 0.247 кадра, трижды подряд и
    # симметрично для `a` и `d`. Величина зависит от дистанции до цели, поэтому
    # это только первое приближение — дальше правим по обратной связи.
    STRAFE_PER_SEC = 1.12

    def step_onto_plate(self, tries: int = 6) -> int | None:
        """Пройти СКВОЗЬ светящийся круг. Плита срабатывает и от края.

        Подсказка пользователя, и она снимает всю прежнюю возню: не нужно
        вставать точно в центр — достаточно задеть круг, проходя через него.
        Раньше я добивался промаха меньше 0.025 и топтался на месте, потому что
        экранный центр круга на полу не совпадает с точкой, где стоит персонаж;
        на это уходило до тридцати секунд, и всё равно срывалось.

        Теперь: грубо довернуться вбок, если круг заметно в стороне, и идти
        вперёд короткими шагами, проверяя лок после каждого. Проход через круг
        гарантированно задевает его.
        """
        for i in range(tries):
            fr = self.frame()
            w = fr.shape[1]
            glow = self.plate_glow(fr)
            if glow:
                off = (glow[0] - w / 2.0) / w
                # Правим вбок, только если круг ЗАМЕТНО в стороне: точность не
                # нужна, нужно лишь не пройти мимо.
                if abs(off) > 0.09:
                    key = "d" if off > 0 else "a"
                    self.hand.hold(key, min(0.3, abs(off) / self.STRAFE_PER_SEC))
                    time.sleep(0.2)
                log.info("дошаг %d: круг x=%.3f площадь %d", i + 1, glow[0] / w, glow[2])
            else:
                log.info("дошаг %d: круг не виден — иду вперёд", i + 1)
            self.hand.hold("w", 0.22)
            time.sleep(0.25)
            left = self.lock_confirmed()
            if left:
                log.info("база заперта, осталось %d с", left)
                return left
        return self.confirm_lock(tries=2)

    def confirm_lock(self, tries: int = 6, pause: float = 0.7) -> int | None:
        """Дождаться счётчика «Locked: Ns». Плита срабатывает не мгновенно.

        Проверяем несколько раз: одиночное чтение уже давало ложное «не
        подтвердилось» там, где база на самом деле запиралась.
        """
        for _ in range(tries):
            left = self.lock_confirmed()
            if left:
                log.info("база заперта, осталось %d с", left)
                return left
            time.sleep(pause)
        return None

    def lock_base(self, attempts: int = 2) -> int | None:
        """Запереть базу лазерами. Возвращает N секунд лока, None — не подтвердилось.

        Игра сама пишет «You locked your base for N Seconds!» — это и проверка, и
        источник расписания следующего лока. Лучший чек из всех операций.
        """
        for attempt in range(1, attempts + 1):
            # aim=False: респавн и так ставит нас лицом к плите, а наведение на
            # вывеску может утащить камеру на соседнюю базу.
            if not self.to_reference(aim=False):
                return None
            # Камеру НЕ трогаем вовсе — просто идём вперёд.
            #
            # Разбор записи показа: сразу после респавна персонаж стоит на паду
            # COLLECT ZONE и УЖЕ смотрит внутрь базы, синяя плита прямо по курсу,
            # вывеска CASH MULTI в центре кадра. Идти надо прямо, и всё.
            #
            # Уводило нас наведение на якорь: у соседних баз вывески такие же, и
            # `face` цеплялся за чужую, разворачивая камеру в поле. Бот после
            # этого честно шагал восемь секунд не туда. Свой же разворот на 180°,
            # который я добавил следом, лечил симптом и ломал верные случаи.
            # РАЗВЕРНУТЬСЯ. После респавна камера смотрит НАРУЖУ базы: на кадре
            # видны чужие базы, площадь, а надпись COLLECT ZONE читается вверх
            # ногами. Дорожка и плита остаются за спиной, отсюда честное
            # «дорожки не видно» на первом же отрезке.
            #
            # Разворот я уже делал утром по подсказке пользователя, потом снял —
            # обманувшись кадром, снятым после опорного состояния С наведением
            # на вывеску. Там камера действительно смотрит внутрь. Но в локе
            # наведение выключено, и это другая ситуация.
            #
            # Раньше разворот всё равно бы не сработал: ввод не доходил до игры.
            # Теперь он измерен и проверен смыканием круга.
            # Дорога к плите — это ДВА ЧИСЛА: угол от спавна и число шагов.
            # Найдены перебором направлений и подтверждены локом: 210°, три шага.
            # Никакого зрения тут не нужно — спавн один и тот же, плита на месте.
            full = int(self.nav.kb.units_per_turn or self.FULL_TURN)
            heading = self.nav.kb.lock_heading
            steps = self.nav.kb.lock_steps
            if heading and steps:
                # СНАЧАЛА точка отсчёта, потом угол.
                #
                # Разворот после респавна НЕ постоянен — замерено пятью
                # респавнами подряд: совпадение вида с первым 0.082..0.105 при
                # нулевом сдвиге, то есть каждый раз другой вид. Значит угол
                # «от спавна» ни к чему не привязан, и то, что лок дважды
                # сработал на 210°, было везением.
                #
                # Отсчёт даёт зрение: наводимся на вывеску базы, и уже от неё
                # отмеряем угол. Наведение теперь угловое и работает.
                if not self.nav.face("cash_multi", tol=70):
                    log.info("на вывеску не навёлся — угол отсчитывать не от чего")
                else:
                    log.info("запираю базу: от вывески %s град, %s шагов",
                             heading, steps)
                units = int(heading / 360.0 * full)
                for _ in range(max(1, abs(units) // 1000)):
                    self.hand.look(units // max(1, abs(units) // 1000), 0)
                    time.sleep(0.15)
                time.sleep(0.5)
                for step in range(1, steps + 3):
                    self.hand.hold("w", 0.7)
                    time.sleep(0.3)
                    sec = self._read_lock_seconds() or self.read_lock_left()
                    if sec:
                        log.info("база заперта на %s с (шаг %s)", sec, step)
                        return sec
                log.info("по выученной дороге не сработало — ищу заново")

            half = full // 2
            log.info("запираю базу: разворачиваюсь на %s единиц и иду к плите", half)
            for _ in range(5):          # длинную протяжку игра теряет, дробим
                self.hand.look(half // 5, 0)
                time.sleep(0.15)
            time.sleep(0.5)

            # После разворота камера смотрит ВДОЛЬ базы, а не на неё: на кадре
            # дорожка у правого края, пад COLLECT ZONE справа. Не хватает ещё
            # примерно четверти оборота — но гадать не надо, вывеска в кадре
            # есть (замерено: x=857 при центре 640). Доводим наведением, оно
            # теперь угловое и работает.
            if self.nav.face("cash_multi", tol=90):
                log.info("навёлся на вывеску базы — иду к плите")
            else:
                log.info("на вывеску не навёлся, иду как есть")

            def locked():
                return bool(self._read_lock_seconds() or self.read_lock_left())

            # Идём на САМУ ПОДПИСЬ, а не вслепую вперёд. Плита стоит в дальнем
            # конце синей дорожки и СБОКУ от направления взгляда после респавна —
            # слепой ход вперёд уводил на плиты слотов и упирался в стену.
            # Подпись «Lock Base» в кадре есть, просто OCR корёжит её в
            # «цое к в ase»; теперь она узнаётся нечётким сравнением.
            # Цели две, по убыванию точности. Подпись «Lock Base» указывает на
            # саму плиту, но она мелкая: бот теряет её через пару шагов и потом
            # не находит за полный оборот. Синяя дорожка ведёт к той же плите,
            # видна с любого расстояния и не зависит от OCR — она и подхватывает,
            # когда подпись пропала.
            #
            # Внутри базы срабатывание детектора «конвейер» на этой дорожке —
            # не помеха, а ровно то, что нужно: настоящая лента далеко снаружи.
            # Дистанция взята из записи прохода руками, а не с потолка.
            # В уроке `to_lock` человек идёт вперёд ТРЕМЯ рывками общей длиной
            # около четырёх секунд (1.56 + 0.98 + 1.46), между ними доворачивая
            # камеру. Бот же шагал до двенадцати отрезков по 0.8 с — почти
            # десять секунд — и пролетал плиту насквозь, упираясь в стену
            # снаружи базы. Шесть отрезков дают те же ~4.8 с с небольшим запасом.
            # Идём ВДОЛЬ дорожки, а не к её центру.
            #
            # Наведение на центр приводило ровно в середину дорожки, где бот и
            # топтался: плита-то в дальнем конце. Поэтому доворачиваем по
            # РАЗНИЦЕ между тем, где дорожка вдали, и тем, где она под ногами —
            # это её направление, а не положение.
            from .nav import path_direction
            for leg in range(7):
                if locked():
                    sec = self._read_lock_seconds() or self.read_lock_left()
                    log.info("база заперта на %s с (вдоль дорожки, отрезок %s)",
                             sec, leg)
                    return sec
                d = path_direction(self.frame(), self.nav.kb.belt_hsv)
                if d is None:
                    log.info("отрезок %s: дорожки не видно", leg + 1)
                    break
                err, under = d
                log.info("отрезок %s: дорожка ведёт на %+d px, под ногами: %s",
                         leg + 1, err, "да" if under else "нет")
                if abs(err) > 60:
                    move = -self.nav.units_for_pixels(err * 0.5)
                    self.hand.look(max(-1500, min(1500, move)), 0)
                    time.sleep(0.3)
                before = self.frame()
                self.hand.hold("w", 0.8)
                time.sleep(0.35)
                if self._moved(before, self.frame()) < 0.004:
                    log.info("отрезок %s: упёрлись — конец дорожки", leg + 1)
                    break

            if locked():
                sec = self._read_lock_seconds() or self.read_lock_left()
                log.info("база заперта на %s с", sec)
                return sec

            if self.nav.head_to(("lock", "conveyor"), stop_when=locked,
                                legs=4, leg=0.8):
                sec = self._read_lock_seconds() or self.read_lock_left()
                log.info("база заперта на %s с", sec)
                return sec

            # Не дошли по подписи — последняя попытка вслепую: прямо и вбок.
            gone, jumped = 0.0, False
            while gone < 3.0:      # вслепую — не дальше, чем прошёл человек
                before = self.frame()
                self.hand.hold("w", 0.8)
                time.sleep(0.35)
                gone += 0.8
                if locked():
                    sec = self._read_lock_seconds() or self.read_lock_left()
                    log.info("база заперта на %s с (вслепую, %.1f с)", sec, gone)
                    return sec
                if self._moved(before, self.frame()) < 0.004:
                    if jumped:
                        break
                    jumped = True
                    self.hand.jump()
                    time.sleep(0.5)
            log.warning("подтверждения лока нет (попытка %s)", attempt)
            self.shot("fail_lock")
        return None

    # ------------------------------------------------------------------
    # Операция 3: покупка с конвейера
    # ------------------------------------------------------------------

    def _read_prompt_boosted(self, px: int, py: int) -> list[str]:
        """Перечитать окрестность промпта с усилением контраста.

        Промпт рисуется там, где стоит объект, поэтому область берём не
        фиксированную, а вокруг найденного слова «Purchase». Имя висит строкой
        выше, цена — рядом с ним.
        """
        import cv2
        import numpy as np
        frame = self.frame()
        h, w = frame.shape[:2]
        x0, x1 = max(0, px - 320), min(w, px + 320)
        y0, y1 = max(0, py - 130), min(h, py + 20)
        crop = frame[y0:y1, x0:x1]
        if crop.size == 0:
            return []
        big = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        gray = cv2.cvtColor(big, cv2.COLOR_BGR2GRAY)
        # Текст промпта светлее своей плашки, но темнее белого HUD. Порог берём
        # не абсолютный, а от самой картинки: плашка бывает разной яркости.
        thr = max(120, int(np.percentile(gray, 92)) - 30)
        _, th = cv2.threshold(gray, thr, 255, cv2.THRESH_BINARY)
        out = [t for t, _, _ in ocr.lines(cv2.cvtColor(th, cv2.COLOR_GRAY2BGR))
               if len(t.strip()) > 1]
        return out

    # Мутации: они пишутся ОТДЕЛЬНОЙ строкой над именем на крупной вывеске и
    # меняют доход в разы. В справочнике доход расписан по этим же ключам.
    MUTATIONS = ("diamond", "gold", "candy", "lava", "galaxy", "cyber", "cursed",
                 "radioactive", "bloodrot", "yin yang", "rainbow", "celestial")

    # Редкости, как их корёжит OCR крупным шрифтом: «common» приходит кириллицей.
    RARITY_HINTS = {
        "common": "common", "соттоп": "common", "сотто": "common",
        "rare": "rare", "гаге": "rare",
        "epic": "epic", "ерic": "epic", "еріс": "epic",
        "legendary": "legendary", "mythic": "mythic",
        "brainrot": "brainrot", "secret": "secret",
    }

    def read_offer(self, frame=None, lines=None) -> dict:
        """Что предлагает лента — по КРУПНОЙ вывеске над товаром.

        Разделение труда между двумя источниками на экране. Крупная вывеска
        висит над товаром и читается уверенно: имя, мутация, редкость. Мелкий
        промпт внизу даёт цену одной строкой («noobini pizzanini $25») — там она
        чище всего. Доход из кадра не берём вовсе: в вывеске он приходит как
        «$215» вместо «$2/s», а в справочнике он точный и разложен по мутациям.

        Мутация тут не украшение: у Noobini Pizzanini обычный доход 4/с, а
        galaxy — 7/с. Решение «брать или нет» без неё неполное.
        """
        if lines is None:
            lines = ocr.lines(self.frame() if frame is None else frame)
        cat = catalog()

        named = [(t, x, y, cat.match(t)) for t, x, y in lines]
        named = [(t, x, y, it) for t, x, y, it in named if it]
        if not named:
            return {"ready": False}
        # Вывеска висит НАД товаром, то есть выше всех совпадений с именем.
        named.sort(key=lambda r: r[2])
        _, nx, ny, item = named[0]

        mutation = rarity = None
        for text, x, y, in ((t, x, y) for t, x, y in lines):
            if abs(x - nx) > 160 or abs(y - ny) > 170:
                continue
            low = text.strip()
            if mutation is None:
                for m in self.MUTATIONS:
                    if m in low:
                        mutation = m.replace(" ", "_")
                        break
            if rarity is None:
                for key, value in self.RARITY_HINTS.items():
                    if key in low:
                        rarity = value
                        break

        # Цена — из строки промпта: там она рядом с именем и без «/s».
        # Цену берём ТОЛЬКО из строки про этот же товар. На ленте едет несколько
        # штук разом, их подписи попадают в один кадр, и «первая строка с $»
        # давала цену соседа: Noobini Pizzanini за 25 читался как 100000.
        price = None
        for text, x, y in lines:
            if "$" not in text:
                continue
            hit = cat.match(text)
            if hit is None or hit.name != item.name:
                continue
            value = ocr.parse_amount(text.split("$")[-1])
            if value:
                price = value
                break

        income = item.income.get(mutation) if mutation else None
        if income is None:
            income = item.base_income
        return {"ready": True, "item": item, "name": item.name,
                "mutation": mutation, "rarity": rarity or item.rarity,
                "income": income, "price": price}

    def read_card(self) -> dict:
        """Что предлагают купить прямо сейчас: имя, цена, редкость, доход.

        Разобрано по живой игре. Над каждым брейнротом на ленте висит табличка
        «имя / редкость / цена», а когда подходишь вплотную, появляется промпт:

            noobini pizzanini $25
            purchase

        Читаем именно промпт: он означает «этот в зоне действия», а табличка над
        дальним товаром — нет. Редкость и доход из кадра НЕ вытягиваем: имя есть в
        справочнике, а там эти поля точные. OCR ошибается, справочник — нет.
        """
        lines = ocr.lines(self.frame())
        # Не точное равенство, а вхождение в КОРОТКОЙ строке. Точное равенство
        # ломается от любого мусора рядом («purchase.», «e purchase»), и в
        # прогоне 03:20–03:35 бот стоял у ленты пять кругов подряд, ни разу не
        # прочитав карточку: at_belt видел «purchase» вхождением, а read_card
        # требовал равенства. Ограничение по длине отсекает подсказки чата, где
        # это слово встречается в предложении.
        prompt = next(((t, x, y) for t, x, y in lines
                       if "purchase" in t.lower() and len(t.strip()) <= 24), None)
        if not prompt:
            return {"item": None, "name": None, "price": None, "rarity": None,
                    "income": None, "ready": False}
        _, px, py = prompt
        # Строки над словом «purchase»: имя с ценой идёт прямо над ним, но OCR
        # рвёт длинные имена на части и путает регистр, поэтому берём полосу
        # пошире и отдаём справочнику все варианты сразу — он выберет похожее.
        above = [(t, x, y) for t, x, y in lines
                 if 0 < py - y < 120 and abs(x - px) < 340]
        above.sort(key=lambda r: py - r[2])
        texts = [t for t, _, _ in above]
        item = catalog().match_any(texts) if texts else None
        text = texts[0] if texts else ""
        price = None
        for t in texts:
            if "$" in t:
                price = ocr.parse_amount(t.split("$")[-1])
                if price:
                    break
        if item is None:
            # Имя в промпте написано мелким СЕРЫМ по серому, и в общем проходе
            # OCR его не видит вовсе — в кадр попадает только контрастная цена.
            # Проверено на живой игре: при промпте «Lirilì Larilà $250» читался
            # один «$250», и покупка не состоялась, хотя бот стоял вплотную.
            # Лечится тем же приёмом, что и наличные в HUD: вырезать окрестность
            # промпта, увеличить и загнать в чёрно-белое по порогу.
            boosted = self._read_prompt_boosted(px, py)
            if boosted:
                item = catalog().match_any(boosted)
                if item is None:
                    log.info("промпт есть, но имя не опознано даже с подсветкой: %s",
                             " | ".join(boosted[:4]))
                else:
                    log.info("имя опознано со второго прохода: %s", item.name)
                    texts = boosted + texts
                    if price is None:
                        for t in boosted:
                            if "$" in t:
                                price = ocr.parse_amount(t.split("$")[-1])
                                if price:
                                    break
            elif texts:
                log.info("промпт есть, но имя не опознано: %s", " | ".join(texts[:4]))
        return {"item": item, "name": item.name if item else None,
                "rarity": item.rarity if item else None,
                "income": item.base_income if item else None,
                "price": price, "ready": True, "text": text}

    def want(self, card: dict, min_income: float, min_rarity: str | None,
             max_price: float | None = None,
             targets: list[str] | None = None) -> bool:
        """Брать ли этот товар. Решение по справочнику, а не по одному порогу.

        targets — адресный список имён. Нужен, когда охотимся за конкретными:
        для Rebirth 1 требуются именно **Trippi Troppi** и **Gangster Footera**,
        и брать всё подряд ради них бессмысленно — база не резиновая.
        """
        cat = catalog()
        item = card.get("item")
        if item is None:
            return False
        if targets:
            from .brainrots import normalize
            want_set = {normalize(t) for t in targets}
            return normalize(item.name) in want_set
        if max_price and card.get("price") and card["price"] > max_price:
            return False
        if min_rarity:
            need = cat.rank(min_rarity)
            if need >= 0 and cat.rank(item.rarity) >= need:
                return True
        return (item.base_income or 0) >= min_income

    def buy_at_conveyor(self, min_income: float = 100, min_rarity: str | None = None,
                        seconds: float = 60.0, max_price: float | None = None,
                        targets: list[str] | None = None) -> int:
        """Стоять у ленты и покупать подходящих. Возвращает число покупок.

        Лента сама подвозит товар, ходить не нужно — нужно только стоять в зоне
        промпта и вовремя жать E. Покупка засчитывается по ПАДЕНИЮ КЭША: промпт мог
        погаснуть в момент нажатия, потому что лента уехала.
        """
        if not self.to_reference():
            return 0
        if not self.walk_until("purchase", landmark="goods"):
            self.shot("fail_goto_conveyor")
            return 0
        self.hand.move(13, 65)   # курсор в угол, чтобы не перекрывал надписи

        if targets:
            log.info("охочусь адресно за: %s", ", ".join(targets))
        bought, misses, skipped = 0, 0, 0
        end = time.time() + seconds
        while time.time() < end:
            card = self.read_card()
            if not card["ready"]:
                time.sleep(0.25)
                continue
            if not self.want(card, min_income, min_rarity, max_price, targets):
                skipped += 1
                time.sleep(0.4)
                continue
            # Кэш читаем через лидерборд, а не из HUD: крупное «$100.1K» нарисовано
            # стилизованным шрифтом с обводкой, и OCR его берёт через раз, а без
            # числа покупку не подтвердить. Пара нажатий Tab дешевле, чем слепота.
            before = self.read_cash()
            log.info("беру %s (%s, доход %s/с, цена %s)", card["name"],
                     card["rarity"], card["income"], card["price"])
            self.hand.interact()
            time.sleep(0.9)
            after = self.read_cash()
            if before is not None and after is not None and after < before:
                bought += 1
                misses = 0
                log.info("куплено, кэш %.0f -> %.0f", before, after)
            else:
                misses += 1
                if misses >= 5:
                    log.warning("покупки не проходят — возвращаюсь к ленте")
                    self.shot("fail_buy")
                    if not (self.to_reference() and
                            self.walk_until("purchase", landmark="goods")):
                        break
                    self.hand.move(13, 65)
                    misses = 0
            time.sleep(0.3)
        log.info("за проход: куплено %s, пропущено %s", bought, skipped)
        return bought

    # ------------------------------------------------------------------
    # Операция 5: ребёрн
    # ------------------------------------------------------------------

    def open_menu_item(self, word: str, timeout: float = 5.0) -> bool:
        """Кликнуть пункт левого меню (Shop / Rebirth / Index / Codes) по подписи.

        Шаблонов не заводим: подписи читаются OCR, а иконки меняются от обновлений.
        Ищем строго в колонке меню — иначе «rebirth» ловится в заголовке лидерборда
        «Rebirths», и бот кликает в таблицу игроков.
        """
        end_at = time.time() + timeout
        b = self.window.client_box()
        while time.time() < end_at:
            frame = self.frame()
            found = [(t, x, y) for t, x, y in
                     ocr.lines(frame, self._box(self.tuning.menu_box))
                     if word.lower() in t]
            if not found:
                # Вырез меню мелкий, и OCR по нему промахивается: подпись
                # «Rebirth» читается с полного кадра («nebirth»), а с выреза —
                # нет. Замерено 30.08: три захода подряд «пункт меню не найден»
                # при том, что пункт на экране. Поэтому второй заход — по всему
                # кадру с отбором по месту: колонка меню это x < 0.20, а
                # заголовок лидерборда «Rebirths» стоит справа и не мешает.
                found = [(t, x, y) for t, x, y in ocr.lines(frame)
                         if word.lower() in t
                         and x < b.width * 0.20 and b.height * 0.25 < y < b.height * 0.72]
            if found:
                _t, xc, yc = found[0]
                self.hand.click(xc, yc, hold=0.1)
                time.sleep(1.0)
                return True
            time.sleep(0.3)
        log.warning("пункт меню %r не найден", word)
        return False

    # Кириллические двойники в числах. OCR русской локали подставляет их в
    # латинские слова и цифры: «$ 134.зк $ 12.5м» — это «$134.3K / $12.5M».
    # Из-за этого прежний разбор требований возвращал None и оба порога — сумма
    # и предметы — не проверялись вовсе.
    _DIGIT_GLYPHS = str.maketrans({"з": "3", "З": "3", "о": "0", "О": "0",
                                   "к": "k", "К": "k", "м": "m", "М": "m",
                                   "б": "6", "В": "b", "Т": "t"})

    def _requirement_boxes(self, frame) -> list[tuple[int, int, int, int]]:
        """Коробки требуемых брейнротов в окне ребёрна, слева направо.

        Ищем контурами, а не по зашитым координатам: число требований меняется
        от уровня к уровню, и при одном или трёх предметах ряд смещается.
        Коробки — тёмные почти квадратные плашки на светлой панели.
        """
        h, w = frame.shape[:2]
        y0, y1 = int(h * 0.545), int(h * 0.66)
        x0, x1 = int(w * 0.28), int(w * 0.73)
        band = frame[y0:y1, x0:x1]
        if not band.size:
            return []
        gray = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)
        dark = cv2.threshold(gray, 80, 255, cv2.THRESH_BINARY_INV)[1]
        cnts, _ = cv2.findContours(dark, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        boxes = []
        for c in cnts:
            x, y, bw, bh = cv2.boundingRect(c)
            if not (35 <= bw <= 150 and 35 <= bh <= 150):
                continue
            if not 0.6 < bw / bh < 1.6:
                continue
            boxes.append((x0 + x, y0 + y, bw, bh))
        boxes.sort()
        return boxes

    def _read_requirement_box(self, frame, box) -> tuple[str | None, float]:
        """Имя из подписи коробки и «насыщенность» иконки.

        Подпись мелкая: полный кадр её не берёт вовсе, а увеличение цветного
        куска даёт кириллицу («тгиптего»). Работает бинаризация порогом 160 с
        увеличением в шесть раз — проверено перебором на живом кадре, оба имени
        прочитаны верно. Держим несколько вариантов: подпись бывает и светлее.

        Вторым числом отдаём насыщенность иконки: невыполненное требование
        игра рисует ЧЁРНЫМ силуэтом, выполненное — цветным.
        """
        x, y, bw, bh = box
        crop = frame[y:y + int(bh * 0.42), x:x + bw]
        icon = frame[y + int(bh * 0.42):y + bh, x:x + bw]
        sat = 0.0
        if icon.size:
            hsv = cv2.cvtColor(icon, cv2.COLOR_BGR2HSV)
            sat = float(hsv[:, :, 1].mean())
        if not crop.size:
            return None, sat
        cat = catalog()
        for scale, mode in ((6, "порог"), (6, "цвет"), (4, "порог"), (8, "цвет")):
            big = cv2.resize(crop, None, fx=scale, fy=scale,
                             interpolation=cv2.INTER_LANCZOS4)
            if mode == "порог":
                g = cv2.cvtColor(big, cv2.COLOR_BGR2GRAY)
                im = cv2.cvtColor(cv2.threshold(g, 160, 255, cv2.THRESH_BINARY)[1],
                                  cv2.COLOR_GRAY2BGR)
            else:
                im = big
            got = [t for t, _, _ in ocr.lines(im)]
            if not got:
                continue
            it = cat.match(" ".join(got)) or cat.match_any(got)
            if it:
                return it.name, sat
        return None, sat

    def read_rebirth_window(self) -> dict:
        """Что написано в окне ребёрна: сумма и именные брейнроты.

        Имена НЕ ищем скользящим окном по всему тексту окна — так из обрывков
        «ва nini» рождался несуществующий «Tenini Ballini». Читаем каждую
        коробку требования отдельно.
        """
        frame = self.frame()
        lines = [t for t, _, _ in ocr.lines(frame)]
        # Прогресс написан одной строкой «$ 134.3K / $ 12.5M»: слева накоплено,
        # справа нужно. Слэш OCR теряет, поэтому опираемся на два знака доллара,
        # а не на разделитель.
        have = need_cash = None
        for t in lines:
            s = t.translate(self._DIGIT_GLYPHS).lower()
            amounts = re.findall(r"\$\s*([\d.,]+\s*[kmbt]?)", s)
            if len(amounts) >= 2:
                have = ocr.parse_amount(amounts[0])
                need_cash = ocr.parse_amount(amounts[1])
                break
        names, sats = [], {}
        for box in self._requirement_boxes(frame):
            name, sat = self._read_requirement_box(frame, box)
            if name and name not in names:
                names.append(name)
                sats[name] = round(sat, 1)
        # НЕ ХВАТАЕТ только тех, чья иконка ТЁМНАЯ. Насыщенность разводит это
        # уверенно: замер на живом окне 31.08 — купленный Trulimero Trulicina
        # даёт 200.0 (цветная иконка с зелёной галкой), некупленный
        # Chimpanzini Bananini — 17.4 (силуэт). Раньше сюда шли ВСЕ имена
        # подряд, насыщенность только писалась в отчёт, и ребёрн не мог
        # состояться ни при каком раскладе: список требований никогда не
        # пустел.
        missing = [n for n in names if sats.get(n, 0.0) < 60.0]
        return {"have_cash": have, "need_cash": need_cash,
                "need_items": missing, "items_all": names,
                "item_saturation": sats, "lines": lines}

    def rebirth(self) -> bool:
        """Сделать перерождение, если требования выполнены.

        ВАЖНО и неочевидно: ребёрн ОБНУЛЯЕТ базу. Поэтому порядок всегда такой —
        сперва отдать всё ценное складу (scenarios/steal.py), потом ребёрн, потом
        забрать обратно. Именно так это делает сообщество, и отсюда же взялась вся
        идея передачи кражей.

        Успех проверяем по счётчику Rebirths в лидерборде: он должен вырасти.
        """
        # Раньше здесь стояло `to_reference()` — тяжёлый выход в опорное
        # состояние с наведением на вывеску. Для того чтобы нажать кнопку в
        # левой колонке, он не нужен, а падает и стоит секунд сорок. Достаточно
        # чистого экрана и известной камеры.
        self.reset_to_base()
        time.sleep(1.4)
        self.set_work_view()
        self.close_players_table()
        self.dismiss_modals()
        before = (self.me() or {}).get("rebirths")
        # «Rebirth» OCR стабильно читает как «nebirth» — r превращается в n.
        # Поэтому ищем по куску «birth»: он выживает при любой такой подмене.
        if not self.open_menu_item("birth"):
            self.shot("fail_rebirth_menu")
            return False

        info = self.read_rebirth_window()
        log.info("окно ребёрна: накоплено %s из %s, нужны предметы %s",
                 info["have_cash"], info["need_cash"], info["need_items"])
        self.shot("rebirth_window")

        have = info["have_cash"]
        if have is None:
            have = self.read_cash()
        if info["need_cash"] and have is not None and have < info["need_cash"]:
            log.info("рано: накоплено %.0f из %.0f", have, info["need_cash"])
            self.dismiss_modals()
            return False

        # Именные требования тоже надо соблюсти, а не только сумму. Раньше
        # `need_items` читались, писались в лог и на решение не влияли — то есть
        # подтверждение жалось вслепую. Для действия, которое ОБНУЛЯЕТ базу, это
        # недопустимо: если игра вдруг не потребует предметов, всё стоящее на
        # плоту пропадёт молча.
        # `need_items` — это уже СПИСОК НЕДОСТАЮЩИХ: окно рисует купленное
        # цветной иконкой с галкой, некупленное — тёмным силуэтом, и
        # насыщенность разводит их уверенно (200.0 против 17.4). Сверять это
        # ещё и по подписям на базе не нужно: там OCR путается, а окно —
        # источник первой руки.
        if info["need_items"]:
            log.info("рано: не хватает %s (в окне: %s)",
                     info["need_items"], info.get("items_all"))
            self.dismiss_modals()
            return False

        # Последняя развилка перед необратимым. Всё, что стоит на плоту, ребёрн
        # сотрёт; сначала это надо унести складу (scenarios/steal.py).
        doomed = [n for n in self.base_items() if n not in (info["need_items"] or [])]
        if doomed and not self.allow_wipe:
            log.warning("ОСТАНОВЛЕН: ребёрн сотрёт с базы %s. Если это осознанно — "
                        "запускай с --allow-wipe", doomed)
            self.shot("rebirth_would_wipe")
            self.dismiss_modals()
            return False

        # кнопка подтверждения — единственная со словом rebirth внутри окна
        if not self._menu_click("rebirth", timeout=3.0, exact=True):
            log.warning("кнопку подтверждения ребёрна не нашёл")
            self.shot("fail_rebirth_confirm")
            self.dismiss_modals()
            return False
        time.sleep(4.0)
        self.dismiss_modals()

        after = (self.me() or {}).get("rebirths")
        ok = before is not None and after is not None and after > before
        log.info("ребёрн: было %s, стало %s -> %s", before, after,
                 "получилось" if ok else "не подтвердилось")
        return ok

    # ------------------------------------------------------------------
    # Снаряжение из игрового магазина
    # ------------------------------------------------------------------

    def read_shop(self, walk: bool = True) -> list[tuple[str, str]]:
        """Прочитать ассортимент палатки: что продают и почём.

        Отдельно от `buy_gear`, потому что до покупки нужен другой вопрос — ЧТО
        там вообще есть. Из чужих скриптов известны имена предметов, которых мы
        в палатке не видели: Quantum Cloner (он же телепорт, судя по имени его
        ремоута), Medusa's Head, Invisibility Cloak. Продаются они за монеты
        через `CoinsShopService`, а Speed Coil и Slap — за наличные. Одна это
        палатка или разные, из кода игры не следует: надо смотреть.

        `walk=False` — если окно магазина уже открыто и ходить никуда не нужно.
        """
        if walk:
            if not self.to_reference():
                return []
            if not self.walk_until("buy tools to protect", "speed coil",
                                   landmark="ui_shop"):
                # До палатки не дошли — но окно могло открыться само или быть
                # открыто заранее. Читаем что есть: пустой список честнее отказа.
                log.warning("до палатки не дошёл, читаю то, что на экране")
                self.shot("fail_shop_tent")

        rows = ocr.lines(self.frame())
        # Цена стоит в той же строке правее названия. Порог по вертикали тот же,
        # что в buy_gear: строки в этом окне разнесены больше, чем на 40 px.
        prices = [(t, xc, yc) for t, xc, yc in rows if "$" in t]
        goods: list[tuple[str, str]] = []
        for text, xc, yc in rows:
            if "$" in text or len(text) < 3:
                continue
            near = [p for p in prices if abs(p[2] - yc) < 40 and p[1] > xc]
            near.sort(key=lambda p: p[1])
            goods.append((text, near[0][0] if near else ""))

        path = self.shot("shop_list")
        log.info("в палатке %s строк, из них с ценой %s", len(goods),
                 sum(1 for _, p in goods if p))
        for text, price in goods:
            log.info("  %-40s %s", text, price or "—")
        if path:
            log.info("скрин ассортимента: %s", path)
        return goods

    def buy_gear(self, name: str) -> bool:
        """Купить предмет в палатке Shop по названию.

        ВНИМАНИЕ, проверено вживую: кнопка Shop в ЛЕВОМ МЕНЮ открывает не тот
        магазин — там Bee Shop, Gamepasses и покупка валюты за Robux. Инструменты
        («Slap $500», «Speed Coil $750», «Trap») продаются в ПАЛАТКЕ в мире, и её
        окно открывается само, когда подходишь. Поэтому сюда нужна навигация к
        палатке, а не клик по меню — пока функция ждёт именно этого.

        Самый полезный предмет для бота — **Speed Coil**: он ускоряет передвижение,
        то есть сокращает и время операций, и число шагов, на которых копится ошибка.
        При наших $100K его цена ничтожна.
        """
        if not self.to_reference():
            return False
        before = self.read_cash()
        # к палатке инструментов: её окно открывается само при подходе
        if not self.walk_until("buy tools to protect", "speed coil", landmark="ui_shop"):
            self.shot("fail_shop_tent")
            return False

        # Сравниваем через ту же нормализацию, что и имена брейнротов: OCR и здесь
        # подставляет кириллические двойники, и «Speed Coil» приходит как «speed соil».
        from .brainrots import normalize
        want = normalize(name)
        target = None
        for text, xc, yc in ocr.lines(self.frame()):
            if want and want in normalize(text):
                target = (xc, yc)
                log.info("нашёл в магазине: %r", text)
                break
        if target is None:
            log.warning("в магазине нет строки %r", name)
            self.shot("fail_gear_row")
            self.dismiss_modals(force=True)
            return False

        # кнопка с ценой — справа в той же строке; ищем ближайшую строку с «$»
        buy = None
        for text, xc, yc in ocr.lines(self.frame()):
            if "$" in text and abs(yc - target[1]) < 40 and xc > target[0]:
                buy = (xc, yc)
                break
        if buy is None:
            log.warning("кнопку цены рядом с %r не нашёл", name)
            self.shot("fail_gear_price")
            self.dismiss_modals(force=True)
            return False

        log.info("покупаю %s: жму цену @%s", name, buy)
        self.hand.click(buy[0], buy[1], hold=0.12)
        time.sleep(1.2)
        self.dismiss_modals(force=True)

        after = self.read_cash()
        ok = before is not None and after is not None and after < before
        log.info("%s: кэш %s -> %s -> %s", name, before, after,
                 "куплено" if ok else "не подтвердилось")
        return ok

    # ------------------------------------------------------------------
    # Операция 6: продать лишнее
    # ------------------------------------------------------------------

    def base_items(self) -> list[str]:
        """Какие брейнроты стоят у нас на базе — по подписям над ними."""
        cat = catalog()
        b = self.window.client_box()
        names = []
        for text, _, y in ocr.lines(self.frame()):
            # Верхнюю полосу выбрасываем: там висит текст задания «Go buy a Noobini
            # Pizzanini», и справочник честно опознаёт в нём брейнрота. Без отсечки
            # бот решил бы, что этот брейнрот у нас на базе, и мог его «продать».
            if y < b.height * 0.20:
                continue
            it = cat.match(text)
            if it and it.name not in names:
                names.append(it.name)
        return names

    def sell_weakest(self, keep: int = 0, attempts: int = 2) -> str | None:
        """Продать самого слабого по доходу — освободить слот под лучшего.

        Слоты базы ограничены (10 без ребёрнов, 27 на восемнадцати), поэтому смысл
        покупки появляется только вместе с продажей: когда база полна, новый хороший
        брейнрот некуда поставить. Кого именно продать, решает справочник — в кадре
        видно имя, а доход и редкость берутся из базы знаний, где они точные.

        Возвращает имя проданного либо None.
        """
        for attempt in range(1, attempts + 1):
            if not self.to_reference():
                return None
            names = self.base_items()
            if len(names) <= keep:
                log.info("продавать нечего: на базе %s шт.", len(names))
                return None
            target = catalog().weakest(names)
            if not target:
                log.info("на базе %s, но никого не опознал по справочнику", names)
                return None
            log.info("на базе %s; продаю самого слабого: %s", names, target)

            before = self.read_cash()
            if not self.walk_until("sell", "продать", landmark="goods"):
                self.shot("fail_find_sell")
                continue
            self.hand.interact()
            time.sleep(1.2)
            after = self.read_cash()
            if before is not None and after is not None and after > before:
                log.info("продано за %.0f (кэш %.0f -> %.0f)", after - before, before, after)
                return target
            log.warning("продажа не подтвердилась (попытка %s)", attempt)
            self.shot("fail_sell")
        return None


def make_farmer(session, tuning: FarmTuning | None = None) -> Farmer:
    """Собрать фермера из сессии."""
    return Farmer(window=session.window, hand=session.hand,
                  tuning=tuning or FarmTuning(),
                  screens_dir=session.settings.screenshots_dir)
