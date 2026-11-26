# -*- coding: utf-8 -*-
"""
Controlador GPIO Local (Raspberry Pi 5) y Puente Remoto UDP (PC -> RPi 5)
con lógica adaptativa de cruce (3 Fases, 3 Semáforos: S1, S2, S3).
"""
import json
import os
import platform
import socket
import statistics
import threading
import time

ESTADOS_VALIDOS = ("OFF", "VERDE", "AMARILLO", "ROJO", "ESTABLE", "ALERTA", "CRITICO")

# ─── CONFIGURACIÓN DE PUENTE REMOTO UDP (PC LOCAL -> RASPBERRY PI 5) ─────────
RPI_GPIO_HOST = os.getenv("RPI_GPIO_HOST", "192.168.0.6").strip()
RPI_GPIO_PORT = int(os.getenv("RPI_GPIO_PORT", "5055"))

# ─── TIEMPOS DEL CRUCE ADAPTATIVO (SEGUNDOS) ────────────────────────────────
T_VERDE_VACIO = 10.0   # 0 vehículos detenidos
T_VERDE_MIN = 15.0     # 1 - 2 vehículos detenidos
T_VERDE_NORMAL = 25.0  # 3 - 5 vehículos detenidos (y modo normal fijo)
T_VERDE_MAX = 40.0     # 6+ vehículos detenidos
T_AMARILLO = 3.0       # Amarillo fijo de seguridad
T_TODO_ROJO = 1.0      # Todo-Rojo de despeje entre fases

CARGAS_TIEMPOS = {
    "Sin tráfico": T_VERDE_VACIO,
    "Normal": T_VERDE_NORMAL,
    "Congestión": T_VERDE_MAX,
}
CARGAS_ORDEN = ("Sin tráfico", "Normal", "Congestión", "Auto")


def calcular_tiempo_verde_s1(n_detenidos):
    """Devuelve el tiempo de verde asignado según vehículos detenidos en rojo."""
    if n_detenidos <= 0:
        return T_VERDE_VACIO
    if n_detenidos <= 2:
        return T_VERDE_MIN
    if n_detenidos <= 5:
        return T_VERDE_NORMAL
    return T_VERDE_MAX


def _crear_pin_factory():
    """Selecciona un PinFactory compatible con Raspberry Pi 5 sin fallbacks ruidosos."""
    if os.path.exists("/dev/gpiochip4") or os.path.exists("/dev/gpiochip0"):
        try:
            from gpiozero.pins.lgpio import LGPIOFactory
            return LGPIOFactory()
        except Exception:
            pass

    try:
        from gpiozero.pins.rpigpio import RPiGPIOFactory
        return RPiGPIOFactory()
    except Exception:
        pass

    try:
        from gpiozero.pins.lgpio import LGPIOFactory
        return LGPIOFactory()
    except Exception:
        return None


def _normalizar_estado(estado):
    """Normaliza etiquetas de señal o HMI (ej. 'VERDE', '[ ESTABLE ]') a un estado válido."""
    raw = (estado or "OFF").strip().upper()
    limpio = raw.strip("[] ").strip()
    if limpio in ESTADOS_VALIDOS:
        return limpio
    if "VERDE" in limpio or "ESTABLE" in limpio:
        return "VERDE"
    if "AMARILLO" in limpio or "AMBAR" in limpio or "ALERTA" in limpio or "CARGANDO" in limpio:
        return "AMARILLO"
    if "CRITICO" in limpio or "CRÍTICO" in limpio:
        return "CRITICO"
    if "ROJO" in limpio:
        return "ROJO"
    if "OFF" in limpio or "ERROR" in limpio:
        return "OFF"
    raise ValueError(f"Estado de semáforo inválido: {estado!r}")


def _parse_pin(env_name, default_pin, fallback_env=None):
    """Lee un número de pin BCM desde variable de entorno con respaldo seguro."""
    raw = os.getenv(env_name)
    if raw is None and fallback_env:
        raw = os.getenv(fallback_env)
    if raw is None:
        return default_pin
    try:
        return int(raw.strip())
    except ValueError:
        print(f"ADVERTENCIA: Valor inválido en {env_name}={raw!r}. Usando GPIO {default_pin}.")
        return default_pin


PINES_SEMAFOROS_DEFAULT = {
    1: (16, 20, 21),  # S1 (Ida):    Pines físicos 36 (R), 38 (Y), 40 (G) | GND 34
    2: (5, 6, 13),    # S2 (Vuelta): Pines físicos 29 (R), 31 (Y), 33 (G) | GND 30
    3: (12, 19, 26),  # S3 (Cruce):  Pines físicos 32 (R), 35 (Y), 37 (G) | GND 39
}


