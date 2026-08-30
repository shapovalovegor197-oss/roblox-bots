"""Ввод сообщениями прямо в окно, без фокуса.

Зачем. Обычный путь (`SendInput`, он же pydirectinput) кладёт событие в общую
очередь системы, и достаётся оно ТОМУ окну, что сейчас в фокусе. Значит на одной
машине в один момент действует ровно один клиент: бот фокусирует окно,
отрабатывает, переключается на следующее. Клиенты живут параллельно, действия —
нет. С ростом числа складов это и становится потолком.

`PostMessage` кладёт сообщение в очередь КОНКРЕТНОГО окна по его hwnd. Фокус не
нужен, окно может быть перекрыто или свёрнуто — как и при нашем WGC-захвате.
Тогда N клиентов действуют одновременно и по-настоящему.

Но работает это не везде, и обещать заранее нельзя. Игра может читать ввод
тремя разными способами:

  * оконные сообщения (WM_KEYDOWN и родня) — тогда PostMessage дойдёт;
  * Raw Input (WM_INPUT) — сообщения от PostMessage выглядят иначе и, скорее
    всего, будут отброшены;
  * опрос состояния клавиш (GetAsyncKeyState) — PostMessage вообще ни при чём,
    он не меняет физическое состояние клавиатуры.

Что из этого делает Roblox — вопрос к замеру, а не к рассуждению. Поэтому здесь
только канал доставки, а решает `run.py input-test --compare`: он гоняет одну и
ту же батарею обоими способами и показывает числа рядом.

РЕЗУЛЬТАТ ЗАМЕРА (29.08.2026, `input-test --compare`). Не работает.

    окно в фокусе:      16 проб из 20 дошло
    окно НЕ в фокусе:    1 проба из 20, и та — выброс

Единственное «ДОШЛО» при неактивном окне (0.8578) стоит особняком среди ровных
0.006–0.023 и объясняется перерисовкой экрана, а не нажатием. Ни ходьба, ни
камера, ни клики не прошли. Вывод: клиент принимает ввод, только пока считает
себя активным.

Важная деталь методики, на которой замер едва не соврал. Первый прогон показал
13 проб из 20 «без фокуса» — но окно тогда оставалось активным после
предыдущего прохода, потому что `ensure_focus()` в этом режиме ничего не делает
и фокус никто не снимал. Проверять надо принудительно уведённый фокус и
перепроверять его ПОСЛЕ прохода: клик по дороге может вернуть окно наверх.

Модуль оставлен рабочим и выключенным (`backend="focus"` по умолчанию): он
годится как готовый канал, если однажды понадобится слать что-то в окно, не
трогая фокус, и как напоминание, что этот путь уже проверен и закрыт.
Параллельный ввод придётся делать иначе — отдельными рабочими столами Windows
(своя очередь ввода на каждый) или машинами.
"""
from __future__ import annotations

import time

import win32api
import win32con
import win32gui

from .log import get

log = get("postinput")

# Виртуальные коды того, чем мы вообще пользуемся. Держим свою таблицу, а не
# зовём VkKeyScan: раскладка пользователя не должна влиять на бота.
VK = {
    "w": 0x57, "a": 0x41, "s": 0x53, "d": 0x44, "e": 0x45, "f": 0x46,
    "r": 0x52, "q": 0x51, "y": 0x59, "z": 0x5A, "x": 0x58, "c": 0x43,
    "0": 0x30, "1": 0x31, "2": 0x32, "3": 0x33, "4": 0x34, "5": 0x35,
    "6": 0x36, "7": 0x37, "8": 0x38, "9": 0x39,
    "space": win32con.VK_SPACE, "enter": win32con.VK_RETURN,
    "esc": win32con.VK_ESCAPE, "escape": win32con.VK_ESCAPE,
    "tab": win32con.VK_TAB, "shift": win32con.VK_SHIFT,
    "ctrl": win32con.VK_CONTROL, "backspace": win32con.VK_BACK,
    "end": win32con.VK_END, "home": win32con.VK_HOME,
    "slash": 0xBF, "/": 0xBF,
}

MAPVK_VK_TO_VSC = 0


def _lparam_down(vk: int) -> int:
    """lParam для WM_KEYDOWN: счётчик повторов, скан-код, флаги.

    Скан-код обязателен. Игры, разбирающие ввод всерьёз, смотрят именно на него,
    а не на виртуальный код: с нулевым скан-кодом сообщение выглядит поддельным.
    """
    scan = win32api.MapVirtualKey(vk, MAPVK_VK_TO_VSC)
    return 1 | (scan << 16)


