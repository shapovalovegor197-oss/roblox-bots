# -*- coding: utf-8 -*-
"""Снять весь экран через N секунд. Запускать НА столе бота: снимок выйдет,
только пока стол показан на мониторе (это делает desktop.show из главного)."""
import sys, time
delay = float(sys.argv[1]) if len(sys.argv) > 1 else 2.0
out = sys.argv[2] if len(sys.argv) > 2 else "var/screens/desk_full.png"
time.sleep(delay)
import mss
from PIL import Image
with mss.mss() as sct:
    m = sct.monitors[1]
    shot = sct.grab(m)
    Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX").save(out)
print("снят экран стола бота:", out)
