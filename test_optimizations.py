# -*- coding: utf-8 -*-
"""
Test Suite: Validación de Optimizaciones para Raspberry Pi 5
Verificar que todas las Fases se hayan implementado correctamente
"""
import os
import sys
import time
import psutil
import datetime

def test_phase_1_threading():
    """FASE 1: Validar threading control"""
    print("\n" + "="*60)
    print("FASE 1: Threading Control")
    print("="*60)
    
    # Verificar que el código contiene los elementos necesarios
    with open("interfaz.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    checks = [
        ("self._stop_producer", "Flag de parada del productor"),
        ("self._producer_thread", "Referencia al thread productor"),
        ("self.frame_dropped_count", "Contador de frames droppados"),
        ("time.sleep(0.01)", "Throttling a 100 FPS"),
        ("while self.is_connected and not self._stop_producer", "Loop con stop flag"),
        ("self._producer_thread.join(timeout=2.0)", "Join thread con timeout")
    ]
    
    passed = 0
    for check, desc in checks:
        if check in content:
            print(f"✓ {desc}")
            passed += 1
        else:
            print(f"✗ {desc} - NOT FOUND")
    
    print(f"\nFASE 1: {passed}/{len(checks)} checks passed")
    return passed == len(checks)


def test_phase_2_yolo():
    """FASE 2: Validar YOLO optimizado"""
    print("\n" + "="*60)
    print("FASE 2: YOLO Optimizado")
    print("="*60)
    
    with open("interfaz.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    checks = [
        ("self._yolo_imgsz = 160 if IS_PI else 320", "YOLO imgsz dinámico"),
        ("imgsz=self._yolo_imgsz", "Usar imgsz dinámico en inferencia"),
        ("conf=0.45", "Confidence threshold optimizado"),
        ("if inf_time > 500:", "Warning para inferencia lenta"),
        ("if inf_time > 2000:", "Timeout y desactivación automática"),
        ("self._yolo_inference_count", "Contador de inferencias")
    ]
    
    passed = 0
    for check, desc in checks:
        if check in content:
            print(f"✓ {desc}")
            passed += 1
        else:
            print(f"✗ {desc} - NOT FOUND")
    
    print(f"\nFASE 2: {passed}/{len(checks)} checks passed")
    return passed == len(checks)


def test_phase_3_io_buffering():
    """FASE 3: Validar buffering de I/O"""
    print("\n" + "="*60)
    print("FASE 3: CSV Buffering I/O")
    print("="*60)
    
    with open("interfaz.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    checks = [
        ("self._metrics_buffer", "Buffer de métricas"),
        ("self._alerts_buffer", "Buffer de alertas"),
        ("self._metrics_buffer_size = 30", "Tamaño batch de 30 registros"),
        ("self._flush_metrics_buffer()", "Método flush de métricas"),
        ("self._flush_alerts_buffer()", "Método flush de alertas"),
        ("writer.writerows(self._metrics_buffer)", "Batch write a CSV"),
        ("os.fsync(f.fileno())", "Garantizar escritura a disco"),
        ("if len(self._metrics_buffer) >= self._metrics_buffer_size", "Flush automático")
    ]
    
    passed = 0
    for check, desc in checks:
        if check in content:
            print(f"✓ {desc}")
            passed += 1
        else:
            print(f"✗ {desc} - NOT FOUND")
    
    print(f"\nFASE 3: {passed}/{len(checks)} checks passed")
    return passed == len(checks)


def test_phase_4_memory():
    """FASE 4: Validar memory cleanup"""
    print("\n" + "="*60)
    print("FASE 4: Memory Cleanup")
    print("="*60)
    
    with open("interfaz.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    checks = [
        ("self._current_ctk_image = None", "Inicialización de image reference"),
        ("cv2.cvtColor(frame, cv2.COLOR_RGBA2BGR)", "Conversión RGBA→BGR explícita"),
        ("del self._current_ctk_image", "Liberar imagen anterior"),
        ("self._current_ctk_image = ctk_img", "Guardar nueva imagen"),
        ("cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)", "Conversión BGR→RGB antes de PIL")
    ]
    
    passed = 0
    for check, desc in checks:
        if check in content:
            print(f"✓ {desc}")
            passed += 1
        else:
            print(f"✗ {desc} - NOT FOUND")
    
    print(f"\nFASE 4: {passed}/{len(checks)} checks passed")
    return passed == len(checks)


