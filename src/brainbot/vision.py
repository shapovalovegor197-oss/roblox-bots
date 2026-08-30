"""Зрение: находим элементы UI по шаблонам.

Шаблоны — PNG-вырезки из скринов игры, лежат в templates/ и называются по смыслу:
trade_button.png, trade_search_tab.png, trade_ready.png…
Координаты всех попаданий — относительно клиентской области окна.
"""
from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .log import get

log = get("vision")


@dataclass
class Match:
    name: str
    x: int
    y: int
    w: int
    h: int
    score: float

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.w // 2, self.y + self.h // 2


def auto_mask(tpl: np.ndarray, bright: int = 150) -> np.ndarray | None:
    """Маска непрозрачных пикселей шаблона.

    Интерфейс Roblox полупрозрачный: сквозь кнопки видно движущийся 3D-мир,
    и фон под шаблоном меняется каждый кадр. Сравнение по всем пикселям из-за
    этого разваливается — живая кнопка Trade совпадала на 0.60 при пороге 0.87.

    Непрозрачны в этом интерфейсе только значки и подписи, и они почти белые.
    По ним и сравниваем, фон исключаем из сравнения совсем.
    """
    mask = (tpl.min(axis=2) > bright).astype(np.uint8) * 255
    share = mask.mean() / 255
    if share < 0.02 or share > 0.9:
        # Почти нечего или почти всё — маска бесполезна, сравниваем как есть.
        return None
    # Чуть расширяем, чтобы захватить края глифов со сглаживанием
    mask = cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=1)
    return cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)


class Templates:
    def __init__(self, directory: Path, threshold: float = 0.8,
                 regions: dict[str, list[int]] | None = None) -> None:
        self.dir = directory
        self.threshold = threshold
        # Область по умолчанию для каждого шаблона, из конфига.
        self.regions = {k: tuple(v) for k, v in (regions or {}).items()}

    @functools.lru_cache(maxsize=128)
    def _load(self, name: str) -> np.ndarray:
        path = self.dir / f"{name}.png"
        if not path.exists():
            raise FileNotFoundError(
                f"нет шаблона {path}. Сделай вырезку: python -m brainbot.cli cut <скрин>"
            )
        img = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError(f"не читается как картинка: {path}")
        return img

    def available(self) -> list[str]:
        return sorted(p.stem for p in self.dir.glob("*.png"))

    def find(self, name: str, frame: np.ndarray, threshold: float | None = None,
             region: tuple[int, int, int, int] | None = None) -> Match | None:
        """Лучшее совпадение шаблона в кадре, или None.

        region — (x0, y0, x1, y1), куда смотреть. Не оптимизация, а точность:
        интерфейс Roblox полупрозрачный, и на движущемся 3D-фоне шум добирается
        до 0.88 — вплотную к настоящим совпадениям. Настоящие элементы при этом
        всегда в одних и тех же местах экрана, поэтому ограничение области
        убирает ложные срабатывания надёжнее любого порога.
        """
        tpl = self._load(name)
        if tpl.shape[0] > frame.shape[0] or tpl.shape[1] > frame.shape[1]:
            log.warning("шаблон %s больше кадра — окно меньше, чем при съёмке?", name)
            return None

        ox = oy = 0
        region = region or self.regions.get(name)
        if region:
            x0, y0, x1, y1 = self._to_pixels(region, frame.shape)
            x0, y0 = max(0, x0), max(0, y0)
            x1, y1 = min(frame.shape[1], x1), min(frame.shape[0], y1)
            if x1 - x0 < tpl.shape[1] or y1 - y0 < tpl.shape[0]:
                log.warning("область %s меньше шаблона %s", region, name)
                return None
            frame = frame[y0:y1, x0:x1]
            ox, oy = x0, y0

        res = self._match(tpl, frame)
        _, score, _, loc = cv2.minMaxLoc(res)
        limit = self.threshold if threshold is None else threshold
        if score < limit:
            log.debug("шаблон %s не найден (%.3f < %.3f)", name, score, limit)
            return None
        h, w = tpl.shape[:2]
        return Match(name=name, x=loc[0] + ox, y=loc[1] + oy, w=w, h=h, score=float(score))

    @staticmethod
    def _to_pixels(region, shape) -> tuple[int, int, int, int]:
        """Область в долях окна → пиксели. Пиксельные значения пропускает как есть.

        Доли переживают смену разрешения: конфиг переписывать не придётся,
        пересняты будут только сами PNG-шаблоны.
        """
        h, w = shape[:2]
        x0, y0, x1, y1 = region
        if max(region) <= 1.0:
            return int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h)
        return int(x0), int(y0), int(x1), int(y1)

    def _match(self, tpl: np.ndarray, frame: np.ndarray) -> np.ndarray:
        """Карта совпадений, где больше — лучше.

        С маской берём TM_SQDIFF_NORMED, а не TM_CCORR_NORMED: последний
        измеряет корреляцию яркости и цепляется за любое светлое пятно, теряя
        форму. Проверено: при закрытом окне трейда он «находил» все шаблоны с
        оценкой 0.8+ в случайных местах. Разностная метрика так не врёт.
        """
        mask = auto_mask(tpl)
        if mask is None:
            return cv2.matchTemplate(frame, tpl, cv2.TM_CCOEFF_NORMED)
        res = cv2.matchTemplate(frame, tpl, cv2.TM_SQDIFF_NORMED, mask=mask)
        res = np.nan_to_num(res, nan=1.0, posinf=1.0, neginf=1.0)
        return 1.0 - np.clip(res, 0.0, 1.0)     # 0 = идеально → 1 = идеально

    def find_all(self, name: str, frame: np.ndarray, threshold: float | None = None,
                 max_hits: int = 32) -> list[Match]:
        """Все совпадения — для списков: слоты базы, строки инвентаря."""
        tpl = self._load(name)
        h, w = tpl.shape[:2]
        res = self._match(tpl, frame)
        limit = self.threshold if threshold is None else threshold

        hits: list[Match] = []
        work = res.copy()
        for _ in range(max_hits):
            _, score, _, loc = cv2.minMaxLoc(work)
            if score < limit:
                break
            hits.append(Match(name, loc[0], loc[1], w, h, float(score)))
            # гасим окрестность, чтобы не ловить тот же элемент повторно
            x0, y0 = max(0, loc[0] - w // 2), max(0, loc[1] - h // 2)
            work[y0:loc[1] + h // 2, x0:loc[0] + w // 2] = -1.0
        hits.sort(key=lambda m: (m.y, m.x))
        return hits

    def wait(self, name: str, grab_fn, timeout: float = 10.0,
             poll: float = 0.4) -> Match | None:
        """Ждёт появления элемента. grab_fn() должна вернуть свежий кадр."""
        import time
        deadline = time.time() + timeout
        while time.time() < deadline:
            m = self.find(name, grab_fn())
            if m:
                return m
            time.sleep(poll)
        log.warning("не дождались %s за %.1f с", name, timeout)
        return None


def annotate(frame: np.ndarray, matches: list[Match]) -> np.ndarray:
    """Рисует найденное — чтобы глазами проверить, что зрение не врёт."""
    out = frame.copy()
    for m in matches:
        cv2.rectangle(out, (m.x, m.y), (m.x + m.w, m.y + m.h), (0, 0, 255), 2)
        cv2.putText(out, f"{m.name} {m.score:.2f}", (m.x, max(14, m.y - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1, cv2.LINE_AA)
    return out
