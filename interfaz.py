# -*- coding: utf-8 -*-
"""
HMI Industrial Control Panel — PC / Raspberry Pi 5
Monitoreo de Telemetría Hardware, Sistema Multi-Cámara (TEL-1, TEL-2, TEL-3, USB)
e Inferencia YOLOv11n con Doble ROI y Control Inteligente de Semáforo.
"""
import platform, cv2, time, os, sys, json, threading, queue, shutil, csv, datetime, socket, struct
from collections import deque
import numpy as np
import requests
import tkinter as tk
import customtkinter as ctk
from PIL import Image, ImageTk

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from semaforo import (
    ControladorCruceAdaptativo,
    SemaforoController,
    crear_semaforos_fisicos,
    get_remote_gpio_client,
)

IS_PI = platform.system() == "Linux"

ULTRALYTICS_MODEL = os.getenv("MODEL_PATH", "yolov11n.pt")
USB_CAMERA_TARGET = os.getenv("USB_CAMERA_NAME", "USB2.0 PC CAMERA")
TEL1_URL          = os.getenv("TEL1_URL", "http://127.0.0.1:8081/video")
TEL2_URL          = os.getenv("TEL2_URL", "http://127.0.0.1:8082/video")
TEL3_URL          = os.getenv("TEL3_URL", "http://127.0.0.1:8083/video")
SEM_SOURCE_OPTIONS = ("TEL-1", "TEL-2", "TEL-3", "USB", "Ninguna")


def _derive_phone_endpoints(stream_url):
    """Deriva las URLs de captura (/shot.jpg) y estado (/status.json) desde la URL MJPEG de IP Webcam."""
    u = (stream_url or "").strip().rstrip("/")
    if u.endswith("/video"):
        base = u[:-6]
        return u, f"{base}/shot.jpg", f"{base}/status.json"
    if u.endswith("/stream"):
        base = u[:-7]
        return u, f"{base}/capture", f"{base}/status"
    return u, f"{u}/shot.jpg", f"{u}/status.json"

ROI_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "roi_config.json")

# Coordenadas por defecto calibradas en Juan_YOLOV11n.ipynb sobre base 1920x1080
DEFAULT_ROI_PARQUEO_NORM = [
    [900 / 1920.0, 100 / 1080.0],
    [980 / 1920.0, 100 / 1080.0],
    [860 / 1920.0, 560 / 1080.0],
    [715 / 1920.0, 560 / 1080.0]
]
DEFAULT_ROI_MOVIMIENTO_NORM = [
    [990 / 1920.0,  100 / 1080.0],
    [1120 / 1920.0, 100 / 1080.0],
    [1245 / 1920.0, 1050 / 1080.0],
    [665 / 1920.0,  1050 / 1080.0]
]

# ─── PALETA DE COLORES MODERNA DARK THEME ────────────────────────────────────
C_BG       = "#0F0F1A"  # Azul noche muy oscuro
C_PANEL    = "#1A1A2E"  # Azul paneles
C_BORDER   = "#2D2D44"  # Borde sutil azulado
C_ACCENT   = "#3B82F6"  # Azul moderno (primary)
C_ACCENT2  = "#1D4ED8"  # Azul oscuro (hover)
C_TERM_BG  = "#0A0A14"  # Negro azulado consola
C_GREEN    = "#22C55E"  # Verde moderno (OK)
C_RED      = "#EF4444"  # Rojo moderno (Crítico)
C_YELLOW   = "#F59E0B"  # Ámbar moderno (Alerta)
C_BLUE     = "#38BDF8"  # Celeste técnico (ROI)
C_TEXT     = "#F1F5F9"  # Blanco suave
C_MUTED    = "#94A3B8"  # Gris azulado secundario

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


def list_system_usb_cameras():
    """
    Enumera las cámaras de captura reales del sistema con su índice exacto de OpenCV:
    - Windows: Consulta DirectShow (CLSID_VideoInputDeviceCategory) mediante ctypes COM.
    - Linux/RPi5: Consulta /sys/class/video4linux filtrando nodos UVC de captura (index 0).
    """
    devices = []
    if platform.system() == "Windows":
        try:
            import ctypes
            from ctypes import wintypes

            class GUID(ctypes.Structure):
                _fields_ = [
                    ("Data1", wintypes.DWORD),
                    ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD),
                    ("Data4", ctypes.c_ubyte * 8),
                ]
                def __init__(self, l, w1, w2, b1, b2, b3, b4, b5, b6, b7, b8):
                    super().__init__(l, w1, w2, (ctypes.c_ubyte * 8)(b1, b2, b3, b4, b5, b6, b7, b8))

            class VARIANT(ctypes.Structure):
                _fields_ = [
                    ("vt", wintypes.WORD),
                    ("wReserved1", wintypes.WORD),
                    ("wReserved2", wintypes.WORD),
                    ("wReserved3", wintypes.WORD),
                    ("bstrVal", ctypes.c_wchar_p),
                    ("_pad", ctypes.c_void_p),
                ]

            CLSID_SystemDeviceEnum = GUID(0x62BE5D10, 0x60EB, 0x11D0, 0xBD, 0x3B, 0x00, 0xA0, 0xC9, 0x11, 0xCE, 0x86)
            IID_ICreateDevEnum     = GUID(0x29840822, 0x5B84, 0x11D0, 0xBD, 0x3B, 0x00, 0xA0, 0xC9, 0x11, 0xCE, 0x86)
            CLSID_VideoInputDevice = GUID(0x860BB310, 0x5D01, 0x11D0, 0xBD, 0x3B, 0x00, 0xA0, 0xC9, 0x11, 0xCE, 0x86)
            IID_IPropertyBag       = GUID(0x55272A00, 0x42CB, 0x11CE, 0x81, 0x35, 0x00, 0xAA, 0x00, 0x4B, 0xB8, 0x51)

            ole32 = ctypes.windll.ole32
            oleaut32 = ctypes.windll.oleaut32
            ole32.CoInitializeEx(None, 0)
            pDevEnum = ctypes.c_void_p()
            if ole32.CoCreateInstance(ctypes.byref(CLSID_SystemDeviceEnum), None, 1,
                                      ctypes.byref(IID_ICreateDevEnum), ctypes.byref(pDevEnum)) == 0 and pDevEnum:
                vt_dev = ctypes.cast(ctypes.cast(pDevEnum, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
                CreateClassEnum = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p), wintypes.DWORD)(vt_dev[3])
                Release_dev = ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)(vt_dev[2])

                pEnum = ctypes.c_void_p()
                if CreateClassEnum(pDevEnum, ctypes.byref(CLSID_VideoInputDevice), ctypes.byref(pEnum), 0) == 0 and pEnum:
                    vt_enum = ctypes.cast(ctypes.cast(pEnum, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
                    Next = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.ULONG, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.ULONG))(vt_enum[3])
                    Release_enum = ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)(vt_enum[2])

                    idx = 0
                    pMon = ctypes.c_void_p()
                    fetched = wintypes.ULONG()
                    while Next(pEnum, 1, ctypes.byref(pMon), ctypes.byref(fetched)) == 0 and pMon:
                        vt_mon = ctypes.cast(ctypes.cast(pMon, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
                        BindToStorage = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))(vt_mon[9])
                        Release_mon = ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)(vt_mon[2])

                        pBag = ctypes.c_void_p()
                        if BindToStorage(pMon, None, None, ctypes.byref(IID_IPropertyBag), ctypes.byref(pBag)) == 0 and pBag:
                            vt_bag = ctypes.cast(ctypes.cast(pBag, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
                            Read = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(VARIANT), ctypes.c_void_p)(vt_bag[3])
                            Release_bag = ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)(vt_bag[2])

                            var = VARIANT()
                            oleaut32.VariantInit(ctypes.byref(var))
                            name = f"Camera #{idx}"
                            if Read(pBag, "FriendlyName", ctypes.byref(var), None) == 0 and var.bstrVal:
                                name = str(var.bstrVal).strip()
                            oleaut32.VariantClear(ctypes.byref(var))
                            devices.append((idx, name))
                            Release_bag(pBag)
                        Release_mon(pMon)
                        pMon = ctypes.c_void_p()
                        idx += 1
                    Release_enum(pEnum)
                Release_dev(pDevEnum)
        except Exception as e:
            print(f"Aviso enumerando DirectShow: {e}")
    else:
        v4l_base = "/sys/class/video4linux"
        if os.path.isdir(v4l_base):
            ignore_tokens = ("pispbe", "rp1-cfe", "bcm2835", "unicam", "rpi-hevc", "isp")
            for entry in sorted(os.listdir(v4l_base), key=lambda s: int(s.replace("video", "")) if s.startswith("video") and s[5:].isdigit() else 999):
                if not (entry.startswith("video") and entry[5:].isdigit()):
                    continue
                dev_idx = int(entry[5:])
                name_path = os.path.join(v4l_base, entry, "name")
                idx_path = os.path.join(v4l_base, entry, "index")
                try:
                    if os.path.exists(idx_path):
                        with open(idx_path, "r", encoding="utf-8") as fi:
                            if fi.read().strip() != "0":
                                continue
                    with open(name_path, "r", encoding="utf-8") as fn:
                        dev_name = fn.read().strip()
                    if any(tok in dev_name.lower() for tok in ignore_tokens):
                        continue
                    devices.append((dev_idx, dev_name))
                except Exception:
                    continue
    return devices


def find_preferred_usb_camera(preferred_name=USB_CAMERA_TARGET):
    """Localiza el índice exacto de 'USB2.0 PC CAMERA' (o la mejor cámara USB conectada)."""
    devices = list_system_usb_cameras()
    pref_lower = (preferred_name or "USB2.0 PC CAMERA").lower().strip()

    for idx, name in devices:
        if pref_lower in name.lower():
            return idx, name, devices

    for idx, name in devices:
        nl = name.lower()
        if "usb2.0" in nl or "pc camera" in nl or "usb" in nl:
            return idx, name, devices

    for idx, name in devices:
        nl = name.lower()
        if "ir camera" not in nl and "iriun" not in nl:
            return idx, name, devices

    if devices:
        return devices[0][0], devices[0][1], devices
    return 0, preferred_name, []


