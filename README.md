# Creality LAN for Home Assistant

Local LAN integration for Creality printers (tested on **K1 / K1 Max**) using the printer’s WebSocket

-  Live sensors: nozzle/bed/chamber temps, progress, time left/elapsed, current file, state
-  Camera: Webcam from Printer
-  Controls: light, model fan, auxiliary fan, case fan
-  CFS: auto-detected, one device per CFS box (type=0) with temperature, humidity, and **slot 0–3** details
-  Push updates (no polling), reconnect with backoff
-  No account / no token / no cloud

> ⚠️ This project is **unofficial** and not affiliated with Creality.

---

## Screenshots

---

## Configuration
- Enter the printer’s IP (e.g. `192.168.1.50`).
- After adding, open integration **System Options** and enable “**Enable new entities by default**”.
- Ensure your printer’s camera is reachable at `http://<ip>:8080/?action=stream`.
