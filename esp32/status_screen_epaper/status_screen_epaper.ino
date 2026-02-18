
/*  CrowPanel ESP32 5.79" E-Paper (272x792) — Elecrow SSD1683 EPD.h driver

  What this sketch does:
  - Connects to Wi-Fi (with timeout)
  - Initializes time via NTP (so we can render a timestamp)
  - Polls STATUS_URL once per minute
  - Extracts a target person's fields from JSON (name/label/state/detail/next_event_at)
  - Computes a fingerprint of what would be displayed INCLUDING the current minute (keeps clock alive)
  - ONLY updates the e-paper when the fingerprint changes
  - Long-press MENU or BACK for ~1.5s to reboot (ESP.restart)

  Notes:
  - Uses Elecrow's EPD.h stack (which pulls in GUI_Paint / Paint_*)
  - Powers panel rails (GPIO 7 and 42) like Elecrow examples do
*/

#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <SPI.h>
#include <time.h>
#include <esp_wifi.h>

// Elecrow e-paper driver headers
#include "EPD.h"   // provides EPD_* and Paint_* in this stack

// -------------------- USER CONFIG --------------------
static const char *WIFI_SSID = "YOUR_WIFI_SSID";
static const char *WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
static const char *STATUS_URL = "http://<pi-ip>/status.json";
static const char *TARGET_PERSON = "alex"; // Match against the "name" field (case-insensitive).

// Poll server every minute
constexpr unsigned long POLL_MS = 60UL * 1000UL;

// Full refresh every N partial updates (helps reduce ghosting)
constexpr uint8_t FULL_REFRESH_EVERY = 30;  // ~30 minutes if changing every minute

// CrowPanel buttons (per Elecrow docs; some board revs may vary)
static const uint8_t BTN_MENU = 2; // MENU
static const uint8_t BTN_BACK = 1; // EXIT/BACK

// -------------------- DISPLAY BUFFER --------------------
// 792*272/8 = 26928 bytes; Elecrow examples often allocate ~27200.
static uint8_t ImageBW[27200];

// -------------------- STATE --------------------
static unsigned long nextPollAt = 0;
static uint32_t lastFingerprint = 0;
static uint8_t partialSinceFull = 0;

// Button baseline (auto-detect polarity at boot)
static int menuBaseline = HIGH;
static int backBaseline = HIGH;

// -------------------- HELPERS --------------------
static uint32_t fnv1a32(const String &s) {
  uint32_t h = 2166136261u;
  for (size_t i = 0; i < s.length(); i++) {
    h ^= (uint8_t)s[i];
    h *= 16777619u;
  }
  return h;
}

static inline bool buttonPressed(uint8_t pin, int baseline) {
  // Treat "changed from baseline" as pressed (works for active-low and active-high)
  return digitalRead(pin) != baseline;
}

static void epdFullRefreshClear() {
  // Full clear/update sequence (per typical Elecrow flows)
  EPD_Display_Clear();
  EPD_Update();
  partialSinceFull = 0;
}

