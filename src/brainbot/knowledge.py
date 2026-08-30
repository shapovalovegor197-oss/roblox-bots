"""Память между запусками: что бот выучил и обо что уже бился.

До сих пор всё выученное жило ровно один запуск процесса. Таблица «клавиша →
px/с», чувствительность мыши, сторона плота собирались заново при каждом
старте — бот каждое утро заново узнавал, что `w` едет вверх по экрану. А про
упоры не помнил вообще ничего: `local_search` находил стену, откатывался и
пробовал следующую сторону, но завтра шёл в неё же.

Формат подсмотрен у videogamebench (MIT): там модель ведёт собственную память
и переписывает её на каждом шаге, а список «о чём помнить» включает ровно наши
пункты — что сработало, что нет, где были стены. Заполнять это может и код,
модель тут не обязательна.

Две вещи, без которых память вредна, а не полезна:

1. **Упор привязан к состоянию, а не к клавише.** Запись «не ходи на `w`»
   бессмысленна: через десять шагов `w` будет единственным верным. Ключ — что
   видно в кадре и где, огрублённое до клетки. Читается как «оттуда, где
   вывеска базы слева вверху, а ленты не видно, `w` упирается».

2. **Успех стирает запись.** Иначе один случайный стоп (лаг, чужой игрок на
   пути) навсегда запретит направление. Верим только повторённому упору, и
   любой удачный проход в том же состоянии отметку снимает.

Плюс срок годности: на плот встают купленные брейнроты, база физически
меняется, и запись недельной давности начинает врать.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import ROOT
from .log import get

log = get("knowledge")

DEFAULT_PATH = ROOT / "var" / "knowledge.json"

# Сколько живёт запись об упоре. Неделя — компромисс: база меняется медленнее,
# но и держать вечно нельзя.
WALL_TTL = 7 * 24 * 3600

# Через сколько залежавшейся записи дают один шанс быть опровергнутой.
# Шесть часов: за смену бот успевает много, а база так быстро не меняется.
WALL_RETRY = 6 * 3600

# Сколько раз надо упереться, чтобы поверить. Один стоп ничего не значит:
# лаг сервера или чужой игрок на пути дают ровно ту же картину.
WALL_HITS = 2

# Клетка сетки для огрубления координат ориентира. Мельче — и одно и то же
# место будет давать разные ключи из-за дрожания OCR; крупнее — и разные места
# сольются в одно.
CELL = 160


def signature(spots: dict) -> str:
    """Подпись состояния: что видно и в какой клетке экрана.

    `spots` — то, что вернул `Navigator.see()`. Пустой кадр даёт пустую подпись,
    и это осмысленно: «ничего не вижу» — тоже состояние, и упоры в нём свои.
    """
    parts = []
    for name in sorted(spots):
        s = spots[name]
        parts.append(f"{name}@{int(s.x) // CELL},{int(s.y) // CELL}")
    return "|".join(parts) or "-"


@dataclass
class Knowledge:
    """Файл `var/knowledge.json`. Мелкий, читается глазами, чинится руками."""

    path: Path = DEFAULT_PATH
    axes: dict[str, list[float]] = field(default_factory=dict)
    px_per_mouse: float | None = None
    plot_side_right: bool | None = None
    walls: dict[str, dict] = field(default_factory=dict)
    # Цвет ленты в HSV: [[h,s,v], [h,s,v]]. На событиях игра перекрашивает
    # дорожку и окружение, и зашитый в код диапазон перестаёт её видеть — молча.
    # Поэтому цвет не константа, а то, что бот подглядывает у самой ленты.
    belt_hsv: list | None = None
    # Сколько единиц мыши в полном обороте камеры. Точнее, чем «пикселей на
    # единицу»: не зависит от поля зрения и не гуляет между заходами.
    units_per_turn: int | None = None
    # Дорога от спавна к плите лока: на сколько градусов развернуться и сколько
    # шагов пройти. Найдено перебором направлений и подтверждено локом.
    lock_heading: float | None = None
    lock_steps: int | None = None

    _dirty: bool = False
    _last_save: float = 0.0

    # ------------------------------------------------------------------
    # чтение и запись
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, path: Path = DEFAULT_PATH) -> "Knowledge":
        kb = cls(path=path)
        if not path.exists():
            log.info("памяти нет, начинаю с чистого листа: %s", path)
            return kb
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            # Битый файл не должен ронять бота: память — удобство, а не условие.
            log.warning("память не читается (%s), начинаю заново", exc)
            return kb
        kb.axes = {k: list(v) for k, v in (data.get("оси") or {}).items()}
        kb.px_per_mouse = data.get("пикселей_на_мышь")
        kb.plot_side_right = data.get("база_справа")
        kb.walls = data.get("упоры") or {}
        kb.belt_hsv = data.get("цвет_ленты")
        kb.units_per_turn = data.get("единиц_на_оборот")
        kb.lock_heading = data.get("лок_градусов")
        kb.lock_steps = data.get("лок_шагов")
        kb.forget_old()
        log.info("память: %s клавиш в таблице, %s упоров, мышь %s",
                 len(kb.axes), len(kb.walls),
                 f"{kb.px_per_mouse:.2f}" if kb.px_per_mouse else "не мерена")
        return kb

    def save(self, force: bool = False) -> None:
        """Сохранить, но не чаще раза в 5 секунд.

        `_update_axis` дёргается на каждом шаге подхода; писать файл столько же
        раз незачем, а терять при аварийном выходе нечего — в худшем случае
        пропадут последние пять секунд обучения.
        """
        if not self._dirty and not force:
            return
        if not force and time.time() - self._last_save < 5.0:
            return
        data = {
            "_комментарий": "Память бота между запусками. Можно править руками; "
                            "чтобы забыть упоры, достаточно опустошить 'упоры'.",
            "оси": {k: [round(v[0], 2), round(v[1], 2)] for k, v in self.axes.items()},
            "пикселей_на_мышь": (round(self.px_per_mouse, 3)
                                 if self.px_per_mouse else None),
            "база_справа": self.plot_side_right,
            "упоры": self.walls,
            "цвет_ленты": self.belt_hsv,
            "единиц_на_оборот": self.units_per_turn,
            "лок_градусов": self.lock_heading,
            "лок_шагов": self.lock_steps,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)
        self._dirty = False
        self._last_save = time.time()

    def touch(self) -> None:
        self._dirty = True

    # ------------------------------------------------------------------
    # упоры
    # ------------------------------------------------------------------

    def forget_old(self) -> int:
        now = time.time()
        stale = [k for k, v in self.walls.items() if now - v.get("когда", 0) > WALL_TTL]
        for k in stale:
            del self.walls[k]
        if stale:
            log.info("забыл %s протухших упоров", len(stale))
            self._dirty = True
        return len(stale)

    def remember_mouse(self, px_per_mouse: float, direct: bool = False) -> None:
        """Запомнить чувствительность мыши. Замер стоит десятка доворотов.

        Раньше это делал только `_update_axis` старого подхода, а вся живая
        навигация идёт через `head_to` — и он уточнял оценку у себя, в память не
        кладя. В итоге файл оставался с `null`, и каждый заход мерил заново, хотя
        в логах замер честно повторялся по десять раз за прогон.
        """
        if not px_per_mouse or px_per_mouse <= 0:
            return
        old = self.px_per_mouse
        if direct:
            # ПРЯМОЙ замер (по сдвигу всей сцены) кладётся как есть, без
            # сглаживания. Он даёт одно и то же в шести пробах подряд, и
            # усреднять его с косвенными оценками — только портить: именно так
            # точные 0.354 уехали до 2.681 и сломали все довороты.
            self.px_per_mouse = px_per_mouse
        else:
            self.px_per_mouse = (px_per_mouse if old is None
                                 else old * 0.7 + px_per_mouse * 0.3)
        self._dirty = True

    def is_wall(self, sig: str, key: str) -> bool:
        """Верить ли записи прямо сейчас.

        Запись «протухает» дважды. Насовсем — через `WALL_TTL`. И временно —
        через `WALL_RETRY`: залежавшейся стене даётся один шанс быть
        опровергнутой. Без этого получается тупик: исключённую клавишу никто не
        пробует, значит и снять отметку нечем, и ошибочная запись живёт неделю.
        Настоящая стена подтвердится на первом же касании, `note_wall` обновит
        время; исчезнувшую снимет `note_free`.
        """
        rec = self.walls.get(f"{sig}||{key}")
        if not rec:
            return False
        age = time.time() - rec.get("когда", 0)
        if age > WALL_TTL:
            return False
        if age > WALL_RETRY:
            return False
        return rec.get("раз", 0) >= WALL_HITS

    def note_wall(self, sig: str, key: str) -> int:
        """Записать упор. Возвращает, сколько раз уже упирались отсюда этой клавишей.

        Записи с ПУСТОЙ подписью (`-`, то есть в кадре не видно ни одного
        ориентира) отбрасываются. Такая запись читается как «оттуда, где ничего
        не видно, не ходи вперёд» — а это где угодно, и применяться она будет
        не к тому месту. В памяти как раз накопились только такие: два упора,
        оба с подписью `-`, и оба бесполезны.
        """
        if sig in ("", "-"):
            log.debug("упор не записан: в кадре нет ориентиров, место не опознать")
            return 0
        k = f"{sig}||{key}"
        rec = self.walls.setdefault(k, {"раз": 0, "когда": 0.0})
        rec["раз"] += 1
        rec["когда"] = time.time()
        self._dirty = True
        if rec["раз"] == WALL_HITS:
            log.info("запомнил упор: из состояния %r клавиша %r не идёт", sig, key)
        return rec["раз"]

    def note_free(self, sig: str, key: str) -> None:
        """Прошли — значит стены тут нет. Снимаем отметку, если она была."""
        k = f"{sig}||{key}"
        if k in self.walls:
            del self.walls[k]
            self._dirty = True
            log.info("снял отметку упора: из %r клавиша %r прошла", sig, key)
