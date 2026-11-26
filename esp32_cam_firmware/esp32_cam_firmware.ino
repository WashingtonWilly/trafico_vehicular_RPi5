/*
 * ==============================================================================
 * ESP32-CAM Firmware — Monitoreo Tri-Cámara con Raspberry Pi 5
 * ==============================================================================
 * Instrucciones:
 * 1. Para programar cada cámara Wi-Fi, cambia el número CAM_ID (1 o 2) en la línea 25.
 * 2. Selecciona la placa "AI Thinker ESP32-CAM" en Arduino IDE.
 * 3. Conecta el jumper GPIO 0 a GND durante la carga del firmware.
 * ==============================================================================
 */

#include "esp_camera.h"
#include <WiFi.h>
#include "esp_timer.h"
#include "img_converters.h"
#include "Arduino.h"
#include "fb_gfx.h"
#include "soc/soc.h"
#include "soc/rtc_cntl_reg.h"
#include "esp_http_server.h"

// ==============================================================================
// CONFIGURACIÓN INDIVIDUAL DE CÁMARA (Cambiar este ID para cada ESP32-CAM)
// ==============================================================================
#define CAM_ID 1   // <-- Cambiar a 1 (ESP32-1) o 2 (ESP32-2)

// Credenciales Wi-Fi (Punto de Acceso creado por la Raspberry Pi 5)
const char* ssid     = "ESP32_CAM_NET";
const char* password = "esp32cam1234";

// Configuración de IP Estática según el CAM_ID
IPAddress local_IP(192, 168, 4, 100 + CAM_ID); // 101 (ESP32-1) o 102 (ESP32-2)
IPAddress gateway(192, 168, 4, 1);
IPAddress subnet(255, 255, 255, 0);
IPAddress primaryDNS(192, 168, 4, 1);

// Definición de Pines para Modelo AI-THINKER
#define PWDN_GPIO_NUM     32
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM      0
#define SIOD_GPIO_NUM     26
#define SIOC_GPIO_NUM     27
#define Y9_GPIO_NUM       35
#define Y8_GPIO_NUM       34
#define Y7_GPIO_NUM       39
#define Y6_GPIO_NUM       36
#define Y5_GPIO_NUM       21
#define Y4_GPIO_NUM       19
#define Y3_GPIO_NUM       18
#define Y2_GPIO_NUM        5
#define VSYNC_GPIO_NUM    25
#define HREF_GPIO_NUM     23
#define PCLK_GPIO_NUM      22

#define LED_BUILTIN_RED   33 // LED en la placa (Lógica invertida: LOW = Encendido)
#define LED_FLASH_WHITE    4 // LED Flash ultra-brillante traseros

httpd_handle_t stream_httpd = NULL;

#define PART_BOUNDARY "123456789000000000000987654321"
static const char* _STREAM_CONTENT_TYPE = "multipart/x-mixed-replace;boundary=" PART_BOUNDARY;
static const char* _STREAM_BOUNDARY = "\r\n--" PART_BOUNDARY "\r\n";
static const char* _STREAM_PART = "Content-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n";

// Handler para transmisión continua de video MJPEG
static esp_err_t stream_handler(httpd_req_t *req) {
  camera_fb_t * fb = NULL;
  esp_err_t res = ESP_OK;
  size_t _jpg_buf_len = 0;
  uint8_t * _jpg_buf = NULL;
  char part_buf[64];

  res = httpd_resp_set_type(req, _STREAM_CONTENT_TYPE);
  if (res != ESP_OK) return res;

  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");

  while (true) {
    fb = esp_camera_fb_get();
    if (!fb) {
      Serial.println("Error al capturar cuadro de cámara");
      res = ESP_FAIL;
    } else {
      _jpg_buf_len = fb->len;
      _jpg_buf = fb->buf;
    }

    if (res == ESP_OK) {
      res = httpd_resp_send_chunk(req, _STREAM_BOUNDARY, strlen(_STREAM_BOUNDARY));
    }
    if (res == ESP_OK) {
      size_t hlen = snprintf(part_buf, 64, _STREAM_PART, _jpg_buf_len);
      res = httpd_resp_send_chunk(req, part_buf, hlen);
    }
    if (res == ESP_OK) {
      res = httpd_resp_send_chunk(req, (const char *)_jpg_buf, _jpg_buf_len);
    }

    if (fb) {
      esp_camera_fb_return(fb);
      fb = NULL;
      _jpg_buf = NULL;
    } else {
      break;
    }

    if (res != ESP_OK) {
      break;
    }
  }

  return res;
}