class ModernCameraApp(ctk.CTk):
    def __init__(self, open_roi_setup_on_start=False):
        super().__init__()
        self.title("HMI INDUSTRIAL - MONITOREO VEHICULAR YOLOv11n")
        init_geo = "480x320" if IS_PI else "880x540"
        self.geometry(init_geo)
        self.minsize(300, 200)
        self.resizable(True, True)
        self.configure(fg_color=C_BG)

        # Tipografía Monospace Dinámica (escala automáticamente según el tamaño de la ventana)
        self.font_s = ctk.CTkFont(family="Consolas", size=8)
        self.font_b = ctk.CTkFont(family="Consolas", size=9, weight="bold")
        self.font_mono = ctk.CTkFont(family="Consolas", size=9, weight="bold")
        self.font_lcd = ctk.CTkFont(family="Consolas", size=11, weight="bold")
        self._current_font_scale = 0

        self._kiosk_mode = ("--kiosk" in sys.argv)
        if IS_PI and self._kiosk_mode:
            self.overrideredirect(True)
            self.geometry("480x320+0+0")

        self.bind("<F11>", lambda e: self._toggle_window_maximize(self, getattr(self, "btn_win_max", None)))
        self.bind("<Escape>", lambda e: self._restore_if_maximized(self, getattr(self, "btn_win_max", None)))

        # Configuración de las Cámaras del Sistema: USB Local + 3 Teléfonos (TEL-1, TEL-2, TEL-3)
        self.usb_cam_state = {"name": USB_CAMERA_TARGET, "index": None, "online": False, "res": "--"}
        self.phone_cameras = {}
        for cid, label, raw_url in (
            (1, "TEL-1 (IDA)", TEL1_URL),
            (2, "TEL-2 (VUELTA)", TEL2_URL),
            (3, "TEL-3 (CRUCE)", TEL3_URL),
        ):
            s_url, shot_url, st_url = _derive_phone_endpoints(raw_url)
            host_lbl = s_url.replace("http://", "").replace("https://", "").split("/")[0]
            self.phone_cameras[cid] = {
                "id": cid, "name": label, "host": host_lbl,
                "url": s_url, "shot": shot_url, "status": st_url,
                "online": False, "latency": 0, "backoff_until": 0.0,
            }
        self.start_time_stamp = time.time()
        self._last_node_probe_ts = 0.0
        self._node_probe_running = False
        self._last_switch_ts = 0.0

        # Fuente inicial por defecto entre las cámaras integradas (TEL-1, TEL-2, TEL-3, USB)
        default_cam = "USB"
        self.camera_source_var    = ctk.StringVar(value=default_cam)
        self.yolo_enabled         = ctk.BooleanVar(value=False)
        self.show_roi_var         = ctk.BooleanVar(value=True)
        self.brightness           = ctk.DoubleVar(value=0)
        self.contrast             = ctk.DoubleVar(value=1.0)

        # Asignación dinámica de fuente de video por semáforo (S1, S2, S3)
        self.sem_source_map       = {1: "TEL-1", 2: "TEL-2", 3: "TEL-3"}
        self._sem_source_vars     = {}
        self._sem_source_menus    = {}
        self._yolo_infer_lock     = threading.Lock()
        self._bg_worker_running   = False

        # Espejos puros de Python (100% thread-safe sin tocar variables Tcl desde hilos secundarios)
        self._ui_alive            = True
        self._active_source       = default_cam
        self._yolo_active         = False
        self._brightness_val      = 0.0
        self._contrast_val        = 1.0
        
        # Controladores de los 3 Semáforos Físicos (S1, S2, S3) — Local GPIO en RPi5 o Puente UDP desde PC
        self.remote_gpio_client   = get_remote_gpio_client()
        self.semaforos_hw         = crear_semaforos_fisicos(enabled=IS_PI, remote_client=self.remote_gpio_client)
        self.semaforo             = self.semaforos_hw[1]
        self.cruce                = ControladorCruceAdaptativo(
            self.semaforo, on_phase_change=self._on_cruce_phase_change, semaforos_hw=self.semaforos_hw
        )
        self._last_gpio_ping_ts   = 0.0
        self.last_inference_time  = 0.0
        
        self.is_connected         = False
        self.init_in_progress     = False
        self.yolo_loading         = False
        self.cap                  = None
        self.http_stream_resp     = None
        self._switch_lock         = threading.Lock()
        self._switch_seq          = 0
        self._stream_session_id   = 0
        self._producer_stop_event = threading.Event()
        self.local_model          = None
        self._last_frame_time     = time.time()
        self.current_fps          = 0.0
        self._latest_raw_frame    = None
        self._latest_frame        = None
        self.frame_queue          = queue.Queue(maxsize=1)
        self.yolo_input_queue     = queue.Queue(maxsize=1)
        self._latest_yolo_boxes   = []
        self._yolo_worker_running = False
        self._yolo_lock           = threading.Lock()
        
        self.metrics_visible      = False
        self.nodes_visible        = False
        self._metrics_win         = None
        self._nodes_win           = None
        self.node_rows            = {}
        
        # FASE 1: Threading Control
        self._stop_producer       = False
        self._producer_thread     = None
        self.frame_dropped_count  = 0
        
        # FASE 2: YOLO Optimization (YOLOv11n)
        self._yolo_imgsz = 160 if IS_PI else 320  # Pi5: 160, Desktop: 320
        self._yolo_inference_count = 0

        # FASE 7: Doble ROI (Parqueo + Movimiento) independiente por fuente de cámara
        self.roi_ref_width = 1920
        self.roi_ref_height = 1080
        self.roi_parqueo_norm = [list(pt) for pt in DEFAULT_ROI_PARQUEO_NORM]
        self.roi_movimiento_norm = [list(pt) for pt in DEFAULT_ROI_MOVIMIENTO_NORM]
        self.rois_by_source = {}
        self._roi_lock = threading.Lock()
        self._load_roi_config()

        # Estado de calibración interactiva por clics sobre el video (None | "parqueo" | "movimiento")
        self._roi_calib_mode = None
        self._roi_calib_points = []
        self._roi_dialog = None
        
        # FASE 3: CSV Buffering I/O
        self._metrics_buffer = []
        self._metrics_buffer_size = 30  # Batch cada 30 registros (~30 segundos)
        self._alerts_buffer = []
        self._alerts_buffer_max = 5  # Batch cada 5 alertas
        
        # FASE 4: Memory Management
        self._current_ctk_image = None
        
        # FASE 5: Resize Optimization (Adaptativo e interactivo al tamaño real de la ventana)
        self.hmi_expanded = False
        self._hmi_split_ratio = 0.38
        self._last_win_size = (0, 0)
        self._resize_after_id = None
        self._target_display_width = 476
        self._target_display_height = 274
        
        # FASE 6: Robustness Logging
        self._frame_stats = {
            "captured": 0,
            "processed": 0,
            "dropped": 0,
            "inference_timeouts": 0
        }

        # Layout Principal: Barra Superior HMI + Cuerpo Elástico
        self.grid_rowconfigure(0, weight=0) # Header Top Bar (Diagnostic Bar)
        self.grid_rowconfigure(1, weight=1) # Main Display Panel & Sidebar
        self.grid_columnconfigure(0, weight=1)

        self._build_top_diagnostic_bar()
        
        # Contenedor central con divisor interactivo (Video | Sash | Control HMI)
        self.main_container = ctk.CTkFrame(self, corner_radius=0, fg_color=C_BG, border_width=0)
        self.main_container.grid(row=1, column=0, sticky="nsew")
        self.main_container.grid_rowconfigure(0, weight=1)
        self.main_container.grid_columnconfigure(0, weight=1)
        self.main_container.grid_columnconfigure(1, weight=0)
        self.main_container.grid_columnconfigure(2, weight=0)

        self._build_video_panel(self.main_container)
        self._build_sash_divider(self.main_container)
        self._build_sidebar(self.main_container)

        # Grip interactivo de redimensionamiento en esquina inferior derecha + eventos de tamaño
        self._main_grip = self._attach_resize_grip(self, min_w=300, min_h=200)
        self.bind("<Configure>", self._on_main_configure)

        # Iniciar en modo Vista de Video en Grande (menú HMI contraído hasta pulsar [CONTROL HMI])
        self._set_hmi_expanded(False)

        self.after(100, self._consume_frame)
        self.after(200, self._tick_semaforo_cruce)
        self.after(1000, self._update_metrics)
        self.after(1000, self._update_hmi_telemetry)
        self.after(50, lambda: threading.Thread(target=self._init_camera, args=(self._active_source,), daemon=True).start())

        if open_roi_setup_on_start:
            self.after(400, self._open_roi_coord_dialog)

    def _ui_call(self, fn):
        """Encola una actualización en el hilo principal de Tkinter de forma 100% thread-safe."""
        if not self._ui_alive:
            return
        try:
            self.after(0, fn)
        except Exception:
            pass

    # ── UTILIDADES DE VENTANAS INTERACTIVAS Y REDIMENSIONABLES ───────────────

    @staticmethod
    def _safe_cursor(widget, cursor_names):
        """Aplica el primer cursor soportado por el gestor gráfico (Windows / X11 / Wayland)."""
        for cname in cursor_names:
            try:
                widget.configure(cursor=cname)
                return
            except Exception:
                continue

    def _toggle_window_maximize(self, win, btn=None):
        """Alterna entre estado maximizado y tamaño libre restaurado en cualquier ventana."""
        is_max = getattr(win, "_is_maximized_custom", False)
        if not is_max:
            win._normal_geometry = win.geometry()
            win._is_maximized_custom = True
            try:
                if platform.system() == "Windows":
                    win.state("zoomed")
                else:
                    win.attributes("-zoomed", True)
            except Exception:
                sw = win.winfo_screenwidth()
                sh = win.winfo_screenheight()
                win.geometry(f"{sw}x{sh}+0+0")
            if btn is not None and btn.winfo_exists():
                btn.configure(text="[ ❐ ]")
        else:
            self._restore_if_maximized(win, btn)

    def _restore_if_maximized(self, win, btn=None):
        """Restaura una ventana si se encontraba maximizada."""
        if not getattr(win, "_is_maximized_custom", False):
            return
        win._is_maximized_custom = False
        try:
            if platform.system() == "Windows":
                win.state("normal")
            else:
                win.attributes("-zoomed", False)
        except Exception:
            pass
        prev_geo = getattr(win, "_normal_geometry", None)
        if prev_geo:
            try:
                win.geometry(prev_geo)
            except Exception:
                pass
        if btn is not None and btn.winfo_exists():
            btn.configure(text="[ ⛶ ]")

    def _attach_resize_grip(self, win, min_w=300, min_h=200):
        """Agrega un control interactivo en la esquina inferior derecha para agrandar/achicar arrastrando."""
        grip = ctk.CTkLabel(
            win, text="◢", width=16, height=16,
            font=self.font_b, text_color=C_ACCENT, fg_color="transparent", corner_radius=0
        )
        self._safe_cursor(grip, ["bottom_right_corner", "size_nw_se", "sizing"])
        grip.place(relx=1.0, rely=1.0, anchor="se", x=-1, y=-1)
        drag = {"sx": 0, "sy": 0, "sw": 0, "sh": 0}

        def _press(evt):
            if getattr(win, "_is_maximized_custom", False):
                self._restore_if_maximized(win)
            drag["sx"] = evt.x_root
            drag["sy"] = evt.y_root
            drag["sw"] = win.winfo_width()
            drag["sh"] = win.winfo_height()
            grip.lift()

        def _motion(evt):
            nw = max(min_w, drag["sw"] + (evt.x_root - drag["sx"]))
            nh = max(min_h, drag["sh"] + (evt.y_root - drag["sy"]))
            try:
                win.geometry(f"{nw}x{nh}")
            except Exception:
                pass

        grip.bind("<ButtonPress-1>", _press)
        grip.bind("<B1-Motion>", _motion)
        return grip

    def _build_sash_divider(self, parent):
        """Barra divisoria vertical arrastrable para ajustar interactivamente el tamaño Video vs Control HMI."""
        self.sash_bar = ctk.CTkFrame(parent, width=5, corner_radius=0, fg_color=C_BORDER)
        self._safe_cursor(self.sash_bar, ["sb_h_double_arrow", "size_we", "h_double_arrow"])

        def _on_enter(_):
            self.sash_bar.configure(fg_color=C_ACCENT)

        def _on_leave(_):
            self.sash_bar.configure(fg_color=C_BORDER)

        def _on_drag(evt):
            if not self.hmi_expanded:
                return
            total_w = max(260, self.main_container.winfo_width())
            rel_x = evt.x_root - self.main_container.winfo_rootx()
            self._hmi_split_ratio = float(np.clip(rel_x / float(total_w), 0.22, 0.78))
            self._apply_hmi_split_weights()
            self._schedule_resize_refresh(delay_ms=15)

        self.sash_bar.bind("<Enter>", _on_enter)
        self.sash_bar.bind("<Leave>", _on_leave)
        self.sash_bar.bind("<B1-Motion>", _on_drag)

    # ── GESTIÓN Y CALIBRACIÓN DE POLÍGONOS ROI (PRE-INICIO Y EN VIVO) ────────

    def _default_roi_entry(self):
        return {
            "reference_resolution": [1920, 1080],
            "parqueo_norm": [list(pt) for pt in DEFAULT_ROI_PARQUEO_NORM],
            "movimiento_norm": [list(pt) for pt in DEFAULT_ROI_MOVIMIENTO_NORM],
        }

    def _apply_source_roi_to_active(self, source_name):
        """Carga en las variables activas las coordenadas ROI propias de `source_name`."""
        with self._roi_lock:
            entry = self.rois_by_source.get(source_name)
            if entry is None:
                entry = self._default_roi_entry()
                self.rois_by_source[source_name] = entry
            ref = entry.get("reference_resolution", [1920, 1080])
            self.roi_ref_width = int(ref[0])
            self.roi_ref_height = int(ref[1])
            self.roi_parqueo_norm = [list(pt) for pt in entry["parqueo_norm"]]
            self.roi_movimiento_norm = [list(pt) for pt in entry["movimiento_norm"]]

    def _load_roi_config(self):
        """Carga las coordenadas normalizadas de los polígonos ROI por cámara desde roi_config.json."""
        try:
            base_ref = [1920, 1080]
            base_pk = [list(pt) for pt in DEFAULT_ROI_PARQUEO_NORM]
            base_mv = [list(pt) for pt in DEFAULT_ROI_MOVIMIENTO_NORM]

            if os.path.exists(ROI_CONFIG_PATH):
                with open(ROI_CONFIG_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                ref = data.get("reference_resolution", base_ref)
                if isinstance(ref, list) and len(ref) == 2 and ref[0] > 0 and ref[1] > 0:
                    base_ref = [int(ref[0]), int(ref[1])]
                pk = data.get("parqueo_norm")
                mv = data.get("movimiento_norm")
                if isinstance(pk, list) and len(pk) >= 3:
                    base_pk = [[float(x), float(y)] for x, y in pk]
                if isinstance(mv, list) and len(mv) >= 3:
                    base_mv = [[float(x), float(y)] for x, y in mv]
                if "roi_enabled" in data:
                    self.show_roi_var.set(bool(data["roi_enabled"]))
                sem_src = data.get("semaforo_sources")
                if isinstance(sem_src, dict):
                    for sid in (1, 2, 3):
                        val = sem_src.get(str(sid))
                        if val in SEM_SOURCE_OPTIONS:
                            self.sem_source_map[sid] = val

                raw_by_src = data.get("rois_by_source")
                for src in ("TEL-1", "TEL-2", "TEL-3", "USB"):
                    src_data = raw_by_src.get(src) if isinstance(raw_by_src, dict) else None
                    if isinstance(src_data, dict):
                        s_ref = src_data.get("reference_resolution", base_ref)
                        s_pk = src_data.get("parqueo_norm", base_pk)
                        s_mv = src_data.get("movimiento_norm", base_mv)
                        self.rois_by_source[src] = {
                            "reference_resolution": [int(s_ref[0]), int(s_ref[1])] if isinstance(s_ref, list) and len(s_ref) == 2 else list(base_ref),
                            "parqueo_norm": [[float(x), float(y)] for x, y in s_pk] if isinstance(s_pk, list) and len(s_pk) >= 3 else [list(pt) for pt in base_pk],
                            "movimiento_norm": [[float(x), float(y)] for x, y in s_mv] if isinstance(s_mv, list) and len(s_mv) >= 3 else [list(pt) for pt in base_mv],
                        }
                    else:
                        self.rois_by_source[src] = {
                            "reference_resolution": list(base_ref),
                            "parqueo_norm": [list(pt) for pt in base_pk],
                            "movimiento_norm": [list(pt) for pt in base_mv],
                        }
            else:
                for src in ("TEL-1", "TEL-2", "TEL-3", "USB"):
                    self.rois_by_source[src] = self._default_roi_entry()

            self._apply_source_roi_to_active(self._active_source)
        except Exception as e:
            print(f"Advertencia cargando roi_config.json: {e}")

    def _save_roi_config(self):
        """Guarda las coordenadas ROI de la cámara activa y de todas las fuentes en roi_config.json."""
        try:
            with self._roi_lock:
                self.rois_by_source[self._active_source] = {
                    "reference_resolution": [self.roi_ref_width, self.roi_ref_height],
                    "parqueo_norm": [[round(x, 4), round(y, 4)] for x, y in self.roi_parqueo_norm],
                    "movimiento_norm": [[round(x, 4), round(y, 4)] for x, y in self.roi_movimiento_norm],
                }
                serialized_by_src = {}
                for src, entry in self.rois_by_source.items():
                    serialized_by_src[src] = {
                        "reference_resolution": list(entry["reference_resolution"]),
                        "parqueo_norm": [[round(x, 4), round(y, 4)] for x, y in entry["parqueo_norm"]],
                        "movimiento_norm": [[round(x, 4), round(y, 4)] for x, y in entry["movimiento_norm"]],
                    }

            data = {
                "reference_resolution": [self.roi_ref_width, self.roi_ref_height],
                "roi_enabled": bool(self.show_roi_var.get()),
                "parqueo_norm": [[round(x, 4), round(y, 4)] for x, y in self.roi_parqueo_norm],
                "movimiento_norm": [[round(x, 4), round(y, 4)] for x, y in self.roi_movimiento_norm],
                "semaforo_sources": {str(k): v for k, v in self.sem_source_map.items()},
                "rois_by_source": serialized_by_src,
            }
            with open(ROI_CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            self._log_terminal("ROI", f"ROI [{self._active_source}] GUARDADA EN roi_config.json")
        except Exception as e:
            self._log_terminal("ERR", f"ERROR GUARDANDO ROI: {e}")

    def _get_scaled_rois(self, width, height, source_name=None):
        """Escala los polígonos normalizados (0..1) de `source_name` (o cámara activa) a (width, height)."""
        if source_name is not None:
            with self._roi_lock:
                entry = self.rois_by_source.get(source_name)
                pk_norm = entry["parqueo_norm"] if entry is not None else self.roi_parqueo_norm
                mv_norm = entry["movimiento_norm"] if entry is not None else self.roi_movimiento_norm
        else:
            pk_norm = self.roi_parqueo_norm
            mv_norm = self.roi_movimiento_norm

        pts_pk = np.array(
            [[int(np.clip(nx * width, 0, width - 1)), int(np.clip(ny * height, 0, height - 1))]
             for nx, ny in pk_norm],
            dtype=np.int32
        )
        pts_mv = np.array(
            [[int(np.clip(nx * width, 0, width - 1)), int(np.clip(ny * height, 0, height - 1))]
             for nx, ny in mv_norm],
            dtype=np.int32
        )
        return pts_pk, pts_mv

    def _toggle_roi_visibility(self):
        state = "VISIBLE" if self.show_roi_var.get() else "OCULTO"
        self._save_roi_config()
        self._log_terminal("ROI", f"POLIGONOS ROI: {state}")

    def _start_interactive_roi_draw(self):
        """
        Activa la delimitación interactiva por clics/toques directamente sobre el panel de video.
        Paso 1: 4 puntos para Zona Parqueo (Azul).
        Paso 2: 4 puntos para Zona Movimiento/Flujo (Verde).
        """
        if self._roi_calib_mode is not None:
            self._roi_calib_mode = None
            self._roi_calib_points.clear()
            self.btn_draw_roi.configure(text="TRAZAR ROI", fg_color=C_BLUE)
            self._log_terminal("ROI", "TRAZADO INTERACTIVO CANCELADO")
            return

        # Maximizar automáticamente la imagen de video para facilitar el trazado de los 4+4 puntos
        if self.hmi_expanded:
            self._set_hmi_expanded(False)

        self.show_roi_var.set(True)
        self._roi_calib_mode = "parqueo"
        self._roi_calib_points = []
        self.btn_draw_roi.configure(text="CANCELAR", fg_color=C_RED)
        self._log_terminal("ROI", "CLIC EN VIDEO: 4 PTS ZONA PARQUEO (AZUL)")
        self._set_status("ROI: MARCA 4 PTS PARQUEO", C_BLUE)

    def _on_video_click(self, event):
        """Captura clics en el visor de video para delimitar los polígonos ROI en vivo."""
        if self._roi_calib_mode is None:
            return

        w_widget = max(1, self.video_label.winfo_width())
        h_widget = max(1, self.video_label.winfo_height())

        pad_x = max(0, (w_widget - self._target_display_width) // 2)
        pad_y = max(0, (h_widget - self._target_display_height) // 2)

        rel_x = (event.x - pad_x) / float(self._target_display_width)
        rel_y = (event.y - pad_y) / float(self._target_display_height)
        rel_x = float(np.clip(rel_x, 0.0, 1.0))
        rel_y = float(np.clip(rel_y, 0.0, 1.0))

        self._roi_calib_points.append([rel_x, rel_y])
        n_pts = len(self._roi_calib_points)

        if self._roi_calib_mode == "parqueo":
            self._log_terminal("ROI", f"PARQUEO PT {n_pts}/4: ({int(rel_x*self.roi_ref_width)},{int(rel_y*self.roi_ref_height)})")
            if n_pts >= 4:
                self.roi_parqueo_norm = [list(p) for p in self._roi_calib_points]
                self._roi_calib_points = []
                self._roi_calib_mode = "movimiento"
                self._log_terminal("ROI", "AHORA MARCA 4 PTS ZONA FLUJO (VERDE)")
                self._set_status("ROI: MARCA 4 PTS FLUJO", C_GREEN)
        elif self._roi_calib_mode == "movimiento":
            self._log_terminal("ROI", f"FLUJO PT {n_pts}/4: ({int(rel_x*self.roi_ref_width)},{int(rel_y*self.roi_ref_height)})")
            if n_pts >= 4:
                self.roi_movimiento_norm = [list(p) for p in self._roi_calib_points]
                self._roi_calib_points = []
                self._roi_calib_mode = None
                self.btn_draw_roi.configure(text="TRAZAR ROI", fg_color=C_BLUE)
                self._save_roi_config()
                self._set_status("ROI ACTUALIZADA", C_GREEN)
                self._log_terminal("ROI", "DOBLE ROI CALIBRADA Y GUARDADA")

    def _reset_default_rois(self):
        """Restaura las coordenadas calibradas originales del estudio Juan_YOLOV11n."""
        self.roi_ref_width = 1920
        self.roi_ref_height = 1080
        self.roi_parqueo_norm = [list(pt) for pt in DEFAULT_ROI_PARQUEO_NORM]
        self.roi_movimiento_norm = [list(pt) for pt in DEFAULT_ROI_MOVIMIENTO_NORM]
        self._roi_calib_mode = None
        self._roi_calib_points.clear()
        if hasattr(self, "btn_draw_roi"):
            self.btn_draw_roi.configure(text="TRAZAR ROI", fg_color=C_BLUE)
        self._save_roi_config()
        self._log_terminal("ROI", "ROIs RESTAURADAS A VALORES POR DEFECTO")

    def _open_roi_coord_dialog(self):
        """
        Abre una ventana independiente, interactiva y totalmente redimensionable para visualizar,
        arrastrar y editar las coordenadas de los polígonos ROI (Parqueo y Flujo).
        """
        if self._roi_dialog is not None and self._roi_dialog.winfo_exists():
            try:
                self._roi_dialog.deiconify()
                self._roi_dialog.lift()
                self._roi_dialog.focus_force()
            except Exception:
                pass
            return

        dlg = ctk.CTkToplevel(self)
        dlg.title(f"CONFIGURADOR ROI [{self._active_source}] — VENTANA REDIMENSIONABLE")
        parent_w = max(480, self.winfo_width())
        parent_h = max(320, self.winfo_height())
        init_w = min(max(460, int(parent_w * 0.90)), max(480, dlg.winfo_screenwidth() - 40))
        init_h = min(max(300, int(parent_h * 0.90)), max(320, dlg.winfo_screenheight() - 60))
        pos_x = max(0, self.winfo_x() + (parent_w - init_w) // 2)
        pos_y = max(0, self.winfo_y() + (parent_h - init_h) // 2)
        dlg.geometry(f"{init_w}x{init_h}+{pos_x}+{pos_y}")
        dlg.minsize(340, 220)
        dlg.resizable(True, True)
        dlg.configure(fg_color=C_BG)

        if IS_PI and self._kiosk_mode:
            dlg.overrideredirect(True)
        else:
            try:
                dlg.transient(self)
            except Exception:
                pass
        dlg.lift()
        dlg.focus_force()
        self._roi_dialog = dlg

        # Barra superior propia con arrastrar, maximizar/restaurar y cerrar
        top_hdr = ctk.CTkFrame(dlg, height=26, corner_radius=0, fg_color=C_ACCENT2, border_width=1, border_color=C_BORDER)
        top_hdr.pack(fill="x", side="top")
        top_hdr.pack_propagate(False)

        ctk.CTkLabel(
            top_hdr, text="◈ CONFIGURACIÓN Y VISTA PREVIA ROI",
            font=self.font_b, text_color=C_TEXT
        ).pack(side="left", padx=8)

        ctk.CTkButton(
            top_hdr, text="[X] CERRAR", width=68, height=20,
            font=self.font_b, fg_color=C_RED, hover_color="#B71C1C",
            corner_radius=0, command=dlg.destroy
        ).pack(side="right", padx=2, pady=2)

        btn_dlg_max = ctk.CTkButton(
            top_hdr, text="[ ⛶ ]", width=42, height=20,
            font=self.font_b, fg_color="#334155", hover_color=C_ACCENT,
            corner_radius=0
        )
        btn_dlg_max.configure(command=lambda: self._toggle_window_maximize(dlg, btn_dlg_max))
        btn_dlg_max.pack(side="right", padx=2, pady=2)

        drag_state = {"x": 0, "y": 0}
        def _start_win_drag(evt):
            drag_state["x"] = evt.x
            drag_state["y"] = evt.y
        def _do_win_drag(evt):
            if getattr(dlg, "_is_maximized_custom", False):
                return
            try:
                nx = dlg.winfo_x() + (evt.x - drag_state["x"])
                ny = dlg.winfo_y() + (evt.y - drag_state["y"])
                dlg.geometry(f"+{nx}+{ny}")
            except Exception:
                pass
        top_hdr.bind("<ButtonPress-1>", _start_win_drag)
        top_hdr.bind("<B1-Motion>", _do_win_drag)
        top_hdr.bind("<Double-Button-1>", lambda e: self._toggle_window_maximize(dlg, btn_dlg_max))

        # Cuerpo central elástico dividido con barra arrastrable:
        #   Col 0: Lienzo Visual Interactivo (escala con la ventana)
        #   Col 1: Divisor vertical arrastrable (sash interactivo)
        #   Col 2: Panel desplazable con Tablas de Coordenadas y Botones (escala con la ventana)
        body_frame = ctk.CTkFrame(dlg, fg_color="transparent")
        body_frame.pack(fill="both", expand=True, padx=6, pady=(4, 8))
        body_frame.grid_rowconfigure(0, weight=1)

        roi_split = {"ratio": 0.46}
        def _apply_roi_split():
            w0 = max(20, int(round(roi_split["ratio"] * 100)))
            w2 = max(20, 100 - w0)
            body_frame.grid_columnconfigure(0, weight=w0, uniform="roi_split", minsize=120)
            body_frame.grid_columnconfigure(1, weight=0, uniform="", minsize=5)
            body_frame.grid_columnconfigure(2, weight=w2, uniform="roi_split", minsize=160)
        _apply_roi_split()

        # ── Panel Izquierdo: Lienzo Visual Interactivo Elástico ──
        preview_box = ctk.CTkFrame(body_frame, fg_color="#080810", corner_radius=0, border_width=1, border_color=C_ACCENT, width=60, height=60)
        preview_box.grid(row=0, column=0, sticky="nsew")
        preview_box.grid_propagate(False)
        preview_box.grid_rowconfigure(1, weight=1)
        preview_box.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            preview_box, text="VISTA INTERACTIVA (P1..P4 / F1..F4)",
            font=self.font_s, text_color=C_ACCENT
        ).grid(row=0, column=0, pady=(3, 1), padx=2)

        preview_canvas = tk.Canvas(preview_box, bg="#0A0A14", highlightthickness=0, width=80, height=80, cursor="crosshair")
        preview_canvas.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)

        # ── Divisor Central Arrastrable ──
        dlg_sash = ctk.CTkFrame(body_frame, width=5, corner_radius=0, fg_color=C_BORDER)
        dlg_sash.grid(row=0, column=1, sticky="ns", padx=2)
        self._safe_cursor(dlg_sash, ["sb_h_double_arrow", "size_we", "h_double_arrow"])
        dlg_sash.bind("<Enter>", lambda _: dlg_sash.configure(fg_color=C_ACCENT))
        dlg_sash.bind("<Leave>", lambda _: dlg_sash.configure(fg_color=C_BORDER))
        def _on_dlg_sash_drag(evt):
            tw = max(280, body_frame.winfo_width())
            rx = evt.x_root - body_frame.winfo_rootx()
            roi_split["ratio"] = float(np.clip(rx / float(tw), 0.22, 0.78))
            _apply_roi_split()
        dlg_sash.bind("<B1-Motion>", _on_dlg_sash_drag)

        # ── Panel Derecho Elástico con Barra de Desplazamiento ──
        right_scroll = ctk.CTkScrollableFrame(
            body_frame, corner_radius=0, fg_color=C_PANEL,
            border_width=1, border_color=C_BORDER, width=60, height=60,
            scrollbar_button_color=C_ACCENT,
            scrollbar_button_hover_color=C_ACCENT2
        )
        right_scroll.grid(row=0, column=2, sticky="nsew")
        right_scroll.grid_columnconfigure(0, weight=1)

        # 1. Resolución Base dentro del área desplazable
        ref_frame = ctk.CTkFrame(right_scroll, fg_color="#10101C", corner_radius=0, border_width=1, border_color=C_BORDER)
        ref_frame.grid(row=0, column=0, sticky="ew", padx=2, pady=(2, 4))
        ctk.CTkLabel(ref_frame, text="Resolución Base:", font=self.font_s, text_color=C_MUTED).pack(side="left", padx=(6, 4), pady=3)
        ent_ref_w = ctk.CTkEntry(ref_frame, width=54, height=22, font=self.font_s, corner_radius=0)
        ent_ref_w.pack(side="left", padx=2, pady=3, fill="x", expand=True)
        ent_ref_w.insert(0, str(self.roi_ref_width))
        ctk.CTkLabel(ref_frame, text="x", font=self.font_s, text_color=C_TEXT).pack(side="left", padx=2)
        ent_ref_h = ctk.CTkEntry(ref_frame, width=54, height=22, font=self.font_s, corner_radius=0)
        ent_ref_h.pack(side="left", padx=2, pady=3, fill="x", expand=True)
        ent_ref_h.insert(0, str(self.roi_ref_height))

        # 2. Tablas de Coordenadas X, Y elásticas
        tables_frame = ctk.CTkFrame(right_scroll, fg_color="transparent")
        tables_frame.grid(row=1, column=0, sticky="ew", padx=2, pady=2)
        tables_frame.grid_columnconfigure((0, 1), weight=1)

        pk_entries = []
        mv_entries = []
        corner_names = ["1.Sup-Izq", "2.Sup-Der", "3.Inf-Der", "4.Inf-Izq"]

        box_pk = ctk.CTkFrame(tables_frame, fg_color="#121828", corner_radius=0, border_width=1, border_color=C_BLUE)
        box_pk.grid(row=0, column=0, padx=(0, 2), sticky="nsew")
        box_pk.grid_columnconfigure((1, 2), weight=1)
        ctk.CTkLabel(box_pk, text="PARQUEO (AZUL)", font=self.font_b, text_color=C_BLUE).grid(row=0, column=0, columnspan=3, pady=(3, 1), padx=2)
        ctk.CTkLabel(box_pk, text="Vért.", font=self.font_s, text_color=C_MUTED).grid(row=1, column=0, padx=2)
        ctk.CTkLabel(box_pk, text="X", font=self.font_s, text_color=C_MUTED).grid(row=1, column=1, padx=2)
        ctk.CTkLabel(box_pk, text="Y", font=self.font_s, text_color=C_MUTED).grid(row=1, column=2, padx=2)

        for i, cname in enumerate(corner_names):
            ctk.CTkLabel(box_pk, text=cname, font=self.font_s, text_color=C_TEXT).grid(row=i+2, column=0, padx=2, pady=2, sticky="w")
            ex = ctk.CTkEntry(box_pk, width=38, height=22, font=self.font_s, corner_radius=0)
            ey = ctk.CTkEntry(box_pk, width=38, height=22, font=self.font_s, corner_radius=0)
            ex.grid(row=i+2, column=1, padx=2, pady=2, sticky="ew")
            ey.grid(row=i+2, column=2, padx=2, pady=2, sticky="ew")
            px = int(round(self.roi_parqueo_norm[i][0] * self.roi_ref_width))
            py = int(round(self.roi_parqueo_norm[i][1] * self.roi_ref_height))
            ex.insert(0, str(px))
            ey.insert(0, str(py))
            pk_entries.append((ex, ey))

        box_mv = ctk.CTkFrame(tables_frame, fg_color="#122218", corner_radius=0, border_width=1, border_color=C_GREEN)
        box_mv.grid(row=0, column=1, padx=(2, 0), sticky="nsew")
        box_mv.grid_columnconfigure((1, 2), weight=1)
        ctk.CTkLabel(box_mv, text="FLUJO (VERDE)", font=self.font_b, text_color=C_GREEN).grid(row=0, column=0, columnspan=3, pady=(3, 1), padx=2)
        ctk.CTkLabel(box_mv, text="Vért.", font=self.font_s, text_color=C_MUTED).grid(row=1, column=0, padx=2)
        ctk.CTkLabel(box_mv, text="X", font=self.font_s, text_color=C_MUTED).grid(row=1, column=1, padx=2)
        ctk.CTkLabel(box_mv, text="Y", font=self.font_s, text_color=C_MUTED).grid(row=1, column=2, padx=2)

        for i, cname in enumerate(corner_names):
            ctk.CTkLabel(box_mv, text=cname, font=self.font_s, text_color=C_TEXT).grid(row=i+2, column=0, padx=2, pady=2, sticky="w")
            ex = ctk.CTkEntry(box_mv, width=38, height=22, font=self.font_s, corner_radius=0)
            ey = ctk.CTkEntry(box_mv, width=38, height=22, font=self.font_s, corner_radius=0)
            ex.grid(row=i+2, column=1, padx=2, pady=2, sticky="ew")
            ey.grid(row=i+2, column=2, padx=2, pady=2, sticky="ew")
            mx = int(round(self.roi_movimiento_norm[i][0] * self.roi_ref_width))
            my = int(round(self.roi_movimiento_norm[i][1] * self.roi_ref_height))
            ex.insert(0, str(mx))
            ey.insert(0, str(my))
            mv_entries.append((ex, ey))

        lbl_msg = ctk.CTkLabel(
            right_scroll, text="Redimensiona la ventana o arrastra el divisor central libremente",
            font=self.font_s, text_color=C_MUTED
        )
        lbl_msg.grid(row=2, column=0, pady=(4, 2), sticky="w", padx=4)

        # Estado local de vista previa interactiva con preservación de relación de aspecto
        preview_state = {"bg_photo": None, "dragging": None, "viewport": (0, 0, 160, 120)}

        def _read_norm_from_entries():
            rw = max(1.0, float(ent_ref_w.get().strip() or self.roi_ref_width))
            rh = max(1.0, float(ent_ref_h.get().strip() or self.roi_ref_height))
            npk = []
            nmv = []
            for ex, ey in pk_entries:
                xv = float(ex.get().strip()) / rw
                yv = float(ey.get().strip()) / rh
                npk.append([float(np.clip(xv, 0.0, 1.0)), float(np.clip(yv, 0.0, 1.0))])
            for ex, ey in mv_entries:
                xv = float(ex.get().strip()) / rw
                yv = float(ey.get().strip()) / rh
                nmv.append([float(np.clip(xv, 0.0, 1.0)), float(np.clip(yv, 0.0, 1.0))])
            return rw, rh, npk, nmv

        def _redraw_preview(_=None):
            if not dlg.winfo_exists():
                return
            cw = max(80, preview_canvas.winfo_width())
            ch = max(80, preview_canvas.winfo_height())
            preview_canvas.delete("all")

            src_img = self._latest_raw_frame if self._latest_raw_frame is not None else self._latest_frame
            if src_img is not None:
                sh, sw = src_img.shape[:2]
                scale = min(cw / float(max(1, sw)), ch / float(max(1, sh)))
                dw = max(40, int(sw * scale))
                dh = max(30, int(sh * scale))
                ox = (cw - dw) // 2
                oy = (ch - dh) // 2
                preview_state["viewport"] = (ox, oy, dw, dh)
                try:
                    rgb = cv2.cvtColor(cv2.resize(src_img, (dw, dh), interpolation=cv2.INTER_LINEAR), cv2.COLOR_BGR2RGB)
                    preview_state["bg_photo"] = ImageTk.PhotoImage(Image.fromarray(rgb))
                    preview_canvas.create_image(ox, oy, image=preview_state["bg_photo"], anchor="nw")
                except Exception:
                    pass
            else:
                preview_state["viewport"] = (0, 0, cw, ch)
                ox, oy, dw, dh = 0, 0, cw, ch
                step = max(20, min(cw, ch) // 8)
                for gx in range(0, cw, step):
                    preview_canvas.create_line(gx, 0, gx, ch, fill="#1E293B")
                for gy in range(0, ch, step):
                    preview_canvas.create_line(0, gy, cw, gy, fill="#1E293B")

            try:
                _, _, npk, nmv = _read_norm_from_entries()
            except Exception:
                return

            ox, oy, dw, dh = preview_state["viewport"]
            r_handle = max(4, min(8, min(dw, dh) // 45))

            # Dibujar Polígono Parqueo (Azul)
            pk_coords = []
            for nx, ny in npk:
                pk_coords.extend([ox + nx * dw, oy + ny * dh])
            if len(pk_coords) >= 6:
                preview_canvas.create_polygon(*pk_coords, outline="#38BDF8", fill="", width=2)
                for idx_p, (nx, ny) in enumerate(npk):
                    cx, cy = ox + nx * dw, oy + ny * dh
                    preview_canvas.create_oval(cx - r_handle, cy - r_handle, cx + r_handle, cy + r_handle, fill="#38BDF8", outline="#FFFFFF")
                    preview_canvas.create_text(cx + 10, cy - 7, text=f"P{idx_p+1}", fill="#38BDF8", font=("Consolas", 8, "bold"))

            # Dibujar Polígono Flujo (Verde)
            mv_coords = []
            for nx, ny in nmv:
                mv_coords.extend([ox + nx * dw, oy + ny * dh])
            if len(mv_coords) >= 6:
                preview_canvas.create_polygon(*mv_coords, outline="#22C55E", fill="", width=2)
                for idx_m, (nx, ny) in enumerate(nmv):
                    cx, cy = ox + nx * dw, oy + ny * dh
                    preview_canvas.create_oval(cx - r_handle, cy - r_handle, cx + r_handle, cy + r_handle, fill="#22C55E", outline="#FFFFFF")
                    preview_canvas.create_text(cx + 10, cy + 7, text=f"F{idx_m+1}", fill="#22C55E", font=("Consolas", 8, "bold"))

        def _on_canvas_press(evt):
            try:
                _, _, npk, nmv = _read_norm_from_entries()
            except Exception:
                return
            ox, oy, dw, dh = preview_state["viewport"]
            best = None
            best_d = 22.0
            for i, (nx, ny) in enumerate(npk):
                d = float(np.hypot(evt.x - (ox + nx * dw), evt.y - (oy + ny * dh)))
                if d < best_d:
                    best_d = d
                    best = ("pk", i)
            for i, (nx, ny) in enumerate(nmv):
                d = float(np.hypot(evt.x - (ox + nx * dw), evt.y - (oy + ny * dh)))
                if d < best_d:
                    best_d = d
                    best = ("mv", i)
            preview_state["dragging"] = best

        def _on_canvas_drag(evt):
            target = preview_state.get("dragging")
            if target is None:
                return
            ox, oy, dw, dh = preview_state["viewport"]
            try:
                rw = max(1.0, float(ent_ref_w.get().strip() or self.roi_ref_width))
                rh = max(1.0, float(ent_ref_h.get().strip() or self.roi_ref_height))
            except Exception:
                rw, rh = float(self.roi_ref_width), float(self.roi_ref_height)

            nx = float(np.clip((evt.x - ox) / float(max(1, dw)), 0.0, 1.0))
            ny = float(np.clip((evt.y - oy) / float(max(1, dh)), 0.0, 1.0))
            px_val = int(round(nx * rw))
            py_val = int(round(ny * rh))

            kind, idx = target
            ex, ey = (pk_entries[idx] if kind == "pk" else mv_entries[idx])
            ex.delete(0, "end")
            ex.insert(0, str(px_val))
            ey.delete(0, "end")
            ey.insert(0, str(py_val))
            _redraw_preview()

        def _on_canvas_release(_):
            preview_state["dragging"] = None

        preview_canvas.bind("<Configure>", _redraw_preview)
        preview_canvas.bind("<ButtonPress-1>", _on_canvas_press)
        preview_canvas.bind("<B1-Motion>", _on_canvas_drag)
        preview_canvas.bind("<ButtonRelease-1>", _on_canvas_release)
        for ex, ey in pk_entries + mv_entries:
            ex.bind("<KeyRelease>", _redraw_preview)
            ey.bind("<KeyRelease>", _redraw_preview)

        def _live_preview_tick():
            if not dlg.winfo_exists():
                return
            if preview_state.get("dragging") is None and self._latest_raw_frame is not None:
                _redraw_preview()
            dlg.after(350, _live_preview_tick)

        def apply_coords():
            try:
                rw, rh, new_pk, new_mv = _read_norm_from_entries()
                self.roi_ref_width = int(rw)
                self.roi_ref_height = int(rh)
                self.roi_parqueo_norm = new_pk
                self.roi_movimiento_norm = new_mv
                self.show_roi_var.set(True)
                self._save_roi_config()
                self._schedule_resize_refresh(delay_ms=10)
                dlg.destroy()
            except Exception as err:
                lbl_msg.configure(text=f"Error en formato numérico: {err}", text_color=C_RED)

        def start_click_mode_from_dlg():
            dlg.destroy()
            self._start_interactive_roi_draw()

        def reset_from_dlg():
            self._reset_default_rois()
            dlg.destroy()

        # 3. Controles de Acción elásticos (2x2 botones)
        btn_bar = ctk.CTkFrame(right_scroll, fg_color="#10101C", corner_radius=0, border_width=1, border_color=C_BORDER)
        btn_bar.grid(row=3, column=0, sticky="ew", padx=2, pady=(4, 8))
        btn_bar.grid_columnconfigure((0, 1), weight=1)

        ctk.CTkButton(btn_bar, text="GUARDAR ROI", height=28, font=self.font_b,
                      fg_color=C_GREEN, text_color="#000000", hover_color="#16A34A",
                      corner_radius=4, command=apply_coords).grid(row=0, column=0, padx=4, pady=(6, 3), sticky="ew")
        ctk.CTkButton(btn_bar, text="TRAZAR EN VIDEO", height=28, font=self.font_b,
                      fg_color=C_BLUE, text_color="#000000", hover_color="#0284C7",
                      corner_radius=4, command=start_click_mode_from_dlg).grid(row=0, column=1, padx=4, pady=(6, 3), sticky="ew")
        ctk.CTkButton(btn_bar, text="POR DEFECTO", height=28, font=self.font_b,
                      fg_color="#334155", hover_color="#475569",
                      corner_radius=4, command=reset_from_dlg).grid(row=1, column=0, padx=4, pady=(3, 6), sticky="ew")
        ctk.CTkButton(btn_bar, text="CERRAR VENTANA", height=28, font=self.font_b,
                      fg_color=C_RED, hover_color="#B71C1C",
                      corner_radius=4, command=dlg.destroy).grid(row=1, column=1, padx=4, pady=(3, 6), sticky="ew")

        self._attach_resize_grip(dlg, min_w=340, min_h=220)
        dlg.after(120, _redraw_preview)
        dlg.after(400, _live_preview_tick)

    # ── BARRA SUPERIOR DE DIAGNÓSTICO INDUSTRIAL & RESIZE RESPONSIVO ─────────

    def _build_top_diagnostic_bar(self):
        self.top_bar = ctk.CTkFrame(self, height=26, corner_radius=0, fg_color=C_PANEL,
                                    border_width=1, border_color=C_BORDER)
        self.top_bar.grid(row=0, column=0, sticky="ew")
        self.top_bar.grid_propagate(False)
        self.top_bar.grid_rowconfigure(0, weight=1)
        self.top_bar.grid_columnconfigure(4, weight=1)

        # 1. Estado de Conexión SYS OK LED
        self.lbl_sys_led = ctk.CTkLabel(
            self.top_bar, text="● SYS_OK", font=self.font_b,
            text_color=C_GREEN, corner_radius=0)
        self.lbl_sys_led.grid(row=0, column=0, padx=(6, 8), sticky="w")

        # 2. Uptime
        self.lbl_uptime = ctk.CTkLabel(
            self.top_bar, text="UPTIME: 00:00:00", font=self.font_s,
            text_color=C_TEXT, corner_radius=0)
        self.lbl_uptime.grid(row=0, column=1, padx=6, sticky="w")

        # 3. Latencia
        self.lbl_ping = ctk.CTkLabel(
            self.top_bar, text="PING: 11ms", font=self.font_s,
            text_color=C_MUTED, corner_radius=0)
        self.lbl_ping.grid(row=0, column=2, padx=6, sticky="w")

        # 4. Red IP AP / Modo Local
        init_net_txt = "NET: 192.168.4.1 AP" if IS_PI else f"GPIO: {self.remote_gpio_client.host}"
        self.lbl_net_ip = ctk.CTkLabel(
            self.top_bar, text=init_net_txt, font=self.font_s,
            text_color=C_ACCENT, corner_radius=0)
        self.lbl_net_ip.grid(row=0, column=3, padx=4, sticky="w")

        # 5. Botón Interactivo [CONTROL HMI] para alternar entre Video Grande y Menú Expandido
        self.btn_top_hmi = ctk.CTkButton(
            self.top_bar, text="[ CONTROL HMI ]", width=105, height=20,
            font=self.font_b, fg_color=C_ACCENT, hover_color=C_ACCENT2,
            command=self._toggle_hmi_panel, corner_radius=2)
        self.btn_top_hmi.grid(row=0, column=5, sticky="e", padx=3)

        # 6. Botón Maximizar / Restaurar Ventana [ ⛶ ]
        self.btn_win_max = ctk.CTkButton(
            self.top_bar, text="[ ⛶ ]", width=38, height=20,
            font=self.font_b, fg_color="#334155", hover_color=C_ACCENT2,
            command=lambda: self._toggle_window_maximize(self, self.btn_win_max), corner_radius=2)
        self.btn_win_max.grid(row=0, column=6, sticky="e", padx=2)

        # 7. Botón de Cierre en modo Kiosk o acceso rápido
        if IS_PI and self._kiosk_mode:
            ctk.CTkButton(self.top_bar, text="[X] SALIR", width=56, height=20,
                          font=self.font_b, fg_color=C_RED, hover_color="#B71C1C",
                          command=self.on_closing, corner_radius=0
                          ).grid(row=0, column=7, sticky="e", padx=2)

        self.top_bar.bind("<Double-Button-1>", lambda e: self._toggle_window_maximize(self, self.btn_win_max))

    def _toggle_hmi_panel(self):
        """Alterna entre Vista de Video en Grande y Menú CONTROL HMI Expandido."""
        self._set_hmi_expanded(not self.hmi_expanded)

    def _apply_hmi_split_weights(self):
        """Aplica proporciones dinámicas entre el Panel de Video y el Panel CONTROL HMI."""
        if self.hmi_expanded:
            w0 = max(20, int(round(self._hmi_split_ratio * 100)))
            w2 = max(20, 100 - w0)
            self.main_container.grid_columnconfigure(0, weight=w0, uniform="hmi_split", minsize=100)
            self.main_container.grid_columnconfigure(1, weight=0, uniform="", minsize=5)
            self.main_container.grid_columnconfigure(2, weight=w2, uniform="hmi_split", minsize=150)
        else:
            self.main_container.grid_columnconfigure(0, weight=1, uniform="", minsize=120)
            self.main_container.grid_columnconfigure(1, weight=0, uniform="", minsize=0)
            self.main_container.grid_columnconfigure(2, weight=0, uniform="", minsize=0)

    def _set_hmi_expanded(self, expanded: bool):
        """
        Configura dinámicamente la distribución elástica de la ventana:
        - expanded=False: El video ocupa el 100% de la ventana actual (pequeña o grande).
        - expanded=True: División interactiva proporcional (ajustable con divisor arrastrable)
          entre el panel de video a la izquierda y el menú [CONTROL HMI] a la derecha.
        """
        self.hmi_expanded = bool(expanded)
        if self.hmi_expanded:
            self.left_mini_card.grid(row=1, column=0, sticky="ew", padx=3, pady=(2, 2))
            self.sash_bar.grid(row=0, column=1, sticky="ns")
            self.sb_container.grid(row=0, column=2, sticky="nsew")
            self._apply_hmi_split_weights()
            if hasattr(self, "btn_top_hmi"):
                self.btn_top_hmi.configure(text="[ ◀ VIDEO GRANDE ]", fg_color="#334155")
        else:
            self.left_mini_card.grid_remove()
            self.sash_bar.grid_remove()
            self.sb_container.grid_remove()
            self._apply_hmi_split_weights()
            if hasattr(self, "btn_top_hmi"):
                self.btn_top_hmi.configure(text="[ CONTROL HMI ]", fg_color=C_ACCENT)

        self._schedule_resize_refresh(delay_ms=20)

    def _on_main_configure(self, event):
        """Detecta cambios en el tamaño de la ventana principal y adapta controles, fuentes y video."""
        if event.widget is not self:
            return
        new_size = (event.width, event.height)
        if new_size == self._last_win_size or event.width < 100 or event.height < 100:
            return
        self._last_win_size = new_size
        self._schedule_resize_refresh(delay_ms=25)

    def _schedule_resize_refresh(self, delay_ms=25):
        if self._resize_after_id is not None:
            try:
                self.after_cancel(self._resize_after_id)
            except Exception:
                pass
        self._resize_after_id = self.after(delay_ms, self._perform_responsive_layout)

    def _perform_responsive_layout(self):
        """Ajusta fuentes, visibilidad de barra superior, altura de consola y re-escala el frame activo."""
        self._resize_after_id = None
        w = max(300, self.winfo_width())
        h = max(200, self.winfo_height())

        # 1. Escalar tipografía suavemente según dimensiones de la ventana
        scale_factor = min(w / 480.0, h / 320.0)
        extra_pt = int(np.clip((scale_factor - 1.0) * 3.2, 0, 6))
        if extra_pt != self._current_font_scale:
            self._current_font_scale = extra_pt
            self.font_s.configure(size=8 + extra_pt)
            self.font_b.configure(size=9 + extra_pt)
            self.font_mono.configure(size=9 + extra_pt)
            self.font_lcd.configure(size=11 + int(extra_pt * 1.3))
            top_h = max(24, 24 + extra_pt * 2)
            self.top_bar.configure(height=top_h)
            self.bottom_bar.configure(height=max(20, 20 + extra_pt * 2))

        # 2. Barra superior adaptativa (oculta etiquetas secundarias en ventanas muy pequeñas)
        if w < 430:
            self.lbl_net_ip.grid_remove()
            self.lbl_ping.grid_remove()
            if w < 350:
                self.lbl_uptime.grid_remove()
            else:
                self.lbl_uptime.grid()
        elif w < 540:
            self.lbl_uptime.grid()
            self.lbl_ping.grid()
            self.lbl_net_ip.grid_remove()
        else:
            self.lbl_uptime.grid()
            self.lbl_ping.grid()
            self.lbl_net_ip.grid()

        # 3. Expandir altura de TERMINAL LOGS cuando la ventana es más alta
        if hasattr(self, "term_box"):
            dyn_term_h = max(68, int(h * 0.26))
            self.term_box.configure(height=dyn_term_h)

        # 4. Ajustar wraplength del texto de estado de video y re-escalar el último frame al instante
        avail_vw = max(100, self.video_label.winfo_width() - 16)
        try:
            self.video_label.configure(wraplength=avail_vw)
        except Exception:
            pass

        if self._latest_raw_frame is not None:
            self._render_frame_to_display(self._latest_raw_frame)

        if hasattr(self, "_main_grip") and self._main_grip.winfo_exists():
            self._main_grip.lift()

    # ── UI PRINCIPAL: PANEL DE VIDEO & TELEMETRÍA ─────────────────────

    def _build_video_panel(self, parent):
        self.video_frame = ctk.CTkFrame(parent, corner_radius=0, fg_color="black",
                                        border_width=1, border_color=C_ACCENT, width=50, height=50)
        self.video_frame.grid(row=0, column=0, sticky="nsew")
        self.video_frame.grid_propagate(False)
        self.video_frame.grid_rowconfigure(0, weight=1)
        self.video_frame.grid_rowconfigure(1, weight=0)
        self.video_frame.grid_rowconfigure(2, weight=0)
        self.video_frame.grid_columnconfigure(0, weight=1)

        self.video_label = ctk.CTkLabel(
            self.video_frame, text="[ INICIALIZANDO MONITOR DE HARDWARE HMI ]",
            font=self.font_lcd, text_color=C_MUTED, corner_radius=0)
        self.video_label.grid(row=0, column=0, sticky="nsew")
        self.video_label.bind("<Button-1>", self._on_video_click)

        # Tarjeta compacta debajo del recuadro izquierdo de video (visible cuando CONTROL HMI está desplegado)
        self.left_mini_card = ctk.CTkFrame(
            self.video_frame, corner_radius=0, fg_color="#121222",
            border_width=1, border_color=C_BORDER)
        self.left_mini_card.grid_columnconfigure(0, weight=1)

        self.lbl_mini_semaforo = ctk.CTkLabel(
            self.left_mini_card, text="[ VERDE: LISTO ]",
            font=self.font_b, text_color=C_GREEN, corner_radius=0)
        self.lbl_mini_semaforo.grid(row=0, column=0, padx=4, pady=(3, 1))

        self.lbl_mini_count = ctk.CTkLabel(
            self.left_mini_card, text="ROJO: 0 veh | PK: 0\nVERDE: 25s",
            font=self.font_s, text_color=C_TEXT, corner_radius=0, justify="center")
        self.lbl_mini_count.grid(row=1, column=0, padx=4, pady=(0, 4))

        # Barra inferior de estado (Conexión + FPS)
        self.bottom_bar = ctk.CTkFrame(self.video_frame, height=20,
                                       corner_radius=0, fg_color=C_PANEL,
                                       border_width=1, border_color=C_BORDER)
        self.bottom_bar.grid(row=2, column=0, sticky="ew")
        self.bottom_bar.grid_propagate(False)
        self.bottom_bar.grid_columnconfigure(1, weight=1)

        self.status_badge = ctk.CTkLabel(
            self.bottom_bar, text=" [ SIN SEÑAL ] ",
            font=self.font_s, text_color=C_MUTED, corner_radius=0)
        self.status_badge.grid(row=0, column=0, padx=4, sticky="w")

        self.fps_badge = ctk.CTkLabel(
            self.bottom_bar, text=" -- FPS ",
            font=self.font_s, text_color=C_MUTED, corner_radius=0)
        self.fps_badge.grid(row=0, column=2, padx=4, sticky="e")

    def _build_sidebar(self, parent):
        self.sb_container = ctk.CTkFrame(parent, corner_radius=0, fg_color=C_PANEL, width=50, height=50,
                                         border_width=1, border_color=C_BORDER)
        self.sb_container.grid(row=0, column=2, sticky="nsew")
        self.sb_container.grid_propagate(False)
        self.sb_container.grid_rowconfigure(1, weight=1)
        self.sb_container.grid_columnconfigure(0, weight=1)

        # Header de la Barra Lateral Expandida
        header = ctk.CTkFrame(self.sb_container, corner_radius=0, fg_color=C_ACCENT2, height=22)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text="◈ PANEL DE CONTROL HMI", font=self.font_b,
                     text_color=C_TEXT, corner_radius=0).grid(row=0, column=0, sticky="w", padx=8, pady=2)

        sb = ctk.CTkScrollableFrame(self.sb_container, corner_radius=0, fg_color="transparent",
                                    scrollbar_button_color=C_ACCENT2,
                                    scrollbar_button_hover_color=C_ACCENT)
        sb.grid(row=1, column=0, sticky="nsew")
        sb.grid_columnconfigure(0, weight=1)

        r = 0

        # ─── BLOQUE 1: Selección de Fuente (Ancho Elástico) ───
        src_row = ctk.CTkFrame(sb, fg_color="#121222", corner_radius=0, border_width=1, border_color=C_BORDER)
        src_row.grid(row=r, column=0, padx=4, pady=(4, 4), sticky="ew"); r += 1
        src_row.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(src_row, text="FUENTE VIDEO:", font=self.font_b, text_color=C_MUTED, corner_radius=0).grid(row=0, column=0, sticky="w", padx=6, pady=4)
        self.camera_source_menu = ctk.CTkOptionMenu(
            src_row, values=["TEL-1", "TEL-2", "TEL-3", "USB"],
            variable=self.camera_source_var,
            command=self._switch_camera,
            font=self.font_b, width=100, height=22,
            fg_color="#333333", button_color=C_ACCENT,
            button_hover_color=C_ACCENT2, text_color=C_TEXT,
            corner_radius=2, dynamic_resizing=False)
        self.camera_source_menu.grid(row=0, column=1, padx=6, pady=4, sticky="ew")

        # ─── BLOQUE 2: Detección IA YOLOv11n + Doble ROI + Semáforo (2 Columnas Elásticas) ───
        det_frame = ctk.CTkFrame(sb, fg_color="#121222", corner_radius=0,
                                 border_width=1, border_color=C_BORDER)
        det_frame.grid(row=r, column=0, padx=4, pady=(2, 4), sticky="ew"); r += 1
        det_frame.grid_columnconfigure((0, 1), weight=1)

        self.yolo_switch = ctk.CTkSwitch(
            det_frame, text="YOLOv11n IA",
            variable=self.yolo_enabled, command=self._toggle_yolo,
            font=self.font_b, height=18,
            progress_color=C_ACCENT, button_color=C_TEXT, corner_radius=0)
        self.yolo_switch.grid(row=0, column=0, padx=6, pady=(6, 3), sticky="w")

        self.roi_switch = ctk.CTkSwitch(
            det_frame, text="VER ROI DUAL",
            variable=self.show_roi_var, command=self._toggle_roi_visibility,
            font=self.font_b, height=18,
            progress_color=C_BLUE, button_color=C_TEXT, corner_radius=0)
        self.roi_switch.grid(row=0, column=1, padx=6, pady=(6, 3), sticky="w")

        self.label_count = ctk.CTkLabel(
            det_frame, text="ROJO: 0 veh | PK: 0\nVERDE: 25s",
            font=self.font_s, text_color=C_TEXT, corner_radius=0, justify="left")
        self.label_count.grid(row=1, column=0, columnspan=2, padx=6, pady=2, sticky="w")

        # Botones de delimitación de Polígonos ROI (En vivo y Por Coordenadas)
        roi_btn_frame = ctk.CTkFrame(det_frame, fg_color="transparent", corner_radius=0)
        roi_btn_frame.grid(row=2, column=0, columnspan=2, padx=4, pady=(2, 6), sticky="ew")
        roi_btn_frame.grid_columnconfigure((0, 1), weight=1)

        self.btn_draw_roi = ctk.CTkButton(
            roi_btn_frame, text="TRAZAR ROI", height=24,
            font=self.font_b, fg_color=C_BLUE, text_color="#000000", hover_color="#0284C7",
            command=self._start_interactive_roi_draw, corner_radius=4)
        self.btn_draw_roi.grid(row=0, column=0, padx=(0, 3), sticky="ew")

        self.btn_coord_roi = ctk.CTkButton(
            roi_btn_frame, text="COORD ROI", height=24,
            font=self.font_b, fg_color="#334155", text_color=C_TEXT, hover_color="#475569",
            command=self._open_roi_coord_dialog, corner_radius=4)
        self.btn_coord_roi.grid(row=0, column=1, padx=(3, 0), sticky="ew")

        # ─── BLOQUE 2.5: Cruce Semafórico Adaptativo de 3 Vías (S1, S2, S3 -> GPIO RPi5) ───
        self._build_semaforo_panel(sb, r); r += 1

        # ─── BLOQUE 3: Botones de Acción en 3 Columnas Elásticas ───
        btn_frame = ctk.CTkFrame(sb, fg_color="transparent", corner_radius=0)
        btn_frame.grid(row=r, column=0, padx=4, pady=(2, 4), sticky="ew"); r += 1
        btn_frame.grid_columnconfigure((0, 1, 2), weight=1)

        self.btn_capture = ctk.CTkButton(
            btn_frame, text="CAPTURA", height=26,
            font=self.font_b, fg_color=C_GREEN, text_color="#000000", hover_color="#16A34A",
            command=lambda: self._take_snapshot(auto=False), corner_radius=4)
        self.btn_capture.grid(row=0, column=0, padx=(0, 2), sticky="ew")

        self.btn_metrics = ctk.CTkButton(
            btn_frame, text="MÉTRICAS", height=26,
            font=self.font_b, fg_color=C_ACCENT, hover_color=C_ACCENT2,
            command=self._toggle_metrics, corner_radius=4)
        self.btn_metrics.grid(row=0, column=1, padx=2, sticky="ew")

        self.btn_nodes = ctk.CTkButton(
            btn_frame, text="NODOS RED", height=26,
            font=self.font_b, fg_color="#334155", hover_color=C_ACCENT2,
            command=self._toggle_nodes, corner_radius=4)
        self.btn_nodes.grid(row=0, column=2, padx=(2, 0), sticky="ew")

        # ─── BLOQUE 4: Sliders de Imagen (Brillo y Contraste) ───
        sliders_box = ctk.CTkFrame(sb, fg_color="#121222", corner_radius=0, border_width=1, border_color=C_BORDER)
        sliders_box.grid(row=r, column=0, padx=4, pady=(2, 4), sticky="ew"); r += 1
        sliders_box.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(sliders_box, text="BRILLO:", font=self.font_s,
                     text_color=C_MUTED, anchor="w", corner_radius=0).grid(row=0, column=0, padx=6, pady=3, sticky="w")
        ctk.CTkSlider(sliders_box, from_=-50, to=50, variable=self.brightness,
                      command=self._update_camera_settings,
                      height=14, button_color=C_ACCENT, progress_color=C_ACCENT, corner_radius=0
                      ).grid(row=0, column=1, padx=6, pady=3, sticky="ew")

        ctk.CTkLabel(sliders_box, text="CONTRASTE:", font=self.font_s,
                     text_color=C_MUTED, anchor="w", corner_radius=0).grid(row=1, column=0, padx=6, pady=3, sticky="w")
        ctk.CTkSlider(sliders_box, from_=0.5, to=2.0, variable=self.contrast,
                      command=self._update_camera_settings,
                      height=14, button_color=C_ACCENT, progress_color=C_ACCENT, corner_radius=0
                      ).grid(row=1, column=1, padx=6, pady=3, sticky="ew")

        # ─── BLOQUE 5: TERMINAL DE LOGS (ANCHO Y ALTO ADAPTATIVOS) ───
        ctk.CTkLabel(sb, text="TERMINAL LOGS", font=self.font_s, text_color=C_MUTED, corner_radius=0).grid(row=r, column=0, sticky="w", padx=6); r += 1
        sb.grid_rowconfigure(r, weight=1)
        self.term_box = ctk.CTkTextbox(
            sb, height=80, font=self.font_s,
            fg_color=C_TERM_BG, text_color=C_GREEN,
            border_width=1, border_color=C_BORDER, corner_radius=0)
        self.term_box.grid(row=r, column=0, padx=4, pady=(0, 6), sticky="nsew"); r += 1
        self._log_terminal("SYS", "HMI YOLOv11n + VENTANAS DINAMICAS READY")

    def _log_terminal(self, tag, msg):
        """Escribe un mensaje plano con timestamp en la terminal de logs (seguro desde cualquier hilo)."""
        t_str = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{t_str}] [{tag}] {msg}\n"
        def _write():
            try:
                self.term_box.insert("end", line)
                self.term_box.see("end")
            except Exception:
                pass
        if threading.current_thread() is threading.main_thread():
            _write()
        else:
            self._ui_call(_write)

    # ── PANEL Y LÓGICA DEL CRUCE SEMAFÓRICO ADAPTATIVO (S1, S2, S3 -> RPi5) ──

    def _build_semaforo_panel(self, sb, row_idx):
        """Construye el bloque de 3 semáforos (S1, S2, S3) conectados al GPIO de la RPi 5."""
        sem_box = ctk.CTkFrame(
            sb, fg_color="#121222", corner_radius=0, border_width=1, border_color=C_BORDER
        )
        sem_box.grid(row=row_idx, column=0, padx=4, pady=(2, 4), sticky="ew")
        sem_box.grid_columnconfigure(0, weight=1)

        hdr = ctk.CTkFrame(sem_box, fg_color="#1B2238", corner_radius=0)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_columnconfigure(0, weight=1)

        self.lbl_cruce_modo = ctk.CTkLabel(
            hdr, text="MODO: NORMAL (25.0s)", font=self.font_s, text_color=C_BLUE, corner_radius=0
        )
        self.lbl_cruce_modo.grid(row=0, column=0, padx=6, pady=2, sticky="w")

        self.lbl_cruce_fase = ctk.CTkLabel(
            hdr, text="F1: S1 IDA", font=self.font_s, text_color=C_TEXT, corner_radius=0
        )
        self.lbl_cruce_fase.grid(row=0, column=1, padx=4, pady=2, sticky="e")

        # Estado del puente GPIO RPi 5 + Botón de prueba de LEDs físicos
        gpio_bar = ctk.CTkFrame(sem_box, fg_color="#0D1322", corner_radius=0)
        gpio_bar.grid(row=1, column=0, sticky="ew", padx=4, pady=(2, 2))
        gpio_bar.grid_columnconfigure(0, weight=1)

        gpio_init_txt = "GPIO LOCAL RPi5: ACTIVO" if IS_PI else f"PUENTE RPi5 ({self.remote_gpio_client.host}:{self.remote_gpio_client.port})"
        self.lbl_gpio_bridge = ctk.CTkLabel(
            gpio_bar, text=gpio_init_txt, font=self.font_s, text_color=C_GREEN, anchor="w"
        )
        self.lbl_gpio_bridge.grid(row=0, column=0, padx=6, pady=2, sticky="w")

        ctk.CTkButton(
            gpio_bar, text="TEST LEDs", width=64, height=18,
            font=self.font_s, fg_color="#334155", hover_color=C_ACCENT,
            corner_radius=3, command=self._test_semaforos_fisicos
        ).grid(row=0, column=1, padx=4, pady=2, sticky="e")

        self._sem_cards = {}
        titulos = {
            1: "S1 · IDA (GPIO 16/20/21)",
            2: "S2 · VUELTA (GPIO 5/6/13)",
            3: "S3 · CRUCE (GPIO 12/19/26)",
        }

        for idx, sid in enumerate((1, 2, 3), start=2):
            card = ctk.CTkFrame(
                sem_box, fg_color="#0D111D", corner_radius=3, border_width=1, border_color=C_BORDER
            )
            card.grid(row=idx, column=0, padx=4, pady=(2, 3 if sid == 3 else 1), sticky="ew")
            card.grid_columnconfigure(1, weight=1)

            lights_pill = ctk.CTkFrame(
                card, fg_color="#06080F", corner_radius=4, border_width=1, border_color="#263047"
            )
            lights_pill.grid(row=0, column=0, padx=(5, 6), pady=4, sticky="w")

            led_font = ctk.CTkFont(family="Consolas", size=11, weight="bold")
            dot_r = ctk.CTkLabel(lights_pill, text="●", font=led_font, text_color="#2A3244", width=12, corner_radius=0)
            dot_y = ctk.CTkLabel(lights_pill, text="●", font=led_font, text_color="#2A3244", width=12, corner_radius=0)
            dot_g = ctk.CTkLabel(lights_pill, text="●", font=led_font, text_color="#2A3244", width=12, corner_radius=0)
            dot_r.grid(row=0, column=0, padx=(3, 1), pady=1)
            dot_y.grid(row=0, column=1, padx=1, pady=1)
            dot_g.grid(row=0, column=2, padx=(1, 3), pady=1)

            info_col = ctk.CTkFrame(card, fg_color="transparent", corner_radius=0)
            info_col.grid(row=0, column=1, padx=2, pady=3, sticky="ew")
            info_col.grid_columnconfigure(0, weight=1)

            ctk.CTkLabel(
                info_col, text=titulos[sid], font=self.font_b, text_color=C_TEXT,
                anchor="w", corner_radius=0
            ).grid(row=0, column=0, sticky="w")

            lbl_sub = ctk.CTkLabel(
                info_col,
                text="VEH: 0 | VERDE: 25.0s",
                font=self.font_s, text_color=C_MUTED, anchor="w", corner_radius=0
            )
            lbl_sub.grid(row=1, column=0, sticky="w")

            right_col = ctk.CTkFrame(card, fg_color="transparent", corner_radius=0)
            right_col.grid(row=0, column=2, padx=(4, 6), pady=3, sticky="e")

            lbl_luz = ctk.CTkLabel(
                right_col, text="VERDE", font=self.font_b, text_color=C_GREEN,
                anchor="e", width=58, corner_radius=0
            )
            lbl_luz.grid(row=0, column=0, sticky="e")

            lbl_t = ctk.CTkLabel(
                right_col, text="25.0s", font=self.font_lcd, text_color=C_TEXT,
                anchor="e", width=58, corner_radius=0
            )
            lbl_t.grid(row=1, column=0, sticky="e")

            self._sem_cards[sid] = {
                "dots": {"ROJO": dot_r, "AMARILLO": dot_y, "VERDE": dot_g},
                "luz": lbl_luz,
                "tiempo": lbl_t,
                "sub": lbl_sub,
            }

            # Selector de cámara asignada a este semáforo (conteo en rojo con YOLOv11n)
            src_assign_row = ctk.CTkFrame(card, fg_color="transparent", corner_radius=0)
            src_assign_row.grid(row=1, column=0, columnspan=3, padx=5, pady=(0, 4), sticky="ew")
            src_assign_row.grid_columnconfigure(1, weight=1)

            ctk.CTkLabel(
                src_assign_row, text="Cámara:", font=self.font_s,
                text_color=C_MUTED, anchor="w", corner_radius=0
            ).grid(row=0, column=0, padx=(2, 4), sticky="w")

            src_var = ctk.StringVar(value=self.sem_source_map.get(sid, "Ninguna"))
            src_menu = ctk.CTkOptionMenu(
                src_assign_row,
                values=list(SEM_SOURCE_OPTIONS),
                variable=src_var,
                command=lambda val, s=sid: self._on_sem_source_change(s, val),
                font=self.font_s,
                height=18,
                fg_color="#1E293B",
                button_color=C_ACCENT,
                button_hover_color=C_ACCENT2,
                text_color=C_TEXT,
                corner_radius=3,
                dynamic_resizing=False,
            )
            src_menu.grid(row=0, column=1, sticky="ew")
            self._sem_source_vars[sid] = src_var
            self._sem_source_menus[sid] = src_menu

    def _on_sem_source_change(self, sem_id, source_val):
        """Asigna dinámicamente una cámara (TEL-1/2/3, USB, Ninguna) a un semáforo."""
        self.sem_source_map[sem_id] = source_val
        self._save_roi_config()
        self._log_terminal("SEM", f"S{sem_id} ASIGNADO A CÁMARA -> {source_val}")

    def _get_primary_sid_for_source(self, source_name):
        """Devuelve el primer semáforo (1, 2 o 3) asignado a `source_name`, o 1 por defecto."""
        for sid in (1, 2, 3):
            if self.sem_source_map.get(sid) == source_name:
                return sid
        return 1

    def _get_semaphores_for_source(self, source_name):
        """Devuelve todos los IDs de semáforo cuya fuente asignada coincide con `source_name`."""
        return [sid for sid in (1, 2, 3) if self.sem_source_map.get(sid) == source_name]

    def _test_semaforos_fisicos(self):
        """Enciende los 9 LEDs (S1, S2, S3) durante 1.2s en la RPi 5 para comprobar conexión física."""
        self._log_terminal("GPIO", "PROBANDO LEDs DE S1, S2 Y S3 EN RPi 5...")
        for ctrl in self.semaforos_hw.values():
            ctrl.encender_todos()
        def _restore():
            self.cruce._aplicar_hardware_actual()
        self.after(1200, _restore)

    def _on_cruce_phase_change(self, fase, duracion, conteo_s1, modo_adaptativo):
        def _log():
            modo_txt = f"ADAPT ({conteo_s1} det)" if modo_adaptativo else "NORMAL"
            if fase == "F1_VERDE":
                self._log_terminal("SEM", f"FASE 1 -> S1 VERDE ({duracion:.1f}s | {modo_txt})")
            elif fase == "F2_VERDE":
                self._log_terminal("SEM", f"FASE 2 -> S2 VERDE ({duracion:.1f}s)")
            elif fase == "F3_VERDE":
                self._log_terminal("SEM", f"FASE 3 -> S3 VERDE ({duracion:.1f}s)")
        self._ui_call(_log)

    def _tick_semaforo_cruce(self):
        if not self._ui_alive:
            return
        estado = self.cruce.tick()
        colores = {"VERDE": C_GREEN, "AMARILLO": C_YELLOW, "ROJO": C_RED}
        abrev = {"VERDE": "V", "AMARILLO": "A", "ROJO": "R"}

        # Obtener estado de la luz correspondiente al semáforo vinculado a la cámara activa
        active_sid = self._get_primary_sid_for_source(self._active_source)
        active_light = estado[f"s{active_sid}"]
        act_col = colores[active_light]

        if hasattr(self, "lbl_mini_semaforo"):
            self.lbl_mini_semaforo.configure(
                text=f"S{active_sid}: {active_light} ({estado[f'restante_s{active_sid}']:.1f}s)",
                text_color=act_col,
            )

        if hasattr(self, "lbl_cruce_modo"):
            if estado["modo"] == "ADAPTATIVO":
                self.lbl_cruce_modo.configure(
                    text=f"MODO: ADAPTATIVO (S1:{estado['ultimo_verde_s1']:.0f}s)",
                    text_color=C_GREEN,
                )
            else:
                self.lbl_cruce_modo.configure(text="MODO: NORMAL (25.0s)", text_color=C_BLUE)

            fase_txt = {
                "F1_VERDE": "F1: S1 IDA",
                "F1_AMARILLO": "F1: AMARILLO",
                "F1_TODO_ROJO": "TODO-ROJO",
                "F2_VERDE": "F2: S2 VUELTA",
                "F2_AMARILLO": "F2: AMARILLO",
                "F2_TODO_ROJO": "TODO-ROJO",
                "F3_VERDE": "F3: S3 CRUCE",
                "F3_AMARILLO": "F3: AMARILLO",
                "F3_TODO_ROJO": "TODO-ROJO",
            }.get(estado["fase"], estado["fase"])
            self.lbl_cruce_fase.configure(text=fase_txt)

        if hasattr(self, "_sem_cards"):
            for sid, key_luz, key_rest in (
                (1, "s1", "restante_s1"),
                (2, "s2", "restante_s2"),
                (3, "s3", "restante_s3"),
            ):
                luz = estado[key_luz]
                card = self._sem_cards[sid]
                for nombre_luz, dot_lbl in card["dots"].items():
                    dot_lbl.configure(
                        text_color=colores[nombre_luz] if nombre_luz == luz else "#2A3244"
                    )
                card["luz"].configure(text=luz, text_color=colores[luz])
                card["tiempo"].configure(text=f"{estado[key_rest]:.1f}s")

            for sid in (1, 2, 3):
                src_asig = self.sem_source_map.get(sid, "Ninguna")
                cola = estado[f"cola_s{sid}"]
                verde_asig = estado[f"ultimo_verde_s{sid}"]
                if src_asig == "Ninguna":
                    sub_txt = f"SIN CÁMARA | VERDE: {verde_asig:.1f}s"
                else:
                    sub_txt = f"VEH: {cola} | VERDE: {verde_asig:.1f}s"
                self._sem_cards[sid]["sub"].configure(text=sub_txt)

        # Verificar periódicamente el puente UDP hacia RPi 5 cuando corre en Windows
        if not IS_PI and (time.time() - self._last_gpio_ping_ts >= 5.0):
            self._last_gpio_ping_ts = time.time()
            def _probe_bridge():
                ok = self.remote_gpio_client.ping(timeout=0.5)
                txt = (
                    f"● GPIO RPi5 ONLINE ({self.remote_gpio_client.host} · {self.remote_gpio_client.last_rtt_ms}ms)"
                    if ok else f"○ GPIO RPi5 ({self.remote_gpio_client.host}:{self.remote_gpio_client.port})"
                )
                col = C_GREEN if ok else C_YELLOW
                if hasattr(self, "lbl_gpio_bridge"):
                    self._ui_call(lambda: self.lbl_gpio_bridge.configure(text=txt, text_color=col))
            threading.Thread(target=_probe_bridge, daemon=True).start()

        self.after(200, self._tick_semaforo_cruce)

    # ── VENTANAS INTERACTIVAS Y REDIMENSIONABLES: MÉTRICAS Y NODOS RED ──────

    def _toggle_metrics(self):
        """Abre o cierra una ventana interactiva y redimensionable para Telemetría de Hardware."""
        if self._metrics_win is not None and self._metrics_win.winfo_exists():
            self.metrics_visible = False
            self._metrics_win.destroy()
            self._metrics_win = None
            return

        win = ctk.CTkToplevel(self)
        win.title("TELEMETRÍA DE HARDWARE — RPi5")
        px = max(0, self.winfo_x() + 25)
        py = max(0, self.winfo_y() + 45)
        win.geometry(f"320x230+{px}+{py}")
        win.minsize(210, 155)
        win.resizable(True, True)
        win.configure(fg_color=C_BG)
        if not (IS_PI and self._kiosk_mode):
            try:
                win.transient(self)
            except Exception:
                pass
        win.lift()
        self._metrics_win = win
        self.metrics_visible = True

        def _on_close():
            self.metrics_visible = False
            win.destroy()
            self._metrics_win = None

        win.protocol("WM_DELETE_WINDOW", _on_close)

        hdr = ctk.CTkFrame(win, height=24, corner_radius=0, fg_color=C_ACCENT2, border_width=1, border_color=C_BORDER)
        hdr.pack(fill="x", side="top")
        hdr.pack_propagate(False)
        ctk.CTkLabel(hdr, text="◈ TELEMETRÍA HARDWARE", font=self.font_b, text_color=C_TEXT).pack(side="left", padx=6)
        ctk.CTkButton(hdr, text="[X]", width=30, height=18, font=self.font_b, fg_color=C_RED, hover_color="#B71C1C",
                      corner_radius=0, command=_on_close).pack(side="right", padx=2, pady=2)
        btn_m_max = ctk.CTkButton(hdr, text="[ ⛶ ]", width=38, height=18, font=self.font_b, fg_color="#334155",
                                  hover_color=C_ACCENT, corner_radius=0)
        btn_m_max.configure(command=lambda: self._toggle_window_maximize(win, btn_m_max))
        btn_m_max.pack(side="right", padx=2, pady=2)

        body = ctk.CTkFrame(win, corner_radius=0, fg_color=C_PANEL, border_width=1, border_color=C_BORDER)
        body.pack(fill="both", expand=True, padx=6, pady=(4, 8))
        body.grid_columnconfigure(0, weight=1)
        for idx_r in range(5):
            body.grid_rowconfigure(idx_r, weight=1)

        def _metric_row(row_idx, init_txt, txt_col):
            card = ctk.CTkFrame(body, corner_radius=0, fg_color="#121222", border_width=1, border_color=C_BORDER)
            card.grid(row=row_idx, column=0, sticky="nsew", padx=4, pady=2)
            card.grid_rowconfigure(0, weight=1)
            card.grid_columnconfigure(0, weight=1)
            lbl = ctk.CTkLabel(card, text=init_txt, font=self.font_lcd, text_color=txt_col, anchor="w", corner_radius=0)
            lbl.grid(row=0, column=0, sticky="nsew", padx=8, pady=2)
            return lbl

        self.lbl_inf_time = _metric_row(0, "INF: -- ms", C_TEXT)
        self.lbl_cpu      = _metric_row(1, "CPU: --%", C_YELLOW)
        self.lbl_temp     = _metric_row(2, "TEMP: --°C", C_RED)
        self.lbl_ram      = _metric_row(3, "RAM: --%", C_GREEN)
        self.lbl_storage  = _metric_row(4, "DISCO: --%", C_MUTED)

        self._attach_resize_grip(win, min_w=210, min_h=155)

    def _toggle_nodes(self):
        """Abre o cierra una ventana interactiva y redimensionable para el Estado de Nodos de Cámara."""
        if self._nodes_win is not None and self._nodes_win.winfo_exists():
            self.nodes_visible = False
            self._nodes_win.destroy()
            self._nodes_win = None
            self.node_rows.clear()
            return

        win = ctk.CTkToplevel(self)
        win.title("NODOS CÁMARAS (TEL-1, TEL-2, TEL-3, USB) — ESTADO EN VIVO")
        px = max(0, self.winfo_x() + 55)
        py = max(0, self.winfo_y() + 65)
        win.geometry(f"360x195+{px}+{py}")
        win.minsize(230, 140)
        win.resizable(True, True)
        win.configure(fg_color=C_BG)
        if not (IS_PI and self._kiosk_mode):
            try:
                win.transient(self)
            except Exception:
                pass
        win.lift()
        self._nodes_win = win
        self.nodes_visible = True

        def _on_close():
            self.nodes_visible = False
            self.node_rows.clear()
            win.destroy()
            self._nodes_win = None

        win.protocol("WM_DELETE_WINDOW", _on_close)

        hdr = ctk.CTkFrame(win, height=24, corner_radius=0, fg_color=C_ACCENT2, border_width=1, border_color=C_BORDER)
        hdr.pack(fill="x", side="top")
        hdr.pack_propagate(False)
        ctk.CTkLabel(hdr, text="◈ NODOS CÁMARAS (TEL-1, TEL-2, TEL-3, USB)", font=self.font_b, text_color=C_TEXT).pack(side="left", padx=6)
        ctk.CTkButton(hdr, text="[X]", width=30, height=18, font=self.font_b, fg_color=C_RED, hover_color="#B71C1C",
                      corner_radius=0, command=_on_close).pack(side="right", padx=2, pady=2)
        btn_n_max = ctk.CTkButton(hdr, text="[ ⛶ ]", width=38, height=18, font=self.font_b, fg_color="#334155",
                                  hover_color=C_ACCENT, corner_radius=0)
        btn_n_max.configure(command=lambda: self._toggle_window_maximize(win, btn_n_max))
        btn_n_max.pack(side="right", padx=2, pady=2)

        tbl = ctk.CTkFrame(win, fg_color="#101010", corner_radius=0, border_width=1, border_color=C_BORDER)
        tbl.pack(fill="both", expand=True, padx=6, pady=(4, 8))
        tbl.grid_columnconfigure((0, 1, 2), weight=1)
        tbl.grid_rowconfigure(0, weight=0)

        headers = ["NODO", "CONEXIÓN", "ESTADO / PING"]
        for c, h in enumerate(headers):
            cell = ctk.CTkFrame(tbl, corner_radius=0, fg_color="#202020", border_width=1, border_color=C_BORDER)
            cell.grid(row=0, column=c, sticky="nsew")
            ctk.CTkLabel(cell, text=h, font=self.font_b, text_color=C_MUTED, corner_radius=0).pack(padx=6, pady=4, expand=True)

        self.node_rows = {}
        # Fila 1: Cámara Local USB2.0 PC CAMERA
        tbl.grid_rowconfigure(1, weight=1)
        u_c1 = ctk.CTkFrame(tbl, corner_radius=0, fg_color="#151515", border_width=1, border_color=C_BORDER)
        u_c1.grid(row=1, column=0, sticky="nsew")
        ctk.CTkLabel(u_c1, text="USB-CAM", font=self.font_b, text_color=C_BLUE, corner_radius=0).pack(padx=4, pady=4, expand=True)

        u_c2 = ctk.CTkFrame(tbl, corner_radius=0, fg_color="#151515", border_width=1, border_color=C_BORDER)
        u_c2.grid(row=1, column=1, sticky="nsew")
        usb_conn_txt = f"IDX #{self.usb_cam_state['index']}" if self.usb_cam_state["index"] is not None else "USB 2.0"
        self._lbl_usb_node_conn = ctk.CTkLabel(u_c2, text=usb_conn_txt, font=self.font_s, text_color=C_TEXT, corner_radius=0)
        self._lbl_usb_node_conn.pack(padx=4, pady=4, expand=True)

        u_c3 = ctk.CTkFrame(tbl, corner_radius=0, fg_color="#151515", border_width=1, border_color=C_BORDER)
        u_c3.grid(row=1, column=2, sticky="nsew")
        usb_st_txt = f"ONLINE ({self.usb_cam_state['res']})" if self.usb_cam_state["online"] else "STANDBY"
        usb_st_col = C_GREEN if self.usb_cam_state["online"] else C_MUTED
        lbl_usb_st = ctk.CTkLabel(u_c3, text=usb_st_txt, font=self.font_b, text_color=usb_st_col, corner_radius=0)
        lbl_usb_st.pack(padx=4, pady=4, expand=True)
        self.node_rows[0] = lbl_usb_st

        # Filas 2..4: Nodos Teléfonos USB/Wi-Fi (TEL-1, TEL-2, TEL-3)
        for idx, (cid, cam) in enumerate(self.phone_cameras.items(), start=2):
            tbl.grid_rowconfigure(idx, weight=1)
            cell1 = ctk.CTkFrame(tbl, corner_radius=0, fg_color="#151515", border_width=1, border_color=C_BORDER)
            cell1.grid(row=idx, column=0, sticky="nsew")
            ctk.CTkLabel(cell1, text=f"TEL-{cid}", font=self.font_b, text_color=C_TEXT, corner_radius=0).pack(padx=4, pady=4, expand=True)

            cell2 = ctk.CTkFrame(tbl, corner_radius=0, fg_color="#151515", border_width=1, border_color=C_BORDER)
            cell2.grid(row=idx, column=1, sticky="nsew")
            ctk.CTkLabel(cell2, text=cam["host"], font=self.font_s, text_color=C_TEXT, corner_radius=0).pack(padx=4, pady=4, expand=True)

            cell3 = ctk.CTkFrame(tbl, corner_radius=0, fg_color="#151515", border_width=1, border_color=C_BORDER)
            cell3.grid(row=idx, column=2, sticky="nsew")
            init_txt = f"{cam['latency']}ms" if cam["online"] else "OFF"
            init_col = C_GREEN if cam["online"] else C_RED
            lbl_p = ctk.CTkLabel(cell3, text=init_txt, font=self.font_b, text_color=init_col, corner_radius=0)
            lbl_p.pack(padx=4, pady=4, expand=True)

            self.node_rows[cid] = lbl_p

        self._attach_resize_grip(win, min_w=230, min_h=150)

    def _update_hmi_telemetry(self):
        """Actualiza la barra de diagnóstico superior, uptime y tabla de nodos."""
        elapsed_sec = int(time.time() - self.start_time_stamp)
        hrs, rem = divmod(elapsed_sec, 3600)
        mins, secs = divmod(rem, 60)
        self.lbl_uptime.configure(text=f"UPTIME: {hrs:02d}:{mins:02d}:{secs:02d}")

        total_lat = 0
        active_nodes = 0
        if (time.time() - self._last_node_probe_ts >= 8.0) and (time.time() - self._last_switch_ts >= 4.0) and not self._node_probe_running:
            self._last_node_probe_ts = time.time()
            self._node_probe_running = True
            threading.Thread(target=self._probe_nodes_lightweight, daemon=True).start()

        nodes_open = (self._nodes_win is not None and self._nodes_win.winfo_exists())
        if nodes_open and 0 in self.node_rows:
            try:
                if self.usb_cam_state["online"]:
                    self.node_rows[0].configure(text=f"ONLINE ({self.usb_cam_state['res']})", text_color=C_GREEN)
                else:
                    self.node_rows[0].configure(text="STANDBY", text_color=C_MUTED)
                if hasattr(self, "_lbl_usb_node_conn") and self._lbl_usb_node_conn.winfo_exists():
                    conn_t = f"IDX #{self.usb_cam_state['index']}" if self.usb_cam_state["index"] is not None else "USB 2.0"
                    self._lbl_usb_node_conn.configure(text=conn_t)
            except Exception:
                pass

        for cid, cam in self.phone_cameras.items():
            if cam["online"]:
                active_nodes += 1
                total_lat += cam["latency"]
            if nodes_open and cid in self.node_rows:
                try:
                    if cam["online"]:
                        self.node_rows[cid].configure(text=f"{cam['latency']}ms", text_color=C_GREEN)
                    else:
                        self.node_rows[cid].configure(text="OFF", text_color=C_RED)
                except Exception:
                    pass
        
        if active_nodes > 0:
            avg_ping = int(total_lat / active_nodes)
            self.lbl_ping.configure(text=f"PING: {avg_ping}ms")
        elif self.usb_cam_state["online"]:
            self.lbl_ping.configure(text="USB: <1ms")
        else:
            self.lbl_ping.configure(text="PING: --ms")

        self.after(1000, self._update_hmi_telemetry)

    def _probe_nodes_lightweight(self):
        """Consulta ligera de estado de los nodos TEL-1/2/3 inactivos sin interferir con el stream activo."""
        try:
            active_cid = 2 if self._active_source == "TEL-2" else (3 if self._active_source == "TEL-3" else (1 if self._active_source == "TEL-1" else None))
            for cid, cam in self.phone_cameras.items():
                if not self._ui_alive or (time.time() - self._last_switch_ts < 4.0):
                    break
                if cid == active_cid and self.is_connected:
                    cam["online"] = True
                    continue
                t0 = time.time()
                try:
                    r = requests.get(cam["status"], timeout=0.9, headers={"Connection": "close"})
                    if r.status_code == 200:
                        cam["online"] = True
                        cam["latency"] = int((time.time() - t0) * 1000)
                    else:
                        cam["online"] = False
                        cam["latency"] = 0
                except Exception:
                    cam["online"] = False
                    cam["latency"] = 0
                time.sleep(0.25)
        finally:
            self._node_probe_running = False

    # ── MONITOREO DE HARDWARE ───────────────────────────────────

    def _update_metrics(self):
        if self.metrics_visible and self._metrics_win is not None and self._metrics_win.winfo_exists():
            inf_val = f"{self.last_inference_time:.1f}"
            self.lbl_inf_time.configure(text=f"INF: {inf_val} ms")
            
            cpu_val = "0.0"
            ram_val = "0.0"
            if PSUTIL_AVAILABLE:
                cpu_p = psutil.cpu_percent()
                cpu_val = f"{cpu_p:.1f}"
                self.lbl_cpu.configure(text=f"CPU: {cpu_val}%")
                mem = psutil.virtual_memory()
                ram_val = f"{mem.percent:.1f}"
                self.lbl_ram.configure(text=f"RAM: {ram_val}% ({mem.used//1048576}MB)")
            
            total, used, free = shutil.disk_usage("/")
            disk_p = (used / total) * 100
            disk_val = f"{disk_p:.1f}"
            self.lbl_storage.configure(text=f"DISCO: {disk_val}%")

            temp_val = "0.0"
            if IS_PI:
                try:
                    with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                        temp = float(f.read().strip()) / 1000.0
                        temp_val = f"{temp:.1f}"
                        self.lbl_temp.configure(text=f"TEMP: {temp_val}°C")
                except Exception:
                    pass

            # FASE 3: Buffering a CSV
            dt_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            fps_val = f"{self.current_fps:.1f}"
            record = [dt_str, inf_val, cpu_val, ram_val, disk_val, temp_val, fps_val]
            self._metrics_buffer.append(record)
            
            # FASE 6: Logging de estadísticas
            drop_pct = 0.0
            if self._frame_stats["captured"] > 0:
                drop_pct = (self._frame_stats["dropped"] / self._frame_stats["captured"]) * 100
            
            stats_text = f"Frames: {self._frame_stats['captured']} cap, {self._frame_stats['processed']} proc, {self._frame_stats['dropped']} drop ({drop_pct:.1f}%)"
            print(f"[STATS] {stats_text}")
            
            if len(self._metrics_buffer) >= self._metrics_buffer_size:
                self._flush_metrics_buffer()

        self.after(1000, self._update_metrics)

    def _flush_metrics_buffer(self):
        """Escribe buffer de métricas a CSV en batch (reduce I/O 70%)."""
        if not self._metrics_buffer:
            return
        
        try:
            save_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "registros")
            os.makedirs(save_dir, exist_ok=True)
            csv_path = f"{save_dir}/metricas_sistema.csv"
            file_exists = os.path.exists(csv_path)
            
            with open(csv_path, "a", newline="") as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(["Timestamp", "Inferencia_ms", "CPU_pct", "RAM_pct", "Disco_pct", "Temp_C", "FPS"])
                writer.writerows(self._metrics_buffer)
                f.flush()
                os.fsync(f.fileno())  # Garantizar escritura a disco
            
            self._metrics_buffer.clear()
        except Exception as e:
            print(f"Error escribiendo métricas: {e}")

    # ── CÁMARA & CONTROL DE FUENTES ─────────────────────────────

    def _set_status(self, text, color=C_MUTED):
        self._ui_call(lambda: self.status_badge.configure(text=f" [ {text} ] ", text_color=color))

    def _force_close_http_resp(self, resp):
        """Envia TCP RST inmediato (SO_LINGER=0) + shutdown para liberar el socket HTTP en <2ms."""
        if resp is None:
            return
        try:
            raw = getattr(resp, "raw", None)
            fp = getattr(raw, "_fp", None)
            fp_inner = getattr(fp, "fp", None)
            raw_inner = getattr(fp_inner, "raw", None)
            sock = getattr(raw_inner, "_sock", None) or getattr(fp_inner, "_sock", None)
            if sock is not None:
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
                except Exception:
                    pass
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                try:
                    sock.close()
                except Exception:
                    pass
        except Exception:
            pass
        try:
            resp.close()
        except Exception:
            pass

    def _clear_video_display(self, text_msg, text_col=C_MUTED):
        """Limpia de forma segura la imagen previa en CustomTkinter para no superponer texto sobre un frame viejo."""
        def _clear():
            self._latest_raw_frame = None
            self._current_ctk_image = None
            self.video_label.image = None
            avail_vw = max(120, self.video_label.winfo_width() - 16)
            try:
                self.video_label.configure(image="", text=text_msg, font=self.font_b, text_color=text_col, wraplength=avail_vw)
            except Exception:
                self.video_label.configure(text=text_msg, font=self.font_b, text_color=text_col)
        self._ui_call(_clear)

    def _close_cameras(self):
        self.is_connected = False
        self.usb_cam_state["online"] = False
        self._stop_producer = True
        self._stream_session_id += 1
        self._producer_stop_event.set()
        self._last_switch_ts = time.time()

        # Cerrar inmediatamente el socket HTTP MJPEG con TCP RST para desbloquear iter_content
        if self.http_stream_resp is not None:
            resp_to_close = self.http_stream_resp
            self.http_stream_resp = None
            self._force_close_http_resp(resp_to_close)

        if self._producer_thread is not None and self._producer_thread.is_alive():
            try:
                self._producer_thread.join(timeout=2.0)
            except Exception as e:
                print(f"Error esperando thread productor: {e}")
            finally:
                self._producer_thread = None

        while not self.frame_queue.empty():
            try:
                self.frame_queue.get_nowait()
            except queue.Empty:
                break

        with self._yolo_lock:
            self._latest_yolo_boxes = []

        if self.cap is not None:
            try:
                self.cap.release()
            except Exception as e:
                print(f"Error OpenCV: {e}")
            finally:
                self.cap = None

    def _switch_camera(self, selection):
        self._active_source = selection
        self._apply_source_roi_to_active(selection)
        self._log_terminal("CAM", f"CAMBIANDO FUENTE A {selection} (ROI PROPIA CARGADA)")
        self._switch_seq += 1
        seq = self._switch_seq
        self._last_switch_ts = time.time()

        def do_switch():
            with self._switch_lock:
                if seq != self._switch_seq:
                    return
                self._close_cameras()
                time.sleep(0.12)
                if seq == self._switch_seq:
                    self._init_camera(selection)
                self._last_switch_ts = time.time()

        threading.Thread(target=do_switch, daemon=True).start()

    def _open_usb_camera_capture(self):
        """Localiza y abre el dispositivo USB2.0 PC CAMERA con el backend óptimo y baja latencia."""
        cam_idx, cam_name, all_devs = find_preferred_usb_camera(USB_CAMERA_TARGET)
        if platform.system() == "Windows":
            backends = [cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY]
        elif IS_PI:
            backends = [cv2.CAP_V4L2, cv2.CAP_ANY]
        else:
            backends = [cv2.CAP_ANY]

        # Probar primero el índice identificado para USB2.0 PC CAMERA y luego otros disponibles si estuviera ocupado
        candidate_indices = [cam_idx] + [i for i, _ in all_devs if i != cam_idx]
        for idx in candidate_indices:
            for b_id in backends:
                cap = cv2.VideoCapture(idx, b_id)
                if not cap.isOpened():
                    cap.release()
                    continue
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                cap.set(cv2.CAP_PROP_FPS, 30)

                first_frame = None
                for _ in range(6):
                    ret, frm = cap.read()
                    if ret and frm is not None and frm.size > 0:
                        first_frame = frm
                        break
                    time.sleep(0.05)

                if first_frame is not None:
                    matched_name = next((n for i, n in all_devs if i == idx), cam_name)
                    return cap, idx, matched_name, first_frame
                cap.release()

        raise ConnectionError(f"No se detectó o está en uso '{USB_CAMERA_TARGET}'")

    def _open_mjpeg_stream(self, url, stop_event):
        """Abre el stream HTTP MJPEG con reintentos rápidos."""
        last_err = None
        for attempt in range(3):
            if stop_event.is_set():
                return None
            try:
                resp = requests.get(
                    url,
                    stream=True,
                    timeout=(2.0, 4.5),
                    headers={"Connection": "close"}
                )
                if resp.status_code == 200:
                    return resp
                self._force_close_http_resp(resp)
                last_err = ConnectionError(f"HTTP {resp.status_code}")
            except Exception as e:
                last_err = e
            if not stop_event.is_set() and attempt < 2:
                time.sleep(0.25)
        if last_err:
            raise last_err
        return None

    def _init_camera(self, selection=None):
        if self.init_in_progress:
            return
        self.init_in_progress = True
        if selection is None:
            selection = self._active_source
        self._stream_session_id += 1
        session_id = self._stream_session_id
        stop_event = threading.Event()
        self._producer_stop_event = stop_event

        self._set_status("CONECTANDO...", C_YELLOW)
        self._clear_video_display(f"[ CONECTANDO A {selection} ]", C_MUTED)

        try:
            if selection in ("USB", "USB2.0 PC CAMERA"):
                print(f"Buscando y abriendo cámara USB '{USB_CAMERA_TARGET}'...")
                cap, cam_idx, dev_name, first_frame = self._open_usb_camera_capture()
                if session_id != self._stream_session_id or stop_event.is_set():
                    cap.release()
                    return
                self.cap = cap
                self.is_connected = True
                fh, fw = first_frame.shape[:2]
                self.usb_cam_state.update({"name": dev_name, "index": cam_idx, "online": True, "res": f"{fw}x{fh}"})
                self._set_status(f"USB ({dev_name[:14]} #{cam_idx})", C_GREEN)
                self._ui_call(lambda: self.video_label.configure(text=""))
                self._log_terminal("CAM", f"USB ACTIVA: {dev_name} (IDX #{cam_idx} | {fw}x{fh})")
                self._push_frame(self._preprocess_frame(first_frame, selection))

            elif selection in ("TEL-1", "TEL-2", "TEL-3"):
                cam_id = 2 if selection == "TEL-2" else (3 if selection == "TEL-3" else 1)

                cam_info = self.phone_cameras[cam_id]
                self._last_switch_ts = time.time()

                print(f"Conectando por HTTP MJPEG directo a {cam_info['name']} ({cam_info['url']})...")
                resp = self._open_mjpeg_stream(cam_info["url"], stop_event)
                if session_id != self._stream_session_id or stop_event.is_set():
                    self._force_close_http_resp(resp)
                    return
                if resp is not None:
                    self.http_stream_resp = resp
                    self.is_connected = True
                    self.phone_cameras[cam_id]["online"] = True
                    self._set_status(f"TEL-{cam_id} ONLINE", C_GREEN)
                    self._ui_call(lambda: self.video_label.configure(text=""))
                else:
                    raise ConnectionError(f"Sin respuesta de {cam_info['name']}")

            else:
                raise ValueError(f"Fuente desconocida: {selection}")

            self._stop_producer = False
            self._producer_thread = threading.Thread(
                target=self._frame_producer_loop,
                args=(session_id, stop_event, selection),
                daemon=True
            )
            self._producer_thread.start()
        except Exception as e:
            if session_id == self._stream_session_id and not stop_event.is_set():
                self.is_connected = False
                self.usb_cam_state["online"] = False
                self._set_status("SIN SEÑAL", C_RED)
                short_err = "Teléfono desconectado o IP Webcam inactiva" if "timed out" in str(e).lower() or "connection" in str(e).lower() else str(e)
                self._clear_video_display(f"[ {selection} SIN SEÑAL ]\n{short_err}", C_RED)
        finally:
            self.init_in_progress = False

    def _preprocess_frame(self, frame, selection):
        """Pre-procesa el cuadro en el hilo secundario optimizando resolución para fluidez a 30 FPS."""
        if len(frame.shape) == 3 and frame.shape[2] == 4:
            frame = cv2.cvtColor(frame, cv2.COLOR_RGBA2BGR)
        fh, fw = frame.shape[:2]
        if fw > 960:
            nh = max(1, int(fh * (960.0 / fw)))
            frame = cv2.resize(frame, (960, nh), interpolation=cv2.INTER_LINEAR)
        frame = self._apply_adjustments(frame)
        return frame

    def _frame_producer_loop(self, session_id=None, stop_event=None, selection=None):
        if stop_event is None:
            stop_event = self._producer_stop_event
        if selection is None:
            selection = self._active_source
        if session_id is None:
            session_id = self._stream_session_id

        try:
            if selection in ("USB", "USB2.0 PC CAMERA") and self.cap is not None:
                missed_reads = 0
                while self.is_connected and not self._stop_producer and not stop_event.is_set() and session_id == self._stream_session_id:
                    ret, frame = self.cap.read()
                    if ret and frame is not None:
                        missed_reads = 0
                        frame = self._preprocess_frame(frame, selection)
                        self._push_frame(frame)
                    else:
                        missed_reads += 1
                        if missed_reads > 25:
                            # Auto-recuperación ante micro-desconexión USB
                            try:
                                self.cap.release()
                            except Exception:
                                pass
                            self.cap, cam_idx, dev_name, _ = self._open_usb_camera_capture()
                            self.usb_cam_state["index"] = cam_idx
                            self.usb_cam_state["name"] = dev_name
                            missed_reads = 0
                        time.sleep(0.03)
                    time.sleep(0.01)

            elif selection in ("TEL-1", "TEL-2", "TEL-3"):
                cam_id = 2 if selection == "TEL-2" else (3 if selection == "TEL-3" else 1)
                cam_url = self.phone_cameras[cam_id]["url"]
                reconnect_attempts = 0

                # Bucle con auto-reconexión transparente ante micro-cortes o Read timed out
                while self.is_connected and not self._stop_producer and not stop_event.is_set() and session_id == self._stream_session_id:
                    resp = self.http_stream_resp
                    if resp is None:
                        try:
                            self._set_status(f"RECONECTANDO TEL-{cam_id}...", C_YELLOW)
                            resp = self._open_mjpeg_stream(cam_url, stop_event)
                            if stop_event.is_set() or session_id != self._stream_session_id:
                                self._force_close_http_resp(resp)
                                break
                            self.http_stream_resp = resp
                            reconnect_attempts = 0
                            self._set_status(f"TEL-{cam_id} ONLINE", C_GREEN)
                        except Exception as rex:
                            reconnect_attempts += 1
                            if reconnect_attempts >= 4:
                                raise rex
                            time.sleep(0.3)
                            continue

                    buf = b""
                    try:
                        for chunk in resp.iter_content(chunk_size=32768):
                            if not self.is_connected or self._stop_producer or stop_event.is_set() or session_id != self._stream_session_id:
                                break
                            if not chunk:
                                continue
                            buf += chunk

                            # Extraer siempre el ÚLTIMO cuadro JPEG completo en el buffer (latencia cero)
                            end_idx = buf.rfind(b"\xff\xd9")
                            if end_idx != -1:
                                start_idx = buf.rfind(b"\xff\xd8", 0, end_idx)
                                if start_idx != -1:
                                    jpg_bytes = buf[start_idx:end_idx + 2]
                                    buf = buf[end_idx + 2:]
                                    arr = np.frombuffer(jpg_bytes, dtype=np.uint8)
                                    # Decodificación rápida 1/2 en libjpeg-turbo para frames 1080p (>85 KB)
                                    decode_flag = cv2.IMREAD_REDUCED_COLOR_2 if len(jpg_bytes) > 85000 else cv2.IMREAD_COLOR
                                    frame = cv2.imdecode(arr, decode_flag)
                                    if frame is not None:
                                        reconnect_attempts = 0
                                        frame = self._preprocess_frame(frame, selection)
                                        self._push_frame(frame)
                                else:
                                    buf = buf[end_idx + 2:]
                            elif len(buf) > 2097152:
                                buf = buf[-262144:]

                        if not stop_event.is_set() and session_id == self._stream_session_id:
                            self._force_close_http_resp(resp)
                            if self.http_stream_resp is resp:
                                self.http_stream_resp = None
                            time.sleep(0.15)
                    except Exception as stream_err:
                        if stop_event.is_set() or session_id != self._stream_session_id:
                            break
                        print(f"[AUTO-RECONNECT] {selection}: {stream_err}")
                        self._force_close_http_resp(resp)
                        if self.http_stream_resp is resp:
                            self.http_stream_resp = None
                        reconnect_attempts += 1
                        if reconnect_attempts >= 4:
                            raise stream_err
                        time.sleep(0.25)

        except Exception as e:
            if not stop_event.is_set() and session_id == self._stream_session_id:
                print(f"Error productor ({selection}): {e}")
                self.is_connected = False
                self._set_status("DESCONECTADO", C_RED)
                self._clear_video_display(f"[ {selection} DESCONECTADO ]\nReintente seleccionando la cámara", C_RED)
        finally:
            if session_id == self._stream_session_id and self.http_stream_resp is not None:
                resp_to_close = self.http_stream_resp
                self.http_stream_resp = None
                self._force_close_http_resp(resp_to_close)

    def _push_frame(self, frame):
        self._frame_stats["captured"] += 1
        if self.frame_queue.full():
            try:
                self.frame_queue.get_nowait()
                self.frame_dropped_count += 1
                self._frame_stats["dropped"] += 1
            except queue.Empty:
                pass
        try:
            self.frame_queue.put_nowait(frame)
        except queue.Full:
            pass

        # Alimentar hilo asíncrono de YOLOv11n sin bloquear el productor de video
        if self._yolo_active and not self.yolo_loading and self.local_model is not None:
            if not self.yolo_input_queue.full():
                try:
                    self.yolo_input_queue.put_nowait(frame.copy())
                except queue.Full:
                    pass

    # ── RENDERIZADO DINÁMICO ADAPTADO AL TAMAÑO DE LA VENTANA ────────────────

    def _render_frame_to_display(self, raw_frame):
        """Escala proporcionalmente cualquier cuadro al tamaño actual del panel de video y dibuja ROIs/YOLO."""
        avail_w = self.video_label.winfo_width() - 4
        avail_h = self.video_label.winfo_height() - 4
        if avail_w <= 40 or avail_h <= 40:
            avail_w = max(120, self.video_frame.winfo_width() - 8)
            avail_h = max(90, self.video_frame.winfo_height() - 30)

        src_h, src_w = raw_frame.shape[:2]
        scale = min(avail_w / float(max(1, src_w)), avail_h / float(max(1, src_h)))
        self._target_display_width = max(64, int(src_w * scale))
        self._target_display_height = max(48, int(src_h * scale))

        target_w = self._target_display_width
        target_h = self._target_display_height

        frame = raw_frame
        if frame.shape[1] != self._target_display_width or frame.shape[0] != self._target_display_height:
            frame = cv2.resize(frame, (self._target_display_width, self._target_display_height), interpolation=cv2.INTER_LINEAR)

        # Dibujar polígonos ROI (si están visibles o en modo calibración) y cajas de YOLOv11n escaladas
        if self.show_roi_var.get() or self._roi_calib_mode is not None or (self.yolo_enabled.get() and self._latest_yolo_boxes):
            frame = frame.copy()
            if self.show_roi_var.get() or self._roi_calib_mode is not None:
                frame = self._draw_roi_overlays(frame)
            if self.yolo_enabled.get() and self._latest_yolo_boxes:
                fh, fw = frame.shape[:2]
                dyn_font = float(np.clip(fw / 750.0, 0.35, 0.78))
                dyn_thick = max(1, int(round(fw / 420.0)))
                with self._yolo_lock:
                    boxes_snapshot = list(self._latest_yolo_boxes)
                for (nx1, ny1, nx2, ny2, label, color) in boxes_snapshot:
                    x1, y1 = int(nx1 * fw), int(ny1 * fh)
                    x2, y2 = int(nx2 * fw), int(ny2 * fh)
                    thickness = 1 if label == "Parqueado" or label == "" else dyn_thick + 1
                    cv2.rectangle(frame, (x1, y1), (x2, y2), color, thickness)
                    if label:
                        cv2.putText(frame, label, (x1, max(14, y1 - 5)),
                                    cv2.FONT_HERSHEY_SIMPLEX, dyn_font, color, max(1, dyn_thick))

        self._latest_frame = frame

        pil_img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))

        if self._current_ctk_image is not None:
            try:
                del self._current_ctk_image
            except Exception:
                pass
            self._current_ctk_image = None

        ctk_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(target_w, target_h))
        self._current_ctk_image = ctk_img
        self.video_label.configure(image=ctk_img, text="")
        self.video_label.image = ctk_img

    def _consume_frame(self):
        if self.init_in_progress:
            self.after(20, self._consume_frame)
            return
        try:
            raw_frame = self.frame_queue.get_nowait()
        except queue.Empty:
            self.after(12, self._consume_frame)
            return

        self._frame_stats["processed"] += 1
        self._latest_raw_frame = raw_frame
        self._render_frame_to_display(raw_frame)

        now = time.time()
        dt = now - self._last_frame_time
        if dt > 0:
            fps = 1.0 / dt
            self.current_fps = 0.7 * self.current_fps + 0.3 * fps if self.current_fps > 0 else fps
        self._last_frame_time = now
        col = C_GREEN if self.current_fps >= 10 else C_YELLOW
        self.fps_badge.configure(text=f" {self.current_fps:.1f} FPS ", text_color=col)

        self.after(16, self._consume_frame)

    def _draw_roi_overlays(self, frame):
        """Dibuja los polígonos de Zona de Parqueo (Azul/Naranja) y Zona de Flujo (Verde) escalados al tamaño actual."""
        h, w = frame.shape[:2]
        pts_pk, pts_mv = self._get_scaled_rois(w, h)
        dyn_thick = max(1, int(round(w / 320.0)))
        dyn_font = float(np.clip(w / 800.0, 0.35, 0.75))

        if self.show_roi_var.get():
            # Zona de Parqueo (Exclusión) en Azul/Naranja
            cv2.polylines(frame, [pts_pk.reshape((-1, 1, 2))], isClosed=True, color=(255, 140, 0), thickness=dyn_thick + 1)
            pk_top = pts_pk[0]
            cv2.putText(frame, "ROI PARQUEO", (max(2, int(pk_top[0]) - 10), max(14, int(pk_top[1]) - 5)),
                        cv2.FONT_HERSHEY_SIMPLEX, dyn_font, (255, 165, 0), max(1, dyn_thick))

            # Zona de Movimiento (Flujo Activo) en Verde
            cv2.polylines(frame, [pts_mv.reshape((-1, 1, 2))], isClosed=True, color=(0, 255, 0), thickness=dyn_thick + 1)
            mv_top = pts_mv[0]
            cv2.putText(frame, "ROI FLUJO", (max(2, int(mv_top[0])), max(14, int(mv_top[1]) - 5)),
                        cv2.FONT_HERSHEY_SIMPLEX, dyn_font, (0, 255, 0), max(1, dyn_thick))

        if self._roi_calib_mode is not None:
            color_cal = (255, 180, 0) if self._roi_calib_mode == "parqueo" else (0, 255, 100)
            titulo_cal = "CLIC PARQUEO" if self._roi_calib_mode == "parqueo" else "CLIC FLUJO"
            bar_h = max(22, int(h * 0.07))
            cv2.rectangle(frame, (0, 0), (w, bar_h), (10, 10, 10), -1)
            cv2.putText(frame, f"{titulo_cal}: PUNTO {len(self._roi_calib_points)+1}/4",
                        (8, int(bar_h * 0.72)), cv2.FONT_HERSHEY_SIMPLEX, max(0.42, dyn_font), color_cal, max(1, dyn_thick))

            scaled_pts = [[int(nx * w), int(ny * h)] for nx, ny in self._roi_calib_points]
            pt_rad = max(4, int(round(w / 110.0)))
            for idx_p, (px, py) in enumerate(scaled_pts):
                cv2.circle(frame, (px, py), pt_rad, (0, 255, 255), -1)
                cv2.putText(frame, str(idx_p + 1), (px + pt_rad + 2, py - 4),
                            cv2.FONT_HERSHEY_SIMPLEX, max(0.4, dyn_font), (0, 255, 255), max(1, dyn_thick))
            if len(scaled_pts) >= 2:
                arr_pts = np.array(scaled_pts, dtype=np.int32).reshape((-1, 1, 2))
                cv2.polylines(frame, [arr_pts], isClosed=False, color=(0, 255, 255), thickness=max(1, dyn_thick))

        return frame

    # ── YOLOv11n & LÓGICA DE INFERENCIA ASÍNCRONA + DOBLE ROI + TRACKING ─────

    def _set_count_labels(self, text_val, col=C_TEXT):
        self.label_count.configure(text=text_val, text_color=col)
        if hasattr(self, "lbl_mini_count"):
            self.lbl_mini_count.configure(text=text_val, text_color=col)

    def _toggle_yolo(self):
        self._yolo_active = bool(self.yolo_enabled.get())
        self.cruce.set_modo_adaptativo(self._yolo_active)
        if not self._yolo_active:
            with self._yolo_lock:
                self._latest_yolo_boxes = []
            self._set_count_labels("ROJO: 0 veh | PK: 0\nVERDE: 25s", C_TEXT)
            self._log_terminal("IA", "YOLOv11n DESACTIVADO (CRUCE EN MODO NORMAL)")
            return

        if self.local_model is not None:
            self._ensure_yolo_worker()
            self._log_terminal("IA", "YOLOv11n REACTIVADO (CRUCE ADAPTATIVO ACTIVO)")
            return
        self._start_yolo_load(threading.Thread(target=self._load_ultralytics, daemon=True))

    def _start_yolo_load(self, thread):
        self.yolo_loading = True
        self.yolo_switch.configure(state="disabled")
        self._set_count_labels("CARGANDO YOLOv11n...", C_YELLOW)
        self._log_terminal("IA", f"CARGANDO PESOS {ULTRALYTICS_MODEL}...")
        thread.start()

    def _load_ultralytics(self):
        try:
            from ultralytics import YOLO
            model_path = ULTRALYTICS_MODEL
            if not os.path.exists(model_path):
                alt_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), model_path)
                if os.path.exists(alt_path):
                    model_path = alt_path
                else:
                    raise FileNotFoundError(f"No encontrado: {ULTRALYTICS_MODEL}")
            self.local_model = YOLO(model_path)
            self._ensure_yolo_worker()
            self._ui_call(lambda: self._set_count_labels(
                "ROJO: 0 veh | PK: 0\nVERDE: 25s", C_TEXT))
            self._log_terminal("IA", "YOLOv11n CARGADO EXITOSAMENTE")
        except Exception as e:
            err_msg = str(e)
            self._ui_call(lambda: self._yolo_failed(err_msg))
        finally:
            self.yolo_loading = False
            self._ui_call(lambda: self.yolo_switch.configure(state="normal"))

    def _yolo_failed(self, msg):
        self._yolo_active = False
        self.yolo_enabled.set(False)
        self._update_semaforo("[ ERROR YOLO ]", C_RED)
        self._set_count_labels("ERROR YOLOv11n", C_RED)
        self._log_terminal("ERR", f"FALLO MODELO YOLO: {msg}")

    def _ensure_yolo_worker(self):
        if not self._yolo_worker_running:
            self._yolo_worker_running = True
            threading.Thread(target=self._yolo_worker_loop, daemon=True).start()
        if not self._bg_worker_running:
            self._bg_worker_running = True
            threading.Thread(target=self._bg_red_worker_loop, daemon=True).start()

    def _yolo_worker_loop(self):
        """Ejecuta inferencia YOLOv11n + Doble ROI solo cuando el semáforo vinculado está en ROJO."""
        import torch
        while True:
            try:
                frame = self.yolo_input_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if not self._yolo_active or self.local_model is None:
                with self._yolo_lock:
                    self._latest_yolo_boxes = []
                continue

            try:
                active_src_snap = self._active_source
                linked_sids = self._get_semaphores_for_source(active_src_snap)
                active_sid = linked_sids[0] if linked_sids else 1
                estado_cruce = self.cruce.get_estado()
                sids_en_rojo = [sid for sid in linked_sids if estado_cruce[f"s{sid}"] == "ROJO"]

                # El semáforo solo activa el modelo y cuenta cuando está en ROJO
                if not sids_en_rojo:
                    with self._yolo_lock:
                        self._latest_yolo_boxes = []
                    cola_prev = estado_cruce[f"cola_s{active_sid}"]
                    green_calc = int(estado_cruce[f"ultimo_verde_s{active_sid}"])
                    sem_tag = ",".join(f"S{s}" for s in linked_sids) if linked_sids else "SIN SEM"
                    luz_actual = estado_cruce[f"s{active_sid}"]
                    label_text = f"{luz_actual} (EN ESPERA) | ULT:{cola_prev} veh\n{sem_tag} VERDE: {green_calc}s"
                    self._ui_call(lambda t=label_text: self._set_count_labels(t, C_MUTED))
                    continue

                start_inf = time.time()
                # FASE 2: YOLO imgsz dinámico (160 en Pi5 = 40% más rápido) con torch.no_grad() y lock seguro
                with self._yolo_infer_lock, torch.no_grad():
                    results = self.local_model(frame, imgsz=self._yolo_imgsz, verbose=False, conf=0.45)
                inf_time = (time.time() - start_inf) * 1000.0
                self.last_inference_time = inf_time
                self._yolo_inference_count += 1

                if inf_time > 500:
                    print(f"WARNING: Inferencia lenta: {inf_time:.0f}ms")
                if inf_time > 2000:
                    self._frame_stats["inference_timeouts"] += 1
                    self._yolo_active = False
                    with self._yolo_lock:
                        self._latest_yolo_boxes = []
                    self._ui_call(lambda: self.yolo_enabled.set(False))
                    self._log_terminal("ERR", f"TIMEOUT INFERENCIA ({inf_time:.0f}ms) - AUTO-KILL")
                    continue

                h, w = frame.shape[:2]
                pts_pk, pts_mv = self._get_scaled_rois(w, h, source_name=active_src_snap)
                cnt_pk = pts_pk.reshape((-1, 1, 2))
                cnt_mv = pts_mv.reshape((-1, 1, 2))

                cuadro_actual = []
                cuadro_estacionados = []
                cuadro_fuera = []

                for box in results[0].boxes:
                    x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                    conf_val = float(box.conf[0])
                    # Punto de contacto inferior central del vehículo con la calzada
                    cx = int((x1 + x2) / 2)
                    cy = int(y2)

                    # Prioridad 1: Zona de Parqueo (Exclusión)
                    if cv2.pointPolygonTest(cnt_pk, (float(cx), float(cy)), False) >= 0:
                        cuadro_estacionados.append((x1, y1, x2, y2, conf_val))
                    # Prioridad 2: Zona de Movimiento / Conteo en Rojo
                    elif cv2.pointPolygonTest(cnt_mv, (float(cx), float(cy)), False) >= 0:
                        cuadro_actual.append((x1, y1, x2, y2, cx, cy, conf_val))
                    else:
                        cuadro_fuera.append((x1, y1, x2, y2, conf_val))

                boxes_out = []

                # 1. Vehículos fuera de ambas ROIs (Gris discreto, normalizados 0..1)
                for (fx1, fy1, fx2, fy2, _) in cuadro_fuera:
                    boxes_out.append((fx1 / float(w), fy1 / float(h), fx2 / float(w), fy2 / float(h), "", (120, 120, 120)))

                # 2. Vehículos en Zona de Parqueo (Naranja, normalizados 0..1)
                for (px1, py1, px2, py2, _) in cuadro_estacionados:
                    boxes_out.append((px1 / float(w), py1 / float(h), px2 / float(w), py2 / float(h), "Parqueado", (0, 165, 255)))

                # 3. Vehículos contados en ROJO dentro de la ROI de vía
                for idx_v, (x1, y1, x2, y2, cx, cy, conf_val) in enumerate(cuadro_actual, start=1):
                    boxes_out.append((x1 / float(w), y1 / float(h), x2 / float(w), y2 / float(h), f"V{idx_v}", (0, 0, 255)))

                with self._yolo_lock:
                    self._latest_yolo_boxes = boxes_out

                total_rojo = len(cuadro_actual)
                parqueados_count = len(cuadro_estacionados)

                # Alimentar la cola únicamente de los semáforos vinculados que están en ROJO
                for sid in sids_en_rojo:
                    self.cruce.registrar_detenidos(sid, total_rojo)

                green_calc = int(self.cruce.get_estado()[f"ultimo_verde_s{active_sid}"])
                sem_tag = ",".join(f"S{s}" for s in sids_en_rojo)
                label_text = f"ROJO: {total_rojo} veh | PK: {parqueados_count}\n{sem_tag} VERDE: {green_calc}s"

                self._ui_call(lambda t=label_text: self._set_count_labels(t, C_TEXT))
            except Exception as e:
                print(f"Error worker YOLO: {e}")

    def _bg_red_worker_loop(self):
        """Muestrea periódicamente (/shot.jpg o /capture) las cámaras en ROJO no visibles en pantalla."""
        import torch
        while self._ui_alive:
            time.sleep(1.5)
            if not self._ui_alive or not self._yolo_active or self.yolo_loading or self.local_model is None:
                continue
            try:
                estado = self.cruce.get_estado()
                luces = {1: estado["s1"], 2: estado["s2"], 3: estado["s3"]}
                active_src = self._active_source
                now_ts = time.time()

                # Caché por fuente para no consultar 2 veces la misma cámara si está asignada a 2 semáforos
                sampled_counts = {}
                for sid in (1, 2, 3):
                    if not self._ui_alive or not self._yolo_active:
                        break
                    if luces[sid] != "ROJO":
                        continue
                    src = self.sem_source_map.get(sid, "Ninguna")
                    if src == "Ninguna" or src == active_src:
                        continue
                    if src not in ("TEL-1", "TEL-2", "TEL-3"):
                        continue

                    if src in sampled_counts:
                        self.cruce.registrar_detenidos(sid, sampled_counts[src])
                        continue

                    cam_id = 2 if src == "TEL-2" else (3 if src == "TEL-3" else 1)
                    cam = self.phone_cameras[cam_id]
                    if now_ts < cam.get("backoff_until", 0.0):
                        continue

                    try:
                        resp = requests.get(cam["shot"], timeout=0.75, headers={"Connection": "close"})
                        if resp.status_code != 200 or not resp.content:
                            cam["backoff_until"] = now_ts + 3.0
                            continue
                        bg_img = cv2.imdecode(np.frombuffer(resp.content, dtype=np.uint8), cv2.IMREAD_COLOR)
                        if bg_img is None:
                            continue
                        sz = 274
                        bg_img = cv2.resize(bg_img, (sz, sz), interpolation=cv2.INTER_LINEAR)
                        with self._yolo_infer_lock, torch.no_grad():
                            res = self.local_model(bg_img, imgsz=self._yolo_imgsz, verbose=False, conf=0.45)

                        pts_pk, pts_mv = self._get_scaled_rois(sz, sz, source_name=src)
                        cnt_pk = pts_pk.reshape((-1, 1, 2))
                        cnt_mv = pts_mv.reshape((-1, 1, 2))
                        count_red = 0
                        for box in res[0].boxes:
                            bx1, _, bx2, by2 = map(int, box.xyxy[0].tolist())
                            cx = int((bx1 + bx2) / 2)
                            cy = int(by2)
                            if cv2.pointPolygonTest(cnt_pk, (float(cx), float(cy)), False) >= 0:
                                continue
                            if cv2.pointPolygonTest(cnt_mv, (float(cx), float(cy)), False) >= 0:
                                count_red += 1

                        sampled_counts[src] = count_red
                        self.cruce.registrar_detenidos(sid, count_red)
                        cam["online"] = True
                    except Exception:
                        cam["backoff_until"] = time.time() + 3.0
            except Exception as e:
                print(f"Error worker segundo plano ROJO: {e}")

    def _update_semaforo(self, text, color):
        def _set_sem(col):
            try:
                if hasattr(self, "lbl_mini_semaforo"):
                    self.lbl_mini_semaforo.configure(text=text, text_color=col)
            except Exception:
                pass
        self._ui_call(lambda: _set_sem(color))

    def _flush_alerts_buffer(self):
        """Escribe buffer de eventos de semáforo a CSV en batch (reduce I/O)."""
        if not self._alerts_buffer:
            return
        
        try:
            save_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "registros")
            os.makedirs(save_dir, exist_ok=True)
            csv_path = f"{save_dir}/historial_semaforo.csv"
            file_exists = os.path.exists(csv_path)
            
            with open(csv_path, "a", newline="") as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(["Timestamp", "Vehiculos_En_Rojo", "Tiempo_Verde_Seg", "Estado"])
                writer.writerows(self._alerts_buffer)
                f.flush()
                os.fsync(f.fileno())  # Garantizar escritura a disco
            
            self._log_terminal("CSV", f"{len(self._alerts_buffer)} CICLOS GUARDADOS EN HISTORIAL_SEMAFORO.CSV")
            self._alerts_buffer.clear()
        except Exception as e:
            print(f"Error escribiendo historial semáforo: {e}")

    # ── UTILIDADES DE CÁMARA & SNAPSHOT ─────────────────────────────

    def _update_camera_settings(self, _=None):
        self._brightness_val = float(self.brightness.get())
        self._contrast_val = float(self.contrast.get())

    def _apply_adjustments(self, frame):
        alpha = self._contrast_val
        beta = int(self._brightness_val)
        if beta == 0 and abs(alpha - 1.0) < 0.02:
            return frame
        return cv2.convertScaleAbs(frame, alpha=alpha, beta=beta)

    def _take_snapshot(self, auto=False):
        ts = int(time.time())
        save_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "registros")
        os.makedirs(save_dir, exist_ok=True)
        
        prefix = "AUTO_" if auto else "MANUAL_"
        selection = self.camera_source_var.get()
        
        if self._latest_frame is not None:
            tag = selection.replace(" ", "_").replace("(", "").replace(")", "").replace("#", "")
            path = f"{save_dir}/{prefix}{tag}_{ts}.jpg"
            cv2.imwrite(path, self._latest_frame)
            self._log_terminal("SNAP", f"CAPTURADO {tag}: {path}")

    def on_closing(self):
        print("Cerrando...")
        self._ui_alive = False
        for ctrl in self.semaforos_hw.values():
            try:
                ctrl.close()
            except Exception:
                pass
        # FASE 3: Flush final de buffers antes de cerrar
        self._flush_metrics_buffer()
        self._flush_alerts_buffer()
        self._close_cameras()
        self.destroy()


if __name__ == "__main__":
    setup_roi_flag = ("--calibrar-roi" in sys.argv) or ("--setup-roi" in sys.argv)
    app = ModernCameraApp(open_roi_setup_on_start=setup_roi_flag)
    app.protocol("WM_DELETE_WINDOW", app.on_closing)
    app.mainloop()
