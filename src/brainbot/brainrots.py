"""Справочник брейнротов: кто это, сколько даёт, сколько стоит.

Собирается скриптом `scripts/build_brainrot_db.py` из выгрузки соседнего проекта
(витрина Starpets, 524 имени с редкостью, мутациями, доходом и ценой). Боту он даёт
три вещи, которых у него не было:

  1. **Исправление OCR.** Имена — итальянская тарабарщина («Tralalero Tralala»,
     «Chef Crabracadabra»), распознаётся с ошибками. Зная список настоящих имён,
     прочитанное притягиваем к ближайшему — тот же приём, что с ником в лидерборде.
  2. **Решение «брать».** В карточке у конвейера видно имя, редкость и доход.
     Справочник добавляет проверку: сходится ли прочитанный доход с известным. Если
     OCR выдал «Common за 5 млрд/с» — это брак распознавания, а не находка.
  3. **Решение «продать».** Когда база забита, продавать надо самое слабое по доходу.
     Без справочника «слабое» определить не из чего.
"""
from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .log import get

log = get("brainrots")

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "brainrots.json"

# Как редкость называется в игре -> как в справочнике.
RARITY_ALIASES = {
    "brainrot god": "brainrot_god",
    "brainrot": "brainrot_god",
    "god": "brainrot_god",
}


# Кириллические двойники латинских букв. Движок OCR настроен на русскую локаль и
# в латинских словах подставляет кириллицу: «Talpa Di Fero» приходит как
# «тата di рего», «Tung Tung Tung Sahur» — как «та та та та sahur». Символы
# выглядят одинаково, а коды разные, и без этой замены имя просто исчезает.
HOMOGLYPHS = {
    "а": "a", "в": "b", "е": "e", "к": "k", "м": "m", "н": "h", "о": "o",
    "р": "p", "с": "c", "т": "t", "у": "y", "х": "x", "і": "i", "ј": "j",
    "ѕ": "s", "ё": "e", "г": "r", "п": "n", "л": "l", "и": "u", "з": "3",
}


def normalize(s: str) -> str:
    """Имя под сравнение: раскладываем двойников и терпим типовые ошибки OCR.

    Помимо кириллицы, движок путает 0/o, 1/l, 5/s — приводим к одному виду,
    пробелы и знаки выбрасываем: сравнение всё равно нечёткое.
    """
    table = str.maketrans({**HOMOGLYPHS,
                           "0": "o", "1": "l", "5": "s", "8": "b", "|": "l", "!": "l"})
    return re.sub(r"[^a-z]", "", s.lower().translate(table))


@dataclass
class Brainrot:
    name: str
    rarity: str
    base_income: float | None
    income: dict
    price_rub: dict

    def income_for(self, mutation: str = "default") -> float | None:
        return self.income.get(mutation) or self.base_income


class Catalog:
    """Справочник в памяти. Грузится один раз, ищет нечётко."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path or DB_PATH)
        self.items: dict[str, Brainrot] = {}
        self.order: list[str] = []
        self._norm: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            log.warning("справочника нет: %s — собери scripts/build_brainrot_db.py",
                        self.path)
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.order = data.get("редкости_по_возрастанию", [])
        for key, it in data.get("items", {}).items():
            self.items[key] = Brainrot(
                name=it["name"], rarity=it.get("rarity", ""),
                base_income=it.get("base_income"),
                income=it.get("income", {}), price_rub=it.get("price_rub", {}),
            )
            self._norm[normalize(it["name"])] = key
        log.info("справочник: %s имён", len(self.items))

    # --- поиск ---

    def match(self, text: str, cutoff: float = 0.72) -> Brainrot | None:
        """Найти брейнрота по прочитанному тексту. None — не похоже ни на что."""
        if not self.items or not text:
            return None
        n = normalize(text)
        # Меньше четырёх букв — это не имя, а обрывок или число. Без этой отсечки
        # строка «$25» уверенно матчилась в брейнрота «25» из справочника.
        if len(n) < 4:
            return None
        if n in self._norm:
            return self.items[self._norm[n]]
        hit = difflib.get_close_matches(n, self._norm.keys(), n=1, cutoff=cutoff)
        if hit:
            return self.items[self._norm[hit[0]]]
        return None

    def match_any(self, texts: list[str], cutoff: float = 0.72) -> Brainrot | None:
        """Перебрать строки карточки и склейки соседних — имя бывает в две строки."""
        candidates = list(texts)
        candidates += [f"{a} {b}" for a, b in zip(texts, texts[1:])]
        best, best_score = None, 0.0
        for t in candidates:
            n = normalize(t)
            if len(n) < 4:
                continue
            hit = difflib.get_close_matches(n, self._norm.keys(), n=1, cutoff=cutoff)
            if not hit:
                continue
            score = difflib.SequenceMatcher(None, n, hit[0]).ratio()
            if score > best_score:
                best, best_score = self.items[self._norm[hit[0]]], score
        return best

    # --- оценки ---

    def rank(self, rarity: str) -> int:
        """Место редкости в лестнице. -1 — незнакомая."""
        r = RARITY_ALIASES.get(rarity.strip().lower(), rarity.strip().lower().replace(" ", "_"))
        return self.order.index(r) if r in self.order else -1

    def plausible(self, item: Brainrot | None, income_read: float | None,
                  tolerance: float = 8.0) -> bool:
        """Похож ли прочитанный доход на правду для этого брейнрота.

        Мутации поднимают доход в разы, поэтому допуск щедрый. Задача не поймать
        неточность, а отсечь явный брак OCR: «Common с доходом в миллиард».
        """
        if item is None or income_read is None or not item.base_income:
            return True
        lo = item.base_income / tolerance
        hi = item.base_income * tolerance * 10   # мутации дают до порядка сверху
        return lo <= income_read <= hi

    def weakest(self, names: list[str]) -> str | None:
        """Из списка имён — самое слабое по доходу. Его и продавать."""
        known = [(n, self.match(n)) for n in names]
        known = [(n, it) for n, it in known if it and it.base_income]
        if not known:
            return None
        return min(known, key=lambda p: p[1].base_income)[0]


_catalog: Catalog | None = None


def catalog() -> Catalog:
    global _catalog
    if _catalog is None:
        _catalog = Catalog()
    return _catalog
