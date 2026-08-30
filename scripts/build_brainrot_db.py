"""Собрать справочник брейнротов из выгрузки соседнего проекта.

Источник — `D:/claude projects/gg/export/positions.csv`: 4536 позиций витрины
Starpets с именем, редкостью, мутацией, доходом в секунду и ценой. Для бота это
готовая база знаний, которой у него не было:

  * ИСПРАВЛЕНИЕ OCR. Имена брейнротов — итальянская тарабарщина («Tralalero Tralala»,
    «Chef Crabracadabra»), и OCR их корёжит. Имея список из 528 настоящих имён,
    прочитанное можно к ним притянуть нечётким сравнением — как мы уже делаем с ником.
  * РЕШЕНИЕ «брать или нет». В карточке у конвейера видно имя, редкость и доход.
    Справочник добавляет к этому рыночную цену и позволяет сравнивать позиции между
    собой, а не только с порогом.
  * РЕШЕНИЕ «что продать». Когда база забита, продавать надо самое дешёвое по доходу,
    а не первое попавшееся.

Запуск: python scripts/build_brainrot_db.py
Результат: data/brainrots.json
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

SRC = Path(r"D:/claude projects/gg/export/positions.csv")
OUT = Path(__file__).resolve().parent.parent / "data" / "brainrots.json"

# Порядок по возрастанию ценности. Первые семь — обычная лестница игры,
# дальше особые категории, которые не встречаются на конвейере.
RARITY_ORDER = ["common", "rare", "epic", "legendary", "mythic",
                "brainrot_god", "secret", "og", "festive", "admin"]


def num(s: str) -> float | None:
    s = (s or "").strip().replace(" ", "").replace(",", ".")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def main() -> None:
    if not SRC.exists():
        raise SystemExit(f"нет источника: {SRC}")

    items: dict[str, dict] = {}
    with SRC.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f, delimiter=";"):
            name = (row.get("имя") or "").strip()
            if not name:
                continue
            key = name.lower()
            it = items.setdefault(key, {
                "name": name,
                "rarity": (row.get("редкость") or "").strip(),
                "income": {},
                "price_rub": {},
            })
            mut = (row.get("мутация") or "default").strip() or "default"
            inc = num(row.get("доход_в_сек"))
            if inc:
                it["income"][mut] = inc
            price = num(row.get("цена_starpets_руб"))
            if price:
                it["price_rub"][mut] = price

    # базовый доход — у немутированной версии; если её нет, берём минимальный
    for it in items.values():
        inc = it["income"]
        it["base_income"] = inc.get("default") or (min(inc.values()) if inc else None)

    data = {
        "источник": str(SRC),
        "редкости_по_возрастанию": RARITY_ORDER,
        "мутации": sorted({m for it in items.values() for m in it["income"]}),
        "items": items,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    by_rarity: dict[str, int] = {}
    for it in items.values():
        by_rarity[it["rarity"]] = by_rarity.get(it["rarity"], 0) + 1
    print(f"{OUT}: {len(items)} имён")
    for r in RARITY_ORDER:
        if r in by_rarity:
            print(f"  {r:14} {by_rarity[r]}")


if __name__ == "__main__":
    main()
