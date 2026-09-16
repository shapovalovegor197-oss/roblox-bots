"""Запуск клиента под конкретным аккаунтом, без переключения аккаунтов в лаунчере.

Схема та же, что у Roblox Account Manager:
  кука .ROBLOSECURITY → одноразовый authentication ticket → аргументы RobloxPlayerBeta.

Штатный account switcher мы намеренно не трогаем: добавление альтов туда — это
добровольная связка аккаунтов внутри системы Roblox.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
import urllib.parse
from pathlib import Path

import requests

from .config import Account
from .log import get

log = get("launcher")

AUTH_TICKET_URL = "https://auth.roblox.com/v1/authentication-ticket"
PLACE_LAUNCHER = "https://assetgame.roblox.com/game/PlaceLauncher.ashx"
UA = "Roblox/WinInet"


class LaunchError(RuntimeError):
    pass


def find_player_exe() -> Path:
    """Самый свежий RobloxPlayerBeta.exe из установленных версий."""
    roots = [
        Path(os.environ["LOCALAPPDATA"]) / "Roblox" / "Versions",
        Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Roblox" / "Versions",
    ]
    candidates: list[Path] = []
    for root in roots:
        if root.exists():
            candidates.extend(root.glob("*/RobloxPlayerBeta.exe"))
    if not candidates:
        raise LaunchError(
            "RobloxPlayerBeta.exe не найден. Roblox установлен? Искали в "
            + ", ".join(str(r) for r in roots)
        )
    newest = max(candidates, key=lambda p: p.stat().st_mtime)
    log.debug("клиент: %s", newest)
    return newest


def _session(cookie: str) -> requests.Session:
    s = requests.Session()
    s.cookies.set(".ROBLOSECURITY", cookie, domain=".roblox.com")
    # Content-Type и Origin ОБЯЗАТЕЛЬНЫ. Без Content-Type: application/json
    # эндпоинт authentication-ticket отвечает 415 (Unsupported Media Type) даже
    # при живой куке — проверено 30.08.2026, whoami при этом проходил. Origin
    # Roblox сверяет как защиту от CSRF.
    s.headers.update({
        "User-Agent": UA,
        "Referer": "https://www.roblox.com/",
        "Origin": "https://www.roblox.com",
        "Content-Type": "application/json",
    })
    return s


def csrf_token(session: requests.Session) -> str:
    """Roblox отдаёт токен в заголовке ответа 403 на запрос без токена."""
    r = session.post(AUTH_TICKET_URL, timeout=15)
    token = r.headers.get("x-csrf-token")
    if not token:
        raise LaunchError(
            f"не удалось получить x-csrf-token (HTTP {r.status_code}). "
            "Скорее всего кука протухла — перелогинься и обнови accounts.json"
        )
    return token


def auth_ticket(account: Account) -> str:
    """Одноразовый тикет. Живёт секунды — брать прямо перед запуском."""
    s = _session(account.cookie)
    s.headers["x-csrf-token"] = csrf_token(s)
    r = s.post(AUTH_TICKET_URL, timeout=15)
    ticket = r.headers.get("rbx-authentication-ticket")
    if not ticket:
        raise LaunchError(
            f"[{account.name}] Roblox не выдал тикет (HTTP {r.status_code}). "
            "Кука недействительна либо аккаунт под ограничением"
        )
    log.info("[%s] тикет получен", account.name)
    return ticket


def build_launch_uri(ticket: str, place_id: int, browser_tracker_id: str = "0",
                     job_id: str | None = None, link_code: str | None = None,
                     access_code: str | None = None) -> str:
    """URI запуска клиента. job_id — заход в КОНКРЕТНЫЙ экземпляр сервера.

    Это штатный механизм Roblox, а не эксплойт: PlaceLauncher умеет не только
    `RequestGame` (любой сервер), но и `RequestGameJob` с идентификатором экземпляра.
    Нужно, чтобы посадить несколько своих аккаунтов в ОДИН сервер — без этого
    передача кражей (scenarios/steal.py) невозможна. Чужие автоджойнеры делают то же
    самое изнутри игры через TeleportToPlaceInstance и потому требуют экзекьютора.

    `link_code` — заход в ПРИВАТНЫЙ сервер по коду из ссылки-приглашения
    (`...?privateServerLinkCode=XXXX`), третий штатный запрос `RequestPrivateGame`.
    Приватка у Steal a Brainrot доступна: `private-servers/enabled-in-universe`
    отвечает `privateServersEnabled: true` (проверено 11.09.2026), а список
    `servers/VIP` отдаёт живые приватные сервера. Поле `createVipServersAllowed` в
    старом games-API при этом `false` — на него ориентироваться нельзя, оно врёт.

    Зачем это нам. В публичном сервере пустой комнаты не бывает (все сто самых
    тихих отдают playing: 1), а приватка — единственный способ получить комнату,
    где кроме нас и покупателя НИКОГО, то есть выполнить условие вердикта
    «состав сервера = наш бот и контрагент» и не отдать товар постороннему.
    """
    if link_code or access_code:
        parts = [
            f"{PLACE_LAUNCHER}?request=RequestPrivateGame",
            f"browserTrackerId={browser_tracker_id}",
            f"placeId={place_id}",
        ]
        if access_code:
            parts.append(f"accessCode={access_code}")
        if link_code:
            parts.append(f"linkCode={link_code}")
        launcher_url = "&".join(parts)
    elif job_id:
        launcher_url = (
            f"{PLACE_LAUNCHER}?request=RequestGameJob"
            f"&browserTrackerId={browser_tracker_id}"
            f"&placeId={place_id}"
            f"&gameId={job_id}"
            f"&isPlayTogetherGame=false"
        )
    else:
        launcher_url = (
            f"{PLACE_LAUNCHER}?request=RequestGame"
            f"&browserTrackerId={browser_tracker_id}"
            f"&placeId={place_id}"
            f"&isPlayTogetherGame=false"
        )
    return (
        "roblox-player:1"
        "+launchmode:play"
        f"+gameinfo:{ticket}"
        f"+launchtime:{int(time.time() * 1000)}"
        f"+placelauncherurl:{urllib.parse.quote(launcher_url, safe='')}"
        f"+browsertrackerid:{browser_tracker_id}"
        "+robloxLocale:en_us+gameLocale:en_us"
    )


def launch(account: Account, place_id: int, apply_fflags: bool = True,
           target_fps: int | None = None, job_id: str | None = None,
           link_code: str | None = None) -> int:
    """Поднимает клиент и возвращает PID процесса.

    Мьютекс ROBLOX_singletonMutex к этому моменту должен быть уже захвачен,
    иначе Roblox прибьёт всё, кроме первого окна.
    """
    if apply_fflags:
        # Обновление клиента создаёт новую папку версии без наших настроек,
        # поэтому пишем их перед каждым запуском, а не однократно.
        from .optimize import FFLAGS_BOT, write_fflags
        flags = dict(FFLAGS_BOT)
        if target_fps:
            flags["DFIntTaskSchedulerTargetFps"] = target_fps
        write_fflags(flags)

    exe = find_player_exe()
    uri = build_launch_uri(auth_ticket(account), place_id, job_id=job_id,
                           link_code=link_code)
    proc = subprocess.Popen(
        [str(exe), uri],
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    куда = f", сервер {job_id}" if job_id else (", приватка" if link_code else "")
    log.info("[%s] клиент запущен, pid=%s, place=%s%s", account.name, proc.pid, place_id, куда)
    return proc.pid


# --------------------------------------------------------------------------
# Какой сервер занял клиент — нужно, чтобы посадить второй аккаунт туда же
# --------------------------------------------------------------------------

JOINING = re.compile(r"Joining game '([0-9a-f-]{36})' place (\d+)", re.I)


def log_dir() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "Roblox" / "logs"


def recent_joins(limit: int = 6) -> list[tuple[float, str, int, Path]]:
    """Последние заходы из логов клиента: [(время файла, jobId, placeId, файл)].

    Roblox пишет в лог строку `Joining game '<jobId>' place <placeId>` — это и есть
    идентификатор конкретного экземпляра сервера. Имя лога не содержит pid, поэтому
    порядок определяем по времени файла: запускаем донора, читаем свежий лог, сажаем
    приёмник в тот же jobId.
    """
    out: list[tuple[float, str, int, Path]] = []
    d = log_dir()
    if not d.exists():
        return out
    files = sorted(d.glob("*_Player_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files[:limit]:
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        found = JOINING.findall(text)
        if found:
            job, place = found[-1]      # последний заход в этом логе
            out.append((f.stat().st_mtime, job, int(place), f))
    return out


В_ИГРЕ = re.compile(r"setStage:\s*\(stage:UGCGame\)", re.I)


def log_snapshot() -> set[str]:
    """Какие логи клиента лежали ДО запуска. Снимать обязательно до launch().

    Без этого списка ждать «в игре» нельзя: предыдущий клиент только что вышел,
    его лог свежий по времени файла и маркер в нём уже стоит — мы поймали бы
    чужой заход и поехали читать экран, которого ещё нет.
    """
    каталог = log_dir()
    return {ф.name for ф in каталог.glob("*_Player_*.log")} if каталог.exists() else set()


def wait_in_game(было: set[str], timeout: float = 25.0, poll: float = 0.5) -> float | None:
    """Сколько секунд ждали, пока клиент встал на игровую поверхность. None — не дождались.

    Раньше после появления окна просто спали фиксированные 25 секунд, потому что
    клиент доедет «когда-нибудь». Замер 16.09 по логу: `setStage: (stage:UGCGame)`
    приходит на 5.7 секунде, то есть три четверти сна тратились впустую. В приёмке
    это видно снаружи — заказ живёт восемь минут от покупки, и один из них опоздал
    на семь секунд.

    Маркер взят именно этот, а не `Connection accepted` (3.6 с): соединение — ещё
    не картинка, а нам нужна поверхность, с которой можно читать. Смотрим только
    в логи, которых не было в `log_snapshot()` до запуска, — иначе поймаем маркер
    предыдущего клиента. Не нашли за `timeout` — возвращаем None, и зовущий
    досыпает по-старому: формат лога чужой и может смениться без нас.
    """
    крайний = time.time() + timeout
    начало = time.time()
    каталог = log_dir()
    while time.time() < крайний:
        свежие = sorted((ф for ф in каталог.glob("*_Player_*.log") if ф.name not in было),
                        key=lambda ф: ф.stat().st_mtime, reverse=True)
        for файл in свежие[:2]:
            try:
                текст = файл.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if В_ИГРЕ.search(текст):
                return time.time() - начало
        time.sleep(poll)
    return None


def newest_job_id(place_id: int | None = None, newer_than: float = 0.0) -> str | None:
    """jobId самого свежего захода. place_id — фильтр по нужной игре."""
    for mtime, job, place, _ in recent_joins():
        if mtime < newer_than:
            continue
        if place_id and place != place_id:
            continue
        return job
    return None

def quietest_job_id(place_id: int, максимум_игроков: int = 3) -> str | None:
    """jobId самого малолюдного публичного сервера. None — не нашли/сеть молчит.

    Зачем. Всё, что стоит на базе, у нас крадут: за ночь 02-03.09 так потеряны
    два легендарных подряд, а после первого ребёрна — все три купленных доходных
    брейнрота, и база к 10:02 стояла пустая при пороге следующего ребёрна в
    $100M. Красть могут только живые соседи по серверу, значит вопрос решается
    не зрением бота, а выбором сервера: их сотни, и среди них есть с одним
    игроком из восьми (проверено 03.09, HTTP 200, сто серверов в ответе).

    Берём самый пустой, но НЕ пустой совсем: сервер без игроков часто вот-вот
    закроется, и клиент попадёт в другой.
    """
    import requests

    url = ("https://games.roblox.com/v1/games/%d/servers/Public"
           "?sortOrder=Asc&limit=100" % place_id)
    try:
        ответ = requests.get(url, timeout=20)
        ответ.raise_for_status()
        сервера = ответ.json().get("data") or []
    except Exception as exc:                                # noqa: BLE001
        log.warning("список серверов не получен (%s) — иду куда пустят", exc)
        return None
    # Берём не САМЫЙ пустой, а тихий и ЖИВОЙ. Замер 03.09, 10:40-10:47: заход в
    # сервер с одним игроком дважды подвесил клиент на «Joining server» больше
    # чем на две минуты — такой сервер часто уже закрывается. Двое-трое игроков
    # для нас всё равно почти пусто: крали нас на полных восьмёрках.
    годные = [s for s in сервера
              if s.get("id") and 2 <= (s.get("playing") or 0) <= максимум_игроков
              and (s.get("playing") or 0) < (s.get("maxPlayers") or 8)]
    if not годные:
        годные = [s for s in сервера
                  if s.get("id") and 0 < (s.get("playing") or 0) <= максимум_игроков]
    if not годные:
        log.info("серверов тише %d игроков нет — иду куда пустят", максимум_игроков)
        return None
    лучший = min(годные, key=lambda s: s["playing"])
    log.info("выбран сервер: игроков %s из %s", лучший["playing"], лучший.get("maxPlayers"))
    return лучший["id"]
