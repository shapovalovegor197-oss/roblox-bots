"""Обучение показом: записать проход человека и повторить его.

Самый дешёвый способ научить бота дороге. Точка спавна одна и та же, камера
приводится к одному и тому же положению — значит достаточно один раз пройти
маршрут руками, записать НАЖАТИЯ с длительностями и потом их повторять.

Зрение при этом не выключается: повтор проверяется по контрольным признакам
(появился промпт покупки, изменились деньги). Слепой макрос ломается от любого
сдвига, а макрос с проверками — говорит, где именно он сломался.

Порядок работы:

    python run.py teach --seconds 60      бот ставит старт, дальше идёшь ты
    python run.py lessons                 что записано
    python run.py replay                  бот повторяет выученное

Несколько уроков одного маршрута усредняются: берётся медиана длительностей по
совпадающим шагам. Человек каждый раз жмёт чуть по-разному, и медиана убирает
случайные отклонения, оставляя суть.
"""
from __future__ import annotations

import json
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path

from .log import get
from .recorder import InputLog, Recorder

log = get("lessons")

# Клавиши, которые имеет смысл повторять. Мышь не пишем: камеру бот ставит сам,
# в известное положение, и повторять её движения не нужно и вредно.
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
    steps: list[Step] = field(default_factory=list)
    note: str = ""
    ok: bool | None = None        # достиг ли урок цели по признакам

    def as_dict(self) -> dict:
        return {"название": self.name, "заметка": self.note, "получилось": self.ok,
                "шаги": [s.as_dict() for s in self.steps]}

    @staticmethod
    def from_dict(d: dict) -> "Lesson":
        steps = [Turn.from_dict(x) if "поворот" in x else Step.from_dict(x)
                 for x in d.get("шаги", [])]
        return Lesson(name=d.get("название", "?"), note=d.get("заметка", ""),
                      ok=d.get("получилось"), steps=steps)

    def duration(self) -> float:
        return sum(getattr(s, "hold", 0.0) + s.pause for s in self.steps)


def holds_to_steps(holds: list[tuple[str, float, float]],
                   glue: float = 0.05) -> list[Step]:
    """Поток удержаний -> шаги. Пересекающиеся во времени склеиваются в один шаг.

    `glue` — насколько близкие по времени удержания считать одновременными:
    человек не нажимает две клавиши в одну и ту же миллисекунду.
    """
    items = [(start, start + dur, key) for key, start, dur in holds
             if key in REPLAY_KEYS and dur >= MIN_HOLD]
    items.sort()

    groups: list[list] = []
    for start, stop, key in items:
        if groups and start <= groups[-1][1] + glue:
            groups[-1][1] = max(groups[-1][1], stop)
            if key not in groups[-1][2]:
                groups[-1][2].append(key)
        else:
            groups.append([start, stop, [key]])

    steps: list[Step] = []
    prev_end = 0.0
    for start, stop, keys in groups:
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
    """Вставить повороты камеры между удержаниями, по времени.

    Урок должен читаться как путь: повернулся -> прошёл -> повернулся -> прошёл.
    Поэтому берём начала удержаний из исходного потока и раскладываем повороты
    по промежуткам между ними.
    """
    if not turns:
        return steps
    # Начала шагов восстанавливаем по паузам: они и так лежат в шагах.
    out, clock, ti = [], 0.0, 0
    for step in steps:
        step_start = clock + step.pause
        while ti < len(turns) and turns[ti][0] <= step_start:
            t, dx, dy = turns[ti]
            out.append(Turn(dx, dy, max(0.0, t - clock)))
            clock = t
            ti += 1
        out.append(Step(step.keys, step.hold, max(0.0, step_start - clock)))
        clock = step_start + step.hold
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
        out.append(lesson)
    return out


def merge(lessons: list[Lesson]) -> Lesson | None:
    """Свести несколько проходов одного маршрута в один — по медиане.

    Берём только удачные уроки и только те, где последовательность клавиш совпала:
    если человек в одном проходе пошёл иначе, усреднять с ним нечего. Длительности
    и паузы усредняются медианой — она устойчива к одному смазанному проходу.
    """
    good = [l for l in lessons if l.ok is not False and l.steps]
    if not good:
        return None
    if len(good) == 1:
        return good[0]

    def shape(lesson: Lesson) -> list:
        """Форма прохода: чем был каждый шаг. Повороты и удержания — разные вещи."""
        out = []
        for st in lesson.steps:
            out.append(("поворот",) if isinstance(st, Turn) else tuple(st.keys))
        return out

    ref = good[0]
    same = [l for l in good if shape(l) == shape(ref)]
    if len(same) < 2:
        log.info("проходы разной формы — беру самый короткий удачный")
        return min(good, key=lambda l: l.duration())

    steps = []
    for i, s in enumerate(ref.steps):
        pauses = [l.steps[i].pause for l in same]
        if isinstance(s, Turn):
            # Углы тоже усредняем медианой: случайные дёргания мышью в одном
            # проходе так не портят общий поворот.
            dxs = [l.steps[i].dx for l in same]
            dys = [l.steps[i].dy for l in same]
            steps.append(Turn(int(statistics.median(dxs)), int(statistics.median(dys)),
                              statistics.median(pauses)))
            continue
        holds = [l.steps[i].hold for l in same]
        steps.append(Step(list(s.keys), statistics.median(holds),
                          statistics.median(pauses)))
    log.info("свёл %s одинаковых проходов в один", len(same))
    return Lesson(name=ref.name, steps=steps, note=f"медиана по {len(same)} проходам",
                  ok=True)


