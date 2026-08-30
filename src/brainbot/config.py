"""Загрузка конфигов и путей проекта."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"


@dataclass
class Account:
    name: str
    cookie: str
    role: str = "storage"
    enabled: bool = True

    def __repr__(self) -> str:  # чтобы кука не утекла в лог
        return f"Account({self.name!r}, role={self.role!r}, enabled={self.enabled})"


@dataclass
class Settings:
    raw: dict
    place_id: int
    game_name: str
    window: dict
    vision: dict
    input: dict
    antiafk: dict
    supervisor: dict
    paths: dict
    capture: dict = field(default_factory=dict)
    optimize: dict = field(default_factory=dict)

    accounts: list[Account] = field(default_factory=list)

    # --- пути, посчитанные от корня проекта ---
    @property
    def screenshots_dir(self) -> Path:
        return self._dir(self.paths["screenshots"])

    @property
    def logs_dir(self) -> Path:
        return self._dir(self.paths["logs"])

    @property
    def template_dir(self) -> Path:
        return self._dir(self.vision["template_dir"])

    @property
    def state_file(self) -> Path:
        p = ROOT / self.paths["state"]
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def _dir(self, rel: str) -> Path:
        p = ROOT / rel
        p.mkdir(parents=True, exist_ok=True)
        return p

    def account(self, name: str) -> Account:
        for a in self.accounts:
            if a.name == name:
                return a
        known = ", ".join(a.name for a in self.accounts) or "(пусто)"
        raise KeyError(f"Аккаунт {name!r} не найден. Есть: {known}")

    @property
    def enabled_accounts(self) -> list[Account]:
        return [a for a in self.accounts if a.enabled and a.cookie]


def load(settings_path: Path | None = None, accounts_path: Path | None = None) -> Settings:
    settings_path = settings_path or CONFIG_DIR / "settings.json"
    accounts_path = accounts_path or CONFIG_DIR / "accounts.json"

    raw = json.loads(settings_path.read_text(encoding="utf-8"))
    s = Settings(
        raw=raw,
        place_id=raw["place_id"],
        game_name=raw["game_name"],
        window=raw["window"],
        vision=raw["vision"],
        input=raw["input"],
        antiafk=raw["antiafk"],
        supervisor=raw["supervisor"],
        paths=raw["paths"],
        capture=raw.get("capture", {}),
        optimize=raw.get("optimize", {}),
    )

    if accounts_path.exists():
        data = json.loads(accounts_path.read_text(encoding="utf-8"))
        s.accounts = [
            Account(
                name=a["name"],
                cookie=a.get("cookie", ""),
                role=a.get("role", "storage"),
                enabled=a.get("enabled", True),
            )
            for a in data.get("accounts", [])
        ]
    return s
