
/*  CrowPanel ESP32 5.79" E-Paper (272x792) — Elecrow SSD1683 EPD.h driver

  What this sketch does:
  - Connects to Wi-Fi (with timeout)
  - Renders timestamp from server-provided status.json generated time
  - Polls STATUS_URL every 30 seconds
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
#include "roboto_fonts.h"  // Roboto Regular glyph metrics + bitmaps

// -------------------- USER CONFIG --------------------
static const char *WIFI_SSID = "YOUR_WIFI_SSID";
static const char *WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
static const char *STATUS_URL = "http://<pi-ip>/status.json";
static const char *TARGET_PERSON = "alex"; // Match against the "name" field (case-insensitive).

// Poll server every 30 seconds
constexpr unsigned long POLL_MS = 30UL * 1000UL;

// Full refresh every N partial updates (helps reduce ghosting)
constexpr uint8_t FULL_REFRESH_EVERY = 30;  // ~15 minutes if changing every 30s

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
static String lastStatusSignature = "";

// Button baseline (auto-detect polarity at boot)
static int menuBaseline = HIGH;
static int backBaseline = HIGH;

// Avoid Arduino auto-prototype generation issues for custom Roboto types.
static const RobotoGlyph* getRobotoGlyph(const RobotoFont &font, char c);
static int robotoTextWidthPx(const RobotoFont &font, const String &s, int tracking = 0);
static int scaledSans24TextWidthPx(const String &s, uint8_t scale, int tracking = 0);
static int sans56TextWidthPx(const String &s, int tracking = 0);
static void drawSans24ScaledString(int x, int y, const String& s, uint8_t scale, int tracking = 0, bool bold = false);
static void drawSans56String(int x, int y, const String &s, int tracking = -1, bool bold = true);

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
  // Reinitialize the display out of fast/partial mode so the full-update
  // waveform LUT uses standard driving voltages.  Without this, a full
  // refresh performed while fast-mode temperature overrides are active
  // can leave ghost pixels from the previous frame.
  EPD_Init();
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

// ---- Timezone formatting (no device NTP clock) ----
static void initTimezonePacific() {
  // Nevada/Pacific time with DST rules.
  // We only use this for formatting server-provided timestamps.
  setenv("TZ", "PST8PDT,M3.2.0,M11.1.0", 1);
  tzset();
}

static String formatTimestampFromEpoch(time_t epoch) {
  if (epoch <= 0) return "";

  struct tm t;
  localtime_r(&epoch, &t);

  char buf[32];
  int hour = t.tm_hour % 12;
  if (hour == 0) hour = 12;
  const char *ampm = (t.tm_hour >= 12) ? "PM" : "AM";

  snprintf(buf, sizeof(buf), "%02d/%02d/%04d %d:%02d %s",
           t.tm_mon + 1, t.tm_mday, t.tm_year + 1900,
           hour, t.tm_min, ampm);
  return String(buf);
}

static String minuteKeyFromEpoch(time_t epoch) {
  if (epoch <= 0) return "";
  struct tm t;
  localtime_r(&epoch, &t);
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

static int64_t daysFromCivil(int y, unsigned m, unsigned d) {
  y -= m <= 2;
  const int era = (y >= 0 ? y : y - 399) / 400;
  const unsigned yoe = (unsigned)(y - era * 400);
  const unsigned doy = (153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + d - 1;
  const unsigned doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
  return era * 146097 + (int)doe - 719468;
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

  int tzOffsetSeconds = 0;
  if (iso.length() >= 20 && iso[19] == 'Z') {
    tzOffsetSeconds = 0;
  } else if (iso.length() >= 25 && (iso[19] == '+' || iso[19] == '-')) {
    int offH = iso.substring(20, 22).toInt();
    int offM = iso.substring(23, 25).toInt();
    tzOffsetSeconds = (offH * 3600) + (offM * 60);
    if (iso[19] == '+') tzOffsetSeconds = tzOffsetSeconds;
    else                tzOffsetSeconds = -tzOffsetSeconds;
  } else {
    return false;
  }

  int64_t days = daysFromCivil(year, (unsigned)month, (unsigned)day);
  int64_t localSeconds = days * 86400LL + hour * 3600LL + minute * 60LL + second;
  int64_t utcSeconds = localSeconds - tzOffsetSeconds;
  epochOut = (time_t)utcSeconds;
  return true;
}

static String formatUntil(const String& iso, const String& source, time_t nowEpoch) {
  time_t eventEpoch = 0;
  if (!parseIsoToEpoch(iso, eventEpoch)) return "";

  if (nowEpoch <= 0) return "";

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

static bool isMicActiveStatus(const String &statusIn) {
  String lowered = statusIn;
  lowered.toLowerCase();
  lowered.trim();
  return lowered == "mic active" || lowered == "mic_active";
}

static inline void setPixelSafe(int x, int y) {
  if (x >= 0 && x < EPD_W && y >= 0 && y < EPD_H) {
    Paint_SetPixel(x, y, BLACK);
  }
}

static const RobotoGlyph* getRobotoGlyph(const RobotoFont &font, char c) {
  if (c < (char)font.first || c > (char)font.last) return nullptr;
  return &font.glyphs[(uint8_t)c - font.first];
}

static int robotoTextWidthPx(const RobotoFont &font, const String &s, int tracking) {
  if (!s.length()) return 0;
  int total = 0;
  for (size_t i = 0; i < s.length(); i++) {
    const RobotoGlyph *glyph = getRobotoGlyph(font, s[i]);
    total += glyph ? glyph->xAdvance : (font.yAdvance / 3);
    if (i + 1 < s.length()) total += tracking;
  }
  return total;
}

static int scaledSans24TextWidthPx(const String &s, uint8_t scale, int tracking) {
  if (!s.length()) return 0;
  return robotoTextWidthPx(roboto24, s, tracking) * (int)scale;
}

static int sans56TextWidthPx(const String &s, int tracking) {
  return robotoTextWidthPx(roboto56, s, tracking);
}

static String ellipsizeSans24ToFit(String s, uint8_t scale, int tracking, int maxWidthPx) {
  if (scaledSans24TextWidthPx(s, scale, tracking) <= maxWidthPx) return s;
  const String dots = "...";
  while (s.length() > 0 && scaledSans24TextWidthPx(s + dots, scale, tracking) > maxWidthPx) {
    s.remove(s.length() - 1);
  }
  return s + dots;
}

static String ellipsizeSans56ToFit(String s, int tracking, int maxWidthPx) {
  if (sans56TextWidthPx(s, tracking) <= maxWidthPx) return s;
  const String dots = "...";
  while (s.length() > 0 && sans56TextWidthPx(s + dots, tracking) > maxWidthPx) {
    s.remove(s.length() - 1);
  }
  return s + dots;
}

static void drawSans24ScaledString(int x, int y, const String& s, uint8_t scale, int tracking, bool bold) {
  if (scale < 1) scale = 1;
  int cursorX = x;
  const int baselineY = y + roboto24.ascent * scale;

  for (size_t ci = 0; ci < s.length(); ci++) {
    const RobotoGlyph *glyph = getRobotoGlyph(roboto24, s[ci]);
    if (!glyph) {
      cursorX += (roboto24.yAdvance / 3) * scale;
      if (ci + 1 < s.length()) cursorX += tracking;
      continue;
    }

    const int rowBytes = (glyph->width + 7) / 8;
    const uint8_t *glyphBitmap = roboto24.bitmap + glyph->bitmapOffset;
    for (int gy = 0; gy < glyph->height; gy++) {
      for (int gx = 0; gx < glyph->width; gx++) {
        uint8_t b = glyphBitmap[gy * rowBytes + gx / 8];
        if (b & (0x80 >> (gx & 7))) {
          int bx = cursorX + (glyph->xOffset + gx) * scale;
          int by = baselineY + (glyph->yOffset + gy) * scale;
          for (uint8_t sx = 0; sx < scale; sx++) {
            for (uint8_t sy = 0; sy < scale; sy++) {
              setPixelSafe(bx + sx, by + sy);
              if (bold) setPixelSafe(bx + sx + 1, by + sy);
            }
          }
        }
      }
    }

    cursorX += (glyph->xAdvance * scale);
    if (ci + 1 < s.length()) cursorX += tracking;
  }
}

static void drawSans56String(int x, int y, const String &s, int tracking, bool bold) {
  const int baselineY = y + roboto56.ascent;
  int cx = x;

  for (size_t ci = 0; ci < s.length(); ci++) {
    const RobotoGlyph *glyph = getRobotoGlyph(roboto56, s[ci]);
    if (!glyph) {
      cx += (roboto56.yAdvance / 3);
      if (ci + 1 < s.length()) cx += tracking;
      continue;
    }
    const int rowBytes = (glyph->width + 7) / 8;
    const uint8_t *glyphBitmap = roboto56.bitmap + glyph->bitmapOffset;
    for (int gy = 0; gy < glyph->height; gy++) {
      for (int gx = 0; gx < glyph->width; gx++) {
        uint8_t b = glyphBitmap[gy * rowBytes + gx / 8];
        if (b & (0x80 >> (gx & 7))) {
          setPixelSafe(cx + glyph->xOffset + gx, baselineY + glyph->yOffset + gy);
          if (bold) setPixelSafe(cx + glyph->xOffset + gx + 1, baselineY + glyph->yOffset + gy);
        }
      }
    }
    cx += glyph->xAdvance;
    if (ci + 1 < s.length()) cx += tracking;
  }
}

// Thick line helper used by all status icons (square brush of radius r).
static void thickLine(int ax, int ay, int bx, int by, int r = 2) {
  int dx = abs(bx - ax), sx = ax < bx ? 1 : -1;
  int dy = -abs(by - ay), sy = ay < by ? 1 : -1;
  int err = dx + dy;
  while (true) {
    for (int tx = -r; tx <= r; tx++)
      for (int ty = -r; ty <= r; ty++)
        Paint_SetPixel(ax + tx, ay + ty, BLACK);
    if (ax == bx && ay == by) break;
    int e2 = 2 * err;
    if (e2 >= dy) { err += dy; ax += sx; }
    if (e2 <= dx) { err += dx; ay += sy; }
  }
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

static void fillRect(int x1, int y1, int x2, int y2) {
  EPD_DrawRectangle(x1, y1, x2, y2, BLACK, 1);
}

static void fillCircle(int cx, int cy, int r) {
  EPD_DrawCircle(cx, cy, r, BLACK, 1);
}

static void drawArc(int cx, int cy, int r, int startDeg, int endDeg, int thickness = 2) {
  for (int deg = startDeg; deg <= endDeg; deg++) {
    float rad = deg * 0.0174533f;
    for (int t = -thickness; t <= thickness; t++) {
      int px = cx + (int)((r + t) * cos(rad));
      int py = cy + (int)((r + t) * sin(rad));
      Paint_SetPixel(px, py, BLACK);
    }
  }
}

static void drawMapPin(int cx, int cy, int scale = 1) {
  int headR = 4 * scale;
  fillCircle(cx, cy - 2 * scale, headR);

  for (int py = 0; py <= 6 * scale; py++) {
    int span = (6 * scale - py) / 2;
    thickLine(cx - span, cy + py, cx + span, cy + py, 0);
  }

  // Ground ring under the pin.
  drawCircle(cx, cy + 8 * scale, 3 * scale, 1);
  drawCircle(cx, cy + 8 * scale, 1 * scale, 1);
}

static void drawStatusIcon(const String& state, int x, int y, int w, int h) {
  String s = state;
  s.toLowerCase();

  if (s == "available") {
    drawCheckmark(x, y, w, h, 3);
    return;
  }

  if (s == "busy") {
    drawCircle(x + w / 2, y + h / 2, min(w, h) / 2 - 3, 3);
    thickLine(x + 14, y + h - 14, x + w - 14, y + 14, 2);
    return;
  }

  if (s == "meeting") {
    // Monitor frame + stand.
    EPD_DrawRectangle(x + 2, y + 8, x + 34, y + 46, BLACK, 0);
    EPD_DrawRectangle(x + 3, y + 9, x + 33, y + 45, BLACK, 0);
    fillRect(x + 14, y + 47, x + 22, y + 51);
    fillRect(x + 11, y + 52, x + 25, y + 54);

    // Person on screen.
    fillCircle(x + 18, y + 22, 5);
    fillCircle(x + 18, y + 39, 8);
    fillRect(x + 10, y + 39, x + 26, y + 45);

    // Main attendee (headset silhouette).
    fillCircle(x + 52, y + 22, 10);
    thickLine(x + 42, y + 22, x + 42, y + 30, 2);
    thickLine(x + 62, y + 22, x + 62, y + 30, 2);
    drawArc(x + 52, y + 22, 14, 205, 335, 2);

    // Shoulders + torso.
    fillCircle(x + 52, y + 46, 15);
    fillRect(x + 36, y + 46, x + 68, y + 57);
    EPD_DrawRectangle(x + 32, y + 51, x + 68, y + 71, BLACK, 0);
    EPD_DrawRectangle(x + 33, y + 52, x + 67, y + 70, BLACK, 0);
    return;
  }

  if (s == "ooo") {
    // Door frame + open doorway.
    EPD_DrawRectangle(x + 40, y + 10, x + 67, y + 66, BLACK, 0);
    EPD_DrawRectangle(x + 41, y + 11, x + 66, y + 65, BLACK, 0);
    fillRect(x + 43, y + 14, x + 64, y + 63);

    // Door opening and floor line for "leaving" direction.
    fillRect(x + 47, y + 16, x + 63, y + 61);
    thickLine(x + 14, y + 62, x + 63, y + 62, 1);

    // Stick figure walking out the door.
    fillCircle(x + 22, y + 24, 4);
    thickLine(x + 22, y + 29, x + 22, y + 43, 1);
    thickLine(x + 22, y + 34, x + 14, y + 39, 1);
    thickLine(x + 22, y + 34, x + 31, y + 31, 1);
    thickLine(x + 22, y + 43, x + 14, y + 56, 1);
    thickLine(x + 22, y + 43, x + 31, y + 52, 1);

    // Motion cue toward the doorway.
    thickLine(x + 29, y + 27, x + 35, y + 24, 0);
    thickLine(x + 29, y + 33, x + 36, y + 31, 0);
    return;
  }

  if (s == "elsewhere") {
    // Global ring with openings.
    drawArc(x + 30, y + 36, 26, 30, 170, 2);
    drawArc(x + 30, y + 36, 26, 185, 300, 2);

    // Location pins around the ring.
    drawMapPin(x + 12, y + 24, 1);
    drawMapPin(x + 55, y + 16, 1);
    drawMapPin(x + 31, y + 24, 1);
    drawMapPin(x + 22, y + 46, 1);

    // Remote worker with laptop.
    fillCircle(x + 52, y + 42, 6);
    fillCircle(x + 52, y + 54, 8);
    fillRect(x + 44, y + 54, x + 60, y + 59);
    fillRect(x + 43, y + 58, x + 63, y + 69);
    fillRect(x + 42, y + 71, x + 63, y + 72);
    return;
  }

  // Error / unknown: warning triangle — thick strokes
  thickLine(x + w / 2, y + 8, x + 8, y + h - 6, 2);
  thickLine(x + 8, y + h - 6, x + w - 8, y + h - 6, 2);
  thickLine(x + w - 8, y + h - 6, x + w / 2, y + 8, 2);
  thickLine(x + w / 2, y + 20, x + w / 2, y + h - 16, 2);
  for (int d = -2; d <= 2; d++)
    for (int e = -2; e <= 2; e++)
      Paint_SetPixel(x + w / 2 + d, y + h - 10 + e, BLACK);
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
                             const String &generatedTimestampIn = "",
                             time_t generatedEpochIn = 0,
                             bool usePartialUpdate = true) {
  const int margin = 14;

  String name   = nameIn;
  String state  = stateIn;
  String status = statusIn;
  String detail = detailIn;
  bool micActive = isMicActiveStatus(statusIn);

  // Make status read like the photo
  status.toUpperCase();
  name.toUpperCase();

  // New image buffer
  Paint_NewImage(ImageBW, EPD_W, EPD_H, Rotation, WHITE);
  Paint_Clear(WHITE);

  // Top name (centered, all caps, a bit larger/heavier)
  int nameMaxW = EPD_W - margin * 2;
  uint8_t nameScale = 2;
  int nameTracking = 1;
  if (scaledSans24TextWidthPx(name, nameScale, nameTracking) > nameMaxW) {
    nameScale = 1;
    nameTracking = 0;
  }
  name = ellipsizeSans24ToFit(name, nameScale, nameTracking, nameMaxW);
  {
    int nw = scaledSans24TextWidthPx(name, nameScale, nameTracking);
    int nx = (EPD_W - nw) / 2;
    if (nx < 0) nx = 0;
    drawSans24ScaledString(nx, 6, name, nameScale, nameTracking, true);
  }

  // Big status + icon.
  const int iconW = 72;
  const int iconH = 72;
  const int gap   = 16;

  int maxGroupW  = EPD_W - margin * 2;
  int statusMaxW = maxGroupW - iconW - gap;

  // Truncate if status word is wider than available space.
  // Keep additional spacing for the primary label so letters don't look crowded.
  const int statusTracking = 1;
  status = ellipsizeSans56ToFit(status, statusTracking, statusMaxW);
  int statusW = sans56TextWidthPx(status, statusTracking);

  int groupW = iconW + gap + statusW;
  int groupX = (EPD_W - groupW) / 2;
  if (groupX < margin) groupX = margin;

  // Vertically centre the 56px text within the 72px icon box.
  int statusY = 40 + (iconH - 56) / 2;
  int iconY   = 40;
  int wordX   = groupX + iconW + gap;

  drawStatusIcon(state, groupX, iconY, iconW, iconH);
  drawSans56String(wordX, statusY, status, statusTracking, true);

  // Divider
  EPD_DrawLine(margin, 124, EPD_W - margin, 124, BLACK);

  String detailLine = detail;
  String untilLine = formatUntil(untilIn, sourceIn, generatedEpochIn);
  String nextLine = formatNextEvent(nextEventIn);
  String lines[3] = { detailLine, untilLine, nextLine };

  // Detail lines: sans-serif scale 1 (24px), 30px line spacing
  int detailMaxW = EPD_W - margin * 2;
  if (micActive) {
    lines[1] = "";
  }
  const int detailTracking = -1;
  for (int i = 0; i < 3; i++) {
    lines[i] = ellipsizeSans24ToFit(lines[i], 1, detailTracking, detailMaxW);
  }
  for (int i = 0; i < 3; i++) {
    if (!lines[i].length()) continue;
    int lw = scaledSans24TextWidthPx(lines[i], 1, detailTracking);
    int lx = (EPD_W - lw) / 2;
    if (lx < 0) lx = 0;
    drawSans24ScaledString(lx, 136 + i * 30, lines[i], 1, detailTracking, false);
  }

  if (sourceIn == "working_hours") {
    EPD_DrawLine(margin, 226, EPD_W - margin, 226, BLACK);
  }

  // Bottom-right timestamp from status.json (formatted in Pacific time)
  String ts = generatedTimestampIn;
  if (ts.length()) {
    int tsW = scaledSans24TextWidthPx(ts, 1, detailTracking);
    int tsX = EPD_W - margin - tsW;
    int tsY = EPD_H - margin - 24;
    if (tsX < margin) tsX = margin;
    drawSans24ScaledString(tsX, tsY, ts, 1, detailTracking, false);
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
  showStatusScreen(line1, "error", line2, "", "", "", "", "", 0);
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
    showStatusScreen("WIFI OFFLINE", "error", "OFFLINE", "MAC " + getMacString(), "", "", "", "", 0);
    return false;
  }

  HTTPClient http;
  http.begin(STATUS_URL);
  int code = http.GET();

  if (code != 200) {
    http.end();
    showStatusScreen("HTTP ERROR", "error", "OFFLINE", String(code), "", "", "", "", 0);
    return false;
  }

  String payload = http.getString();
  http.end();

  // Parse JSON
  StaticJsonDocument<8192> doc;
  DeserializationError err = deserializeJson(doc, payload);
  if (err) {
    showStatusScreen("JSON ERROR", "error", "OFFLINE", err.c_str(), "", "", "", "", 0);
    return false;
  }

  String generatedIso = String((const char*)(doc["generated"] | ""));
  time_t generatedEpoch = 0;
  if (!parseIsoToEpoch(generatedIso, generatedEpoch)) {
    generatedEpoch = 0;
  }
  String generatedTimestampDisplay = formatTimestampFromEpoch(generatedEpoch);

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
  String displayKey = topName + "|" + state + "|" + bigStatus + "|" + detailLine + "|" + until + "|" + nextEventAt + "|" + source + "|" + minuteKeyFromEpoch(generatedEpoch);
  uint32_t fp = fnv1a32(displayKey);

  if (fp == lastFingerprint) {
    Serial.println("[EPD] No change; skipping update.");
    return false;
  }

  String statusSignature = state + "|" + bigStatus + "|" + source;
  bool firstStatusRender = lastStatusSignature.length() == 0;
  bool statusChanged = !firstStatusRender && statusSignature != lastStatusSignature;

  lastFingerprint = fp;
  lastStatusSignature = statusSignature;

  if (firstStatusRender || statusChanged) {
    if (firstStatusRender) {
      Serial.println("[EPD] First status render; forcing full refresh/clear...");
    } else {
      Serial.println("[EPD] Status changed; forcing full refresh/clear...");
    }
    epdFullRefreshClear();
    showStatusScreen(topName, state, bigStatus, detailLine, until, source, nextEventAt, generatedTimestampDisplay, generatedEpoch, false);
    return true;
  }

  // Draw + partial update
  showStatusScreen(topName, state, bigStatus, detailLine, until, source, nextEventAt, generatedTimestampDisplay, generatedEpoch);

  partialSinceFull++;
  Serial.printf("[EPD] Updated. partialSinceFull=%u\n", partialSinceFull);

  if (partialSinceFull >= FULL_REFRESH_EVERY) {
    Serial.println("[EPD] Doing full refresh/clear to reduce ghosting...");
    epdFullRefreshClear();

    // Re-draw current content after a full clear
    showStatusScreen(topName, state, bigStatus, detailLine, until, source, nextEventAt, generatedTimestampDisplay, generatedEpoch, false);
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

  // Timezone init for formatting server-provided timestamps
  initTimezonePacific();

  // WiFi
  bool ok = connectWifiWithTimeout(20000UL);

  if (ok) {
    Serial.print("WiFi connected: ");
    Serial.println(WiFi.localIP());
    showStatusSplash("WiFi OK", WiFi.localIP().toString());

  } else {
    Serial.println("WiFi failed to connect.");
    // Show MAC so you can identify the device even when offline
    showStatusScreen("WIFI FAILED", "error", "OFFLINE", "MAC " + getMacString(), "", "", "", "", 0);
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
