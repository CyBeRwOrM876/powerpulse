/*
 * PowerPulse measurement node (ESP32) - team ZedWatts
 *
 * 6 x SCT-013-000 CT clamps -> 2 x ADS1115 (0x48, 0x49) -> ESP32; PZEM-004T v3 on the incomer for V, PF, Hz.
 * Publishes signed telemetry over MQTT/TLS every 2 s. When Wi-Fi or the broker is unreachable (typical during
 * an outage) it stores records on flash and uploads them later with their original timestamps.
 *
 * Libraries (Library Manager): Adafruit ADS1X15, PZEM004Tv30 (mandulaj), PubSubClient, RTClib (optional).
 * Board: "ESP32 Dev Module", Arduino-ESP32 core 2.0.x or 3.x.
 * NOT tested on hardware in this repository. Follow docs/GUIDE.md section "Bench test" before installing in a board.
 */
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <Wire.h>
#include <Adafruit_ADS1X15.h>
#include <PZEM004Tv30.h>
#include <Preferences.h>
#include <LittleFS.h>
#include <time.h>
#include <sys/time.h>
#include <esp_task_wdt.h>
#include "mbedtls/md.h"
#include "config.h"
#ifdef USE_DS3231
#include <RTClib.h>
RTC_DS3231 rtc;
#endif

#define NUM_CH 6
#define SDA_PIN 21
#define SCL_PIN 22
#define PZEM_RX 16
#define PZEM_TX 17
#define BACKLOG_FILE "/backlog.txt"
#define BACKLOG_MAX_BYTES 200000
#define FLUSH_PER_LOOP 20

Adafruit_ADS1115 ads1, ads2;
PZEM004Tv30 pzem(Serial2, PZEM_RX, PZEM_TX);
WiFiClientSecure net;
PubSubClient mqtt(net);
Preferences prefs;

static const uint16_t MUX[3] = {ADS1X15_REG_CONFIG_MUX_SINGLE_0, ADS1X15_REG_CONFIG_MUX_SINGLE_1, ADS1X15_REG_CONFIG_MUX_SINGLE_2};
static const double ICAL = (CT_RATIO / BURDEN_OHMS) * ADS_LSB_VOLTS;  // amps per ADC count (approx 0.0139)
float calFactor[NUM_CH] = {1, 1, 1, 1, 1, 1};
float irms[NUM_CH] = {0};
uint32_t seqNo = 0;
uint8_t keyBytes[32];
int16_t bufA[520], bufB[520];
uint32_t lastPublish = 0, lastOfflineLog = 0, lastWifiTry = 0, lastMqttTry = 0;
int pzemFails = 0;

// ---------- helpers ----------
static void hexToBytes(const char* hex, uint8_t* out, size_t n) {
  for (size_t i = 0; i < n; i++) { unsigned v; sscanf(hex + 2 * i, "%2x", &v); out[i] = (uint8_t)v; }
}

static void hmacHex(const char* msg, char* outHex) {  // outHex must hold 65 bytes
  uint8_t mac[32];
  mbedtls_md_context_t ctx;
  mbedtls_md_init(&ctx);
  mbedtls_md_setup(&ctx, mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), 1);
  mbedtls_md_hmac_starts(&ctx, keyBytes, 32);
  mbedtls_md_hmac_update(&ctx, (const uint8_t*)msg, strlen(msg));
  mbedtls_md_hmac_finish(&ctx, mac);
  mbedtls_md_free(&ctx);
  for (int i = 0; i < 32; i++) sprintf(outHex + 2 * i, "%02x", mac[i]);
}

static bool timeValid() { return time(nullptr) > 1700000000; }

static void setSystemTime(time_t t) { struct timeval tv = {t, 0}; settimeofday(&tv, nullptr); }

static void syncClock() {
  configTime(0, 0, "pool.ntp.org", "time.google.com");  // UTC; the server converts to local time
  struct tm tmv;
  if (getLocalTime(&tmv, 3000)) {
#ifdef USE_DS3231
    rtc.adjust(DateTime(time(nullptr)));
#endif
  }
}

