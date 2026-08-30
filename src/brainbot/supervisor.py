"""Супервизор: держит клиенты живыми.

Это не украшение архитектуры, а обязательная часть. Roblox сам закрывает лишние
окна при мультиинстансе, клиент падает на обновлениях, а машина уходит в
перезагрузку. Всё, что должно висеть сутками, должно уметь подниматься само.
"""
from __future__ import annotations

import json
import signal
import time

from .antiafk import AntiAfk
from .config import Settings
from .log import get
from .mutex import SingletonMutex
from .session import Session

log = get("supervisor")


class Supervisor:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.sessions: list[Session] = [
            Session(account=a, settings=settings) for a in settings.enabled_accounts
        ]
        self.antiafk = AntiAfk(
            key=settings.antiafk["key"], interval=settings.antiafk["interval_sec"]
        )
        self.mutex = SingletonMutex()
        self.running = False
        self._backoff = settings.supervisor["relaunch_backoff_sec"]
        self._next_try: dict[str, float] = {}
        self._client_version: str | None = None

    # --- состояние на диск, чтобы после ребута было видно, что было ---

    def save_state(self) -> None:
        state = {
            "updated": time.time(),
            "sessions": [
                {
                    "account": s.account.name,
                    "role": s.account.role,
                    "alive": s.alive,
                    "pid": s.pid,
                    "hwnd": s.window.hwnd if s.window else None,
                    "uptime_sec": int(time.time() - s.launched_at) if s.launched_at else 0,
                    "fails": s.fails,
                }
                for s in self.sessions
            ],
        }
        self.settings.state_file.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # --- основной цикл ---

    def _backoff_delay(self, fails: int) -> float:
        idx = min(fails, len(self._backoff) - 1)
        return float(self._backoff[idx])

    def ensure(self, session: Session, index: int) -> None:
        if session.alive:
            return

        now = time.time()
        due = self._next_try.get(session.account.name, 0.0)
        if now < due:
            return

        if session.window is not None:
            log.warning("[%s] окно пропало — поднимаю заново", session.account.name)
            session.close_capture()   # поток WGC привязан к hwnd, он больше не валиден
            session.window = None

        if session.launch():
            session.apply_window_layout(index)
            session.optimize_process(index, len(self.sessions))
            log.info("[%s] в игре", session.account.name)
        else:
            delay = self._backoff_delay(session.fails)
            self._next_try[session.account.name] = now + delay
            log.warning("[%s] не поднялся (попытка %s), следующая через %.0f с",
                        session.account.name, session.fails, delay)

    def check_client_update(self) -> None:
        """Roblox обновляется молча, и старый клиент после этого мёртв.

        Проверено вживую: Roblox выкатил новую версию посреди сессии, поставил
        её и переключил на неё протокол, а работавший старый клиент перестал
        попадать на серверы — «Failed to connect, no response» на любом
        датацентре. Снаружи это выглядит как поломка сети, и диагностируется
        часами. Поэтому смотрим на версию сами и перезапускаемся сразу.
        """
        from .launcher import find_player_exe
        try:
            current = find_player_exe().parent.name
        except Exception:  # noqa: BLE001
            return

        if self._client_version is None:
            self._client_version = current
            return

        if current != self._client_version:
            log.warning("Roblox обновился: %s → %s. Перезапускаю клиенты, "
                        "иначе они не войдут ни на один сервер",
                        self._client_version, current)
            self._client_version = current
            for s in self.sessions:
                if s.alive and s.window:
                    self.kill_session(s)

    def kill_session(self, session: Session) -> None:
        """Закрыть клиент, чтобы супервизор поднял его заново на новой версии."""
        import subprocess
        session.close_capture()
        if session.pid:
            subprocess.run(["taskkill", "/F", "/PID", str(session.pid)],
                           capture_output=True)
        session.window = None
        session.pid = None

    def tick(self) -> None:
        self.check_client_update()
        for i, s in enumerate(self.sessions):
            self.ensure(s, i)
        self.antiafk.tick([s.window for s in self.sessions if s.alive and s.window])
        self.save_state()

    def run(self) -> None:
        if not self.sessions:
            log.error("нет включённых аккаунтов с кукой — заполни config/accounts.json")
            return

        self.mutex.acquire()
        self.running = True

        def stop(_sig, _frm):
            log.info("остановка по сигналу")
            self.running = False

        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)

        log.info("супервизор поднят: %s", ", ".join(s.account.name for s in self.sessions))
        poll = self.settings.supervisor["poll_sec"]
        try:
            while self.running:
                try:
                    self.tick()
                except Exception:  # noqa: BLE001 — цикл не должен умирать от одной ошибки
                    log.exception("сбой в цикле, продолжаю")
                time.sleep(poll)
        finally:
            self.mutex.release()
            log.info("супервизор остановлен")