def _lparam_up(vk: int) -> int:
    scan = win32api.MapVirtualKey(vk, MAPVK_VK_TO_VSC)
    # биты 30 и 31: клавиша была нажата, идёт отпускание
    return 1 | (scan << 16) | (1 << 30) | (1 << 31)


class Poster:
    """Отправка сообщений в одно окно. Ничего не знает про игру."""

    def __init__(self, hwnd: int) -> None:
        self.hwnd = hwnd

    # --- клавиатура ---

    def key_down(self, key: str) -> None:
        vk = VK.get(key.lower())
        if vk is None:
            raise ValueError(f"нет кода для клавиши {key!r}")
        win32api.PostMessage(self.hwnd, win32con.WM_KEYDOWN, vk, _lparam_down(vk))

    def key_up(self, key: str) -> None:
        vk = VK.get(key.lower())
        if vk is None:
            raise ValueError(f"нет кода для клавиши {key!r}")
        win32api.PostMessage(self.hwnd, win32con.WM_KEYUP, vk, _lparam_up(vk))

    def hold(self, key: str, seconds: float) -> None:
        self.key_down(key)
        try:
            time.sleep(seconds)
        finally:
            self.key_up(key)

    def press(self, key: str, hold: float = 0.06) -> None:
        self.hold(key, hold)

    def type_text(self, text: str) -> None:
        """Печать через WM_CHAR — для текста это надёжнее пары down/up."""
        for ch in text:
            win32api.PostMessage(self.hwnd, win32con.WM_CHAR, ord(ch), 1)
            time.sleep(0.02)

    # --- мышь ---
    #
    # Координаты в lParam — КЛИЕНТСКИЕ, ровно те же, в которых работает весь
    # остальной код. Пересчёта не требуется.

    @staticmethod
    def _pos(x: int, y: int) -> int:
        return (int(y) << 16) | (int(x) & 0xFFFF)

    def move(self, x: int, y: int) -> None:
        win32api.PostMessage(self.hwnd, win32con.WM_MOUSEMOVE, 0, self._pos(x, y))

    def click(self, x: int, y: int, button: str = "left", hold: float = 0.06) -> None:
        down, up, flag = {
            "left": (win32con.WM_LBUTTONDOWN, win32con.WM_LBUTTONUP, win32con.MK_LBUTTON),
            "right": (win32con.WM_RBUTTONDOWN, win32con.WM_RBUTTONUP, win32con.MK_RBUTTON),
        }[button]
        pos = self._pos(x, y)
        win32api.PostMessage(self.hwnd, win32con.WM_MOUSEMOVE, 0, pos)
        win32api.PostMessage(self.hwnd, down, flag, pos)
        time.sleep(hold)
        win32api.PostMessage(self.hwnd, up, 0, pos)

    def drag(self, x0: int, y0: int, x1: int, y1: int, button: str = "right",
             steps: int = 12) -> None:
        """Протяжка с зажатой кнопкой — так у нас поворачивается камера."""
        down, up, flag = {
            "left": (win32con.WM_LBUTTONDOWN, win32con.WM_LBUTTONUP, win32con.MK_LBUTTON),
            "right": (win32con.WM_RBUTTONDOWN, win32con.WM_RBUTTONUP, win32con.MK_RBUTTON),
        }[button]
        win32api.PostMessage(self.hwnd, down, flag, self._pos(x0, y0))
        for i in range(1, steps + 1):
            x = x0 + (x1 - x0) * i / steps
            y = y0 + (y1 - y0) * i / steps
            win32api.PostMessage(self.hwnd, win32con.WM_MOUSEMOVE, flag,
                                 self._pos(int(x), int(y)))
            time.sleep(0.015)
        win32api.PostMessage(self.hwnd, up, 0, self._pos(x1, y1))

    def scroll(self, clicks: int) -> None:
        """Колесо. wParam старшим словом несёт дельту, кратную 120."""
        rect = win32gui.GetClientRect(self.hwnd)
        cx, cy = rect[2] // 2, rect[3] // 2
        sx, sy = win32gui.ClientToScreen(self.hwnd, (cx, cy))
        for _ in range(abs(clicks)):
            delta = 120 if clicks > 0 else -120
            win32api.PostMessage(self.hwnd, win32con.WM_MOUSEWHEEL,
                                 (delta << 16), self._pos(sx, sy))
            time.sleep(0.02)
