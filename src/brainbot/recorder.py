"""Запись прогона: mp4 из тех кадров, которые видит бот, с подписями его состояния.

Это не запись экрана. Пишем ровно тот поток, что идёт в зрение — через тот же WGC по
hwnd, — и накладываем сверху, что бот в этот момент прочитал и что собрался делать.
Спорные места («зажат или нет», «дошло нажатие или нет») разбираются просмотром за
минуту, а не переписыванием диагностик.

Второй режим — запись ввода: параллельно пишется поток нажатий. Нужен, чтобы записать
ПРОХОД РУКАМИ и вынуть из него тайминги вместо слепого подбора. Хук не ставим, чтобы
не мешать игре и не выглядеть подозрительно: просто опрашиваем состояние клавиш
через GetAsyncKeyState.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import threading
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .capture import grab, open_source
from .log import get
from .window import RobloxWindow

log = get("recorder")

user32 = ctypes.WinDLL("user32", use_last_error=True)

# Клавиши, за которыми следим при записи прохода руками.
# ПКМ обязательна: по ней определяется, что человек крутит камеру,
# а не просто двигает курсор.
WATCH_KEYS = {
    "w": 0x57, "a": 0x41, "s": 0x53, "d": 0x44,
    "e": 0x45, "space": 0x20, "shift": 0x10, "tab": 0x09,
    "esc": 0x1B, "r": 0x52,
    "ЛКМ": 0x01, "ПКМ": 0x02,
}

_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\segoeui.ttf",
    r"C:\Windows\Fonts\arial.ttf",
]


def _font(size: int = 18):
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


class Recorder:
    """Пишет mp4 в отдельном потоке. note() меняет подпись поверх кадра."""

    def __init__(self, window: RobloxWindow, path: Path, fps: int = 10,
                 overlay: bool = True) -> None:
        self.window = window
        self.path = Path(path)
        self.fps = fps
        self.overlay = overlay
        self._writer: cv2.VideoWriter | None = None
        # Смена файла и запись кадра не должны пересечься: писатель
        # закрывается из главного потока, а пишет в него поток съёмки.
        self._roll_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._note = ""
        self._lock = threading.Lock()
        self._font = _font()
        self.frames = 0

    # --- подписи ---

    def note(self, text: str) -> None:
        """Что бот делает прямо сейчас — попадёт на кадр."""
        with self._lock:
            self._note = text

    def _draw(self, frame: np.ndarray) -> np.ndarray:
        with self._lock:
            text = self._note
        stamp = time.strftime("%H:%M:%S")
        img = Image.fromarray(frame[:, :, ::-1])
        d = ImageDraw.Draw(img)
        line = f"{stamp}  {text}" if text else stamp
        # подложка, чтобы читалось на любом фоне
        d.rectangle([0, 0, img.width, 26], fill=(0, 0, 0))
        d.text((8, 4), line, font=self._font, fill=(255, 255, 255))
        return np.array(img)[:, :, ::-1]

    # --- жизненный цикл ---

    def _loop(self) -> None:
        period = 1.0 / self.fps
        # Свой источник захвата: создаём и закрываем ЗДЕСЬ же. Объект zbl нельзя
        # создать в одном потоке и уничтожить в другом, а общий источник живёт
        # в главном потоке и убирается atexit-ом.
        src = None
        try:
            src = open_source(self.window.hwnd)
        except Exception as e:  # noqa: BLE001
            log.warning("свой поток захвата не открылся (%s) — пишу через общий", e)
        try:
            self._run(src, period)
        finally:
            if src is not None:
                src.close()

    def _run(self, src, period: float) -> None:
        while not self._stop.is_set():
            t0 = time.time()
            try:
                frame = (src.grab() if src is not None
                         else grab(self.window.client_box(), hwnd=self.window.hwnd))
            except Exception as e:  # noqa: BLE001
                log.warning("кадр не снялся: %s", e)
                time.sleep(period)
                continue
            with self._roll_lock:
                if self._writer is None:
                    h, w = frame.shape[:2]
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    self._writer = cv2.VideoWriter(
                        str(self.path), cv2.VideoWriter_fourcc(*"mp4v"),
                        self.fps, (w, h))
                    if not self._writer.isOpened():
                        log.error("не открылся VideoWriter для %s", self.path)
                        return
            with self._roll_lock:
                if self._writer is not None:
                    self._writer.write(self._draw(frame) if self.overlay else frame)
                    self.frames += 1
            time.sleep(max(0.0, period - (time.time() - t0)))

    def roll(self, path: Path) -> Path:
        """Закрыть текущий файл и продолжить писать в новый, НЕ трогая захват.

        Нужно для записи кусками. Наивный способ — создать второй `Recorder` —
        роняет процесс: он заново открывает поток захвата WGC на том же окне, и
        это даёт segmentation fault (проверено, запись показа упала после
        первого же куска). Поэтому поток съёмки живёт один на всю запись, а
        меняется только файл, в который пишутся кадры.
        """
        done, frames = self.path, self.frames
        with self._roll_lock:
            if self._writer:
                self._writer.release()
            self._writer = None          # следующий кадр откроет новый файл
            self.path = path
            self.frames = 0
        log.info("кусок закрыт: %s, кадров %s -> пишу в %s", done, frames, path)
        return done

    def start(self) -> "Recorder":
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        log.info("запись пошла: %s (%s к/с)", self.path, self.fps)
        return self

    def stop(self) -> Path:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        if self._writer:
            self._writer.release()
        log.info("запись закрыта: %s, кадров %s", self.path, self.frames)
        return self.path

    def __enter__(self) -> "Recorder":
        return self.start()

    def __exit__(self, *_) -> None:
        self.stop()


class InputLog:
    """Поток нажатий: [(время, клавиша, 'вниз'|'вверх')]. Для записи прохода руками."""

    def __init__(self, poll: float = 0.02) -> None:
        self.poll = poll
        self.events: list[tuple[float, str, str]] = []
        # Повороты камеры: [(время, суммарный сдвиг мыши по x, по y)].
        # Пишем ТОЛЬКО пока зажата ПКМ — именно тогда движение мыши крутит
        # камеру, а не просто возит курсор по экрану.
        self.turns: list[tuple[float, int, int]] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _cursor(self) -> tuple[int, int]:
        pt = ctypes.wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y

    def _loop(self) -> None:
        state = {k: False for k in WATCH_KEYS}
        t0 = time.time()
        # Накопитель поворота: пока ПКМ зажата, складываем смещения курсора.
        # Без этого урок остаётся наполовину пустым: в нём есть «прошёл вперёд 1.5 с», но
        # нет «повернулся на столько-то», а весь путь как раз и состоит из
        # чередования того и другого.
        turning = False
        acc_x = acc_y = 0
        last = self._cursor()
        while not self._stop.is_set():
            for name, vk in WATCH_KEYS.items():
                down = bool(user32.GetAsyncKeyState(vk) & 0x8000)
                if down != state[name]:
                    state[name] = down
                    self.events.append((time.time() - t0, name, "вниз" if down else "вверх"))

            rmb = state.get("ПКМ", False)
            now = self._cursor()
            if rmb:
                if not turning:
                    turning, acc_x, acc_y = True, 0, 0
                else:
                    acc_x += now[0] - last[0]
                    acc_y += now[1] - last[1]
            elif turning:
                turning = False
                if abs(acc_x) > 3 or abs(acc_y) > 3:
                    self.turns.append((time.time() - t0, acc_x, acc_y))
            last = now
            time.sleep(self.poll)

    def start(self) -> "InputLog":
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> list[tuple[float, str, str]]:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        return self.events

    def holds(self) -> list[tuple[str, float, float]]:
        """Свернуть события в удержания: [(клавиша, начало, длительность)].

        Это и есть готовые тайминги: прошёл маршрут руками — получил числа для
        FarmTuning, вместо того чтобы подбирать их вслепую.
        """
        out: list[tuple[str, float, float]] = []
        opened: dict[str, float] = {}
        for t, key, kind in self.events:
            if kind == "вниз":
                opened[key] = t
            elif key in opened:
                out.append((key, opened[key], t - opened.pop(key)))
        return sorted(out, key=lambda r: r[1])