def teach(farmer, name: str, seconds: float = 60.0, countdown: int = 5,
          setup: bool = False) -> Lesson:
    """Записать проход человека.

    По умолчанию бот НИЧЕГО не трогает: ни мыши, ни клавиатуры, ни камеры. Только
    смотрит и пишет. Управление всё время у человека — это и есть смысл урока.

    setup=True попросит бота сначала выставить старт (респавн и камера). Так тайминги
    получаются воспроизводимее, но управление на несколько секунд уходит к боту —
    поэтому по умолчанию выключено.
    """
    if setup:
        log.info("готовлю старт: респавн и камера")
        if not farmer.to_reference():
            log.warning("опорное состояние не взято — урок будет от неизвестной точки")

    print()
    print("=" * 60)
    print(f"  ТВОЙ ХОД. Пишу {seconds:.0f} секунд: {name}")
    print("  Иди как надо — я записываю клавиши и смотрю в кадр.")
    print("  Управление полностью твоё — я только смотрю и записываю клавиши.")
    print("=" * 60)
    for i in range(countdown, 0, -1):
        print(f"  начинаю через {i}...", flush=True)
        time.sleep(1)
    print("  ПОШЁЛ")

    rec = Recorder(farmer.window, farmer.screens_dir / f"lesson_{name}_{int(time.time())}.mp4",
                   fps=8) if farmer.screens_dir else None
    if rec:
        rec.note(f"урок: {name}")
        rec.start()
    inp = InputLog().start()

    # Вехи урока. Лок отмечаем отдельно: он должен быть ПЕРВЫМ действием цикла,
    # потому что окно лока — 60 секунд на нуле ребёрнов, и весь поход обязан в него
    # уложиться. По временам вех сразу видно, укладывается или нет.
    marks: dict[str, float] = {}
    t0 = time.time()
    end = t0 + seconds
    while time.time() < end:
        text = None
        for key, needles in (("лок", ("locked your base",)),
                             ("покупка", ("purchase",))):
            if key in marks:
                continue
            if text is None:
                text = ""
            if farmer.sees(*needles):
                marks[key] = round(time.time() - t0, 1)
                log.info("веха %r на %.1f с", key, marks[key])
                if rec:
                    rec.note(f"урок: {name} — {key}")
        time.sleep(0.5)
    saw_prompt = "покупка" in marks

    holds = inp.stop() and inp.holds()
    turns = list(inp.turns)
    if rec:
        rec.stop()
    steps = merge_turns(holds_to_steps(holds), turns, holds)
    mouse = [(k, dur) for k, _, dur in holds if k in MOUSE_KEYS]
    note = ", ".join(f"{k} на {v} с" for k, v in marks.items()) or "вех не было"
    if mouse:
        held = sum(d for _, d in mouse)
        note += f"; ПКМ/ЛКМ {len(mouse)} раз, суммарно {held:.1f} с"
    lesson = Lesson(name=name, steps=steps, ok=saw_prompt or None, note=note)
    print(f"  записано {len(steps)} шагов, {lesson.duration():.1f} с — {note}")
    if "лок" in marks and "покупка" in marks:
        spent = marks["покупка"] - marks["лок"]
        print(f"  от лока до покупки {spent:.1f} с из 60 доступных"
              f"{' — НЕ УКЛАДЫВАЕМСЯ' if spent > 55 else ''}")
    return lesson


def replay(farmer, lesson: Lesson, check=None, tolerance: float = 1.0) -> dict:
    """Повторить урок. check — признак цели; проверяется по ходу и в конце."""
    report = {"урок": lesson.name, "шагов": len(lesson.steps), "цель": False}
    # Опорное состояние БЕЗ наведения на якорь.
    #
    # Повороты в уроке относительные: они записаны от того положения, в котором
    # человек оказался после СВОЕГО респавна. Если перед повтором развернуть
    # камеру на якорь, весь путь окажется смещён ровно на этот доворот — а он
    # бывает огромным, в логе видели 1395 единиц мыши, почти четверть оборота.
    if not farmer.to_reference(aim=False):
        report["ошибка"] = "опорное состояние не взято"
        return report

    log.info("повторяю урок %r: %s шагов, %.1f с", lesson.name, len(lesson.steps),
             lesson.duration())
    for i, step in enumerate(lesson.steps, 1):
        if step.pause:
            time.sleep(min(step.pause, tolerance))
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
    return report
