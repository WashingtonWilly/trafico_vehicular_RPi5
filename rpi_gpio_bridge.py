# -*- coding: utf-8 -*-
"""
Receptor UDP de Baja Latencia en Raspberry Pi 5 (Puerto 5055)
Recibe los cambios de fase de S1, S2 y S3 desde el computador local (Windows)
y conmuta en tiempo real los LEDs físicos conectados al GPIO de la RPi 5.
"""
import json
import signal
import socket
import sys
import time
from semaforo import crear_semaforos_fisicos

UDP_HOST = "0.0.0.0"
UDP_PORT = 5055


def main():
    semaforos = crear_semaforos_fisicos(enabled=True)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((UDP_HOST, UDP_PORT))
    print(f"=== Puente GPIO Semáforos RPi 5 escuchando en UDP {UDP_HOST}:{UDP_PORT} ===", flush=True)

    running = True

    def _handle_sig(*_):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, _handle_sig)
    signal.signal(signal.SIGTERM, _handle_sig)

    try:
        while running:
            data, addr = sock.recvfrom(1024)
            if not data:
                continue
            try:
                msg = json.loads(data.decode("utf-8"))
            except Exception:
                continue

            cmd = msg.get("cmd")
            if cmd == "set_estado":
                sid = int(msg.get("id", 1))
                est = msg.get("estado", "OFF")
                blink = bool(msg.get("blink", True))
                if sid in semaforos:
                    semaforos[sid].set_estado(est, blink_critico=blink)
            elif cmd == "batch":
                estados = msg.get("estados", {})
                for k, est in estados.items():
                    sid = int(k)
                    if sid in semaforos:
                        semaforos[sid].set_estado(est)
            elif cmd == "test":
                sid = msg.get("id")
                if sid is None:
                    for ctrl in semaforos.values():
                        ctrl.encender_todos()
                elif int(sid) in semaforos:
                    semaforos[int(sid)].encender_todos()
            elif cmd == "off":
                sid = msg.get("id")
                if sid is None:
                    for ctrl in semaforos.values():
                        ctrl.set_estado("OFF")
                elif int(sid) in semaforos:
                    semaforos[int(sid)].set_estado("OFF")
            elif cmd == "ping":
                resp = {
                    "ok": True,
                    "ts": time.time(),
                    "estados": {sid: ctrl.estado_actual for sid, ctrl in semaforos.items()},
                    "gpio_ok": {sid: ctrl.disponible for sid, ctrl in semaforos.items()},
                }
                sock.sendto(json.dumps(resp).encode("utf-8"), addr)
    finally:
        print("Cerrando puente GPIO y apagando LEDs...", flush=True)
        for ctrl in semaforos.values():
            ctrl.close()
        sock.close()


if __name__ == "__main__":
    main()
