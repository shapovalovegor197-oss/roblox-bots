"""Снижение потребления ресурсов: FFlags клиента, привязка к ядрам, приоритеты.

Три независимых рычага:

1. FFlags — внутренние настройки клиента. Главный из них DFIntTaskSchedulerTargetFps:
   боту не нужно 60 кадров, ему нужен стабильный UI. 20 кадров вместо 60 — это
   втрое меньше работы GPU и заметно меньше CPU на каждого клиента.

2. Привязка к ядрам. Roblox однопоточный по главному циклу, а ядер 12. Если не
   пиновать, планировщик будет таскать клиентов между ядрами и рушить кэш.
   Даём каждому свои ядра — клиенты перестают драться.

3. Приоритеты. Активный клиент (тот, в котором сейчас работает сценарий) —
   Normal, остальные — BelowNormal. Так фокусный отклик не проседает.

FFlags Roblox периодически переименовывает и выпиливает. Неизвестный флаг просто
игнорируется, ломающего эффекта нет — но и полагаться на конкретное имя вечно
нельзя. Если после обновления клиента поведение изменилось, начинать проверку
отсюда.
"""
from __future__ import annotations

import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path

from .log import get

log = get("optimize")

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

PROCESS_SET_INFORMATION = 0x0200
PROCESS_QUERY_INFORMATION = 0x0400

PRIORITY = {
    "idle": 0x00000040,
    "below": 0x00004000,
    "normal": 0x00000020,
    "above": 0x00008000,
}

# ---------------------------------------------------------------------------
# FFlags
# ---------------------------------------------------------------------------

# Профиль «бот»: картинка нужна ровно настолько, чтобы распознавались кнопки.
FFLAGS_BOT = {
    # --- главное: потолок кадров ---
    "DFIntTaskSchedulerTargetFps": 20,

    # --- рендер по минимуму ---
    "DFIntDebugFRMQualityLevelOverride": 1,     # нижний уровень качества
    "FFlagDebugGraphicsPreferD3D11": "True",    # D3D11 вместо Vulkan: предсказуемее
    "FIntRenderShadowIntensity": 0,             # тени
    "FFlagDisablePostFx": "True",               # постобработка
    "FIntDebugForceMSAASamples": 1,             # сглаживание
    "DFFlagDebugPauseVoxelizer": "True",        # пересчёт воксельного света
    "FIntTerrainArraySliceSize": 4,             # детализация ландшафта

    # --- текстуры: это про видеопамять и про ОЗУ ---
    "DFFlagTextureQualityOverrideEnabled": "True",
    "DFIntTextureQualityOverride": 0,

    # --- телеметрия: сеть и фоновый CPU ни за чем ---
    "FFlagDebugDisableTelemetryEphemeralCounter": "True",
    "FFlagDebugDisableTelemetryEphemeralStat": "True",
    "FFlagDebugDisableTelemetryEventIngest": "True",
    "FFlagDebugDisableTelemetryPoint": "True",
    "FFlagDebugDisableTelemetryV2Counter": "True",
    "FFlagDebugDisableTelemetryV2Event": "True",
    "FFlagDebugDisableTelemetryV2Stat": "True",

    # --- чтобы клиент не ушёл в полный экран сам ---
    "FFlagHandleAltEnterFullscreenManually": "False",
}


def player_version_dirs() -> list[Path]:
    """Папки установленных версий клиента (не Studio)."""
    roots = [
        Path(os.environ["LOCALAPPDATA"]) / "Roblox" / "Versions",
        Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Roblox" / "Versions",
    ]
    out: list[Path] = []
    for root in roots:
        if root.exists():
            out.extend(p.parent for p in root.glob("*/RobloxPlayerBeta.exe"))
    return out


def write_fflags(flags: dict | None = None) -> list[Path]:
    """Кладёт ClientAppSettings.json во все версии клиента.

    Обновление клиента создаёт новую папку версии без наших настроек, поэтому
    вызывать это надо перед каждым запуском, а не один раз руками.
    """
    flags = flags or FFLAGS_BOT
    written: list[Path] = []
    for version in player_version_dirs():
        settings_dir = version / "ClientSettings"
        settings_dir.mkdir(exist_ok=True)
        path = settings_dir / "ClientAppSettings.json"
        payload = json.dumps(flags, indent=2)
        if path.exists() and path.read_text(encoding="utf-8") == payload:
            continue
        path.write_text(payload, encoding="utf-8")
        written.append(path)
        log.info("FFlags записаны: %s", path)
    return written


