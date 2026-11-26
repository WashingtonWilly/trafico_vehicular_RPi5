#!/bin/bash
# ==============================================================================
# Script de Configuracion de Punto de Acceso (AP Wi-Fi) en Raspberry Pi 5
# Red optimizada para 3 Camaras ESP32-CAM (Rango: 192.168.4.x)
# ==============================================================================

if [ "$EUID" -ne 0 ]; then
  echo "[ERROR] Este script debe ejecutarse como root. Usa: sudo bash setup_rpi5_ap.sh"
  exit 1
fi

SSID_NAME="ESP32_CAM_NET"
WIFI_PASS="esp32cam1234"
AP_IP="192.168.4.1/24"

echo "[1/5] Deshabilitando Bluetooth para evitar interferencia en 2.4 GHz..."
systemctl stop bluetooth 2>/dev/null || true
systemctl disable bluetooth 2>/dev/null || true
/usr/sbin/rfkill block bluetooth 2>/dev/null || true
/usr/sbin/iw reg set EC 2>/dev/null || true

echo "[2/5] Deteniendo posibles hotspots previos de nmcli..."
nmcli connection down Hotspot 2>/dev/null || true
nmcli connection delete Hotspot 2>/dev/null || true

echo "[3/5] Creando nuevo Hotspot Wi-Fi '$SSID_NAME'..."
nmcli device wifi hotspot ifname wlan0 ssid "$SSID_NAME" password "$WIFI_PASS"

echo "[4/5] Optimizando parametros de radio 2.4 GHz y desactivando ahorro de energia..."
nmcli connection modify Hotspot \
  ipv4.addresses "$AP_IP" \
  ipv4.method shared \
  connection.autoconnect yes \
  802-11-wireless.band bg \
  802-11-wireless.channel 11 \
  802-11-wireless.powersave 2 \
  802-11-wireless-security.pmf 1 \
  802-11-wireless-security.proto rsn \
  802-11-wireless-security.pairwise ccmp \
  802-11-wireless-security.group ccmp

echo "[5/5] Activando la red Hotspot..."
nmcli connection up Hotspot
/usr/sbin/iw dev wlan0 set power_save off 2>/dev/null || true

echo "--------------------------------------------------------"
echo " Punto de Acceso Wi-Fi Optimizado Activo"
echo " SSID: $SSID_NAME | IP: 192.168.4.1 | Canal: 11 - 2.4GHz"
echo "--------------------------------------------------------"
