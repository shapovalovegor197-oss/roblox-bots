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
                     job_id: str | None = None) -> str:
    """URI запуска клиента. job_id — заход в КОНКРЕТНЫЙ экземпляр сервера.

    Это штатный механизм Roblox, а не эксплойт: PlaceLauncher умеет не только
    `RequestGame` (любой сервер), но и `RequestGameJob` с идентификатором экземпляра.
    Нужно, чтобы посадить несколько своих аккаунтов в ОДИН сервер — без этого
    передача кражей (scenarios/steal.py) невозможна. Чужие автоджойнеры делают то же
    самое изнутри игры через TeleportToPlaceInstance и потому требуют экзекьютора.
    """
    if job_id:
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
           target_fps: int | None = None, job_id: str | None = None) -> int:
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
    uri = build_launch_uri(auth_ticket(account), place_id, job_id=job_id)
    proc = subprocess.Popen(
        [str(exe), uri],
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    log.info("[%s] клиент запущен, pid=%s, place=%s%s", account.name, proc.pid, place_id,
             f", сервер {job_id}" if job_id else "")
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


def newest_job_id(place_id: int | None = None, newer_than: float = 0.0) -> str | None:
    """jobId самого свежего захода. place_id — фильтр по нужной игре."""
    for mtime, job, place, _ in recent_joins():
        if mtime < newer_than:
            continue
        if place_id and place != place_id:
            continue
        return job
    return None
