#!/usr/bin/env python
"""Точка входа без установки пакета: python run.py <команда>"""
import sys
from pathlib import Path

# Консоль Windows бывает в cp1251, и тогда вывод с тире и «ё» падает на кодировке.
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent / "src"))

from brainbot.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
