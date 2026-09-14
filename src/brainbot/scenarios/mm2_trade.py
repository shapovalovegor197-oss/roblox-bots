"""Обмен в Murder Mystery 2. Бот — всегда ИНИЦИАТОР сделки.

Почему инициатор. В MM2 поля поиска по нику НЕТ (это отличает игру от Steal a
Brainrot и от старого scenarios/trade.py). Обмен начинается кликом по строке
игрока в лидерборде: открывается карточка (имя, Profile, Trade), жмём Trade.
Раз инициатор всегда мы — отдельная кнопка «принять входящее приглашение» не
нужна, и мы не гадаем шаблон, которого не сняли (кадра попапа входящего
приглашения в проходе 13.09 нет).

Поток, снятый живьём 13.09.2026 (кадры var/screens/mm2_*, геометрия
data/mm2/layout.json), для окна 1280x720:

    лидерборд (Tab)  →  клик по строке ника  →  карточка  →  Trade
      →  окно обмена (якорь: шаблон mm2_your_offer)
      →  give:    выложить предмет в свою половину
         receive: свою половину НЕ трогаем, ждём выкладку контрагента
      →  ACCEPT  →  ARE YOU SURE?  →  (партнёр принял)  →  второй клик = сделка
      →  пруф: кадр ДО невозврата + дельта инвентаря до/после

ЧЕГО ЗДЕСЬ ЧЕСТНО НЕТ. Сценарий не заходит в сервер и не подводит к контрагенту —
предполагается, что оба уже в одном месте (см. sab-private-server: обмен удобнее
в приватке). Пруф по инвентарю — пока кадры до/после, а не разбор дельты числом:
чтения инвентаря MM2 у нас ещё нет. Всё построено на кадрах 13.09 и в игре после
переписывания НЕ перепроверено — сначала `mm2 survey`, потом боевой прогон.
"""
from __future__ import annotations

import difflib
import json
import time
from dataclasses import dataclass
from pathlib import Path

from .. import ocr
from ..log import get
from ..session import Session

log = get("mm2")

_LAYOUT = Path(__file__).resolve().parents[3] / "data" / "mm2" / "layout.json"


def load_layout(path: Path | None = None) -> dict:
    return json.loads((path or _LAYOUT).read_text(encoding="utf-8"))


@dataclass
class TradeResult:
    ok: bool
    stage: str
    message: str = ""
    proof: str = ""


class TradeAborted(Exception):
    """Сценарий прерван на конкретном шаге — состояние окна неизвестно."""

    def __init__(self, stage: str, message: str) -> None:
        super().__init__(f"{stage}: {message}")
        self.stage = stage
        self.message = message


def _norm(s: str) -> str:
    """Ник/подпись под нечёткое сравнение: OCR путает 0/o, 1/l, 5/s, 8/b."""
    table = str.maketrans({"0": "o", "1": "l", "5": "s", "8": "b", "|": "l"})
    return "".join(ch for ch in s.lower().translate(table) if ch.isalnum())


# --- чтение состояния ---

def find_player_row(session: Session, nickname: str, L: dict,
                    min_ratio: float = 0.6) -> tuple[int, int] | None:
    """Строка игрока в лидерборде → (x, y) центра ника в кадре, либо None.

    Ник сравниваем нечётко: OCR его корёжит, а состав комнаты меняется за минуту,
    поэтому ищем заново перед каждым обменом.
    """
    region = tuple(L["leaderboard"]["region"])
    words = ocr.read(session.frame(), region)
    want = _norm(nickname)
    best, score = None, 0.0
    for w in words:
        cand = _norm(w.text)
        if not cand:
            continue
        r = difflib.SequenceMatcher(None, want, cand).ratio()
        if r > score:
            best, score = w, r
    if best and score >= min_ratio:
        log.info("[%s] %r в лидерборде (%.2f) @(%d,%d)",
                 session.account.name, nickname, score, best.x, best.y)
        return best.x, best.y
    log.warning("[%s] %r в лидерборде не найден (лучшее %.2f)",
                session.account.name, nickname, score)
    return None


def _slot_filled(session: Session, slot_xy: tuple[int, int], label_dy: int = 56) -> bool:
    """В слоте что-то лежит? Проверяем по подписи под ячейкой (напр. «Rune»)."""
    x, y = slot_xy
    region = (x - 55, y + label_dy - 15, x + 55, y + label_dy + 15)
    return bool(ocr.all_text(session.frame(), region).strip())


