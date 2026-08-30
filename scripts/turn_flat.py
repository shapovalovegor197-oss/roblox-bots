# -*- coding: utf-8 -*-
"""Период поворота БЕЗ единого вертикального движения камеры.

Камера остаётся в том положении, в каком её оставили. Меряем горизонтальный
сдвиг сцены фазовой корреляцией и переводим пиксели в градусы через угол
обзора: Roblox по умолчанию 70 градусов по вертикали, при 1280x720 это
102.5 по горизонтали, то есть 0.0800 градуса на пиксель.

Запуск: python scripts/turn_flat.py [шаг] [сколько шагов]
"""
import sys, time
import cv2
import numpy as np
sys.path.insert(0, "src")
from brainbot import config, log
from brainbot.window import enum_roblox_windows
from brainbot.inputs import Hand
from brainbot.capture import grab

s = config.load(); log.setup(s.logs_dir)
w = enum_roblox_windows()[0]
hand = Hand(w, s.input)

STEP = int(sys.argv[1]) if len(sys.argv) > 1 else 50
COUNT = int(sys.argv[2]) if len(sys.argv) > 2 else 8

FOV_V = 70.0
box = w.client_box()
DEG_PER_PX = (2 * np.degrees(np.arctan(np.tan(np.radians(FOV_V / 2)) *
                                       box.width / box.height))) / box.width


def scene():
    """Середина кадра в сером: без меню слева, без чата сверху, без HUD снизу."""
    fr = grab(box, hwnd=w.hwnd)
    h, wd = fr.shape[:2]
    cut = fr[int(h * 0.10):int(h * 0.80), int(wd * 0.22):int(wd * 0.95)]
    return cv2.cvtColor(cut, cv2.COLOR_BGR2GRAY).astype(np.float32)


print("градусов на пиксель: %.4f (окно %dx%d)" % (DEG_PER_PX, box.width, box.height), flush=True)
prev = scene()
total_px, total_units = 0.0, 0
for i in range(COUNT):
    hand.look(STEP, 0)
    time.sleep(0.45)
    now = scene()
    (dx, dy), resp = cv2.phaseCorrelate(prev, now)
    prev = now
    if resp < 0.05:
        print("шаг %d: сцена не совпала (отклик %.3f), пропускаю" % (i, resp), flush=True)
        continue
    total_px += abs(dx); total_units += STEP
    print("шаг %d: сдвиг %+.1f px (отклик %.2f) = %+.2f град"
          % (i, dx, resp, dx * DEG_PER_PX), flush=True)
if total_units:
    deg = total_px * DEG_PER_PX
    rate = deg / total_units
    print("\nНАКОПЛЕНО: %.1f град за %d единиц; %.4f град/ед; ПОЛНЫЙ ОБОРОТ %.0f единиц"
          % (deg, total_units, rate, 360.0 / rate), flush=True)
