"""Чтение текста с экрана игры через встроенный OCR Windows (WinRT).

Зачем свой модуль, а не просто matchTemplate: имена брейнротов, их доход ($X/s),
редкость и наличные — это ДИНАМИЧЕСКИЙ текст, шаблоном его не поймать. Референсный
макрос (Namesnipes) читает его через `screen_ocr` с WinRT-движком — берём тот же
путь, он быстрый и не требует установки Tesseract.

Одно улучшение над оригиналом: макрос читает область ЭКРАНА, поэтому окно должно
быть видимым. Мы отдаём OCR наш WGC-кадр (read_image), снятый по hwnd, — значит
окно может быть перекрыто, как и везде в проекте.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
from PIL import Image

from .log import get

log = get("ocr")

_reader = None


def _get_reader():
    global _reader
    if _reader is None:
        from screen_ocr import Reader
        _reader = Reader.create_quality_reader()
        log.info("OCR-движок WinRT поднят")
    return _reader


@dataclass
class Word:
    text: str
    x: int          # центр слова в координатах ПЕРЕДАННОГО кадра/области
    y: int


def read(frame: np.ndarray, region: tuple[int, int, int, int] | None = None) -> list[Word]:
    """OCR по кадру (BGR из нашего capture). region — (x0,y0,x1,y1) в кадре, или весь кадр.

    Координаты слов возвращаются в системе ПОЛНОГО кадра, даже если задан region.
    """
    ox = oy = 0
    if region:
        x0, y0, x1, y1 = region
        ox, oy = x0, y0
        frame = frame[y0:y1, x0:x1]

    # BGR (OpenCV) -> RGB (PIL)
    img = Image.fromarray(frame[:, :, ::-1])
    result = _get_reader().read_image(img)

    words: list[Word] = []
    for line in result.result.lines:
        for w in line.words:
            words.append(Word(
                text=w.text,
                x=ox + int(w.left + w.width / 2),
                y=oy + int(w.top + w.height / 2),
            ))
    return words


def lines(frame: np.ndarray, region=None) -> list[tuple[str, int, int]]:
    """То же, но склеенными строками: [(текст_строки_в_нижнем_регистре, xc, yc)]."""
    ox = oy = 0
    if region:
        x0, y0, x1, y1 = region
        ox, oy = x0, y0
        frame = frame[y0:y1, x0:x1]

    img = Image.fromarray(frame[:, :, ::-1])
    result = _get_reader().read_image(img)

    out = []
    for line in result.result.lines:
        if not line.words:
            continue
        text = " ".join(w.text for w in line.words).strip().lower()
        first, last = line.words[0], line.words[-1]
        yc = oy + int(first.top + first.height / 2)
        xc = ox + int((first.left + last.left + last.width) / 2)
        out.append((text, xc, yc))
    return out


def all_text(frame: np.ndarray, region=None) -> str:
    return " ".join(t for t, _, _ in lines(frame, region))


# --- разбор игровых чисел ---

_NUM = re.compile(r"(\d+(?:[.,]\d+)?)\s*([kmbt]?)", re.I)
_MULT = {"": 1, "k": 1_000, "m": 1_000_000, "b": 1_000_000_000, "t": 1_000_000_000_000}


_THOUSANDS = re.compile(r"\d{1,3}(?:,\d{3})+")


def parse_amount(text: str) -> float | None:
    """'$1.5M', '250k', '$100', '100,100' -> число. Терпимо к мусору OCR вокруг.

    Запятая в игре — разделитель ТЫСЯЧ ('100,100'), а не десятичная точка. Если её
    трактовать как точку, 100 100 превращается в 100.1 — на этом мы уже обожглись,
    цифры в лидерборде выходили в тысячу раз меньше.
    """
    text = _THOUSANDS.sub(lambda m: m.group(0).replace(",", ""), text)
    m = _NUM.search(text.replace(" ", ""))
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ".")) * _MULT[m.group(2).lower()]
    except (ValueError, KeyError):
        return None


def find_income(text: str) -> float | None:
    """Доход брейнрота из строки вида '$1.5K/s'."""
    m = re.search(r"\$?\s*([\d.,]+\s*[kmbt]?)\s*/\s*s", text, re.I)
    return parse_amount(m.group(1)) if m else None