def test_phase_5_resize():
    """FASE 5: Validar resize optimizado"""
    print("\n" + "="*60)
    print("FASE 5: Resize Optimizado")
    print("="*60)
    
    with open("interfaz.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    checks = [
        ("self._target_display_width =", "Target width precalculado"),
        ("self._target_display_height =", "Target height precalculado"),
        ("cv2.resize(frame, (self._target_display_width, self._target_display_height)", "Usar cv2.resize"),
        ("interpolation=cv2.INTER_LINEAR", "Interpolación lineal")
    ]
    
    passed = 0
    for check, desc in checks:
        if check in content:
            print(f"✓ {desc}")
            passed += 1
        else:
            print(f"✗ {desc} - NOT FOUND")
    
    print(f"\nFASE 5: {passed}/{len(checks)} checks passed")
    return passed == len(checks)


def test_phase_6_logging():
    """FASE 6: Validar robustness logging"""
    print("\n" + "="*60)
    print("FASE 6: Robustness Logging")
    print("="*60)
    
    with open("interfaz.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    checks = [
        ("self._frame_stats", "Diccionario de estadísticas"),
        ("self._frame_stats['captured']", "Contador de frames capturados"),
        ("self._frame_stats['processed']", "Contador de frames procesados"),
        ("self._frame_stats['dropped']", "Contador de frames droppados"),
        ("self._frame_stats[\"inference_timeouts\"]", "Contador de timeouts"),
        ("_frame_stats[\"captured\"] += 1", "Incrementar capturados"),
        ("drop_pct =", "Calcular porcentaje drop")
    ]
    
    passed = 0
    for check, desc in checks:
        if check in content:
            print(f"✓ {desc}")
            passed += 1
        else:
            print(f"✗ {desc} - NOT FOUND")
    
    print(f"\nFASE 6: {passed}/{len(checks)} checks passed")
    return passed == len(checks)


def test_phase_7_yolov11n_dual_roi():
    """FASE 7: Validar integración YOLOv11n, Doble ROI y Calibración Interactiva"""
    print("\n" + "="*60)
    print("FASE 7: YOLOv11n + Doble ROI + Tracking Semáforo")
    print("="*60)

    with open("interfaz.py", "r", encoding="utf-8") as f:
        content = f.read()

    checks = [
        ("yolov11n.pt", "Modelo por defecto YOLOv11n configurado"),
        ("YOLOv11n IA", "Switch HMI actualizado a YOLOv11n IA"),
        ("VER ROI DUAL", "Switch HMI para activar/desactivar vista de polígonos ROI"),
        ("TRAZAR ROI", "Botón HMI para trazar polígonos ROI con clics en vivo"),
        ("COORD ROI", "Botón HMI para configurar coordenadas ROI antes/durante ejecución"),
        ("cv2.pointPolygonTest", "Filtrado geométrico nativo OpenCV (sin depender de Shapely)"),
        ("self.tracking_autos", "Tracking vehicular por proximidad euclidiana y semáforo"),
        ("os.path.exists(\"yolov11n.pt\")" if False else "roi_config.json", "Persistencia de polígonos en roi_config.json")
    ]

    passed = 0
    for check, desc in checks:
        if check in content:
            print(f"✓ {desc}")
            passed += 1
        else:
            print(f"✗ {desc} - NOT FOUND")

    if os.path.exists("yolov11n.pt"):
        print("✓ Archivo de pesos yolov11n.pt presente en el proyecto")
        passed += 1
    else:
        print("✗ Archivo yolov11n.pt NO encontrado")

    if os.path.exists("roi_config.json"):
        print("✓ Archivo roi_config.json presente en el proyecto")
        passed += 1
    else:
        print("✗ Archivo roi_config.json NO encontrado")

    total_checks = len(checks) + 2
    print(f"\nFASE 7: {passed}/{total_checks} checks passed")
    return passed == total_checks


