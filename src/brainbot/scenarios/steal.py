"""Операция 4: передача брейнрота между своими аккаунтами — кражей, не трейдом.

Почему кражей. Трейд кросс-серверный и по нику, но открывается только с Rebirth 1:
~$500K плюс две именные зверушки НА КАЖДЫЙ склад. Кража требует общего сервера и
ходьбы, но ребёрн не нужен вообще — а значит склад запускается сразу. Так это и делает
всё сообщество перед ребёрном: отдал альту, ребёрнулся, забрал обратно.

Поток (спецификация — research/SCENARIOS.md, операция 4):

    оба клиента в ОДНОМ сервере (launcher.build_launch_uri(job_id=...))
    донор: стоит на своей базе, не мешает
    приёмник: опорное состояние -> дойти вдоль ряда до базы донора
              -> подойти к брейнроту, E -> донести до своей базы -> поставить
              -> запереть базу
    проверка: у донора минус, у приёмника плюс, скрины до/после

ЧТО ЗДЕСЬ ЧЕСТНО НЕ ДОДЕЛАНО. Шаги «взять» и «поставить» упираются в тексты, которых
мы ещё не видели: как выглядит промпт над брейнротом, что показывает HUD во время
переноски, как подтверждается постановка на слот. Гадать нельзя — шаблон, снятый
наугад, хуже отсутствующего. Поэтому здесь есть `survey()`: он проходит маршрут и
СОБИРАЕТ тексты, которые игра показывает в каждой точке. Один прогон на живой игре —
и строки известны, после чего проверки дописываются на факты, а не на догадки.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .. import ocr
from ..farm import Farmer
from ..log import get

log = get("steal")

# Кандидаты строк, по которым узнаём состояние. Список открытый: реальные строки
# добираются из survey() на живой игре и дописываются сюда.
PROMPT_TAKE = ("steal", "take", "grab", "украсть", "забрать")
PROMPT_UNLOCK = ("unlock base", "unlock", "открыть базу")
PROMPT_PLACE = ("place", "drop", "поставить", "положить")
CARRYING = ("carrying", "несёшь", "несешь", "held")


@dataclass
class TransferTuning:
    """Тайминги перехода между базами. Калибруются один раз на нашем ряду баз."""
    # сколько идти вдоль ряда, чтобы сместиться на одну базу
    base_step_sec: float = 2.2
    # сколько идти вперёд/назад между линией ряда и глубиной базы
    into_base_sec: float = 1.2
    # пауза после каждого отрезка, чтобы кадр устоялся
    settle: float = 0.6


@dataclass
class Transfer:
    receiver: Farmer                    # склад: он ворует и уносит к себе
    donor: Farmer | None = None         # основной: стоит и не мешает; None — он вне бота
    tuning: TransferTuning = field(default_factory=TransferTuning)

    # --- восприятие ---

    def _texts(self) -> list[str]:
        return [t for t, _, _ in ocr.lines(self.receiver.frame())]

    def _sees(self, needles: tuple[str, ...]) -> str | None:
        for t in self._texts():
            for n in needles:
                if n in t:
                    return t
        return None

    def carrying(self) -> bool:
        """Несём ли мы сейчас брейнрота. Пока — по кандидатам строк из CARRYING."""
        return self._sees(CARRYING) is not None

    # --- перемещение вдоль ряда ---

    def step_along_row(self, bases: int, to_right: bool) -> None:
        """Сместиться на `bases` баз вдоль ряда.

        Ряд из 8 баз идёт линией, конвейер вдоль него. При виде сверху (его ставит
        опорное состояние) a/d стабильно = экранные лево/право, поэтому смещение
        считается таймингом. Величина шага — единственное, что нужно откалибровать.
        """
        key = "d" if to_right else "a"
        for i in range(abs(bases)):
            self.receiver.hand.hold(key, self.tuning.base_step_sec)
            time.sleep(self.tuning.settle)
            log.info("прошёл базу %s/%s", i + 1, abs(bases))

    # --- сценарий ---

    def survey(self, bases: int, to_right: bool) -> dict:
        """Разведка маршрута: пройти к базе донора и записать, ЧТО игра показывает.

        Ничего не жмёт, кроме ходьбы. Нужен один раз, чтобы узнать реальные строки
        промптов вместо догадок — после этого дописываются проверки в take()/place().
        """
        out: dict[str, list[str]] = {}
        if not self.receiver.to_reference():
            return {"ошибка": ["опорное состояние не взято"]}

        out["на своей базе"] = self._texts()
        self.receiver.shot("survey_home")

        self.step_along_row(bases, to_right)
        time.sleep(0.8)
        out["у базы донора"] = self._texts()
        self.receiver.shot("survey_donor_base")

        self.receiver.hand.hold("w", self.tuning.into_base_sec)
        time.sleep(0.8)
        out["внутри базы донора"] = self._texts()
        self.receiver.shot("survey_inside")

        for where, texts in out.items():
            log.info("[survey] %s: %s", where, " | ".join(texts[:12]))
        return out

    def take(self, attempts: int = 2) -> bool:
        """Подойти к брейнроту и забрать (E). Успех — признак переноски."""
        for attempt in range(1, attempts + 1):
            seen = self._sees(PROMPT_TAKE) or self._sees(PROMPT_UNLOCK)
            if seen:
                log.info("вижу промпт: %r", seen)
            else:
                log.warning("промпта не видно (попытка %s) — жму вслепую", attempt)
            self.receiver.hand.interact()
            time.sleep(1.0)
            if self.carrying():
                log.info("брейнрот в руках")
                return True
            self.receiver.shot("fail_take")
        log.error("забрать не удалось — либо промпт другой, либо мы не у объекта. "
                  "Прогони survey() и допиши строки в PROMPT_TAKE/CARRYING")
        return False

    def carry_home(self, bases: int, to_right: bool) -> None:
        """Донести до своей базы — тем же путём, но в обратную сторону."""
        self.receiver.hand.hold("s", self.tuning.into_base_sec)
        time.sleep(self.tuning.settle)
        self.step_along_row(bases, not to_right)

    def place(self, attempts: int = 2) -> bool:
        """Поставить брейнрота на свободный слот своей базы."""
        for attempt in range(1, attempts + 1):
            seen = self._sees(PROMPT_PLACE)
            if seen:
                log.info("вижу промпт постановки: %r", seen)
            self.receiver.hand.interact()
            time.sleep(1.2)
            if not self.carrying():
                log.info("поставлено")
                return True
            self.receiver.shot("fail_place")
            log.warning("всё ещё несём (попытка %s)", attempt)
        return False

    def run(self, bases: int, to_right: bool) -> bool:
        """Полный перенос: дойти, забрать, вернуться, поставить, запереть.

        bases — на сколько баз вдоль ряда стоит база донора, to_right — в какую сторону.
        Пока считаем шагами по ряду, а не по нику: мелкие ники над базами OCR не тянет,
        только крупную плашку YOUR BASE. Донор в этот момент стоит на своей базе и
        служит живой меткой.
        """
        if self.donor is not None:
            # донор просто приводит себя в известное состояние и не мешает
            self.donor.to_reference()

        if not self.receiver.to_reference():
            return False
        before = self.receiver.shot("transfer_before")

        self.step_along_row(bases, to_right)
        self.receiver.hand.hold("w", self.tuning.into_base_sec)
        time.sleep(0.8)

        if not self.take():
            return False

        self.carry_home(bases, to_right)
        if not self.place():
            return False

        sec = self.receiver.lock_base()
        after = self.receiver.shot("transfer_after")
        log.info("перенос завершён, база заперта на %s с. Пруфы: %s | %s", sec, before, after)
        return True
