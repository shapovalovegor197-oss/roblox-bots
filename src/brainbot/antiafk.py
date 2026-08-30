"""Anti-AFK: Roblox выкидывает примерно через 20 минут без ввода.

Ввод должен дойти именно до нужного клиента, а Roblox читает raw input и игнорирует
PostMessage в фон. Значит окно надо на секунду сфокусировать. Отсюда правило:
джиглим по одному окну за раз и коротко, чтобы не драться за фокус со сценариями.
"""
from __future__ import annotations

import random
import time

import pydirectinput

from .log import get
from .window import RobloxWindow

log = get("antiafk")

# Порог Roblox — 20 минут. Держим заметный запас: клиент мог не заметить
# последний ввод, а цена ошибки — вылет посреди фарма.
DEFAULT_INTERVAL = 480.0


class AntiAfk:
    def __init__(self, key: str = "space", interval: float = DEFAULT_INTERVAL) -> None:
        self.key = key
        self.interval = interval
        self._last: dict[int, float] = {}

    def due(self, window: RobloxWindow) -> bool:
        last = self._last.get(window.hwnd, 0.0)
        # разброс, чтобы окна не дёргались синхронно
        return time.time() - last >= self.interval * random.uniform(0.85, 1.0)

    def poke(self, window: RobloxWindow) -> None:
        """Одно короткое нажатие. Space безопасен: прыжок ничего не ломает."""
        if not window.alive():
            return
        try:
            window.focus()
            time.sleep(0.2)
            pydirectinput.press(self.key)
            self._last[window.hwnd] = time.time()
            log.debug("anti-afk: hwnd=%s", window.hwnd)
        except Exception as e:  # noqa: BLE001
            log.warning("anti-afk не сработал для hwnd=%s: %s", window.hwnd, e)

    def tick(self, windows: list[RobloxWindow]) -> None:
        for w in windows:
            if self.due(w):
                self.poke(w)
                return  # по одному окну за проход — фокус не рвём
