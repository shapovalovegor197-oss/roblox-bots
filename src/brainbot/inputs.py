"""Ввод: клики и печать в координатах клиентской области окна.

Модуль называется inputs, а не input — чтобы не затенять встроенный input().

Работаем через pydirectinput-rgx (форк, он и стоит в requirements) — у него есть
две вещи, которых нет в обычном pydirectinput и без которых игра ввод теряет:

  * `duration=` у press/keyDown — клавиша ДЕРЖИТСЯ заданное время. Нажатие
    нулевой длины игра имеет полное право не заметить: она опрашивает ввод раз
    в кадр, а мы сами срезали клиенту частоту до 20 FPS (`optimize`), то есть
    кадр = 50 мс. Отсюда `key_hold` — минимальная длина любого «тычка».
  * `disable_mouse_acceleration=` у moveRel — на время движения гасит «Enhance
    pointer precision». Без этого Windows масштабирует сдвиг мыши нелинейно, и
    поворот камеры на одно и то же dx каждый раз даёт разный угол.
"""
from __future__ import annotations

import random
import time

import pydirectinput

from .log import get
from .window import RobloxWindow

log = get("inputs")

pydirectinput.PAUSE = 0.0          # паузы считаем сами, они должны плавать
pydirectinput.FAILSAFE = True      # мышь в угол экрана = аварийный стоп


