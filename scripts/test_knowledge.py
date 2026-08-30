"""Проверка памяти и детектора упора без игры.

Подсовываем `Navigator` подставного `Farmer`: кадры рисуем сами, ввод никуда не
идёт. Это позволяет проверить логику до боевого прогона — живой заход стоит
минут и всё равно не даст повторить одну и ту же ситуацию дважды.

    python scripts/test_knowledge.py
"""
from __future__ import annotations

import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from brainbot import nav as navmod                                  # noqa: E402
from brainbot.knowledge import (Knowledge, WALL_HITS, WALL_RETRY,
                                signature)      # noqa: E402
from brainbot.nav import Navigator, Spot                            # noqa: E402

W, H = 1280, 720


class Box:
    width, height = W, H


@dataclass
class FakeHand:
    log: list = field(default_factory=list)

    def hold(self, key, sec):
        self.log.append(("hold", key, round(sec, 2)))

    def jump(self):
        self.log.append(("jump",))

    def look(self, dx, dy=0):
        self.log.append(("look", dx))


@dataclass
class FakeFarmer:
    """Мир, где ориентир едет к цели от 'd' и упирается на 'w'."""

    hand: FakeHand = field(default_factory=FakeHand)
    spot: Spot = None
    walls: tuple = ("w",)
    tick: int = 0

    class window:
        @staticmethod
        def client_box():
            return Box()

    def __post_init__(self):
        if self.spot is None:
            self.spot = Spot("collect", 200, 300, 1.0)

    def frame(self):
        # Кадр несёт ровно одно: сдвинулся ли мир. Рисуем шум, чей рисунок
        # зависит от счётчика — так `_moved` увидит изменение, когда мы его
        # разрешили, и ноль, когда упёрлись.
        rng = np.random.default_rng(self.tick)
        return rng.integers(0, 255, (H, W, 3), dtype=np.uint8)

    def _moved(self, a, b):
        return float((np.abs(a[..., 0].astype(int) - b[..., 0].astype(int)) > 25).mean())

    def sees(self, *needles):
        return None

    def dismiss_modals(self, force=False):
        return True

    def shot(self, tag):
        return None


def patched_navigator(farmer, kb_path):
    """Navigator, который видит один ориентир — тот, что держит FakeFarmer."""
    nav = Navigator(farmer=farmer)
    nav._kb = Knowledge.load(kb_path)
    nav.snapshot = lambda: (farmer.frame(), {"collect": farmer.spot})
    return nav


SPEED = 180.0      # px в секунду удержания, одинаково по обеим осям

def move(farmer, key, hold):
    """Как мир отвечает на удержание: стена не двигает ничего.

    Скорость пропорциональна удержанию — иначе выученная ботом таблица
    противоречит миру, и цикл не сходится не из-за логики, а из-за подделки.
    """
    if key in farmer.walls:
        return                      # tick не растёт -> кадр тот же -> упор
    if hold < 0.3:
        return                      # замерено в игре: короче 0.3 с — топтание
    farmer.tick += 1
    step = SPEED * hold
    dx = {"d": step, "a": -step}.get(key, 0.0)
    dy = {"s": step, "w": -step}.get(key, 0.0)
    farmer.spot = Spot("collect", farmer.spot.x + dx, farmer.spot.y + dy, 1.0)