def _slot_label(session: Session, slot_xy: tuple[int, int], label_dy: int = 56) -> str:
    x, y = slot_xy
    region = (x - 60, y + label_dy - 15, x + 60, y + label_dy + 15)
    return ocr.all_text(session.frame(), region).strip()


def _confirm_phase(session: Session, L: dict) -> str:
    """Фаза кнопки подтверждения: counting / ready / are_you_sure / unknown.

    Порядок проверок важен: «Please wait (N) before accepting» содержит подстроку
    «accept», и без приоритета отсчёта его приняли бы за готовность нажать.
    """
    txt = ocr.all_text(session.frame(), tuple(L["trade_window"]["confirm_text_region"])).lower()
    st = L["confirm_states"]
    if any(k in txt for k in st["are_you_sure"]):
        return "are_you_sure"
    if any(k in txt for k in st["counting"]):
        return "counting"
    if any(k in txt for k in st["ready"]):
        return "ready"
    return "unknown"


def _partner_ready(session: Session, L: dict) -> bool:
    txt = ocr.all_text(session.frame(), tuple(L["trade_window"]["partner_status_region"])).lower()
    return any(k in txt for k in L["partner_ready_text"])


# --- шаги ---

def open_trade(session: Session, nickname: str, L: dict, timeout: float = 15.0) -> None:
    """Открыть окно обмена с игроком: лидерборд → карточка → Trade."""
    hand = session.hand
    if find_player_row(session, nickname, L) is None:
        hand.press(L["leaderboard"]["toggle_key"])
        time.sleep(0.7)
    row = find_player_row(session, nickname, L)
    if row is None:
        path = session.shot("mm2_fail_find")
        raise TradeAborted("поиск игрока", f"{nickname!r} не в лидерборде, скрин: {path}")

    rx, ry = row
    hand.click(rx, ry)          # строка → карточка
    time.sleep(0.6)
    card = L["player_card"]
    # Кнопка Trade: X фиксирован, Y отсчитывается от строки (карточка выровнена
    # по ней). Смещение +95px проверено на двух кадрах разной высоты строки.
    hand.click(card["trade_x_abs"], ry + card["trade_dy_from_row"])

    if not session.wait_for("mm2_your_offer", timeout=timeout):
        path = session.shot("mm2_fail_open")
        raise TradeAborted("открыть обмен", f"окно обмена не появилось, скрин: {path}")
    log.info("[%s] окно обмена с %s открыто", session.account.name, nickname)


def offer_item(session: Session, item_name: str, L: dict) -> bool:
    """Найти предмет по названию и выложить в свою половину. True — слот занялся."""
    hand = session.hand
    inv = L["trade_window"]["inventory"]
    hand.click(*inv["search_field"])
    hand.clear_field()
    hand.type_text(item_name)
    time.sleep(0.8)                        # игре нужно время отфильтровать
    hand.click(*inv["first_cell"])         # первая карточка результата
    time.sleep(0.5)
    ok = _slot_filled(session, tuple(L["trade_window"]["your_offer_slots"][0]),
                      inv.get("label_dy", 56))
    log.info("[%s] выкладка %r: слот %s", session.account.name, item_name,
             "занят" if ok else "ПУСТ")
    return ok


def wait_their_offer(session: Session, L: dict, timeout: float,
                     expect_item: str | None = None, poll: float = 1.5) -> bool:
    """Ждать, пока контрагент положит предмет в свою половину."""
    slot0 = tuple(L["trade_window"]["their_offer_slots"][0])
    dy = L["trade_window"]["inventory"].get("label_dy", 56)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _slot_filled(session, slot0, dy):
            if expect_item:
                got = _slot_label(session, slot0, dy)
                if _norm(expect_item) not in _norm(got):
                    log.warning("[%s] контрагент выложил %r, ждали %r",
                                session.account.name, got, expect_item)
            return True
        time.sleep(poll)
    return False