// ---------- measurement ----------
// Reads channel k of both ADCs in lockstep for WINDOW_MS and returns the true RMS of each (DC offset removed).
static void measurePair(int k, float& a, float& b) {
  ads1.startADCReading(MUX[k], true);
  ads2.startADCReading(MUX[k], true);
  delay(3);  // discard the first conversion after the mux change
  uint32_t n = 0, t0 = millis(), last = micros();
  while (millis() - t0 < WINDOW_MS && n < 520) {
    while (micros() - last < 1200) { }  // ~860 samples per second
    last += 1200;
    bufA[n] = ads1.getLastConversionResults();
    bufB[n] = ads2.getLastConversionResults();
    n++;
  }
  if (n < 50) { a = b = 0; return; }
  double mA = 0, mB = 0;
  for (uint32_t i = 0; i < n; i++) { mA += bufA[i]; mB += bufB[i]; }
  mA /= n; mB /= n;
  double sA = 0, sB = 0;
  for (uint32_t i = 0; i < n; i++) { double x = bufA[i] - mA, y = bufB[i] - mB; sA += x * x; sB += y * y; }
  a = sqrt(sA / n) * ICAL * calFactor[k];
  b = sqrt(sB / n) * ICAL * calFactor[k + 3];
  if (a < NOISE_FLOOR_A) a = 0;
  if (b < NOISE_FLOOR_A) b = 0;
}

static void measureAll() {
  for (int k = 0; k < 3; k++) { float a, b; measurePair(k, a, b); irms[k] = a; irms[k + 3] = b; esp_task_wdt_reset(); }
}

// ---------- messages ----------
// Builds the signed JSON line. Numbers are printed with the same precision that the server uses to verify the signature.
static bool buildMessage(char* out, size_t cap, uint32_t ts, float v, float pf, float f, int grid) {
  int w[NUM_CH];
  for (int i = 0; i < NUM_CH; i++) w[i] = grid ? (int)lroundf(irms[i] * v) : 0;  // apparent power per circuit
  char canon[200];
  snprintf(canon, sizeof(canon), "%s|%lu|%lu|%.1f|%.2f|%.1f|%d|%d,%d,%d,%d,%d,%d", DEVICE_ID, (unsigned long)seqNo, (unsigned long)ts,
           v, pf, f, grid, w[0], w[1], w[2], w[3], w[4], w[5]);
  char sig[65];
  hmacHex(canon, sig);
  int n = snprintf(out, cap, "{\"d\":\"%s\",\"seq\":%lu,\"ts\":%lu,\"v\":%.1f,\"pf\":%.2f,\"f\":%.1f,\"grid\":%d,\"ch\":[%d,%d,%d,%d,%d,%d],\"sig\":\"%s\"}",
                   DEVICE_ID, (unsigned long)seqNo, (unsigned long)ts, v, pf, f, grid, w[0], w[1], w[2], w[3], w[4], w[5], sig);
  return n > 0 && (size_t)n < cap;
}

static void appendBacklog(const char* line) {
  File f = LittleFS.open(BACKLOG_FILE, FILE_APPEND);
  if (!f) return;
  if (f.size() > BACKLOG_MAX_BYTES) {  // keep flash safe: drop the oldest data by starting a new file
    f.close();
    LittleFS.remove(BACKLOG_FILE);
    f = LittleFS.open(BACKLOG_FILE, FILE_WRITE);
    if (!f) return;
  }
  f.println(line);
  f.close();
}

static void flushBacklog() {
  if (!LittleFS.exists(BACKLOG_FILE) || !mqtt.connected()) return;
  File in = LittleFS.open(BACKLOG_FILE, FILE_READ);
  if (!in) return;
  File out = LittleFS.open("/backlog.tmp", FILE_WRITE);
  if (!out) { in.close(); return; }
  char topic[64];
  snprintf(topic, sizeof(topic), "powerpulse/%s/telemetry", DEVICE_ID);
  int sent = 0;
  bool stop = false;
  while (in.available()) {
    String line = in.readStringUntil('\n');
    line.trim();
    if (line.length() < 10) continue;
    if (!stop && sent < FLUSH_PER_LOOP && mqtt.publish(topic, line.c_str())) { sent++; mqtt.loop(); }
    else { stop = true; out.println(line); }  // keep everything not yet delivered
  }
  in.close(); out.close();
  LittleFS.remove(BACKLOG_FILE);
  LittleFS.rename("/backlog.tmp", BACKLOG_FILE);
}

// ---------- connectivity ----------
static void ensureWifi() {
  if (WiFi.status() == WL_CONNECTED) return;
  if (millis() - lastWifiTry < 10000) return;
  lastWifiTry = millis();
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
}

static void ensureMqtt() {
  if (WiFi.status() != WL_CONNECTED || mqtt.connected()) return;
  if (millis() - lastMqttTry < 8000) return;
  lastMqttTry = millis();
  if (!timeValid()) syncClock();
  char willTopic[64];
  snprintf(willTopic, sizeof(willTopic), "powerpulse/%s/status", DEVICE_ID);
  if (mqtt.connect(DEVICE_ID, MQTT_USER, MQTT_PASS, willTopic, 1, true, "offline")) mqtt.publish(willTopic, "online", true);
}

