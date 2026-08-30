"""Сессия — один аккаунт плюс его окно, плюс всё, чем в нём работать."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import capture, launcher
from .config import Account, Settings
from .inputs import Hand
from .log import get
from .vision import Match, Templates
from .window import RobloxWindow, enum_roblox_windows, wait_for_window

log = get("session")


@dataclass
class Session:
    account: Account
    settings: Settings
    window: RobloxWindow | None = None
    pid: int | None = None
    launched_at: float = 0.0
    fails: int = 0
    _hand: Hand | None = field(default=None, repr=False)

    # --- жизненный цикл ---

    @property
    def alive(self) -> bool:
        return self.window is not None and self.window.alive()

    def launch(self) -> bool:
        """Поднимает клиент и дожидается окна. False — не взлетело."""
        opt = self.settings.optimize
        try:
            self.pid = launcher.launch(
                self.account, self.settings.place_id,
                apply_fflags=opt.get("apply_fflags", True),
                target_fps=opt.get("target_fps"),
            )
        except launcher.LaunchError as e:
            log.error("[%s] %s", self.account.name, e)
            self.fails += 1
            return False

        timeout = self.settings.supervisor["launch_timeout_sec"]
        win = wait_for_window(self.pid, timeout=timeout)
        if win is None:
            self.fails += 1
            return False

        self.window = win
        self._hand = None
        self.launched_at = time.time()
        self.fails = 0
        self.apply_window_layout()
        return True

    def apply_window_layout(self, index: int = 0) -> None:
        """Ставит окну фиксированный размер и место.

        Размер фиксирован жёстко: от него зависят координаты всех шаблонов.
        А вот место зависит от режима — при stack окна лежат друг на друге,
        потому что WGC снимает их независимо от того, что видно на экране.
        """
        if not self.window:
            return
        w = self.settings.window
        mode = w.get("layout", "stack")
        if mode == "none":
            return

        ox, oy = w.get("origin", [0, 0])
        if mode == "tile":
            cols = max(1, 1920 // w["width"])
            x = ox + (index % cols) * w["width"]
            y = oy + (index // cols) * w["height"]
        else:  # stack — лесенкой, чтобы окна можно было различить глазом
            step = w.get("cascade_step", 24)
            x, y = ox + index * step, oy + index * step

        self.window.move_resize(x, y, w["width"], w["height"])
        log.info("[%s] окно %sx%s @(%s,%s), режим %s",
                 self.account.name, w["width"], w["height"], x, y, mode)

    def optimize_process(self, index: int = 0, total: int = 1) -> None:
        """Привязка к ядрам и приоритет. Без этого клиенты дерутся за планировщик."""
        from . import optimize
        opt = self.settings.optimize
        if not opt.get("pin_cores", True) or not self.pid:
            return
        plan = optimize.plan_affinity(max(1, total), opt.get("reserve_threads", 4))
        cores = plan[index % len(plan)]
        optimize.pin_process(self.pid, cores, opt.get("background_priority", "below"))

    def adopt(self, window: RobloxWindow) -> None:
        """Привязать уже открытое вручную окно — удобно на этапе отладки."""
        self.window = window
        self.pid = window.pid
        self._hand = None

    # --- работа внутри окна ---

    @property
    def hand(self) -> Hand:
        if not self.window:
            raise RuntimeError(f"[{self.account.name}] окна нет — сначала launch()")
        if self._hand is None:
            self._hand = Hand(self.window, self.settings.input)
        return self._hand

    @property
    def templates(self) -> Templates:
        return Templates(self.settings.template_dir, self.settings.vision["match_threshold"], self.settings.vision.get("regions"))

    def frame(self) -> np.ndarray:
        """Кадр окна. hwnd передаём обязательно — иначе перекрытое окно не снять."""
        if not self.window:
            raise RuntimeError(f"[{self.account.name}] окна нет")
        hwnd = None if self.settings.capture.get("backend") == "screen" else self.window.hwnd
        return capture.grab(self.window.client_box(), hwnd=hwnd)

    def close_capture(self) -> None:
        """Отпустить поток захвата — вызывать, когда окно умерло."""
        if self.window:
            capture.release(self.window.hwnd)

    def shot(self, tag: str | None = None) -> Path:
        return capture.save(self.frame(), self.settings.screenshots_dir,
                            tag or self.account.name)

    def find(self, template: str) -> Match | None:
        return self.templates.find(template, self.frame())

    def wait_for(self, template: str, timeout: float = 10.0) -> Match | None:
        return self.templates.wait(template, self.frame, timeout=timeout)

    def click_template(self, template: str, timeout: float = 10.0) -> bool:
        """Дождаться элемент и кликнуть в него. Основной кирпич всех сценариев."""
        m = self.wait_for(template, timeout=timeout)
        if not m:
            return False
        self.hand.click_match(m)
        return True


def discover(settings: Settings) -> list[RobloxWindow]:
    """Открытые прямо сейчас окна Roblox — привязать к сессиям можно вручную."""
    return enum_roblox_windows()