class RemoteGPIOClient:
    """
    Cliente UDP no bloqueante para enviar estados de los 3 semáforos desde la PC local
    hacia la Raspberry Pi 5 (rpi_gpio_bridge.py en 192.168.0.6:5055) con latencia < 1 ms.
    """

    def __init__(self, host=RPI_GPIO_HOST, port=RPI_GPIO_PORT):
        self.host = host
        self.port = int(port)
        self.enabled = bool(self.host) and (platform.system() != "Linux" or os.getenv("FORCE_REMOTE_GPIO", "0") == "1")
        self._sock = None
        self._lock = threading.Lock()
        self.remote_online = False
        self.last_rtt_ms = 0
        if self.enabled:
            try:
                self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self._sock.setblocking(False)
            except Exception as e:
                print(f"Aviso creando socket UDP remoto GPIO: {e}")

    def send_payload(self, payload_dict):
        if not self.enabled or self._sock is None:
            return False
        try:
            raw = json.dumps(payload_dict).encode("utf-8")
            with self._lock:
                self._sock.sendto(raw, (self.host, self.port))
            return True
        except Exception:
            return False

    def ping(self, timeout=0.6):
        """Verifica conectividad bidireccional con el demonio rpi_gpio_bridge.py en la RPi 5."""
        if not self.host:
            return False
        t0 = time.time()
        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(timeout)
            s.sendto(json.dumps({"cmd": "ping"}).encode("utf-8"), (self.host, self.port))
            data, _ = s.recvfrom(512)
            resp = json.loads(data.decode("utf-8"))
            if resp.get("ok"):
                self.remote_online = True
                self.last_rtt_ms = int((time.time() - t0) * 1000)
                return True
        except Exception:
            self.remote_online = False
        finally:
            if s is not None:
                try:
                    s.close()
                except Exception:
                    pass
        return False


_SHARED_REMOTE_CLIENT = RemoteGPIOClient()


def get_remote_gpio_client():
    return _SHARED_REMOTE_CLIENT


def crear_semaforos_fisicos(enabled=None, remote_client=None):
    """Instancia los 3 controladores GPIO (locales en RPi 5 o remotos vía UDP desde PC)."""
    rc = _SHARED_REMOTE_CLIENT if remote_client is None else remote_client
    return {
        1: SemaforoController(
            pin_red=_parse_pin("SEMAFORO_S1_PIN_R", 16, fallback_env="SEMAFORO_PIN_R"),
            pin_yellow=_parse_pin("SEMAFORO_S1_PIN_Y", 20, fallback_env="SEMAFORO_PIN_Y"),
            pin_green=_parse_pin("SEMAFORO_S1_PIN_G", 21, fallback_env="SEMAFORO_PIN_G"),
            enabled=enabled,
            nombre="S1",
            sem_id=1,
            remote_client=rc,
        ),
        2: SemaforoController(
            pin_red=_parse_pin("SEMAFORO_S2_PIN_R", 5),
            pin_yellow=_parse_pin("SEMAFORO_S2_PIN_Y", 6),
            pin_green=_parse_pin("SEMAFORO_S2_PIN_G", 13),
            enabled=enabled,
            nombre="S2",
            sem_id=2,
            remote_client=rc,
        ),
        3: SemaforoController(
            pin_red=_parse_pin("SEMAFORO_S3_PIN_R", 12),
            pin_yellow=_parse_pin("SEMAFORO_S3_PIN_Y", 19),
            pin_green=_parse_pin("SEMAFORO_S3_PIN_G", 26),
            enabled=enabled,
            nombre="S3",
            sem_id=3,
            remote_client=rc,
        ),
    }