def clear_fflags() -> list[Path]:
    """Убрать наши настройки — вернуть клиент к штатному поведению."""
    removed = []
    for version in player_version_dirs():
        path = version / "ClientSettings" / "ClientAppSettings.json"
        if path.exists():
            path.unlink()
            removed.append(path)
            log.info("FFlags убраны: %s", path)
    return removed


# ---------------------------------------------------------------------------
# Ядра и приоритеты
# ---------------------------------------------------------------------------

def cpu_count() -> int:
    return os.cpu_count() or 1


def plan_affinity(n_clients: int, reserve_threads: int = 4) -> list[list[int]]:
    """Раздаёт ядра клиентам.

    reserve_threads — сколько потоков оставить системе, нашему боту и захвату.
    Если клиентов больше, чем ядер, наборы начнут пересекаться: это нормально,
    смысл пиновки не в изоляции, а в том, чтобы клиент не кочевал по всем ядрам.
    """
    total = cpu_count()
    usable = list(range(reserve_threads, total)) or list(range(total))
    if n_clients <= 0:
        return []
    per = max(1, len(usable) // n_clients)
    plan = []
    for i in range(n_clients):
        start = (i * per) % len(usable)
        chunk = [usable[(start + k) % len(usable)] for k in range(per)]
        plan.append(sorted(set(chunk)))
    return plan


def pin_process(pid: int, cores: list[int], priority: str = "below") -> bool:
    """Привязывает процесс к ядрам и ставит приоритет."""
    handle = kernel32.OpenProcess(
        PROCESS_SET_INFORMATION | PROCESS_QUERY_INFORMATION, False, pid
    )
    if not handle:
        log.warning("не открыть процесс pid=%s (код %s)", pid, ctypes.get_last_error())
        return False
    try:
        mask = 0
        for c in cores:
            mask |= 1 << c
        if mask and not kernel32.SetProcessAffinityMask(handle, wintypes.WPARAM(mask)):
            log.warning("SetProcessAffinityMask не удался для pid=%s", pid)
            return False
        cls = PRIORITY.get(priority, PRIORITY["below"])
        kernel32.SetPriorityClass(handle, cls)
        log.info("pid=%s → ядра %s, приоритет %s", pid, cores, priority)
        return True
    finally:
        kernel32.CloseHandle(handle)


# ---------------------------------------------------------------------------
# Замер: сколько клиентов реально влезет
# ---------------------------------------------------------------------------

def _mem_status() -> tuple[float, float]:
    """(всего ГБ, доступно ГБ)."""
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    st = MEMORYSTATUSEX()
    st.dwLength = ctypes.sizeof(st)
    kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
    return st.ullTotalPhys / 2**30, st.ullAvailPhys / 2**30


def client_memory() -> list[tuple[int, float]]:
    """[(pid, ГБ)] по каждому живому клиенту Roblox."""
    import subprocess
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq RobloxPlayerBeta.exe", "/FO", "CSV", "/NH"],
        capture_output=True, text=True, encoding="cp866", errors="replace",
    ).stdout
    result = []
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) < 5 or not parts[1].isdigit():
            continue
        kb = "".join(ch for ch in parts[4] if ch.isdigit())
        if kb:
            result.append((int(parts[1]), int(kb) / 1024 / 1024))
    return result


def estimate_capacity(per_client_gb: float | None = None,
                      headroom_gb: float = 2.0) -> dict:
    """Сколько клиентов влезет по памяти. Меряет по живым, если они есть."""
    total, avail = _mem_status()
    live = client_memory()
    measured = sum(g for _, g in live) / len(live) if live else None
    per = per_client_gb or measured or 2.0

    return {
        "total_gb": total,
        "avail_gb": avail,
        "live_clients": len(live),
        "per_client_gb": per,
        "measured": measured is not None,
        "fits_now": max(0, int((avail - headroom_gb) / per)),
        "fits_if_freed": max(0, int((total * 0.85 - headroom_gb) / per)),
    }
