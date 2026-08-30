"""Логирование: консоль + файл с ротацией. Куки в логи не попадают."""
from __future__ import annotations

import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_COOKIE_RE = re.compile(r"_\|WARNING:[^\"'\s]{20,}")


class _Redact(logging.Filter):
    """Вырезает .ROBLOSECURITY и auth-тикеты из любых сообщений."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _COOKIE_RE.sub("<COOKIE>", record.msg)
        def clean(v):
            return _COOKIE_RE.sub("<COOKIE>", v) if isinstance(v, str) else v

        # Словарь надо сохранить словарём. Если единственный аргумент — Mapping,
        # logging кладёт его в args как есть, и `tuple(record.args)` перебирает
        # КЛЮЧИ: запись превращается в кортеж имён полей, форматирование падает
        # с «not all arguments converted», и строка пропадает из лога целиком.
        # Так был потерян отчёт `errand` — единственное, ради чего гоняли заход.
        if isinstance(record.args, dict):
            record.args = {k: clean(v) for k, v in record.args.items()}
        elif record.args:
            record.args = tuple(clean(a) for a in record.args)
        return True


_configured = False


def setup(logs_dir: Path, level: int = logging.INFO) -> None:
    global _configured
    if _configured:
        return

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)-14s %(message)s", "%H:%M:%S"
    )

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)

    logs_dir.mkdir(parents=True, exist_ok=True)
    fileh = RotatingFileHandler(
        logs_dir / "brainbot.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    fileh.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(level)
    for h in (console, fileh):
        h.addFilter(_Redact())
        root.addHandler(h)

    _configured = True


def get(name: str) -> logging.Logger:
    return logging.getLogger(name)