class SemaforoController:
    """Controla un módulo semáforo LED físico en Raspberry Pi 5 (local por GPIO o remoto por UDP)."""

    def __init__(self, pin_red=None, pin_yellow=None, pin_green=None, enabled=None, nombre="S1", sem_id=1, remote_client=None):
        self.nombre = nombre
        self.sem_id = int(sem_id)
        self.pin_red = pin_red if pin_red is not None else _parse_pin("SEMAFORO_PIN_R", 16)
        self.pin_yellow = pin_yellow if pin_yellow is not None else _parse_pin("SEMAFORO_PIN_Y", 20)
        self.pin_green = pin_green if pin_green is not None else _parse_pin("SEMAFORO_PIN_G", 21)
        self._remote = _SHARED_REMOTE_CLIENT if remote_client is None else remote_client

        if enabled is None:
            enabled = platform.system() == "Linux"

        self.disponible = False
        self.error_msg = None
        self.estado_actual = "OFF"
        self._blink_actual = None
        self._lock = threading.Lock()
        self._leds = {}

        if not enabled:
            if self._remote is not None and self._remote.enabled:
                self.error_msg = f"Modo Remoto UDP activo hacia RPi 5 ({self._remote.host}:{self._remote.port})"
            else:
                self.error_msg = "Control GPIO local deshabilitado fuera de Raspberry Pi."
            return

        self._init_gpio()

    def _init_gpio(self):
        try:
            from gpiozero import LED
        except ImportError as exc:
            self.error_msg = (
                f"No se pudo importar gpiozero ({exc}). "
                "Instala con: pip install gpiozero lgpio"
            )
            print(f"ADVERTENCIA Semáforo: {self.error_msg}")
            return

        pines = {
            "R": self.pin_red,
            "Y": self.pin_yellow,
            "G": self.pin_green,
        }
        factory = _crear_pin_factory()

        creados = {}
        try:
            for clave, bcm_pin in pines.items():
                creados[clave] = LED(bcm_pin, pin_factory=factory) if factory is not None else LED(bcm_pin)
            self._leds = creados
            self.disponible = True
            self._apagar_todos()
            print(
                f"Semáforo {self.nombre} GPIO listo (BCM R:{self.pin_red}, "
                f"Y:{self.pin_yellow}, G:{self.pin_green})"
            )
        except Exception as exc:
            for led in creados.values():
                try:
                    led.close()
                except Exception:
                    pass
            self.error_msg = (
                f"Error inicializando pines GPIO ({exc}). "
                "En Raspberry Pi 5 asegúrate de tener 'lgpio' instalado: pip install gpiozero lgpio"
            )
            print(f"ADVERTENCIA Semáforo: {self.error_msg}")

    def _apagar_todos(self):
        for led in self._leds.values():
            led.off()

    def set_estado(self, estado, blink_critico=True):
        """Actualiza el LED físico local o envía el comando UDP a la Raspberry Pi 5."""
        estado_norm = _normalizar_estado(estado)

        with self._lock:
            if estado_norm == self.estado_actual and self._blink_actual == blink_critico:
                return

            self.estado_actual = estado_norm
            self._blink_actual = blink_critico

            if self._remote is not None and self._remote.enabled:
                self._remote.send_payload({
                    "cmd": "set_estado",
                    "id": self.sem_id,
                    "nombre": self.nombre,
                    "estado": estado_norm,
                    "blink": bool(blink_critico),
                })

            if not self.disponible:
                return

            self._apagar_todos()

            if estado_norm in ("VERDE", "ESTABLE"):
                self._leds["G"].on()
            elif estado_norm in ("AMARILLO", "ALERTA"):
                self._leds["Y"].on()
            elif estado_norm == "ROJO":
                self._leds["R"].on()
            elif estado_norm == "CRITICO":
                if blink_critico:
                    self._leds["R"].blink(on_time=0.35, off_time=0.35)
                else:
                    self._leds["R"].on()

    def encender_todos(self):
        """Enciende los 3 LEDs simultáneamente para verificar cableado."""
        with self._lock:
            self.estado_actual = "TEST"
            self._blink_actual = None
            if self._remote is not None and self._remote.enabled:
                self._remote.send_payload({"cmd": "test", "id": self.sem_id})
            if not self.disponible:
                return
            self._apagar_todos()
            for led in self._leds.values():
                led.on()

    def close(self):
        """Apaga los LEDs y libera los recursos GPIO."""
        with self._lock:
            self.estado_actual = "OFF"
            self._blink_actual = None
            if self._remote is not None and self._remote.enabled:
                self._remote.send_payload({"cmd": "off", "id": self.sem_id})
            if not self._leds:
                self.disponible = False
                return
            for led in self._leds.values():
                try:
                    led.off()
                    led.close()
                except Exception:
                    pass
            self._leds.clear()
            self.disponible = False


