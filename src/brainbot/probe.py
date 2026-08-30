"""Диагностика ввода: дошло нажатие до клиента или нет.

Зачем отдельный модуль. «Персонаж не поехал» — это не факт, а ощущение: мир
живёт сам по себе (бежит конвейер, тикает таймер, ходят чужие), а персонаж
может упираться в стену. Поэтому мерим, а не смотрим.

Метод один на все случаи:

    кадр ДО  ->  действие  ->  кадр ПОСЛЕ  ->  насколько изменилось

и сначала снимаем ФОН — несколько пар кадров вообще без ввода. Фон — это цена
жизни мира. Порог «дошло» = втрое выше фона. Так «оно не работает» превращается
в число, и видно, ЧТО именно не работает: клавиши вообще, только короткие
нажатия, только движение или только камера.

Отдельно проверяются два независимых канала:

  * UI (Tab — таблица игроков, / — чат). Это чистый тест доставки: интерфейс
    отвечает мгновенно и не зависит от того, где стоит персонаж.
  * Мир (WASD, поворот камеры). Тест того, что игра принимает ввод как игровой,
    а не только как ввод в интерфейс.

Если UI отвечает, а мир нет — дело не в доставке, а в персонаже (уперся,
не в фокусе мира, открыто меню). Если молчит и UI — дело в доставке.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .capture import grab, save
from .inputs import Hand
from .log import get
from .window import RobloxWindow

log = get("probe")

# Пиксель считаем изменившимся при разнице ярче этой. 25 из 255 — выше шума
# сжатия и мерцания, ниже любого реального изменения картинки.
PIXEL_DELTA = 25


@dataclass
class Result:
    name: str
    changed: float          # доля изменившихся пикселей, 0..1
    mean: float             # средняя разница по кадру, 0..255
    verdict: str = ""
    cells: list[float] = field(default_factory=list)   # сетка 4x4, где менялось


def _gray(frame: np.ndarray) -> np.ndarray:
    return frame[:, :, :3].mean(axis=2)


def _compare(a: np.ndarray, b: np.ndarray) -> tuple[float, float, list[float]]:
    """Доля изменившихся пикселей, средняя разница и сетка 4x4 по долям."""
    d = np.abs(_gray(a) - _gray(b))
    changed = float((d > PIXEL_DELTA).mean())
    h, w = d.shape
    cells = []
    for r in range(4):
        for c in range(4):
            cell = d[r * h // 4:(r + 1) * h // 4, c * w // 4:(c + 1) * w // 4]
            cells.append(float((cell > PIXEL_DELTA).mean()))
    return changed, float(d.mean()), cells


@dataclass
class Probe:
    window: RobloxWindow
    hand: Hand
    screens_dir: Path | None = None
    settle: float = 0.7        # сколько ждать до кадра ДО
    react: float = 0.7         # сколько ждать реакции игры после действия
    floor: float = 0.004       # ниже этой доли не верим никакому изменению

    noise: float = 0.0
    results: list[Result] = field(default_factory=list)

    def frame(self) -> np.ndarray:
        return grab(self.window.client_box(), hwnd=self.window.hwnd)

    # --- фон ---

    def measure_noise(self, samples: int = 5) -> float:
        """Насколько кадр меняется САМ, без нашего вмешательства."""
        vals = []
        for _ in range(samples):
            a = self.frame()
            time.sleep(self.react)
            b = self.frame()
            vals.append(_compare(a, b)[0])
        self.noise = max(vals)
        log.info("фон: %.4f (макс из %s замеров)", self.noise, samples)
        return self.noise

    # --- один случай ---

    def case(self, name: str, action, *, keep: bool = False) -> Result:
        """Прогнать одно действие и измерить отклик.

        keep=True — сохранить кадры до/после на диск, чтобы посмотреть глазами.
        """
        time.sleep(self.settle)
        before = self.frame()
        try:
            action()
        except Exception as e:  # noqa: BLE001
            log.warning("%s: действие упало: %s", name, e)
        time.sleep(self.react)
        after = self.frame()

        changed, mean, cells = _compare(before, after)
        threshold = max(self.noise * 3, self.floor)
        verdict = "ДОШЛО" if changed > threshold else "нет"
        res = Result(name=name, changed=changed, mean=mean, verdict=verdict, cells=cells)
        self.results.append(res)
        log.info("%-22s изменилось %.4f (порог %.4f) -> %s", name, changed, threshold, verdict)

        if keep and self.screens_dir:
            save(before, self.screens_dir, f"probe_{name}_before")
            save(after, self.screens_dir, f"probe_{name}_after")
        return res

    # --- набор случаев ---

    def run(self, keep: bool = False) -> list[Result]:
        b = self.window.client_box()
        cx, cy = b.width // 2, b.height // 2

        self.hand.ensure_focus()
        time.sleep(0.3)
        self.measure_noise()

        # Контроль: пустое действие. Должно дать «нет» — иначе порог занижен и
        # верить остальным строкам нельзя.
        self.case("control", lambda: None)

        # Клик в мир. Без него клавиатура может уходить в интерфейс, а не в игру.
        self.case("click_center", lambda: self.hand.click(cx, cy))

        # --- канал UI: доставка нажатий как таковая ---
        # Tab — таблица игроков, переключатель. Нажатие НУЛЕВОЙ длины против
        # нажатия с удержанием: ровно та разница, из-за которой всё встало.
        self.case("tab_hold_0ms", lambda: self.hand.press("tab", hold=0.0), keep=keep)
        self.case("tab_hold_0ms_off", lambda: self.hand.press("tab", hold=0.0))
        self.case("tab_hold_60ms", lambda: self.hand.press("tab", hold=0.06), keep=keep)
        self.case("tab_hold_60ms_off", lambda: self.hand.press("tab", hold=0.06))
        self.case("tab_hold_250ms", lambda: self.hand.press("tab", hold=0.25), keep=keep)
        self.case("tab_hold_250ms_off", lambda: self.hand.press("tab", hold=0.25))

        # Чат по «/». На свежих аккаунтах чат бывает выключен настройками —
        # тогда «нет» здесь ничего не доказывает, смотреть надо на Tab.
        self.case("slash_chat", lambda: self.hand.press("/", hold=0.06), keep=keep)
        self.case("escape_close", lambda: self.hand.press("escape", hold=0.06))
        self.case("escape_close2", lambda: self.hand.press("escape", hold=0.06))

        # --- канал мира: движение и камера ---
        self.hand.click(cx, cy)
        time.sleep(0.4)
        for key, back in (("w", "s"), ("a", "d")):
            self.case(f"hold_{key}_800ms", lambda k=key: self.hand.hold(k, 0.8), keep=keep)
            self.case(f"back_{back}_800ms", lambda k=back: self.hand.hold(k, 0.8))

        self.case("look_right_600", lambda: self.hand.look(600, 0), keep=keep)
        self.case("look_left_600", lambda: self.hand.look(-600, 0))
        self.case("pitch_down", lambda: self.hand.pitch_down(), keep=keep)
        self.case("scroll_out_5", lambda: self.hand.scroll(-5))
        self.case("scroll_in_5", lambda: self.hand.scroll(5))
        return self.results

    # --- вывод ---

    def delivered(self) -> int:
        """Сколько проб дошло. Нужно, чтобы сравнивать каналы доставки числом."""
        return sum(1 for r in self.results if r.verdict == "ДОШЛО")

    def passed(self) -> list[str]:
        """Имена дошедших проб — по ним видно, что именно годится без фокуса."""
        return [r.name for r in self.results if r.verdict == "ДОШЛО"]

    def report(self) -> str:
        lines = [f"фон (мир сам по себе): {self.noise:.4f}",
                 f"порог «дошло»:         {max(self.noise * 3, self.floor):.4f}", ""]
        for r in self.results:
            hot = max(range(16), key=lambda i: r.cells[i]) if r.cells else 0
            where = f"строка {hot // 4 + 1}, столбец {hot % 4 + 1}" if r.verdict == "ДОШЛО" else ""
            lines.append(f"{r.name:22} {r.changed:7.4f}  {r.verdict:7} {where}")

        ui = [r for r in self.results if r.name.startswith(("tab_", "slash"))]
        world = [r for r in self.results if r.name.startswith(("hold_", "look_", "pitch"))]
        ui_ok = any(r.verdict == "ДОШЛО" for r in ui)
        world_ok = any(r.verdict == "ДОШЛО" for r in world)
        lines += ["", "итог:"]
        if ui_ok and world_ok:
            lines.append("  ввод доходит и в интерфейс, и в мир — блокера нет")
        elif ui_ok:
            lines.append("  интерфейс отвечает, мир нет: дело не в доставке нажатий,")
            lines.append("  а в персонаже — уперся, не в фокусе мира или открыто меню")
        elif world_ok:
            lines.append("  мир отвечает, интерфейс нет: скорее всего эти конкретные")
            lines.append("  элементы отключены на аккаунте (чат у новых), а не ввод")
        else:
            lines.append("  не отвечает ничего — вот это и есть проблема доставки")
        zero = next((r for r in self.results if r.name == "tab_hold_0ms"), None)
        held = next((r for r in self.results if r.name == "tab_hold_60ms"), None)
        if zero and held and zero.verdict != "ДОШЛО" and held.verdict == "ДОШЛО":
            lines.append("  нажатие нулевой длины теряется, с удержанием доходит —")
            lines.append("  держать клавишу обязательно (key_hold в конфиге)")
        return "\n".join(lines)