// ---------- serial calibration console ----------
// "show"               print currents
// "cal <ch 0-5> <A>"   clamp one wire carrying a known current (from a reference meter) and run this
// "reset"              restore factory calibration
static void handleSerial() {
  if (!Serial.available()) return;
  String cmd = Serial.readStringUntil('\n');
  cmd.trim();
  if (cmd == "show") {
    for (int i = 0; i < NUM_CH; i++) Serial.printf("ch%d %.3f A (cal %.4f)\n", i, irms[i], calFactor[i]);
  } else if (cmd.startsWith("cal ")) {
    int ch; float known;
    if (sscanf(cmd.c_str(), "cal %d %f", &ch, &known) == 2 && ch >= 0 && ch < NUM_CH && known > 0.2 && irms[ch] > 0.05) {
      calFactor[ch] *= known / irms[ch];
      char key[8]; snprintf(key, sizeof(key), "cal%d", ch);
      prefs.putFloat(key, calFactor[ch]);
      Serial.printf("ch%d calibration now %.4f\n", ch, calFactor[ch]);
    } else Serial.println("usage: cal <0-5> <amps>, with a load of at least 0.2 A on that clamp");
  } else if (cmd == "reset") {
    for (int i = 0; i < NUM_CH; i++) { calFactor[i] = 1; char key[8]; snprintf(key, sizeof(key), "cal%d", i); prefs.putFloat(key, 1); }
    Serial.println("calibration reset");
  }
}

// ---------- main ----------
void setup() {
  Serial.begin(115200);
  hexToBytes(DEVICE_SECRET_HEX, keyBytes, 32);
  prefs.begin("pp", false);
  seqNo = prefs.getUInt("seq", 0) + 50;  // skip ahead so a reboot can never reuse a sequence number
  prefs.putUInt("seq", seqNo);
  for (int i = 0; i < NUM_CH; i++) { char key[8]; snprintf(key, sizeof(key), "cal%d", i); calFactor[i] = prefs.getFloat(key, 1.0f); }
  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(400000);
  ads1.setGain(GAIN_ONE); ads1.setDataRate(RATE_ADS1115_860SPS);
  ads2.setGain(GAIN_ONE); ads2.setDataRate(RATE_ADS1115_860SPS);
  if (!ads1.begin(0x48) || !ads2.begin(0x49)) Serial.println("ADS1115 not found: check wiring and addresses");
  Serial2.begin(9600, SERIAL_8N1, PZEM_RX, PZEM_TX);
  LittleFS.begin(true);
#ifdef USE_DS3231
  if (rtc.begin() && rtc.now().unixtime() > 1700000000) setSystemTime(rtc.now().unixtime());
#endif
  net.setCACert(CA_PEM);
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setBufferSize(512);
  mqtt.setKeepAlive(30);
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  esp_task_wdt_config_t wdt = {.timeout_ms = 30000, .idle_core_mask = 0, .trigger_panic = true};
  esp_task_wdt_reconfigure(&wdt);
#else
  esp_task_wdt_init(30, true);
#endif
  esp_task_wdt_add(NULL);
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
}

void loop() {
  esp_task_wdt_reset();
  handleSerial();
  ensureWifi();
  ensureMqtt();
  if (mqtt.connected()) { mqtt.loop(); flushBacklog(); }

  measureAll();

  uint32_t now = millis();
  bool online = mqtt.connected();
  uint32_t interval = online ? PUBLISH_MS : OFFLINE_LOG_MS;
  if (now - lastPublish < interval) return;
  lastPublish = now;

  float v = pzem.voltage(), pf = pzem.pf(), f = pzem.frequency();
  bool ok = !isnan(v) && v >= 80.0f;
  pzemFails = ok ? 0 : pzemFails + 1;
  if (!ok && pzemFails < 3) return;  // ignore an isolated bad read; three in a row means the supply is gone
  int grid = ok ? 1 : 0;
  if (!ok) { v = 0; pf = 0; f = 0; }
  if (isnan(pf)) pf = 0;
  if (isnan(f)) f = 0;
  if (!timeValid()) return;  // never send a record without a trustworthy timestamp

  char line[400];
  seqNo++;
  if (seqNo % 20 == 0) prefs.putUInt("seq", seqNo);
  if (!buildMessage(line, sizeof(line), (uint32_t)time(nullptr), v, pf, f, grid)) return;
  char topic[64];
  snprintf(topic, sizeof(topic), "powerpulse/%s/telemetry", DEVICE_ID);
  if (!(online && mqtt.publish(topic, line))) appendBacklog(line);
}