class Hand:
    """Мышь и клавиатура, привязанные к одному окну."""

    def __init__(self, window: RobloxWindow, cfg: dict,
                 backend: str | None = None) -> None:
        self.win = window
        # Канал доставки ввода:
        #   focus — SendInput в общую очередь, достаётся окну в фокусе. Работает
        #           наверняка, но действовать может только один клиент за раз.
        #   post  — PostMessage прямо в окно по hwnd, фокус не нужен: несколько
        #           клиентов действуют одновременно. Дойдёт ли — вопрос замера,
        #           см. `run.py input-test --compare`.
        self.backend = backend or cfg.get("backend", "focus")
        # Шифт-лок включён в самой игре: прицел закреплён по центру, персонаж
        # всегда развёрнут по камере, и держать ПКМ на ходу больше НЕ НУЖНО.
        # Это не мелочь: с зажатой ПКМ Roblox забирает мышь под вращение
        # камеры, и удержание игровых промптов (покупка, сбор, лок) не
        # засчитывается — из-за этого покупка когда-то не работала вовсе.
        self.shift_lock = bool(cfg.get("shift_lock", False))
        self._poster = None
        self.move_duration = cfg.get("move_duration", 0.12)
        self.jitter = cfg.get("click_jitter_px", 2)
        self.post_click = cfg.get("post_click_delay", [0.18, 0.35])
        self.type_delay = cfg.get("type_delay", [0.04, 0.11])
        # Минимальная длина нажатия. Меньше — и клиент на 20 FPS может проспать
        # событие между кадрами. 60 мс с запасом перекрывают кадр в 50 мс.
        self.key_hold = cfg.get("key_hold", 0.06)
        # Сколько держать E у промптов. У ProximityPrompt в Roblox есть
        # индикатор заполнения; типичное время удержания — около секунды.
        self.interact_hold = cfg.get("interact_hold", 1.1)
        # Режим уступки: пока пользователь за компом — бот не трогает ввод.
        #   yield_grace       — насколько свежий живой ввод считаем «занято»;
        #   yield_resume_idle — сколько тишины ждём перед возвратом.
        # Отличать пользователя от бота нельзя по GetLastInputInfo (там и наши
        # инъекции), поэтому монитор ловит ввод низкоуровневым хуком по флагу
        # инъекции — см. useractivity.
        self.yield_to_user = bool(cfg.get("yield_to_user", False))
        self.yield_grace = float(cfg.get("yield_grace", 2.0))
        self.yield_resume_idle = float(cfg.get("yield_resume_idle", 12.0))
        self._activity = None
        if self.yield_to_user:
            from .useractivity import UserActivity
            self._activity = UserActivity().start()

    def _yield_if_user(self) -> None:
        """Пользователь за компом — отпустить ввод и ждать, пока отойдёт.

        Ворота стоят перед КАЖДЫМ действием бота (нажатие, поворот, клик).
        Действия короткие, поэтому пользователь получает управление меньше чем
        за секунду после того, как тронул мышь.
        """
        if self._activity is None:
            return
        if self._activity.seconds_since() >= self.yield_grace:
            return
        # Пользователь активен. Всё отпустить и ждать тишины.
        self.release_all()
        waited = False
        while self._activity.seconds_since() < self.yield_resume_idle:
            if not waited:
                log.info("пользователь за компом — уступаю ввод")
                waited = True
            time.sleep(0.4)
        if waited:
            log.info("тихо %.0f с — продолжаю", self.yield_resume_idle)

    # --- служебное ---

    def _pause(self, span: list[float]) -> None:
        time.sleep(random.uniform(span[0], span[1]))

    @property
    def poster(self):
        """Канал PostMessage для этого окна. Создаётся при первом обращении."""
        if self._poster is None:
            from .postinput import Poster
            self._poster = Poster(self.win.hwnd)
        return self._poster

    @property
    def direct(self) -> bool:
        """Идём ли мимо фокуса."""
        return self.backend == "post"

    def ensure_focus(self) -> None:
        """Довести окно до переднего плана и УБЕДИТЬСЯ, что получилось.

        Раньше здесь был один вызов `focus()` без проверки. Windows его часто
        игнорирует, и тогда весь ввод уходит в чужое окно — мышь ведётся по
        рабочему столу, клавиши сыплются в терминал. Молча: бот считает, что
        отработал, а в игре не шевельнулось ничего. Замерено — поворот на 4000
        единиц дал сдвиг сцены 0.0 px при отклике 0.94, то есть кадр тот же.
        Отсюда же часть сегодняшних «бот пошёл не туда»: он никуда не шёл.

        Здесь же ворота уступки: перед тем как забрать фокус и начать
        действовать, проверяем, не за компом ли пользователь. Это единая точка —
        `ensure_focus` зовёт каждое действие (hold, press, click), а `look`
        через `move`. Значит фокус НЕ отбирается, пока пользователь работает.
        """
        self._yield_if_user()
        if self.direct:
            return
        for attempt in range(3):
            if self.win.is_foreground():
                return
            self.win.focus()
            time.sleep(0.3 + 0.2 * attempt)
        if not self.win.is_foreground():
            log.warning("окно игры не удалось вывести в фокус — ВВОД УЙДЁТ МИМО")

    # --- мышь ---

    def move(self, x: int, y: int) -> None:
        """x, y — относительно клиентской области окна."""
        j = self.jitter
        sx, sy = self.win.client_box().to_screen(
            x + random.randint(-j, j), y + random.randint(-j, j)
        )
        pydirectinput.moveTo(sx, sy, duration=self.move_duration)

    def click(self, x: int, y: int, button: str = "left", hold: float | None = None) -> None:
        """Клик с УДЕРЖАНИЕМ кнопки, а не мгновенным down+up.

        Ровно та же история, что с клавишами: `pydirectinput.click()` шлёт нажатие
        нулевой длины, и элементы интерфейса Roblox его теряют — проверено на диалоге
        «Disconnected», кнопка Reconnect не срабатывала вообще.
        """
        self.ensure_focus()
        if self.direct:
            self.poster.click(x, y, button, self.key_hold if hold is None else hold)
            self._pause(self.post_click)
            log.debug("клик (%s, %s) без фокуса", x, y)
            return
        self.move(x, y)
        time.sleep(random.uniform(0.03, 0.08))
        pydirectinput.mouseDown(button=button)
        time.sleep(self.key_hold if hold is None else hold)
        pydirectinput.mouseUp(button=button)
        self._pause(self.post_click)
        log.debug("клик (%s, %s)", x, y)

    def click_match(self, match) -> None:
        """Кликает в центр найденного шаблона."""
        self.click(*match.center)

    # --- клавиатура ---

    def press(self, key: str, presses: int = 1, hold: float | None = None) -> None:
        """Нажать клавишу. hold — сколько её держать; по умолчанию key_hold.

        Держать ОБЯЗАТЕЛЬНО. `pydirectinput.press(key)` без duration шлёт down и up
        подряд, между ними нули миллисекунд — такое нажатие клиент теряет.
        """
        self.ensure_focus()
        hold = self.key_hold if hold is None else hold
        for _ in range(presses):
            if self.direct:
                self.poster.press(key, hold)
            else:
                pydirectinput.press(key, duration=hold)
            time.sleep(random.uniform(0.05, 0.12))

    def type_text(self, text: str) -> None:
        """Печатает по символу с плавающей задержкой.

        pydirectinput умеет только ASCII-раскладку — для ников Roblox этого хватает,
        они и так латиница с цифрами и подчёркиванием.
        """
        self.ensure_focus()
        for ch in text:
            pydirectinput.press(ch, duration=self.key_hold, auto_shift=True)
            self._pause(self.type_delay)

    def clear_field(self, presses: int = 40) -> None:
        """Чистит поле ввода: в конец, потом backspace."""
        self.ensure_focus()
        pydirectinput.press("end", duration=self.key_hold)
        for _ in range(presses):
            pydirectinput.press("backspace", duration=self.key_hold)
            time.sleep(0.01)

    # --- движение персонажа ---
    #
    # В Roblox ходьба на WASD, камера мышью, взаимодействие на E, прыжок space.
    # Ключевое отличие от кликов: клавишу надо ДЕРЖАТЬ, а не нажимать. Держим
    # ровно столько, сколько задано, и всегда отпускаем в finally — иначе при
    # сбое персонаж уйдёт в стену и будет бесконечно упираться.

    def hold(self, key: str, seconds: float, steady: bool = True) -> None:
        """Держит одну клавишу заданное время.

        `steady` — держать при этом ПРАВУЮ КНОПКУ МЫШИ. Так играет человек, и
        это не украшение: с зажатой ПКМ камера зафиксирована относительно
        персонажа, он развёрнут туда же, куда она смотрит, и `w` идёт ровно
        вперёд по взгляду. Без неё камера и персонаж расходятся на ходу, и
        направление уезжает — отсюда и кривые проходы.

        Отключать имеет смысл только для клавиш интерфейса, где мышь не нужна.
        """
        self.ensure_focus()
        if self.direct:
            self.poster.hold(key, seconds)
            return
        steady = steady and not self.shift_lock
        if steady:
            pydirectinput.mouseDown(button="right")
            time.sleep(0.05)
        pydirectinput.keyDown(key)
        try:
            time.sleep(seconds)
        finally:
            pydirectinput.keyUp(key)
            if steady:
                time.sleep(0.05)
                pydirectinput.mouseUp(button="right")

    def hold_keys(self, keys: list[str], seconds: float, steady: bool = True) -> None:
        """Держит несколько клавиш разом — например W+D по диагонали."""
        self.ensure_focus()
        steady = steady and not self.shift_lock
        if steady and not self.direct:
            pydirectinput.mouseDown(button="right")
            time.sleep(0.05)
        if self.direct:
            for k in keys:
                self.poster.key_down(k)
            try:
                time.sleep(seconds)
            finally:
                for k in reversed(keys):
                    self.poster.key_up(k)
            return
        for k in keys:
            pydirectinput.keyDown(k)
        try:
            time.sleep(seconds)
        finally:
            for k in reversed(keys):
                pydirectinput.keyUp(k)
            if steady:
                time.sleep(0.05)
                pydirectinput.mouseUp(button="right")

    def walk(self, direction: str, seconds: float) -> None:
        """Идти в сторону: forward/back/left/right или их пары, напр. 'forward-left'."""
        keymap = {"forward": "w", "back": "s", "left": "a", "right": "d"}
        keys = [keymap[d] for d in direction.split("-") if d in keymap]
        if not keys:
            raise ValueError(f"непонятное направление: {direction}")
        self.hold_keys(keys, seconds)

    def scroll(self, clicks: int, interval: float = 0.02) -> None:
        """Колесо мыши: >0 к себе (зум камеры внутрь), <0 от себя (наружу)."""
        self.ensure_focus()
        if self.direct:
            self.poster.scroll(clicks)
            return
        pydirectinput.scroll(clicks, interval=interval)

    def jump(self) -> None:
        self.press("space")

    def interact(self, seconds: float | None = None) -> None:
        """E — купить/поднять/подтвердить у объектов мира.

        Клавишу ДЕРЖИМ, а не тычем: промпты Roblox (ProximityPrompt) почти всегда
        требуют удержания — у них круговой индикатор заполнения. Проверено на живой
        игре: бот шесть раз «нажал покупку» подряд, промпт был на экране, а кэш не
        изменился и база осталась пустой. Короткое нажатие такие промпты просто
        не засчитывают.
        """
        # steady=False ОБЯЗАТЕЛЬНО: `hold` по умолчанию зажимает ещё и правую
        # кнопку мыши (это нужно при ходьбе, чтобы камера не расходилась с
        # персонажем). Но с зажатой ПКМ Roblox забирает мышь под вращение
        # камеры, и удержание ProximityPrompt не засчитывается. Замерено: промпт
        # «E — Pipi Kiwi $1.5K — Purchase» висел на экране, цена $1.5K при
        # наличных $110K, удержание 1.2 с — и кэш не менялся ни разу.
        self.hold("e", self.interact_hold if seconds is None else seconds,
                  steady=False)

    def release_all(self) -> None:
        """Аварийно отпустить все клавиши движения. Дёргать при любом сбое цикла."""
        for k in ("w", "a", "s", "d", "space", "shift"):
            try:
                pydirectinput.keyUp(k)
            except Exception:  # noqa: BLE001
                pass

    # Сколько единиц отдавать за один сдвиг мыши. Чем меньше, тем плавнее идёт
    # камера. Было `steps=12` на любой поворот: разворот на 180 градусов летел
    # двенадцатью рывками по 150 единиц — резко и на глаз, и для игры. Теперь
    # шаг постоянный по ВЕЛИЧИНЕ, а не по количеству: длинный поворот просто
    # занимает больше времени.
    UNITS_PER_MOVE = 12
    MOVE_PAUSE = 0.012
    MAX_MOVES = 240          # потолок, чтобы совсем длинный поворот не завис

    def look(self, dx: int, dy: int = 0, steps: int | None = None) -> None:
        """Поворот камеры ПЛАВНО: зажать ПКМ и вести мышь. dx>0 вправо, dy>0 вниз.

        `steps` можно задать явно, но по умолчанию число сдвигов считается от
        величины поворота, чтобы каждый сдвиг был мелким.

        Сдвиг ОТНОСИТЕЛЬНЫЙ и с погашенной акселерацией: без этого Windows
        масштабирует движение по своей кривой («Enhance pointer precision»),
        и одно и то же dx даёт разный угол — камера проворачивается через раз.
        rgx на время каждого сдвига сам выключает акселерацию и ставит нейтральную
        скорость 10, потом возвращает как было.
        """
        self.ensure_focus()
        # ШИФТ-ЛОК: камера крутится ПРЯМОЙ мышью, без правой кнопки. В этом и
        # смысл шифт-лока — мышь захвачена под mouselook. Протяжка с зажатой ПКМ
        # тут дерётся с самим шифт-локом: большие повороты уводили камеру в небо
        # (замерено 30.08 на развороте к ленте). Двигаем относительно, курсор в
        # центр не тащим — игра сама держит его по центру.
        if steps is None:
            big = max(abs(int(dx)), abs(int(dy)))
            steps = max(12, min(self.MAX_MOVES, -(-big // self.UNITS_PER_MOVE)))
        if self.shift_lock and not self.direct:
            sx = int(dx / steps) if steps else int(dx)
            sy = int(dy / steps) if steps else int(dy)
            for _ in range(max(1, steps)):
                pydirectinput.moveRel(sx, sy, relative=True,
                                      disable_mouse_acceleration=True)
                time.sleep(self.MOVE_PAUSE)
            return
        # Курсор — В ОКНО, прежде чем жать правую кнопку.
        #
        # Раньше поворот начинался там, где курсор случайно оказался: над
        # терминалом, на другом мониторе, за краем окна. ПКМ зажималась мимо
        # игры, и протяжка шла по рабочему столу. В замере это выглядело так:
        # окно в фокусе, поворот на 2000 единиц, сдвиг сцены 0.0 px — камера не
        # шевельнулась вообще.
        if not self.direct:
            b = self.win.client_box()
            self.move(b.width // 2, b.height // 2)
            time.sleep(0.05)
        if self.direct:
            # Без фокуса поворот делается протяжкой от центра клиентской области:
            # абсолютных координат курсора у нас тут нет, есть только окно.
            b = self.win.client_box()
            cx, cy = b.width // 2, b.height // 2
            self.poster.drag(cx, cy, cx + int(dx), cy + int(dy),
                             button="right", steps=steps)
            return
        pydirectinput.mouseDown(button="right")
        time.sleep(0.06)
        try:
            sx, sy = int(dx / steps), int(dy / steps)
            for _ in range(steps):
                pydirectinput.moveRel(sx, sy, relative=True,
                                      disable_mouse_acceleration=True)
                time.sleep(self.MOVE_PAUSE)
        finally:
            time.sleep(0.06)
            pydirectinput.mouseUp(button="right")

    # Поворот на УГОЛ. Одной протяжкой большой угол не берётся: замер 31.08 —
    # 1748 единиц (по дневной формуле это 174 градуса) дают на деле 104, а те
    # же 1748 четырьмя порциями — 115. Зато шагами по 437 единиц поворот идёт
    # ровно: шесть шагов разворачивают спиной к базе, лицом на площадь
    # (кадр turn6_check_20260831-001410), то есть примерно 29 градусов на шаг.
    #
    # Мелкие углы шагами не наберёшь, поэтому остаток докручивается короткой
    # протяжкой по своей мере: 0.102 градуса на единицу (замер по сдвигу сцены
    # при чувствительности клиента 1.0).
    TURN_STEP_UNITS = 437
    TURN_STEP_DEG = 29.0
    SMALL_DEG_PER_UNIT = 0.102

    def turn_degrees(self, deg: float) -> None:
        """Повернуть камеру на угол в градусах. Знак: плюс — вправо."""
        sign = 1 if deg >= 0 else -1
        left = abs(float(deg))
        while left >= self.TURN_STEP_DEG:
            self.look(sign * self.TURN_STEP_UNITS, 0)
            time.sleep(0.25)
            left -= self.TURN_STEP_DEG
        if left > 1.0:
            self.look(int(sign * left / self.SMALL_DEG_PER_UNIT), 0)
            time.sleep(0.15)

    def drag_look(self, x0: int, y0: int, x1: int, y1: int) -> None:
        """Тот же поворот, но АБСОЛЮТНЫМ протаскиванием курсора из точки в точку.

        Координаты — в клиентской области окна. Абсолютное движение вообще не
        зависит от настроек мыши, поэтому запасной вариант, если относительный
        сдвиг где-то поведёт себя странно. Так делает референсный макрос.
        """
        self.ensure_focus()
        b = self.win.client_box()
        sx0, sy0 = b.to_screen(x0, y0)
        sx1, sy1 = b.to_screen(x1, y1)
        pydirectinput.moveTo(sx0, sy0)
        time.sleep(0.1)
        pydirectinput.dragTo(sx1, sy1, duration=0.5, button="right")

    # Наклон камеры делается ТЕМ ЖЕ относительным сдвигом, что и поворот.
    #
    # Прежняя версия тянула курсор в абсолютную точку `0.20h + strength*0.5h`, и
    # при strength >= 1.6 эта точка оказывалась ЗА нижним краем окна: протяжка
    # уходила мимо, камера вместо вида сверху заваливалась под персонажа и
    # смотрела в небо. Все замеры «сильного наклона» из-за этого были мусором.
    # Относительный сдвиг такой границы не имеет.
    #
    # PITCH_RANGE — сколько единиц мыши укладывается от упора вниз до упора
    # вверх. Меряется `scripts/pitch_calib.py`, держится с запасом: упор всё
    # равно обрежет лишнее, а недобор оставит камеру в промежуточном положении.
    PITCH_RANGE = 1800
    PITCH_CHUNK = 700

    def pitch_top(self) -> None:
        """Вид сверху: упереть камеру в верхний предел наклона.

        Знак проверен замером, а не выведен из здравого смысла: вид сверху даёт
        мышь ВНИЗ (dy > 0). Обратное направление уводит камеру под персонажа, в
        небо, — и именно так выглядели все «сильные наклоны» до починки.

        Тянем кусками: длинную протяжку игра теряет целиком (замерено на
        поворотах — 2000 единиц дали 265 вместо 710). До упора важно именно
        дойти, поэтому суммарно берём с запасом.

        Упор — единственное повторяемое положение камеры по вертикали. Отсчёт
        любого другого наклона ведём отсюда, иначе состояние камеры зависит от
        того, что делали перед этим (на этом сгорел вечер замеров).
        """
        for _ in range(3):
            self.look(0, self.PITCH_CHUNK)
            time.sleep(0.15)

    def pitch_normal(self, back: int = 700, already_top: bool = False) -> None:
        """Обычный вид из-за плеча: от упора сверху поднять камеру на `back`.

        Рысканье при этом не меняется — сдвиг строго вертикальный. Поэтому
        пеленг, снятый в виде сверху, остаётся верным и здесь.

        `already_top` — камера УЖЕ у верхнего упора, повторно упирать не надо.
        Без этого флага каждый разворот делал упор дважды (три протяжки на
        пеленг и ещё три на возврат), и на круг набегало несколько секунд впустую
        при бюджете лока в 75 секунд.
        """
        if not already_top:
            self.pitch_top()
            time.sleep(0.2)
        self.look(0, -abs(back))

    def pitch_down(self, strength: float = 0.5) -> None:
        """Совместимость со старым кодом: наклон вниз долей от полного хода."""
        self.look(0, int(self.PITCH_RANGE * min(strength, 1.0)))

    def pitch_up(self, strength: float = 0.5) -> None:
        """Совместимость со старым кодом: наклон вверх долей от полного хода."""
        self.look(0, -int(self.PITCH_RANGE * min(strength, 1.0)))
