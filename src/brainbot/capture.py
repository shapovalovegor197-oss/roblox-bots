"""Захват картинки из окна.

Три бэкенда, в порядке предпочтения:

  wgc   Windows.Graphics.Capture через zbl — снимает КОНКРЕТНОЕ ОКНО по hwnd,
        даже если оно перекрыто другими окнами или уехало за край экрана.
  dd    dxcam — быстрый, но это desktop duplication: снимает ЭКРАН. Окно должно
        быть видимым и ничем не закрытым.
  mss   то же самое, медленнее, зато работает везде.

Почему это важно именно здесь: на экране 1920x1080 помещается два-три окна
Roblox, и на этом сессии кончаются. WGC снимает окна независимо от того, что
на экране, поэтому клиентов можно складывать стопкой и упираться уже в память,
а не в размер монитора.
"""
from __future__ import annotations

import atexit
import threading
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from .log import get
from .window import Box

log = get("capture")

BACKEND_WGC = "wgc"
BACKEND_DD = "dd"
BACKEND_MSS = "mss"

# Захват дёргают из нескольких потоков (сценарий смотрит, запись пишет), а поток
# WGC на это не рассчитан. Один общий замок дешевле, чем второй источник кадров.
_grab_lock = threading.Lock()

_wgc_broken = False
_dxcam_camera = None
_dxcam_broken = False


# --------------------------------------------------------------------------
# WGC — основной путь
# --------------------------------------------------------------------------

# Сколько кадров максимум вычерпываем из очереди за один grab. Ограничение на
# случай, если клиент отдаёт кадры быстрее, чем мы успеваем их выбрасывать.
_DRAIN_LIMIT = 120


class _WgcSource:
    """Открытый поток захвата одного окна. Держим открытым — переоткрывать дорого."""

    def __init__(self, hwnd: int) -> None:
        import zbl
        self.hwnd = hwnd
        self._zbl = zbl
        self._cap = zbl.Capture(window_handle=hwnd)
        self._cap.__enter__()
        self.drained = 0          # сколько кадров выброшено на последнем grab

    def grab(self) -> np.ndarray:
        """Отдаёт СВЕЖИЙ кадр, а не следующий по очереди.

        Windows.Graphics.Capture складывает кадры в очередь. Если брать их по
        одному (`grab()`/`next(frames())`), читаешь прошлое: очередь копится,
        отставание растёт неограниченно, и два снимка, сделанных с интервалом в
        секунду, отличаются на 1/FPS игрового времени — то есть почти ничем.

        Именно на этом мы и встали: диагностика показывала «нажатие не дошло»,
        хотя персонаж бегал. Не доходил не ввод, а кадр.

        Поэтому очередь вычерпываем досуха (`try_grab_raw` отдаёт None, когда
        пусто) и берём последний кадр. Если очередь пуста — ждём новый.
        """
        self.drained = 0
        for _ in range(_DRAIN_LIMIT):
            if self._cap.try_grab_raw() is None:
                break
            self.drained += 1
        # А теперь БЛОКИРУЮЩИЙ захват: он ждёт следующий кадр, то есть снятый уже
        # после вычерпывания. Без этого возвращался последний кадр из очереди —
        # то есть прошлое. Замерено: grab сразу после удержания клавиши показывал
        # изменение 0.0015 (ничего), а следующий за ним — 0.2877. Из-за этого
        # диагностика уверенно сообщала «нажатие не дошло», хотя персонаж шёл.
        latest = self._cap.grab_raw()
        arr = self._zbl.frame_to_numpy_array(latest)
        if arr.ndim == 3 and arr.shape[2] == 4:
            return cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
        return arr

    def close(self) -> None:
        try:
            self._cap.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass


_wgc_sources: dict[int, _WgcSource] = {}


def _wgc_grab(hwnd: int) -> np.ndarray | None:
    global _wgc_broken
    if _wgc_broken or not hwnd:
        return None
    src = _wgc_sources.get(hwnd)
    try:
        if src is None:
            src = _WgcSource(hwnd)
            _wgc_sources[hwnd] = src
            log.info("WGC: поток захвата открыт для hwnd=%s", hwnd)
        return src.grab()
    except ImportError:
        log.warning("zbl не установлен — WGC недоступен, окна придётся держать видимыми")
        _wgc_broken = True
        return None
    except Exception as e:  # noqa: BLE001
        log.warning("WGC сорвался на hwnd=%s (%s) — закрываю поток, пробую иначе", hwnd, e)
        release(hwnd)
        return None


def open_source(hwnd: int):
    """Отдельный поток захвата, НЕ попадающий в общий реестр.

    Нужен тому, кто снимает из своего потока: объект zbl нельзя создать в одном
    потоке и уничтожить в другом («Capture is unsendable»). Свой источник владелец
    и создаёт, и закрывает у себя, а общий реестр с atexit-уборкой не трогает.
    """
    return _WgcSource(hwnd)


def release(hwnd: int) -> None:
    """Закрыть поток захвата — вызывать, когда окно умерло."""
    src = _wgc_sources.pop(hwnd, None)
    if src:
        src.close()


@atexit.register
def _cleanup() -> None:
    for hwnd in list(_wgc_sources):
        release(hwnd)


# --------------------------------------------------------------------------
# Запасные пути — снимают экран, окно должно быть видимым
# --------------------------------------------------------------------------

def _dxcam_grab(box: Box) -> np.ndarray | None:
    global _dxcam_camera, _dxcam_broken
    if _dxcam_broken:
        return None
    try:
        if _dxcam_camera is None:
            import dxcam
            _dxcam_camera = dxcam.create(output_color="BGR")
            if _dxcam_camera is None:
                raise RuntimeError("dxcam.create вернул None")
            log.info("запасной захват: dxcam")
        return _dxcam_camera.grab(region=box.region)
    except Exception as e:  # noqa: BLE001
        # Частый случай: zbl уже проинициализировал COM-апартамент потока,
        # и dxcam в него не встаёт. Не страшно — mss справится.
        log.info("dxcam недоступен (%s), дальше mss", e)
        _dxcam_broken = True
        _dxcam_camera = None
        return None


def _mss_grab(box: Box) -> np.ndarray:
    import mss
    with mss.mss() as sct:
        shot = sct.grab({"left": box.left, "top": box.top,
                         "width": box.width, "height": box.height})
        return cv2.cvtColor(np.asarray(shot), cv2.COLOR_BGRA2BGR)


# --------------------------------------------------------------------------

def grab(box: Box, hwnd: int | None = None) -> np.ndarray:
    """Кадр клиентской области окна, BGR.

    Передавай hwnd — тогда пойдём через WGC и окно может быть перекрыто.
    Без hwnd остаётся только снимок экрана по координатам.
    """
    with _grab_lock:
        if hwnd:
            frame = _wgc_grab(hwnd)
            if frame is not None:
                return frame

        if box.width <= 0 or box.height <= 0:
            raise ValueError(f"пустая область захвата: {box}")
        frame = _dxcam_grab(box)
        if frame is None:
            frame = _mss_grab(box)
        return frame


def backend_for(hwnd: int | None) -> str:
    if hwnd and not _wgc_broken:
        return BACKEND_WGC
    return BACKEND_MSS if _dxcam_broken else BACKEND_DD


def save(frame: np.ndarray, directory: Path, tag: str = "shot") -> Path:
    """Сохраняет кадр с меткой времени."""
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    path = directory / f"{tag}_{stamp}.png"
    cv2.imwrite(str(path), frame)
    return path
