# -*- coding: utf-8 -*-
"""
Configurador automático de túneles USB (ADB) para conectar hasta 3 teléfonos Android
con la app IP Webcam hacia interfaz.py (TEL-1: 8081, TEL-2: 8082, TEL-3: 8083).
"""
import os
import shutil
import subprocess
import sys

BASE_LOCAL_PORT = 8081
PHONE_APP_PORT = 8080


def find_adb():
    adb = shutil.which("adb")
    if adb:
        return adb
    candidates = [
        os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"),
        r"C:\platform-tools\adb.exe",
        r"C:\Android\platform-tools\adb.exe",
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def main():
    adb = find_adb()
    if not adb:
        print("[ERROR] No se encontró 'adb' en el PATH ni en rutas estándar.")
        print("Instala Android Platform Tools y agrega 'platform-tools' al PATH de Windows.")
        return 1

    print(f"Usando ADB: {adb}")
    res = subprocess.run([adb, "devices"], capture_output=True, text=True, check=False)
    lines = [l.strip() for l in res.stdout.splitlines()[1:] if l.strip()]

    ready_serials = []
    for line in lines:
        parts = line.split()
        if len(parts) >= 2:
            serial, state = parts[0], parts[1]
            if state == "device":
                ready_serials.append(serial)
            else:
                print(f"[AVISO] Dispositivo {serial} en estado '{state}' (acepta Depuración USB en la pantalla).")

    if not ready_serials:
        print("\n[SIN TELÉFONOS LISTOS] Conecta los teléfonos por USB con Depuración USB activada.")
        return 1

    print(f"\nTeléfonos detectados listos: {len(ready_serials)}")
    for idx, serial in enumerate(ready_serials[:3]):
        local_port = BASE_LOCAL_PORT + idx
        cmd = [adb, "-s", serial, "forward", f"tcp:{local_port}", f"tcp:{PHONE_APP_PORT}"]
        r = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if r.returncode == 0:
            print(f"  [OK] TEL-{idx + 1} ({serial}) -> http://127.0.0.1:{local_port}/video")
        else:
            print(f"  [ERROR] TEL-{idx + 1} ({serial}): {r.stderr.strip()}")

    print("\nAsegúrate de presionar 'Iniciar servidor' dentro de la app IP Webcam en cada teléfono.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
