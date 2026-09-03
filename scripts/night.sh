#!/bin/sh
# Ночной надзор: держать ферму живой до N перерождений.
#
# Зачем отдельный слой. Прогон farm_loop кончается по таймеру, и дальше база
# стоит открытая — а клиент Roblox за сутки умирал дважды (02.09 в 15:33 и в
# ночь на 03.09), и ферма всё это время работала бы вхолостую. Надзор закрывает
# обе дыры: поднимает клиент, если окна нет, и запирает дверь после каждого
# прогона, чем бы тот ни кончился.
#
# Имена переменных латиницей: /bin/sh кириллицу в идентификаторах не принимает,
# на этом первая версия и легла (03.09, 03:00).
#
# Запуск: sh scripts/night.sh [сколько_ребёрнов]
goal=${1:-5}
log=var/night_$(date +%m%d_%H%M)_run.txt

echo "$(date +%H:%M:%S) надзор пошёл, цель $goal ребёрнов" >> "$log"

while :; do
    done_count=$(python -c "
import json
try:
    print(json.load(open('var/farm_status.json', encoding='utf-8')).get('ребёрнов', 0))
except Exception:
    print(0)" 2>/dev/null)
    done_count=${done_count:-0}
    if [ "$done_count" -ge "$goal" ]; then
        echo "$(date +%H:%M:%S) ЦЕЛЬ ДОСТИГНУТА: ребёрнов $done_count" >> "$log"
        python scripts/lock_now.py >> "$log" 2>&1
        break
    fi

    windows=$(python -c "
import sys
sys.path.insert(0, 'src')
from brainbot.window import enum_roblox_windows
print(len(enum_roblox_windows()))" 2>/dev/null)
    if [ "${windows:-0}" -eq 0 ]; then
        echo "$(date +%H:%M:%S) окна нет — поднимаю клиент" >> "$log"
        python run.py launch raven >> "$log" 2>&1
        sleep 25
    fi

    echo "$(date +%H:%M:%S) старт прогона (сделано $done_count из $goal)" >> "$log"
    python scripts/farm_loop.py 60 100 "$goal" >> "$log" 2>&1
    rc=$?
    if [ $rc -ne 0 ]; then
        # Чаще всего это общий замок: ввод держит кто-то другой.
        echo "$(date +%H:%M:%S) прогон вышел с кодом $rc — жду минуту" >> "$log"
        sleep 60
        continue
    fi
    # Дверь закрываем ПОСЛЕ каждого прогона, а не «когда вспомню».
    python scripts/lock_now.py >> "$log" 2>&1
    sleep 5
done