// Handler para captura de 1 solo cuadro JPEG
static esp_err_t capture_handler(httpd_req_t *req) {
  camera_fb_t * fb = esp_camera_fb_get();
  if (!fb) {
    httpd_resp_send_500(req);
    return ESP_FAIL;
  }
  httpd_resp_set_type(req, "image/jpeg");
  httpd_resp_set_hdr(req, "Content-Disposition", "inline; filename=capture.jpg");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  esp_err_t res = httpd_resp_send(req, (const char *)fb->buf, fb->len);
  esp_camera_fb_return(fb);
  return res;
}

// Handler para estado de la cámara (JSON)
static esp_err_t status_handler(httpd_req_t *req) {
  char json_response[256];
  snprintf(json_response, sizeof(json_response), 
           "{\"cam_id\":%d,\"ip\":\"%s\",\"uptime_ms\":%lu,\"rssi\":%d}",
           CAM_ID, WiFi.localIP().toString().c_str(), millis(), WiFi.RSSI());

  httpd_resp_set_type(req, "application/json");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  return httpd_resp_send(req, json_response, strlen(json_response));
}

void startCameraServer() {
  httpd_config_t config = HTTPD_DEFAULT_CONFIG();
  config.server_port = 80;

  httpd_uri_t stream_uri = {
    .uri       = "/stream",
    .method    = HTTP_GET,
    .handler   = stream_handler,
    .user_ctx  = NULL
  };

  httpd_uri_t capture_uri = {
    .uri       = "/capture",
    .method    = HTTP_GET,
    .handler   = capture_handler,
    .user_ctx  = NULL
  };

  httpd_uri_t status_uri = {
    .uri       = "/status",
    .method    = HTTP_GET,
    .handler   = status_handler,
    .user_ctx  = NULL
  };

  if (httpd_start(&stream_httpd, &config) == ESP_OK) {
    httpd_register_uri_handler(stream_httpd, &stream_uri);
    httpd_register_uri_handler(stream_httpd, &capture_uri);
    httpd_register_uri_handler(stream_httpd, &status_uri);
    Serial.println("Servidor HTTP iniciado en puerto 80");
  }
}

void setup() {
  WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0); // Desactivar detector de Brownout brusco

  pinMode(LED_BUILTIN_RED, OUTPUT);
  pinMode(LED_FLASH_WHITE, OUTPUT);
  digitalWrite(LED_BUILTIN_RED, HIGH); // Apagado inicial
  digitalWrite(LED_FLASH_WHITE, LOW);

  Serial.begin(115200);
  Serial.setDebugOutput(true);
  Serial.println();
  Serial.printf("=== ESP32-CAM Unidad #%d Inicializando ===\n", CAM_ID);

  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sscb_sda = SIOD_GPIO_NUM;
  config.pin_sscb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  
  // Ajuste de resolución para optimizar 3 transmisiones simultáneas
  if(psramFound()){
    config.frame_size = FRAMESIZE_VGA;  // 640x480
    config.jpeg_quality = 12;            // 10-63 (Menor = mayor calidad)
    config.fb_count = 2;
  } else {
    config.frame_size = FRAMESIZE_QVGA; // 320x240 si no hay PSRAM
    config.jpeg_quality = 15;
    config.fb_count = 1;
  }

  // Inicializar Cámara
  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Fallo al inicializar la cámara con error 0x%x\n", err);
    return;
  }

  // Configurar IP Estática
  if (!WiFi.config(local_IP, gateway, subnet, primaryDNS)) {
    Serial.println("Error al configurar IP Estática");
  }

  // Conectar a Wi-Fi (Red AP de la Raspberry Pi 5)
  WiFi.begin(ssid, password);
  Serial.print("Conectando a Wi-Fi AP de RPi 5: ");
  Serial.println(ssid);

  int retries = 0;
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
    digitalWrite(LED_BUILTIN_RED, !digitalRead(LED_BUILTIN_RED)); // Parpadeo LED
    retries++;
    if (retries > 40) {
      Serial.println("\nReintentando conexión Wi-Fi...");
      WiFi.begin(ssid, password);
      retries = 0;
    }
  }

  digitalWrite(LED_BUILTIN_RED, LOW); // Encedido sólido indicando conexión exitosa
  Serial.println("");
  Serial.println("Wi-Fi Conectado con éxito!");
  Serial.print("Dirección IP Estática: http://");
  Serial.println(WiFi.localIP());

  startCameraServer();
  Serial.printf("Cámara #%d Lista! Transmitiendo en http://%s/stream\n", CAM_ID, WiFi.localIP().toString().c_str());
}

void loop() {
  // El servidor HTTP corre en tareas en segundo plano de ESP-IDF
  delay(10000);
}