static String getMacString() {
  // MAC is available even if not connected
  uint8_t mac[6];
  esp_wifi_get_mac(WIFI_IF_STA, mac);

  char buf[18];
  snprintf(buf, sizeof(buf), "%02X:%02X:%02X:%02X:%02X:%02X",
           mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
  return String(buf);
}

// ---- Time init (Nevada/Pacific) ----
static void initTimePacific() {
  // Nevada/Pacific time with DST rules
  setenv("TZ", "PST8PDT,M3.2.0,M11.1.0", 1);
  tzset();

  // NTP
  configTime(0, 0, "pool.ntp.org", "time.nist.gov");
}

static String formatLocalTimestamp() {
  struct tm t;
  if (!getLocalTime(&t, 50)) return "";

  char buf[32];
  int hour = t.tm_hour % 12;
  if (hour == 0) hour = 12;
  const char *ampm = (t.tm_hour >= 12) ? "PM" : "AM";

  snprintf(buf, sizeof(buf), "%02d/%02d/%04d %d:%02d %s",
           t.tm_mon + 1, t.tm_mday, t.tm_year + 1900,
           hour, t.tm_min, ampm);

  return String(buf);
}

// A short per-minute key so the screen updates even when status doesn't change
static String minuteKey() {
  struct tm t;
  if (!getLocalTime(&t, 10)) return ""; // no time yet, don't force minute refresh
  char buf[8];
  snprintf(buf, sizeof(buf), "%02d%02d", t.tm_hour, t.tm_min);
  return String(buf);
}

// ---- Next event formatter ----
static String formatNextEvent(const String& iso) {
  // expects: YYYY-MM-DDTHH:MM:SS-08:00 (or similar)
  if (iso.length() < 16) return "";

  int hh = iso.substring(11, 13).toInt();
  int mm = iso.substring(14, 16).toInt();

  int h12 = hh % 12;
  if (h12 == 0) h12 = 12;
  const char *ampm = (hh >= 12) ? "PM" : "AM";

  char buf[40];
  snprintf(buf, sizeof(buf), "Next event at %d:%02d %s", h12, mm, ampm);
  return String(buf);
}

static bool parseIsoToEpoch(const String& iso, time_t &epochOut) {
  // Accepts offsets like ±HH:MM and Z.
  if (iso.length() < 19) return false;

  int year = iso.substring(0, 4).toInt();
  int month = iso.substring(5, 7).toInt();
  int day = iso.substring(8, 10).toInt();
  int hour = iso.substring(11, 13).toInt();
  int minute = iso.substring(14, 16).toInt();
  int second = iso.substring(17, 19).toInt();

  struct tm tmUtc = {};
  tmUtc.tm_year = year - 1900;
  tmUtc.tm_mon = month - 1;
  tmUtc.tm_mday = day;
  tmUtc.tm_hour = hour;
  tmUtc.tm_min = minute;
  tmUtc.tm_sec = second;

  time_t utc = mktime(&tmUtc);
  if (utc <= 0) return false;

  if (iso.length() >= 20 && iso[19] == 'Z') {
    epochOut = utc;
    return true;
  }

  if (iso.length() >= 25) {
    char sign = iso[19];
    int offH = iso.substring(20, 22).toInt();
    int offM = iso.substring(23, 25).toInt();
    int offset = (offH * 3600) + (offM * 60);
    if (sign == '+') {
      utc -= offset;
    } else if (sign == '-') {
      utc += offset;
    }
    epochOut = utc;
    return true;
  }

  return false;
}

static String formatUntil(const String& iso, const String& source) {
  time_t eventEpoch = 0;
  if (!parseIsoToEpoch(iso, eventEpoch)) return "";

  time_t nowEpoch = time(nullptr);
  long seconds = (long)difftime(eventEpoch, nowEpoch);
  if (seconds <= 0) return "";

  if (source == "working_hours") {
    struct tm local;
    localtime_r(&eventEpoch, &local);
    int h12 = local.tm_hour % 12;
    if (h12 == 0) h12 = 12;
    const char *ampm = (local.tm_hour >= 12) ? "PM" : "AM";
    char buf[36];
    snprintf(buf, sizeof(buf), "Back at %d:%02d %s", h12, local.tm_min, ampm);
    return String(buf);
  }

  // E-paper refresh cadence is coarse; avoid displaying seconds.
  // Round up to the next whole minute so 4m01s -> 5m.
  long minutesTotal = (seconds + 59) / 60;
  long hours = minutesTotal / 60;
  long minutes = minutesTotal % 60;
  char buf[32];
  if (hours > 0) {
    snprintf(buf, sizeof(buf), "Ends in %ldh %ldm", hours, minutes);
  } else {
    if (minutesTotal < 1) minutesTotal = 1;
    snprintf(buf, sizeof(buf), "Ends in %ldm", minutesTotal);
  }
  return String(buf);
}

// -------------------- DRAWING HELPERS --------------------
// Your EPD_ShowString advances x by (size/2) per char (monospace-ish)
static int textWidthPx(const String &s, uint16_t fontSize) {
  return (int)s.length() * (int)(fontSize / 2);
}

static String ellipsizeToFit(String s, uint16_t fontSize, int maxWidthPx) {
  if (textWidthPx(s, fontSize) <= maxWidthPx) return s;
  const String dots = "...";
  while (s.length() > 0 && textWidthPx(s + dots, fontSize) > maxWidthPx) {
    s.remove(s.length() - 1);
  }
  return s + dots;
}

static uint16_t pickFontToFit(const String &s, int maxWidthPx, uint16_t preferred) {
  // allowed sizes in your EPD_ShowChar: 12, 16, 24, 48
  const uint16_t sizes[] = { preferred, 24, 16, 12 };
  for (uint16_t sz : sizes) {
    if (textWidthPx(s, sz) <= maxWidthPx) return sz;
  }
  return 12;
}

static void drawCenteredText(int y, const String &s, uint16_t fontSize) {
  int w = textWidthPx(s, fontSize);
  int x = (EPD_W - w) / 2;
  if (x < 0) x = 0;
  EPD_ShowString((uint16_t)x, (uint16_t)y, (char*)s.c_str(), fontSize, BLACK);
}

static void drawBoldString(int x, int y, const String& s, uint16_t size) {
  // Faux bold: draw twice with a 1px offset. Looks cleaner than 48px glyphs on many panels.
  EPD_ShowString((uint16_t)x, (uint16_t)y, (char*)s.c_str(), size, BLACK);
  EPD_ShowString((uint16_t)(x + 1), (uint16_t)y, (char*)s.c_str(), size, BLACK);
}

static void drawCheckmark(int x, int y, int w, int h, int thickness);

static void drawCircle(int cx, int cy, int r, int thickness = 2) {
  for (int deg = 0; deg < 360; deg++) {
    float rad = deg * 0.0174533f;
    for (int t = -thickness; t <= thickness; t++) {
      int x = cx + (int)((r + t) * cos(rad));
      int y = cy + (int)((r + t) * sin(rad));
      Paint_SetPixel(x, y, BLACK);
    }
  }
}

static void drawStatusIcon(const String& state, int x, int y, int w, int h) {
  String s = state;
  s.toLowerCase();

  if (s == "available") {
    drawCheckmark(x, y, w, h, 2);
    return;
  }

  if (s == "busy") {
    drawCircle(x + w / 2, y + h / 2, min(w, h) / 2 - 3, 2);
    for (int i = 0; i < 3; i++) {
      EPD_DrawLine(x + 10 + i, y + h - 10, x + w - 10 + i, y + 10, BLACK);
    }
    return;
  }

  if (s == "meeting") {
    EPD_DrawRectangle(x + 4, y + 10, x + w - 4, y + h - 6, BLACK, 0);
    EPD_DrawLine(x + 4, y + 20, x + w - 4, y + 20, BLACK);
    EPD_DrawLine(x + 14, y + 6, x + 14, y + 16, BLACK);
    EPD_DrawLine(x + w - 14, y + 6, x + w - 14, y + 16, BLACK);
    return;
  }

  if (s == "ooo") {
    // Airplane-ish icon.
    EPD_DrawLine(x + 8, y + h - 14, x + w - 8, y + 12, BLACK);
    EPD_DrawLine(x + w - 18, y + 22, x + w - 8, y + 12, BLACK);
    EPD_DrawLine(x + w - 24, y + 18, x + w - 16, y + 30, BLACK);
    EPD_DrawLine(x + 18, y + h - 12, x + 28, y + h - 2, BLACK);
    return;
  }

  // Error / unknown: warning triangle.
  EPD_DrawLine(x + w / 2, y + 8, x + 8, y + h - 6, BLACK);
  EPD_DrawLine(x + 8, y + h - 6, x + w - 8, y + h - 6, BLACK);
  EPD_DrawLine(x + w - 8, y + h - 6, x + w / 2, y + 8, BLACK);
  EPD_DrawLine(x + w / 2, y + 18, x + w / 2, y + h - 16, BLACK);
  Paint_SetPixel(x + w / 2, y + h - 11, BLACK);
}

static void drawCenteredLines(int yStart, String lines[], int lineCount, uint16_t fontSize, int lineGap) {
  for (int i = 0; i < lineCount; i++) {
    if (!lines[i].length()) continue;
    drawCenteredText(yStart + i * (fontSize + lineGap), lines[i], fontSize);
  }
}

// Pixel-drawn checkmark
static void drawCheckmark(int x, int y, int w, int h, int thickness) {
  int x1 = x + (w * 20) / 100;
  int y1 = y + (h * 55) / 100;
  int x2 = x + (w * 40) / 100;
  int y2 = y + (h * 75) / 100;
  int x3 = x + (w * 80) / 100;
  int y3 = y + (h * 30) / 100;

  auto drawThickLine = [&](int ax, int ay, int bx, int by) {
    int dx = abs(bx - ax), sx = ax < bx ? 1 : -1;
    int dy = -abs(by - ay), sy = ay < by ? 1 : -1;
    int err = dx + dy;

    while (true) {
      for (int tx = -thickness; tx <= thickness; tx++) {
        for (int ty = -thickness; ty <= thickness; ty++) {
          Paint_SetPixel(ax + tx, ay + ty, BLACK);
        }
      }
      if (ax == bx && ay == by) break;
      int e2 = 2 * err;
      if (e2 >= dy) { err += dy; ax += sx; }
      if (e2 <= dx) { err += dx; ay += sy; }
    }
  };

  drawThickLine(x1, y1, x2, y2);
  drawThickLine(x2, y2, x3, y3);
}

// -------------------- MAIN STATUS SCREEN --------------------
static void showStatusScreen(const String &nameIn,
                             const String &stateIn,
                             const String &statusIn,
                             const String &detailIn,
                             const String &untilIn,
                             const String &sourceIn,
                             const String &nextEventIn,
                             bool usePartialUpdate = true) {
  const int margin = 14;

  String name   = nameIn;
  String state  = stateIn;
  String status = statusIn;
  String detail = detailIn;

  // Make status read like the photo
  status.toUpperCase();

  // New image buffer
  Paint_NewImage(ImageBW, EPD_W, EPD_H, Rotation, WHITE);
  Paint_Clear(WHITE);

  // Top name (centered)
  int nameMaxW = EPD_W - margin * 2;
  uint16_t nameFont = pickFontToFit(name, nameMaxW, 24);
  name = ellipsizeToFit(name, nameFont, nameMaxW);
  drawCenteredText(22, name, nameFont);

  // Big status + icon as a centered group.
  // Use the largest available built-in sans bitmap font that fits.
  uint16_t statusFont = pickFontToFit(status, EPD_W - margin * 2 - 56 - 16, 48);

  int iconW = 56;
  int iconH = 56;
  int gap   = 16;

  int maxGroupW = EPD_W - margin * 2;

  // Fit status text
  status = ellipsizeToFit(status, statusFont, maxGroupW - iconW - gap);
  int statusW = textWidthPx(status, statusFont);

  int groupW = iconW + gap + statusW;
  int groupX = (EPD_W - groupW) / 2;
  if (groupX < margin) groupX = margin;

  int statusY = 98;          // tuned for 272px height
  int iconY   = statusY - 8; // align check with word visually
  int wordX   = groupX + iconW + gap;

  drawStatusIcon(state, groupX, iconY, iconW, iconH);
  drawBoldString(wordX, statusY, status, statusFont);

  // Divider and detail block (closer to web layout)
  EPD_DrawLine(margin, 156, EPD_W - margin, 156, BLACK);

  String detailLine = detail;
  String untilLine = formatUntil(untilIn, sourceIn);
  String nextLine = formatNextEvent(nextEventIn);
  String lines[3] = { detailLine, untilLine, nextLine };

  int detailMaxW = EPD_W - margin * 2;
  for (int i = 0; i < 3; i++) {
    lines[i] = ellipsizeToFit(lines[i], 16, detailMaxW);
  }

  drawCenteredLines(168, lines, 3, 16, 6);

  if (sourceIn == "working_hours") {
    EPD_DrawLine(margin, 222, EPD_W - margin, 222, BLACK);
  }

  // Bottom-right timestamp
  String ts = formatLocalTimestamp();
  if (ts.length()) {
    uint16_t tsFont = 16;
    int tsW = textWidthPx(ts, tsFont);
    int tsX = EPD_W - margin - tsW;
    int tsY = EPD_H - margin - tsFont;
    if (tsX < margin) tsX = margin;
    EPD_ShowString((uint16_t)tsX, (uint16_t)tsY, (char*)ts.c_str(), tsFont, BLACK);
  }

  EPD_Display(ImageBW);
  if (usePartialUpdate) {
    EPD_PartUpdate();
  } else {
    EPD_Update();
    // Restore fast/partial mode after a full update cycle.
    EPD_FastMode1Init();
  }
}

static void showStatusSplash(const String &line1, const String &line2) {
  // Use the same status layout for splash screens
  showStatusScreen(line1, "error", line2, "", "", "", "");
}

// -------------------- PANEL INIT --------------------
static void initPanel() {
  // Panel power rails (Elecrow examples set these high first)
  pinMode(7, OUTPUT);
  digitalWrite(7, HIGH);
  pinMode(42, OUTPUT);
  digitalWrite(42, HIGH);
  delay(10);

  // Init display + paint buffer
  EPD_GPIOInit();

  Paint_NewImage(ImageBW, EPD_W, EPD_H, Rotation, WHITE);
  Paint_Clear(WHITE);

  // Fast partial mode init
  EPD_FastMode1Init();

  // Do an initial clear
  epdFullRefreshClear();
}

static void handleRebootButtons() {
  static unsigned long downSince = 0;

  bool down = buttonPressed(BTN_MENU, menuBaseline) || buttonPressed(BTN_BACK, backBaseline);

  if (!down) {
    downSince = 0;
    return;
  }

  if (downSince == 0) downSince = millis();

  if (millis() - downSince >= 1500) {
    showStatusSplash("Rebooting...", "");
    delay(150);
    ESP.restart();
  }
}

// -------------------- NETWORK / JSON --------------------
static bool fetchAndMaybeUpdateDisplay() {
  if (WiFi.status() != WL_CONNECTED) {
    // Don't spam partial refreshes; show a useful offline screen.
    showStatusScreen("WIFI OFFLINE", "error", "OFFLINE", "MAC " + getMacString(), "", "", "");
    return false;
  }

  HTTPClient http;
  http.begin(STATUS_URL);
  int code = http.GET();

  if (code != 200) {
    http.end();
    showStatusScreen("HTTP ERROR", "error", "OFFLINE", String(code), "", "", "");
    return false;
  }

  String payload = http.getString();
  http.end();

  // Parse JSON
  StaticJsonDocument<8192> doc;
  DeserializationError err = deserializeJson(doc, payload);
  if (err) {
    showStatusScreen("JSON ERROR", "error", "OFFLINE", err.c_str(), "", "", "");
    return false;
  }

  String target = String(TARGET_PERSON);
  target.toLowerCase();

  String name, label, state, detail, nextEventAt, source, until;

  JsonArray people = doc["people"].as<JsonArray>();
  for (JsonObject p : people) {
    const char *n = p["name"] | "";
    String n2(n);
    n2.toLowerCase();

    if (n2 == target) {
      name        = String(n);
      label       = String((const char*)(p["label"]         | ""));
      state       = String((const char*)(p["state"]         | ""));
      detail      = String((const char*)(p["detail"]        | ""));
      nextEventAt = String((const char*)(p["next_event_at"] | ""));
      source      = String((const char*)(p["source"]        | ""));
      until       = String((const char*)(p["until"]         | ""));
      break;
    }
  }

  if (name.length() == 0) {
    name = "Not found";
    label = TARGET_PERSON;
    state = "";
    detail = "";
    nextEventAt = "";
    source = "";
    until = "";
  }

  // Choose what goes where:
  String topName   = name;
  // Match web UI: show label text as the primary status word.
  String bigStatus = (label.length() ? label : state);

  // Detail line fallback
  String detailLine = detail;
  if (detailLine.length() == 0) {
    detailLine = label;
  }

  // Fingerprint what matters + current minute (so clock updates)
  String displayKey = topName + "|" + state + "|" + bigStatus + "|" + detailLine + "|" + until + "|" + nextEventAt + "|" + source + "|" + minuteKey();
  uint32_t fp = fnv1a32(displayKey);

  if (fp == lastFingerprint) {
    Serial.println("[EPD] No change; skipping update.");
    return false;
  }

  lastFingerprint = fp;

  // Draw + partial update
  showStatusScreen(topName, state, bigStatus, detailLine, until, source, nextEventAt);

  partialSinceFull++;
  Serial.printf("[EPD] Updated. partialSinceFull=%u\n", partialSinceFull);

  if (partialSinceFull >= FULL_REFRESH_EVERY) {
    Serial.println("[EPD] Doing full refresh/clear to reduce ghosting...");
    epdFullRefreshClear();

    // Re-draw current content after a full clear
    showStatusScreen(topName, state, bigStatus, detailLine, until, source, nextEventAt, false);
  }

  return true;
}

// -------------------- WIFI CONNECT --------------------
static bool connectWifiWithTimeout(uint32_t timeoutMs) {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  uint32_t start = millis();
  while (WiFi.status() != WL_CONNECTED && (millis() - start) < timeoutMs) {
    handleRebootButtons();
    delay(50);
  }
  return (WiFi.status() == WL_CONNECTED);
}

// -------------------- ARDUINO --------------------
void setup() {
  Serial.begin(115200);
  delay(50);

  // Ensure WiFi stack is initialized enough for MAC reads
  WiFi.mode(WIFI_STA);

  // Buttons
  pinMode(BTN_MENU, INPUT_PULLUP);
  pinMode(BTN_BACK, INPUT_PULLUP);
  delay(10);
  menuBaseline = digitalRead(BTN_MENU);
  backBaseline = digitalRead(BTN_BACK);
  Serial.printf("Button baselines: MENU=%s, BACK=%s\n",
                menuBaseline ? "HIGH" : "LOW",
                backBaseline ? "HIGH" : "LOW");

  // Display
  initPanel();
  showStatusSplash("Booting...", "Starting WiFi");

  // WiFi
  bool ok = connectWifiWithTimeout(20000UL);

  if (ok) {
    Serial.print("WiFi connected: ");
    Serial.println(WiFi.localIP());
    showStatusSplash("WiFi OK", WiFi.localIP().toString());

    // Time init so timestamp renders
    initTimePacific();
  } else {
    Serial.println("WiFi failed to connect.");
    // Show MAC so you can identify the device even when offline
    showStatusScreen("WIFI FAILED", "error", "OFFLINE", "MAC " + getMacString(), "", "", "");
  }

  // Poll immediately on boot
  nextPollAt = millis();
}

void loop() {
  handleRebootButtons();

  unsigned long now = millis();
  if ((long)(now - nextPollAt) >= 0) {
    nextPollAt = now + POLL_MS;
    Serial.println("[NET] Polling server...");
    fetchAndMaybeUpdateDisplay();
  }

  delay(20);
}