def run_once(kb_path, walls=("w",)) -> tuple[bool, FakeFarmer, Navigator]:
    farmer = FakeFarmer(walls=walls)
    nav = patched_navigator(farmer, kb_path)
    real_hold = farmer.hand.hold

    def hold(key, sec):
        real_hold(key, sec)
        move(farmer, key, sec)

    farmer.hand.hold = hold
    ok = nav.approach("collect", target=(640, 518), tol=60, max_steps=20,
                      step_hold=0.5)
    return ok, farmer, nav


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="brainbot-kb-")) / "knowledge.json"
    bad = 0

    def check(name, cond, extra=""):
        nonlocal bad
        print(f"  [{'ok ' if cond else 'ПРОВАЛ'}] {name} {extra}")
        if not cond:
            bad += 1

    print("1. Подпись состояния")
    sig = signature({"collect": Spot("collect", 200, 300, 1.0)})
    check("огрубляется до клетки", sig == "collect@1,1", f"-> {sig}")
    near = signature({"collect": Spot("collect", 250, 310, 1.0)})
    check("соседние точки дают ту же подпись", near == sig, f"-> {near}")

    print("2. Первый заход: упор находится и запоминается")
    ok, farmer, nav = run_once(tmp)
    keys = [e[1] for e in farmer.hand.log if e[0] == "hold"]
    wall_hits = sum(1 for k in keys if k == "w")
    check("подход завершился", ok is True)
    check("в стену стучались, но не бесконечно", 0 < wall_hits <= 3,
          f"(ударов в 'w': {wall_hits})")
    check("прыжок был ровно один", sum(1 for e in farmer.hand.log if e[0] == "jump") == 1)
    nav.kb.save(force=True)

    print("3. Память пережила перезапуск")
    again = Knowledge.load(tmp)
    walls = [k for k in again.walls if k.endswith("||w")]
    check("упор записан", bool(walls), f"-> {walls[:1]}")
    check("порог срабатывания соблюдён",
          all(v["раз"] >= 1 for v in again.walls.values()))
    check("таблица управления сохранилась", bool(again.axes), f"-> {list(again.axes)}")

    print(f"4. Порог доверия: верим только после {WALL_HITS} упоров")
    ok2, farmer2, nav2 = run_once(tmp)
    hits2 = sum(1 for e in farmer2.hand.log if e[0] == "hold" and e[1] == "w")
    check("подход завершился", ok2 is True)
    check("одного упора мало — пробуем ещё раз", hits2 >= 1, f"(ударов: {hits2})")
    nav2.kb.save(force=True)
    check("после второго упора запись стала весомой",
          Knowledge.load(tmp).is_wall("collect@1,1", "w"))

    print("5. Третий заход: в известную стену из ТОЙ ЖЕ точки не идём")
    ok3, farmer3, nav3 = run_once(tmp)
    first = next(e[1] for e in farmer3.hand.log if e[0] == "hold")
    check("подход завершился", ok3 is True)
    # Память привязана к месту, а не к клавише: в подставном мире 'w' закрыта
    # везде, но бот об этом знает только про стартовую клетку. Проверяем именно
    # это — из неё он в стену больше не пойдёт.
    check("первый шаг уже не в стену", first != "w", f"(взял {first})")

    print("6. Залежавшуюся запись перепроверяем, и удачный проход её снимает")
    kb = Knowledge.load(tmp)
    for rec in kb.walls.values():          # состарим запись на семь часов
        rec["когда"] -= WALL_RETRY + 3600
    kb.save(force=True)
    check("устаревшей записи больше не верим", not kb.is_wall("collect@1,1", "w"))
    ok4, farmer4, nav4 = run_once(tmp, walls=())
    nav4.kb.save(force=True)
    check("подход завершился", ok4 is True)
    check("в стартовой клетке стены больше нет",
          not Knowledge.load(tmp).is_wall("collect@1,1", "w"))

    print("7. note_free снимает отметку напрямую")
    solo = Knowledge(path=Path(tempfile.mkdtemp()) / "k.json")
    for _ in range(WALL_HITS):
        solo.note_wall("s1", "w")
    check("после порога — стена", solo.is_wall("s1", "w"))
    solo.note_free("s1", "w")
    check("после удачного прохода — снята", not solo.is_wall("s1", "w"))
    check("соседнее состояние не задето", not solo.is_wall("s2", "w"))

    print(f"\nитого: {'всё сошлось' if not bad else str(bad) + ' проверок провалено'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
