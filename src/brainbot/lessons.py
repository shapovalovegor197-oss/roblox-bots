"""Обучение показом: записать проход человека и повторить его.

Самый дешёвый способ научить бота дороге. Точка спавна одна и та же, камера
приводится к одному и тому же положению — значит достаточно один раз пройти
маршрут руками, записать НАЖАТИЯ с длительностями и потом их повторять.

Зрение при этом не выключается: повтор проверяется по контрольным признакам
(появился промпт покупки, изменились деньги). Слепой макрос ломается от любого
сдвига, а макрос с проверками — говорит, где именно он сломался.

Порядок работы:

    python run.py teach --seconds 60      пассивная запись прохода
    python run.py lessons                 что записано
    python run.py replay                  бот повторяет выученное

Выбирается полный успешный показ; успешно проверенный повтор имеет приоритет.
Новая запись хранит исходные события мыши и клавиш на общей временной шкале.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from .log import get

log = get("lessons")

# Клавиши маршрута. Мышь нового урока хранится отдельно в потоке событий.
REPLAY_KEYS = {"w", "a", "s", "d", "e", "space", "shift"}

# Правая кнопка мыши в уроке НЕ повторяется как отдельный шаг, но записывается
# в заметку: человек держит её всю дорогу, чтобы камера была зафиксирована
# относительно персонажа. Наш `Hand.hold` теперь делает это сам на каждом
# удержании, так что в шагах ПКМ не нужна — а вот знать, держал ли её человек,
# полезно: если нет, значит и боту не надо.
MOUSE_KEYS = {"ПКМ", "ЛКМ"}

# Более коротких удержаний не бывает осмысленных — это дребезг.
MIN_HOLD = 0.06


@dataclass
class Turn:
    """Поворот камеры: на сколько единиц мыши и сколько ждать перед ним.

    Без этого урок был наполовину пуст. В нём стояло «прошёл вперёд 1.5 с», но
    не стояло «повернулся на столько-то» — а путь человека как раз и состоит из
    чередования того и другого. Повторить по такому уроку нельзя: бот шёл в ту
    сторону, куда случайно смотрел.
    """
    dx: int
    dy: int
    pause: float

    def as_dict(self) -> dict:
        return {"поворот": [self.dx, self.dy], "пауза": round(self.pause, 2)}

    @staticmethod
    def from_dict(d: dict) -> "Turn":
        dx, dy = d["поворот"]
        return Turn(int(dx), int(dy), float(d["пауза"]))


@dataclass
class Step:
    """Одновременное удержание одной или нескольких клавиш.

    Клавиш может быть несколько: человек ходит по диагонали, держа W и D разом.
    Если разложить такое в два последовательных шага, повтор пойдёт по ломаной
    вместо диагонали и уедет в сторону — поэтому пересекающиеся во времени
    удержания склеиваются в один шаг.
    """
    keys: list[str]
    hold: float       # сколько держать
    pause: float      # сколько ждать перед этим шагом

    @property
    def key(self) -> str:
        return self.keys[0] if self.keys else ""

    def as_dict(self) -> dict:
        return {"клавиши": self.keys, "держать": round(self.hold, 2),
                "пауза": round(self.pause, 2)}

    @staticmethod
    def from_dict(d: dict) -> "Step":
        keys = d.get("клавиши")
        if not keys:
            keys = [d["клавиша"]]          # старый формат — одна клавиша
        return Step(list(keys), float(d["держать"]), float(d["пауза"]))


@dataclass
class Lesson:
    name: str
    steps: list[Step | Turn] = field(default_factory=list)
    note: str = ""
    ok: bool | None = None        # достиг ли урок цели по признакам
    goal: str | None = None
    replay_ok: bool | None = None
    capture_ok: bool = True
    events: list = field(default_factory=list)
    context: dict = field(default_factory=dict)
    source_path: Path | None = field(default=None, repr=False)

    def as_dict(self) -> dict:
        return {"название": self.name, "заметка": self.note, "получилось": self.ok,
                "шаги": [s.as_dict() for s in self.steps], "цель": self.goal,
                "повтор_получился": self.replay_ok, "запись_полная": self.capture_ok,
                "события": self.events, "контекст": self.context}

    @staticmethod
    def from_dict(d: dict) -> "Lesson":
        steps = [Turn.from_dict(x) if "поворот" in x else Step.from_dict(x)
                 for x in d.get("шаги", [])]
        return Lesson(name=d.get("название", "?"), note=d.get("заметка", ""),
                      ok=d.get("получилось"), steps=steps, goal=d.get("цель"),
                      replay_ok=d.get("повтор_получился"),
                      capture_ok=d.get("запись_полная", True),
                      events=d.get("события", []), context=d.get("контекст", {}))

    def duration(self) -> float:
        return sum(getattr(s, "hold", 0.0) + s.pause for s in self.steps)


def holds_to_steps(holds: list[tuple[str, float, float]],
                   glue: float = 0.05) -> list[Step]:
    """Split at every key transition; partial overlaps retain their duration.

    `glue` remains accepted for callers using the old API, but never changes
    the timing of a recorded key press.
    """
    items = [(start, start + dur, key) for key, start, dur in holds
             if key in REPLAY_KEYS and dur >= MIN_HOLD]
    boundaries = sorted({t for start, stop, _ in items for t in (start, stop)})
    steps: list[Step] = []
    prev_end = 0.0
    for start, stop in zip(boundaries, boundaries[1:]):
        keys = sorted({key for a, b, key in items if a <= start < b})
        if not keys:
            continue
        steps.append(Step(keys=keys, hold=stop - start,
                          pause=max(0.0, start - prev_end)))
        prev_end = stop
    return steps


def turns_from_video(path, fps: int = 8, px_per_mouse: float = 0.39,
                     min_px: float = 12.0, gap: float = 0.4) -> list:
    """Достать повороты камеры ИЗ ВИДЕО прохода. [(время, dx_мыши, dy_мыши)].

    Записать повороты по курсору нельзя: Roblox прижимает мышь, пока зажата
    ПКМ, и GetCursorPos возвращает одну точку. Но повороты видны на самой
    записи — при вращении камеры вся сцена уезжает по горизонтали, и это
    измеряется фазовой корреляцией соседних кадров.

    Важно, что это НЕ противоречит правилу «не мерить глобальный поток»: то
    правило про ХОДЬБУ, где камера едет за персонажем и картинка почти не
    меняется. Поворот же двигает весь кадр разом, и как раз тут корреляция
    работает.

    Соседние сдвиги одного знака склеиваются в один поворот: человек крутит
    мышью плавно, и один жест размазан по десятку кадров.
    """
    import cv2
    import numpy as np

    cap = cv2.VideoCapture(str(path))
    prev, i, raw = None, 0, []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        i += 1
        g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)[120:600, 160:1120].astype(np.float32)
        if prev is not None:
            (dx, dy), _ = cv2.phaseCorrelate(prev, g)
            # Берём только ГОРИЗОНТАЛЬНЫЕ сдвиги. Вертикаль такой же величины
            # означает не поворот камеры, а смену сцены: респавн, телепорт,
            # всплывшее окно. На таких кадрах корреляция даёт ерунду вроде
            # dy=1638, и она попадала в урок как «поворот».
            if abs(dx) >= min_px and abs(dy) < abs(dx) * 0.8:
                raw.append((i / fps, dx, dy))
        prev = g
    cap.release()

    out, cur = [], None
    for t, dx, dy in raw:
        if cur and (t - cur[3]) <= gap and (dx > 0) == (cur[1] > 0):
            cur[1] += dx
            cur[2] += dy
            cur[3] = t
        else:
            if cur:
                out.append(cur)
            cur = [t, dx, dy, t]
    if cur:
        out.append(cur)

    # Сдвиг сцены в пикселях -> единицы мыши. Знак обратный: камера уехала
    # влево, значит мышь вели вправо.
    k = max(px_per_mouse, 0.05)
    return [(t, int(-dx / k), int(-dy / k)) for t, dx, dy, _ in out]


def merge_turns(steps: list, turns: list, holds: list) -> list:
    """Split holds at turn timestamps, never moving the clock backwards."""
    if not turns:
        return steps
    turns = sorted(turns)
    out, clock, source_clock, ti = [], 0.0, 0.0, 0
    for step in steps:
        step_start = source_clock + step.pause
        step_end = step_start + step.hold
        while ti < len(turns) and turns[ti][0] <= step_start:
            t, dx, dy = turns[ti]
            out.append(Turn(dx, dy, max(0.0, t - clock)))
            clock = t
            ti += 1
        cursor = step_start
        while ti < len(turns) and turns[ti][0] < step_end:
            t, dx, dy = turns[ti]
            if t > cursor:
                out.append(Step(list(step.keys), t - cursor, max(0.0, cursor - clock)))
            out.append(Turn(dx, dy, 0.0))
            clock = cursor = t
            ti += 1
        if step_end > cursor:
            out.append(Step(list(step.keys), step_end - cursor, max(0.0, cursor - clock)))
        clock = source_clock = step_end
    while ti < len(turns):
        t, dx, dy = turns[ti]
        out.append(Turn(dx, dy, max(0.0, t - clock)))
        clock = t
        ti += 1
    return out


def lessons_dir(base: Path) -> Path:
    d = base / "lessons"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save(lesson: Lesson, base: Path) -> Path:
    path = lessons_dir(base) / f"{lesson.name}_{int(time.time())}.json"
    path.write_text(json.dumps(lesson.as_dict(), ensure_ascii=False, indent=1),
                    encoding="utf-8")
    log.info("урок записан: %s (%s шагов, %.1f с)", path, len(lesson.steps),
             lesson.duration())
    return path


def load_all(base: Path, name: str | None = None) -> list[Lesson]:
    out = []
    for path in sorted(lessons_dir(base).glob("*.json")):
        try:
            lesson = Lesson.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
        if name and lesson.name != name:
            continue
        lesson.source_path = path
        out.append(lesson)
    return out


def merge(lessons: list[Lesson]) -> Lesson | None:
    """Select a complete successful demonstration, preferring proven replay."""
    good = [l for l in lessons if l.ok is True and l.steps and l.capture_ok
            and l.replay_ok is not False and l.goal is not None]
    if not good:
        return None
    if len(good) == 1:
        return good[0]
    # Choose a demonstrated trajectory. A median is a new, untested route;
    # it must not silently replace one that has actually been replayed.
    return min(good, key=lambda l: (l.replay_ok is not True, l.duration()))


def infer_goal(name: str) -> str | None:
    name = name.lower()
    if "lock" in name or "лок" in name or "закры" in name:
        return "lock"
    if "лент" in name or "belt" in name:
        return "prompt"
    return None


def goal_check(farmer, goal):
    """Read-only checks. A purchase prompt proves arrival, not a transaction."""
    if goal == "lock":
        return bool(farmer.read_lock_left(quick=True))
    if goal == "prompt":
        return bool(farmer.sees("purchase"))
    return False


class GoalCheck:
    """Purchase evidence follows the farm's existing cash-debit check.

    Two independent reads must confirm a debit, and the lesson must contain E.
    Missing OCR data never becomes success. This confirms spending, not delivery
    of an item to the base (which is a separate task).
    """
    def __init__(self, farmer, goal, interacted=lambda: False):
        self.farmer, self.goal, self.interacted = farmer, goal, interacted
        self.before = None
        if goal == "purchase":
            a, b = self._cash(), self._cash()
            if a is not None and b is not None:
                self.before = min(a, b)

    def _cash(self):
        value = self.farmer.read_cash(toggle=False)
        return self.farmer.read_hud_cash() if value is None else value

    def __call__(self):
        if self.goal != "purchase":
            return goal_check(self.farmer, self.goal)
        if self.before is None or not self.interacted():
            return False
        a = self._cash()
        if a is None or a >= self.before:
            return False
        b = self._cash()
        return b is not None and b < self.before


def teach(farmer, name: str, seconds: float = 60.0, countdown: int = 5,
          setup: bool = False, goal: str | None = None) -> Lesson:
    """Записать проход человека.

    По умолчанию бот НИЧЕГО не трогает: ни мыши, ни клавиатуры, ни камеры. Только
    смотрит и пишет. Управление всё время у человека — это и есть смысл урока.

    setup=True попросит бота сначала выставить старт (респавн и камера). Так тайминги
    получаются воспроизводимее, но управление на несколько секунд уходит к боту —
    поэтому по умолчанию выключено.
    """
    from .recorder import InputLog, Recorder
    goal = goal or infer_goal(name)
    if setup:
        log.info("готовлю старт: респавн и камера")
        if not farmer.to_reference(aim=False):
            raise RuntimeError("опорное состояние не взято — запись отменена")

    print()
    print("=" * 60)
    print(f"  ТВОЙ ХОД. Пишу {seconds:.0f} секунд: {name}")
    print("  Иди как надо — я записываю клавиши и смотрю в кадр.")
    print("  Управление полностью твоё — я только смотрю и записываю клавиши.")
    print("=" * 60)
    for i in range(countdown, 0, -1):
        print(f"  начинаю через {i}...", flush=True)
        time.sleep(1)
    rec = Recorder(farmer.window, farmer.screens_dir / f"lesson_{name}_{int(time.time())}.mp4",
                   fps=8) if farmer.screens_dir else None
    box = farmer.window.client_box()
    context = {"size": [box.width, box.height],
               "shift_lock": bool(farmer.hand.shift_lock),
               "start": "reference" if setup else "manual"}
    check = GoalCheck(farmer, goal)
    baseline = check()
    inp = InputLog(hwnd=farmer.window.hwnd, shift_lock=farmer.hand.shift_lock).start()
    check.interacted = lambda: any(k == "e" and kind == "вниз" for _, k, kind in inp.events)

    # Вехи урока. Лок отмечаем отдельно: он должен быть ПЕРВЫМ действием цикла,
    # потому что окно лока — 60 секунд на нуле ребёрнов, и весь поход обязан в него
    # уложиться. По временам вех сразу видно, укладывается или нет.
    marks: dict[str, float] = {}
    t0 = inp.started
    end = t0 + seconds
    armed = not baseline
    try:
        if rec:
            rec.note(f"урок: {name}")
            rec.start()
        print("  ПОШЁЛ", flush=True)
        while time.monotonic() < end:
            reached = check()
            if not reached:
                armed = True
            if armed and reached and goal not in marks:
                marks[goal] = round(time.monotonic() - t0, 1)
                if rec:
                    rec.note(f"урок: {name} — {goal}")
            time.sleep(0.5)
    finally:
        try:
            inp.stop()
        finally:
            if rec:
                rec.stop()
    holds = inp.holds()
    turns = list(inp.turns)
    steps = merge_turns(holds_to_steps(holds), turns, holds)
    mouse = [(k, dur) for k, _, dur in holds if k in MOUSE_KEYS]
    note = ", ".join(f"{k} на {v} с" for k, v in marks.items()) or "вех не было"
    if mouse:
        held = sum(d for _, d in mouse)
        note += f"; ПКМ/ЛКМ {len(mouse)} раз, суммарно {held:.1f} с"
    unsupported = {k for _, k, kind in inp.events
                   if kind == "вниз" and k not in REPLAY_KEYS and k != "ПКМ"}
    if unsupported:
        inp.capture_ok = False
        inp.error = "есть действия интерфейса без координат: " + ", ".join(sorted(unsupported))
    events = [(t, "key", k, kind) for t, k, kind in inp.events
              if k in REPLAY_KEYS or k == "ПКМ"]
    events += [(t, "turn", dx, dy) for t, dx, dy in turns]
    if not inp.capture_ok:
        note += f"; запись неполная: {inp.error}"
    lesson = Lesson(name=name, steps=steps, ok=(goal in marks) if goal else None,
                    note=note, goal=goal, capture_ok=inp.capture_ok,
                    events=sorted(events, key=lambda e: e[0]), context=context)
    print(f"  записано {len(steps)} шагов, {lesson.duration():.1f} с — {note}")
    return lesson


def replay(farmer, lesson: Lesson, check=None, tolerance: float | None = None) -> dict:
    """Повторить урок. check — признак цели; проверяется по ходу и в конце."""
    report = {"урок": lesson.name, "шагов": len(lesson.steps), "цель": False}
    if check and check():
        report["ошибка"] = "цель уже выполнена до повтора; результат нельзя приписать уроку"
        return report
    # Опорное состояние БЕЗ наведения на якорь.
    #
    # Повороты в уроке относительные: они записаны от того положения, в котором
    # человек оказался после СВОЕГО респавна. Если перед повтором развернуть
    # камеру на якорь, весь путь окажется смещён ровно на этот доворот — а он
    # бывает огромным, в логе видели 1395 единиц мыши, почти четверть оборота.
    box = farmer.window.client_box()
    if lesson.context and (lesson.context.get("size") != [box.width, box.height]
                           or lesson.context.get("shift_lock") != bool(farmer.hand.shift_lock)):
        report["ошибка"] = "размер окна или режим камеры отличается от урока"
        return report
    if lesson.context.get("start") != "manual" and not farmer.to_reference(aim=False):
        report["ошибка"] = "опорное состояние не взято"
        return report

    log.info("повторяю урок %r: %s шагов, %.1f с", lesson.name, len(lesson.steps),
             lesson.duration())
    if lesson.events:
        farmer.hand.replay_timeline(lesson.events)
        report["цель"] = bool(check and check())
        lesson.replay_ok = report["цель"] if check else None
        return report
    for i, step in enumerate(lesson.steps, 1):
        if step.pause:
            time.sleep(step.pause)
        if isinstance(step, Turn):
            farmer.hand.look(step.dx, step.dy)
            continue
        if step.keys == ["e"]:
            # E у промптов: человек держал около полусекунды, и этого хватало.
            # Держим не меньше — но и не втрое дольше, чем нужно.
            farmer.hand.interact(max(step.hold, 0.6))
        elif len(step.keys) == 1:
            farmer.hand.hold(step.keys[0], step.hold)
        else:
            farmer.hand.hold_keys(step.keys, step.hold)
        if check and check():
            report["цель"] = True
            report["на шаге"] = i
            log.info("цель достигнута на шаге %s из %s", i, len(lesson.steps))
            break
    if check and not report["цель"]:
        report["цель"] = bool(check())
    log.info("повтор: %s", report)
    lesson.replay_ok = report["цель"] if check else None
    return report
