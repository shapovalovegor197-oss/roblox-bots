"""Мост к движку MM2: находим чужой проект и зовём его, ничего не запуская.

Проверять здесь нечего кроме поиска папки и сборки команды — но именно на этом
мост и ломается: путь у движка кириллический, проекты лежат рядом, и «не нашли»
должно звучать понятно, а не ImportError'ом изнутри питона.

    python scripts/test_mm2_bridge.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from brainbot import mm2_bridge                                     # noqa: E402


def движок(корень: Path) -> Path:
    """Подставной roblox-swap: моста касается только наличие папки tools."""
    (корень / "agents" / "mm2" / "tools").mkdir(parents=True)
    return корень


def проверить(условие, что: str) -> None:
    print(("ок   " if условие else "ПЛОХО") + "  " + что)
    if not условие:
        raise SystemExit(1)


def main() -> None:
    with tempfile.TemporaryDirectory() as врем:
        папка = движок(Path(врем) / "старпепс" / "roblox-swap")

        проверить(mm2_bridge.найти_движок(str(папка)) == папка,
                  "указанный путь берётся как есть")

        import os
        os.environ["ROBLOX_SWAP"] = str(папка)
        проверить(mm2_bridge.найти_движок() == папка,
                  "без аргумента путь берётся из ROBLOX_SWAP")
        os.environ.pop("ROBLOX_SWAP")

        # Названный путь не подменяется соседним: проекты лежат рядом, и «взяли
        # другой» — это запуск не той версии движка.
        пустая = Path(врем) / "не-движок"
        пустая.mkdir()
        try:
            mm2_bridge.найти_движок(str(пустая))
        except SystemExit as беда:
            проверить("не-движок" in str(беда),
                      "названный путь без инструментов отказывает и называет себя")
        else:
            проверить(False, "названный путь без инструментов должен был отказать")

        # Когда искать негде вовсе — отказ с подсказкой, а не ImportError изнутри.
        сосед, mm2_bridge.СОСЕД = mm2_bridge.СОСЕД, Path(врем) / "никого"
        try:
            mm2_bridge.найти_движок()
        except SystemExit as беда:
            проверить("ROBLOX_SWAP" in str(беда), "не нашли — сказано, где искали")
        else:
            проверить(False, "без движка запуск должен был отказать")
        finally:
            mm2_bridge.СОСЕД = сосед

        try:
            mm2_bridge.run("выдумка", [], engine=str(папка))
        except SystemExit as беда:
            проверить("выдумка" in str(беда), "неизвестный инструмент назван по имени")
        else:
            проверить(False, "неизвестный инструмент должен был отказать")

    проверить("deal" in mm2_bridge.TOOLS and "join" in mm2_bridge.TOOLS,
              "список инструментов закрытый и не пустой")
    print("\nвсё сошлось")


if __name__ == "__main__":
    main()
