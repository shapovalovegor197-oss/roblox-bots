"""Операция 7: цикл, который сшивает отдельные действия в суточную работу.

Отдельные операции без цикла бесполезны — склад должен жить сутками сам.
Приоритеты жёсткие и в этом порядке (спецификация — research/SCENARIOS.md):

    1. LOCK     если до истечения лока меньше запаса. Всё остальное ждёт:
                незапертую базу обносят, и тогда вся ферма была зря.
    2. COLLECT  по расписанию.
    3. BUY      всё оставшееся время, ломтями, чтобы между ними успевали
                отработать первые два приоритета.

Любой отказ операции возвращает цикл в опорное состояние и повторяет. Три отказа
подряд на одной операции — пауза и запись в лог: дальше это забота супервизора,
клиент мог вылететь или сервер умереть.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .farm import Farmer
from .log import get

log = get("routine")


@dataclass
class RoutineConfig:
    collect_every: float = 300.0     # как часто собирать кэш
    lock_margin: float = 20.0        # перезапирать за столько секунд до истечения
    lock_fallback: float = 60.0      # если игра не сказала длительность — считаем такой
    buy_slice: float = 45.0          # ломоть покупки между проверками приоритетов
    min_income: float = 100.0
    min_rarity: str | None = None
    max_fails: int = 3               # отказов подряд на одной операции до паузы
    fail_pause: float = 60.0


@dataclass
class Routine:
    farmer: Farmer
    cfg: RoutineConfig = field(default_factory=RoutineConfig)

    lock_until: float = 0.0
    next_collect: float = 0.0
    fails: dict = field(default_factory=dict)
    stats: dict = field(default_factory=lambda: {"bought": 0, "collected": 0.0, "locks": 0})

    def _fail(self, op: str) -> bool:
        """Учесть отказ. True — пора делать паузу, отказов подряд слишком много."""
        n = self.fails.get(op, 0) + 1
        self.fails[op] = n
        log.warning("операция %s: отказ %s подряд", op, n)
        if n >= self.cfg.max_fails:
            log.error("операция %s валится %s раз подряд — пауза %.0f с",
                      op, n, self.cfg.fail_pause)
            self.fails[op] = 0
            return True
        return False

    def _ok(self, op: str) -> None:
        self.fails[op] = 0

    # --- отдельные шаги цикла ---

    def do_lock(self) -> None:
        sec = self.farmer.lock_base()
        if sec:
            self.lock_until = time.time() + sec
            self.stats["locks"] += 1
            self._ok("lock")
            log.info("лок держится до +%s с", sec)
        else:
            # не подтвердилось — не считаем, что заперты, но и не долбим без конца
            self.lock_until = time.time() + self.cfg.lock_fallback / 2
            if self._fail("lock"):
                time.sleep(self.cfg.fail_pause)

    def do_collect(self) -> None:
        gain = self.farmer.collect_money()
        self.next_collect = time.time() + self.cfg.collect_every
        if gain > 0:
            self.stats["collected"] += gain
            self._ok("collect")
        # ноль — не обязательно отказ: копить могло быть нечего

    def do_buy(self) -> None:
        n = self.farmer.buy_at_conveyor(
            min_income=self.cfg.min_income,
            min_rarity=self.cfg.min_rarity,
            seconds=self.cfg.buy_slice,
        )
        self.stats["bought"] += n
        if n:
            self._ok("buy")

    # --- цикл ---

    def tick(self) -> str:
        """Один шаг цикла. Возвращает имя выполненной операции — для лога."""
        now = time.time()
        if now >= self.lock_until - self.cfg.lock_margin:
            self.do_lock()
            return "lock"
        if now >= self.next_collect:
            self.do_collect()
            return "collect"
        self.do_buy()
        return "buy"

    def run(self, seconds: float | None = None) -> dict:
        """Гонять цикл. seconds=None — до Ctrl+C."""
        log.info("цикл пошёл: лок раз в ~%.0f с, сбор раз в %.0f с, покупка ломтями по %.0f с",
                 self.cfg.lock_fallback, self.cfg.collect_every, self.cfg.buy_slice)
        end = time.time() + seconds if seconds else None
        try:
            while end is None or time.time() < end:
                if not self.farmer.window.alive():
                    log.error("окно клиента исчезло — выходим, дальше дело супервизора")
                    break
                op = self.tick()
                log.info("такт: %s | куплено %s, собрано %.0f, локов %s",
                         op, self.stats["bought"], self.stats["collected"], self.stats["locks"])
        except KeyboardInterrupt:
            log.info("остановлено вручную")
        finally:
            self.farmer.hand.release_all()
        return self.stats
