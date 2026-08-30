"""Сценарий передачи: отдать брейнрота игроку по нику.

Поток, как он выглядит в игре после Update 53:
    кнопка Trade в основном GUI  →  вкладка поиска  →  ввод ника  →  найденный игрок
    →  отправка приглашения  →  ждём принятия  →  кладём предмет в свою половину окна
    →  Ready  →  ждём Ready второй стороны  →  подтверждение  →  скрин-пруф

ШАБЛОНЫ, которые нужны (положить в templates/ под этими именами):
    trade_button        кнопка Trade в основном интерфейсе, рядом с Codes
    trade_window        любой якорь открытого окна трейда — заголовок или рамка
    trade_tab_search    вкладка поиска по нику
    trade_search_field  поле ввода ника
    trade_search_result первая строка результата поиска
    trade_invite        кнопка отправки приглашения
    trade_accepted      признак, что вторая сторона вошла в трейд
    trade_my_slot_empty пустой слот в своей половине окна
    trade_ready         кнопка Ready
    trade_ready_theirs  индикатор, что вторая сторона нажала Ready
    trade_confirm       финальное подтверждение
    trade_done          признак завершённого трейда
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from ..log import get
from ..session import Session

log = get("trade")


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


def _step(session: Session, template: str, stage: str, timeout: float = 12.0) -> None:
    """Дождаться элемент и кликнуть. Не получилось — валимся со скрином."""
    if not session.click_template(template, timeout=timeout):
        path = session.shot(f"fail_{stage}")
        raise TradeAborted(stage, f"не нашли {template}, скрин: {path}")
    log.info("[%s] %s ok", session.account.name, stage)


def give(session: Session, nickname: str, item_template: str,
         wait_partner_sec: float = 180.0) -> TradeResult:
    """Отдать один предмет игроку с ником nickname.

    item_template — шаблон конкретного брейнрота в инвентаре. Пока шаблонов
    брейнротов нет, для проверки потока годится любой предмет.
    """
    name = session.account.name
    log.info("[%s] трейд → %s, предмет %s", name, nickname, item_template)

    try:
        # 1. Открыть окно трейда
        _step(session, "trade_button", "открыть трейд")
        if not session.wait_for("trade_window", timeout=10):
            raise TradeAborted("открыть трейд", "окно трейда не появилось")

        # 2. Найти игрока по нику. Кросс-сервер работает — сервер партнёра неважен.
        _step(session, "trade_tab_search", "вкладка поиска")
        _step(session, "trade_search_field", "поле ввода")
        session.hand.clear_field()
        session.hand.type_text(nickname)
        time.sleep(0.8)  # игре нужно время на запрос

        if not session.click_template("trade_search_result", timeout=8):
            path = session.shot("fail_search")
            raise TradeAborted("поиск", f"ник {nickname!r} не найден в игре, скрин: {path}")

        # 3. Пригласить и дождаться, пока вторая сторона примет
        _step(session, "trade_invite", "приглашение")
        if not session.wait_for("trade_accepted", timeout=wait_partner_sec):
            raise TradeAborted("ожидание", f"{nickname} не принял за {wait_partner_sec:.0f} с")

        # 4. Положить предмет
        _step(session, item_template, "выбор предмета")

        # 5. Готовность с обеих сторон
        _step(session, "trade_ready", "наш Ready")
        if not session.wait_for("trade_ready_theirs", timeout=120):
            raise TradeAborted("ожидание", "вторая сторона не нажала Ready")

        # 6. Пруф ДО подтверждения — на нём видно, что именно уходит.
        #    Эскроу в игре нет, подмена в последний момент ловится только здесь.
        before = session.shot("trade_before_confirm")

        _step(session, "trade_confirm", "подтверждение")
        if not session.wait_for("trade_done", timeout=30):
            path = session.shot("fail_confirm")
            return TradeResult(False, "подтверждение",
                               f"нет признака завершения — проверь вручную, скрин: {path}",
                               proof=str(before))

        after = session.shot("trade_done")
        log.info("[%s] трейд с %s завершён", name, nickname)
        return TradeResult(True, "готово", f"отдано {item_template} → {nickname}",
                           proof=f"{before} | {after}")

    except TradeAborted as e:
        log.error("[%s] трейд прерван на «%s»: %s", name, e.stage, e.message)
        return TradeResult(False, e.stage, e.message)


def receive(session: Session, nickname: str, wait_sec: float = 180.0) -> TradeResult:
    """Принять входящий трейд от игрока nickname.

    Пока заглушка потока: нужны шаблоны входящего приглашения. Логика зеркальна
    give(), но инициатор — вторая сторона.
    """
    raise NotImplementedError(
        "нужны шаблоны входящего приглашения — снимем, когда будет скрин уведомления"
    )