class ControladorCruceAdaptativo:
    """
    Máquina de estados de 3 fases exclusivas (1 semáforo en verde por fase) para evitar colisiones:
      - Fase 1: S1 (Ida) en Verde -> Amarillo -> Todo-Rojo (S2 y S3 en Rojo)
      - Fase 2: S2 (Vuelta) en Verde -> Amarillo -> Todo-Rojo (S1 y S3 en Rojo)
      - Fase 3: S3 (Cruce) en Verde -> Amarillo -> Todo-Rojo (S1 y S2 en Rojo)
    Soporta carga simulada manual ('Sin tráfico', 'Normal', 'Congestión') y modo 'Auto'
    alimentado por las cámaras ESP32-1, ESP32-2, ESP32-3 o USB.
    """

    def __init__(self, semaforo_hw=None, on_phase_change=None, clock=time.monotonic, semaforos_hw=None):
        if semaforos_hw is not None:
            self.semaforos_hw = dict(semaforos_hw)
            self.semaforo_hw = self.semaforos_hw.get(1, semaforo_hw)
        elif isinstance(semaforo_hw, dict):
            self.semaforos_hw = dict(semaforo_hw)
            self.semaforo_hw = self.semaforos_hw.get(1)
        else:
            self.semaforo_hw = semaforo_hw
            self.semaforos_hw = {1: semaforo_hw} if semaforo_hw is not None else {}

        self._on_phase_change = on_phase_change
        self._clock = clock
        self._lock = threading.Lock()

        self.modo_adaptativo = False
        self.carga_s2 = "Sin tráfico"
        self.carga_s3 = "Sin tráfico"

        self._muestras_rojas = {1: [], 2: [], 3: []}
        self._cola_actual = {1: 0, 2: 0, 3: 0}
        self.ultimo_conteo = {1: 0, 2: 0, 3: 0}
        self.ultimo_conteo_s1 = 0
        self.ultimo_verde_s1 = T_VERDE_NORMAL
        self.ultimo_verde_s2 = T_VERDE_VACIO
        self.ultimo_verde_s3 = T_VERDE_VACIO
        self.ultimo_verde_f1 = T_VERDE_NORMAL

        self.fase = "F1_VERDE"
        self.fase_inicio = self._clock()
        self.fase_duracion = T_VERDE_NORMAL
        self._aplicar_hardware_actual()

    def set_modo_adaptativo(self, activo):
        """Activa o desactiva el ajuste dinámico de tiempos según YOLO."""
        with self._lock:
            self.modo_adaptativo = bool(activo)

    def set_carga_simulada(self, semaforo_id, carga):
        """Configura la carga ('Sin tráfico', 'Normal', 'Congestión', 'Auto') para S2 o S3."""
        if carga not in CARGAS_ORDEN:
            raise ValueError(f"Carga inválida: {carga!r}")
        with self._lock:
            if semaforo_id == 2:
                self.carga_s2 = carga
            elif semaforo_id == 3:
                self.carga_s3 = carga
            else:
                raise ValueError(f"Solo S2 y S3 aceptan selector de carga, recibido: {semaforo_id!r}")

    def registrar_detenidos(self, semaforo_id, count, now=None):
        """Registra la cantidad actual de vehículos detenidos en la ROI de S1, S2 o S3."""
        if semaforo_id not in (1, 2, 3):
            raise ValueError(f"ID de semáforo inválido: {semaforo_id!r}")
        t = self._clock() if now is None else now
        val = max(0, int(count))
        with self._lock:
            self._cola_actual[semaforo_id] = val
            en_rojo = self._es_en_rojo_locked(semaforo_id)
            if en_rojo:
                lista = self._muestras_rojas[semaforo_id]
                lista.append((t, val))
                if len(lista) > 120:
                    corte = t - 5.0
                    self._muestras_rojas[semaforo_id] = [
                        (ts, v) for ts, v in lista if ts >= corte
                    ]

    def registrar_detenidos_s1(self, count, now=None):
        """Alias compatible para registrar vehículos detenidos en S1."""
        self.registrar_detenidos(1, count, now=now)

    def _es_en_rojo_locked(self, semaforo_id):
        if semaforo_id == 1:
            return self.fase not in ("F1_VERDE", "F1_AMARILLO")
        if semaforo_id == 2:
            return self.fase not in ("F2_VERDE", "F2_AMARILLO")
        return self.fase not in ("F3_VERDE", "F3_AMARILLO")

    def _luces_por_fase_locked(self):
        if self.fase == "F1_VERDE":
            return "VERDE", "ROJO", "ROJO"
        if self.fase == "F1_AMARILLO":
            return "AMARILLO", "ROJO", "ROJO"
        if self.fase == "F2_VERDE":
            return "ROJO", "VERDE", "ROJO"
        if self.fase == "F2_AMARILLO":
            return "ROJO", "AMARILLO", "ROJO"
        if self.fase == "F3_VERDE":
            return "ROJO", "ROJO", "VERDE"
        if self.fase == "F3_AMARILLO":
            return "ROJO", "ROJO", "AMARILLO"
        return "ROJO", "ROJO", "ROJO"

    def _aplicar_hardware_actual(self):
        s1, s2, s3 = self._luces_por_fase_locked()
        estados = {1: s1, 2: s2, 3: s3}
        for sid, ctrl in self.semaforos_hw.items():
            if ctrl is not None and sid in estados:
                ctrl.set_estado(estados[sid])

    def _calcular_conteo_estable_rojo_locked(self, semaforo_id, now):
        muestras = self._muestras_rojas[semaforo_id]
        if not muestras:
            return self._cola_actual.get(semaforo_id, 0)
        recientes = [v for ts, v in muestras if ts >= (now - 1.5)]
        if not recientes:
            recientes = [muestras[-1][1]]
        return int(round(statistics.median(recientes)))

    def _tiempo_para_secundario_locked(self, semaforo_id, carga, conteo):
        if carga == "Auto":
            return calcular_tiempo_verde_s1(conteo) if self.modo_adaptativo else T_VERDE_NORMAL
        return CARGAS_TIEMPOS[carga]

    def _avanzar_fase_locked(self, t_transicion):
        siguiente_inicio = self.fase_inicio + self.fase_duracion
        if t_transicion - siguiente_inicio > 5.0:
            siguiente_inicio = t_transicion

        if self.fase == "F1_VERDE":
            self.fase = "F1_AMARILLO"
            self.fase_duracion = T_AMARILLO
        elif self.fase == "F1_AMARILLO":
            self.fase = "F1_TODO_ROJO"
            self.fase_duracion = T_TODO_ROJO
        elif self.fase == "F1_TODO_ROJO":
            n_s2 = self._calcular_conteo_estable_rojo_locked(2, siguiente_inicio)
            t_s2 = self._tiempo_para_secundario_locked(2, self.carga_s2, n_s2)
            self.ultimo_conteo[2] = n_s2
            self.ultimo_verde_s2 = t_s2
            self._muestras_rojas[2] = []

            self.fase = "F2_VERDE"
            self.fase_duracion = t_s2
        elif self.fase == "F2_VERDE":
            self.fase = "F2_AMARILLO"
            self.fase_duracion = T_AMARILLO
        elif self.fase == "F2_AMARILLO":
            self.fase = "F2_TODO_ROJO"
            self.fase_duracion = T_TODO_ROJO
        elif self.fase == "F2_TODO_ROJO":
            n_s3 = self._calcular_conteo_estable_rojo_locked(3, siguiente_inicio)
            t_s3 = self._tiempo_para_secundario_locked(3, self.carga_s3, n_s3)
            self.ultimo_conteo[3] = n_s3
            self.ultimo_verde_s3 = t_s3
            self._muestras_rojas[3] = []

            self.fase = "F3_VERDE"
            self.fase_duracion = t_s3
        elif self.fase == "F3_VERDE":
            self.fase = "F3_AMARILLO"
            self.fase_duracion = T_AMARILLO
        elif self.fase == "F3_AMARILLO":
            self.fase = "F3_TODO_ROJO"
            self.fase_duracion = T_TODO_ROJO
        else:  # F3_TODO_ROJO -> F1_VERDE
            n_s1 = self._calcular_conteo_estable_rojo_locked(1, siguiente_inicio)
            t_s1 = calcular_tiempo_verde_s1(n_s1) if self.modo_adaptativo else T_VERDE_NORMAL

            self.ultimo_conteo[1] = n_s1
            self.ultimo_conteo_s1 = n_s1
            self.ultimo_verde_s1 = t_s1
            self.ultimo_verde_f1 = t_s1
            self._muestras_rojas[1] = []

            self.fase = "F1_VERDE"
            self.fase_duracion = t_s1

        self.fase_inicio = siguiente_inicio
        self._aplicar_hardware_actual()

    def tick(self, now=None):
        """Avanza la máquina de estados si venció la fase actual y devuelve el estado."""
        t = self._clock() if now is None else now
        eventos = []
        with self._lock:
            while (t - self.fase_inicio) >= self.fase_duracion:
                self._avanzar_fase_locked(t)
                eventos.append((self.fase, self.fase_duracion, self.ultimo_conteo_s1, self.modo_adaptativo))
            snapshot = self._build_estado_locked(t)

        if self._on_phase_change is not None:
            for ev in eventos:
                try:
                    self._on_phase_change(*ev)
                except Exception:
                    pass
        return snapshot

    def _build_estado_locked(self, now):
        restante = max(0.0, self.fase_duracion - (now - self.fase_inicio))
        s1, s2, s3 = self._luces_por_fase_locked()

        t_f1_est = calcular_tiempo_verde_s1(self._cola_actual[1]) if self.modo_adaptativo else T_VERDE_NORMAL
        t_f2_est = self._tiempo_para_secundario_locked(2, self.carga_s2, self._cola_actual[2])
        t_f3_est = self._tiempo_para_secundario_locked(3, self.carga_s3, self._cola_actual[3])

        if self.fase == "F1_VERDE":
            rest_s1 = restante
            rest_s2 = restante + T_AMARILLO + T_TODO_ROJO
            rest_s3 = rest_s2 + t_f2_est + T_AMARILLO + T_TODO_ROJO
        elif self.fase == "F1_AMARILLO":
            rest_s1 = restante
            rest_s2 = restante + T_TODO_ROJO
            rest_s3 = rest_s2 + t_f2_est + T_AMARILLO + T_TODO_ROJO
        elif self.fase == "F1_TODO_ROJO":
            rest_s2 = restante
            rest_s3 = restante + t_f2_est + T_AMARILLO + T_TODO_ROJO
            rest_s1 = rest_s3 + t_f3_est + T_AMARILLO + T_TODO_ROJO
        elif self.fase == "F2_VERDE":
            rest_s2 = restante
            rest_s3 = restante + T_AMARILLO + T_TODO_ROJO
            rest_s1 = rest_s3 + t_f3_est + T_AMARILLO + T_TODO_ROJO
        elif self.fase == "F2_AMARILLO":
            rest_s2 = restante
            rest_s3 = restante + T_TODO_ROJO
            rest_s1 = rest_s3 + t_f3_est + T_AMARILLO + T_TODO_ROJO
        elif self.fase == "F2_TODO_ROJO":
            rest_s3 = restante
            rest_s1 = restante + t_f3_est + T_AMARILLO + T_TODO_ROJO
            rest_s2 = rest_s1 + t_f1_est + T_AMARILLO + T_TODO_ROJO
        elif self.fase == "F3_VERDE":
            rest_s3 = restante
            rest_s1 = restante + T_AMARILLO + T_TODO_ROJO
            rest_s2 = rest_s1 + t_f1_est + T_AMARILLO + T_TODO_ROJO
        elif self.fase == "F3_AMARILLO":
            rest_s3 = restante
            rest_s1 = restante + T_TODO_ROJO
            rest_s2 = rest_s1 + t_f1_est + T_AMARILLO + T_TODO_ROJO
        else:  # F3_TODO_ROJO
            rest_s1 = restante
            rest_s2 = restante + t_f1_est + T_AMARILLO + T_TODO_ROJO
            rest_s3 = rest_s2 + t_f2_est + T_AMARILLO + T_TODO_ROJO

        cola_s1 = self._cola_actual[1] if self._es_en_rojo_locked(1) else self.ultimo_conteo[1]
        cola_s2 = self._cola_actual[2] if self._es_en_rojo_locked(2) else self.ultimo_conteo[2]
        cola_s3 = self._cola_actual[3] if self._es_en_rojo_locked(3) else self.ultimo_conteo[3]

        return {
            "fase": self.fase,
            "modo": "ADAPTATIVO" if self.modo_adaptativo else "NORMAL",
            "s1": s1,
            "s2": s2,
            "s3": s3,
            "restante_fase": restante,
            "restante_s1": rest_s1,
            "restante_s2": rest_s2,
            "restante_s3": rest_s3,
            "cola_s1": cola_s1,
            "cola_s2": cola_s2,
            "cola_s3": cola_s3,
            "ultimo_conteo_s1": self.ultimo_conteo_s1,
            "ultimo_verde_s1": self.ultimo_verde_s1,
            "ultimo_verde_s2": self.ultimo_verde_s2,
            "ultimo_verde_s3": self.ultimo_verde_s3,
            "ultimo_verde_f1": self.ultimo_verde_f1,
            "carga_s2": self.carga_s2,
            "carga_s3": self.carga_s3,
        }

    def get_estado(self, now=None):
        """Devuelve un resumen del estado actual sin avanzar fases."""
        t = self._clock() if now is None else now
        with self._lock:
            return self._build_estado_locked(t)
