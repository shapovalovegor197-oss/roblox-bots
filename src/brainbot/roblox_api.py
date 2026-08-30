"""Тонкий клиент веб-API Roblox.

Нужен для трёх вещей и только для них:
  ник друга → userId  (проверить, что ник вообще существует, до трейда)
  presence            (в игре ли он сейчас — иначе трейд уйдёт вхолостую)
  whoami              (проверить, что кука жива, до запуска клиента)

Внутри игры это не умеет ничего. Трейд делается клиентом.
"""
from __future__ import annotations

from dataclasses import dataclass

import requests

from .log import get

log = get("api")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
TIMEOUT = 15


@dataclass
class User:
    id: int
    name: str
    display_name: str


@dataclass
class Presence:
    user_id: int
    type: int              # 0 offline, 1 online (сайт), 2 в игре, 3 в Studio
    place_id: int | None
    last_location: str

    @property
    def in_game(self) -> bool:
        return self.type == 2

    def in_place(self, place_id: int) -> bool:
        return self.type == 2 and self.place_id == place_id


def _session(cookie: str | None = None) -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Referer": "https://www.roblox.com/"})
    if cookie:
        s.cookies.set(".ROBLOSECURITY", cookie, domain=".roblox.com")
    return s


def whoami(cookie: str) -> User | None:
    """Кто мы под этой кукой. None — кука мертва."""
    r = _session(cookie).get(
        "https://users.roblox.com/v1/users/authenticated", timeout=TIMEOUT
    )
    if r.status_code != 200:
        log.warning("кука недействительна (HTTP %s)", r.status_code)
        return None
    d = r.json()
    return User(id=d["id"], name=d["name"], display_name=d.get("displayName", d["name"]))


def users_by_name(names: list[str]) -> dict[str, User]:
    """Ники → пользователи. Ключи в ответе — как их вернул Roblox.

    Несуществующие ники просто отсутствуют в результате: это и есть проверка,
    что друг продиктовал ник правильно.
    """
    if not names:
        return {}
    r = _session().post(
        "https://users.roblox.com/v1/usernames/users",
        json={"usernames": names, "excludeBannedUsers": False},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    out: dict[str, User] = {}
    for d in r.json().get("data", []):
        out[d["name"]] = User(
            id=d["id"], name=d["name"], display_name=d.get("displayName", d["name"])
        )
    missing = [n for n in names if n not in out]
    if missing:
        log.warning("ники не найдены: %s", ", ".join(missing))
    return out


def presence(cookie: str, user_ids: list[int]) -> dict[int, Presence]:
    """Presence требует авторизации — без куки Roblox отдаёт пустышки."""
    if not user_ids:
        return {}
    r = _session(cookie).post(
        "https://presence.roblox.com/v1/presence/users",
        json={"userIds": user_ids},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    out: dict[int, Presence] = {}
    for d in r.json().get("userPresences", []):
        out[d["userId"]] = Presence(
            user_id=d["userId"],
            type=d.get("userPresenceType", 0),
            place_id=d.get("placeId"),
            last_location=d.get("lastLocation", ""),
        )
    return out