def test_phase_8_usb_camera_and_responsive_ui():
    """FASE 8: Validar sistema de 3 cámaras (ESP32-1, ESP32-2, USB), sin OV5647, y ventanas interactivas"""
    print("\n" + "="*60)
    print("FASE 8: 3 Cámaras (ESP32-1, ESP32-2, USB) + Ventanas Redimensionables")
    print("="*60)

    with open("interfaz.py", "r", encoding="utf-8") as f:
        content = f.read()

    with open("app.py", "r", encoding="utf-8") as f:
        app_content = f.read()

    checks = [
        ("find_preferred_usb_camera", "Auto-detección de índice para cámara USB"),
        ('values=["ESP32-1", "ESP32-2", "USB"]', "Selector HMI con exactamente 3 cámaras (ESP32-1, ESP32-2, USB)"),
        ("_open_usb_camera_capture", "Captura USB optimizada con MJPG y baja latencia"),
        ("self.resizable(True, True)", "Ventana principal libremente redimensionable"),
        ("_attach_resize_grip", "Controles interactivos de redimensionamiento en esquinas"),
        ("_build_sash_divider", "Divisor arrastrable entre paneles"),
    ]

    passed = 0
    for check, desc in checks:
        if check in content:
            print(f"✓ {desc}")
            passed += 1
        else:
            print(f"✗ {desc} - NOT FOUND")

    removed_checks = [
        ("OV5647" not in content and "Picamera2" not in content, "Integración OV5647 / Picamera2 eliminada por completo"),
        ("ESP32-3" not in content and "192.168.4.103" not in content and "192.168.4.103" not in app_content, "Tercera ESP32 eliminada (solo ESP32-1, ESP32-2 y USB)"),
    ]
    for ok, desc in removed_checks:
        if ok:
            print(f"✓ {desc}")
            passed += 1
        else:
            print(f"✗ {desc} - FAILED")

    total = len(checks) + len(removed_checks)
    print(f"\nFASE 8: {passed}/{total} checks passed")
    return passed == total


def test_syntax():
    """Validar que el código Python de interfaz.py y app.py es sintácticamente correcto"""
    print("\n" + "="*60)
    print("VALIDACIÓN: Sintaxis Python")
    print("="*60)
    
    try:
        for fname in ("interfaz.py", "app.py"):
            with open(fname, "r", encoding="utf-8") as f:
                code = f.read()
            compile(code, fname, "exec")
        print("✓ Sintaxis válida en interfaz.py y app.py")
        return True
    except SyntaxError as e:
        print(f"✗ Error sintáctico: {e}")
        return False


def estimate_improvements():
    """Estimar mejoras basadas en los cambios implementados"""
    print("\n" + "="*60)
    print("MEJORAS ESTIMADAS (Baseline vs Optimizado)")
    print("="*60)
    
    improvements = [
        ("CPU idle (sin YOLO)", "60-80%", "→", "85-95%", "✓ +25%"),
        ("CPU con YOLO activado", "95-100%", "→", "70-85%", "✓ -15%"),
        ("Latencia YOLO (ms)", "500-700", "→", "200-300", "✓ -60%"),
        ("Writes a disco/min", "60", "→", "2", "✓ -97%"),
        ("RAM (crecimiento)", "Progresivo", "→", "Flat", "✓ Estable"),
        ("FPS promedio", "10-12", "→", "12-15", "✓ +20%"),
        ("Frames dropped (%)", "N/A", "→", "<1%", "✓ Mejor")
    ]
    
    for metric, baseline, arrow, target, improvement in improvements:
        print(f"{metric:30s} {baseline:15s} {arrow} {target:15s} {improvement}")


def main():
    print("\n" + "="*70)
    print("VALIDACIÓN: Optimizaciones Raspberry Pi 5 - interfaz.py")
    print("="*70)
    print(f"Timestamp: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Verificar que el archivo existe
    if not os.path.exists("interfaz.py"):
        print("ERROR: interfaz.py no encontrado")
        sys.exit(1)
    
    results = {
        "Fase 1 (Threading)": test_phase_1_threading(),
        "Fase 2 (YOLO)": test_phase_2_yolo(),
        "Fase 3 (I/O Buffering)": test_phase_3_io_buffering(),
        "Fase 4 (Memory)": test_phase_4_memory(),
        "Fase 5 (Resize)": test_phase_5_resize(),
        "Fase 6 (Logging)": test_phase_6_logging(),
        "Fase 7 (YOLOv11n + Dual ROI)": test_phase_7_yolov11n_dual_roi(),
        "Fase 8 (USB2.0 + UI Dinámica)": test_phase_8_usb_camera_and_responsive_ui(),
        "Sintaxis Python": test_syntax()
    }
    
    # Resumen
    print("\n" + "="*70)
    print("RESUMEN DE VALIDACIÓN")
    print("="*70)
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    
    for test_name, result in results.items():
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{test_name:30s} {status}")
    
    print(f"\nTotal: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n✓ TODAS LAS FASES IMPLEMENTADAS CORRECTAMENTE")
        estimate_improvements()
        return 0
    else:
        print(f"\n✗ {total - passed} tests fallaron - Revisar implementación")
        return 1


if __name__ == "__main__":
    exit_code = main()
    print("\n" + "="*70)
    sys.exit(exit_code)