def confirm(session: Session, L: dict, wait_partner_sec: float = 180.0,
            poll: float = 1.5) -> TradeResult:
    """Двухшаговое подтверждение: ACCEPT → ARE YOU SURE? → второй клик = сделка.

    Точка невозврата — второй клик. Жмём его, только когда прочитали и надпись
    ARE YOU SURE, и готовность партнёра («OTHER PLAYER HAS ACCEPTED»): если он уже
    принял, наш клик завершает обмен. Кадр-пруф снимаем ДО этого клика.
    """
    hand = session.hand
    btn = tuple(L["trade_window"]["confirm_button"])
    deadline = time.time() + wait_partner_sec

    # Фаза 1: дождаться конца отсчёта, кнопка ACCEPT — первый клик.
    while time.time() < deadline:
        phase = _confirm_phase(session, L)
        if phase == "ready":
            hand.click(*btn)
            time.sleep(0.6)
            break
        if phase == "are_you_sure":
            break  # уже во второй фазе — не кликаем ACCEPT вслепую
        time.sleep(poll)
    else:
        return TradeResult(False, "подтверждение", "кнопка ACCEPT так и не появилась")

    # Фаза 2: дождаться ARE YOU SURE и готовности партнёра — второй клик.
    while time.time() < deadline:
        sure = _confirm_phase(session, L) == "are_you_sure" or bool(session.find("mm2_are_you_sure"))
        if sure and _partner_ready(session, L):
            before = session.shot("mm2_before_confirm")   # пруф до невозврата
            hand.click(*btn)                              # исполнение
            time.sleep(1.5)
            # Успех = окно обмена закрылось (якорь YOUR OFFER пропал).
            if session.wait_for("mm2_your_offer", timeout=6):
                after = session.shot("mm2_after_confirm")
                return TradeResult(False, "подтверждение",
                                   "окно не закрылось — проверь вручную",
                                   proof=f"{before} | {after}")
            after = session.shot("mm2_done")
            return TradeResult(True, "готово", "сделка подтверждена",
                               proof=f"{before} | {after}")
        time.sleep(poll)
    return TradeResult(False, "подтверждение", "партнёр не подтвердил готовность вовремя")


# --- операции ---

def give(session: Session, nickname: str, item_name: str, L: dict | None = None,
         wait_partner_sec: float = 180.0) -> TradeResult:
    """Отдать один предмет игроку nickname."""
    L = L or load_layout()
    name = session.account.name
    log.info("[%s] ОТДАЮ %s → %s", name, item_name, nickname)
    inv_before = session.shot("mm2_inv_before")
    try:
        open_trade(session, nickname, L)
        if not offer_item(session, item_name, L):
            raise TradeAborted("выкладка", f"{item_name!r} не выложился (нет в инвентаре?)")
        res = confirm(session, L, wait_partner_sec=wait_partner_sec)
        res.proof = f"{inv_before} | {res.proof}"
        return res
    except TradeAborted as e:
        log.error("[%s] прервано на «%s»: %s", name, e.stage, e.message)
        return TradeResult(False, e.stage, e.message)


def receive(session: Session, nickname: str, expect_item: str | None = None,
            L: dict | None = None, wait_partner_sec: float = 180.0) -> TradeResult:
    """Принять предмет: инициируем обмен, свою половину не заполняем."""
    L = L or load_layout()
    name = session.account.name
    log.info("[%s] ПРИНИМАЮ у %s (жду %s)", name, nickname, expect_item or "любой предмет")
    inv_before = session.shot("mm2_inv_before")
    try:
        open_trade(session, nickname, L)
        if not wait_their_offer(session, L, timeout=wait_partner_sec, expect_item=expect_item):
            raise TradeAborted("ожидание выкладки", f"{nickname} ничего не выложил")
        res = confirm(session, L, wait_partner_sec=wait_partner_sec)
        res.proof = f"{inv_before} | {res.proof}"
        return res
    except TradeAborted as e:
        log.error("[%s] прервано на «%s»: %s", name, e.stage, e.message)
        return TradeResult(False, e.stage, e.message)


def survey(session: Session, L: dict | None = None) -> dict:
    """Снять состояние окна обмена без кликов — для калибровки координат/текстов."""
    L = L or load_layout()
    tw = L["trade_window"]
    return {
        "window_open": bool(session.find("mm2_your_offer")),
        "your_offer_title": ocr.all_text(session.frame(), tuple(tw["your_offer_title_region"])),
        "their_offer_title": ocr.all_text(session.frame(), tuple(tw["their_offer_title_region"])),
        "confirm_text": ocr.all_text(session.frame(), tuple(tw["confirm_text_region"])),
        "partner_status": ocr.all_text(session.frame(), tuple(tw["partner_status_region"])),
        "your_slot0": _slot_label(session, tuple(tw["your_offer_slots"][0])),
        "their_slot0": _slot_label(session, tuple(tw["their_offer_slots"][0])),
        "shot": str(session.shot("mm2_survey")),
    }
