/*
 * kbd-bridge ESP32 client — PlatformIO build (Arduino framework).
 *
 * Reads events from USB serial and POSTs them to the kbd-bridge server.
 * Serial protocol (one line per event):
 *   k <code> <down> <mask> [ch]   key        e.g.  k KeyA 1 0 a
 *   t <text...>                   bulk text        t hello world
 *   mu <dx> <dy>                  relative move    mu 5 -3
 *   ma <x> <y>                    absolute move    ma 640 300
 *   mc <button> <down> [x] [y]    click            mc 0 1 640 300
 *   sc <dy>                       wheel            sc -120
 *
 * Suits the BT-keyboard leg too: wire your HID host into onKeyDown/onKeyUp.
 */

#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>

// --------------------------- CONFIG -------------------------------------
#define WIFI_SSID     "PRNet84"
#define WIFI_PASS     "Alotbsol123!3374"

// Where the kbd-bridge server is reachable. 192.168.0.56 = this PC's LAN IP,
// served by win/lan_relay.py on the Windows side. Change to your public host
// (e.g. tesla.aijy.com) once the Cloudflare tunnel is up.
#define SERVER_HOST   "192.168.0.56"
#define SERVER_PORT   8766
#define KBD_TOKEN     ""          // set if the server requires KBD_TOKEN
// ------------------------------------------------------------------------

static String lineBuf;

void postEvent(const String& json) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[kbd] wifi down, dropping event");
    return;
  }
  HTTPClient http;
  String url = "http://" SERVER_HOST ":" + String(SERVER_PORT) + "/events";
  if (strlen(KBD_TOKEN)) url += "?token=" + String(KBD_TOKEN);

  http.begin(url);
  http.addHeader("Content-Type", "application/json");
  if (strlen(KBD_TOKEN)) http.addHeader("Authorization", String("Bearer ") + KBD_TOKEN);
  http.setTimeout(4000);
  int code = http.POST(json);
  if (code > 0) {
    Serial.printf("[kbd] POST %d  %s\n", code, json.c_str());
  } else {
    Serial.printf("[kbd] POST failed: %s\n", http.errorToString(code).c_str());
  }
  http.end();
}

// --- event emitters ------------------------------------------------------

void emitKeyDown(const char* code, const char* ch, uint32_t mask) {
  String j = "{\"t\":\"k\",\"c\":\"" + String(code) + "\",\"d\":1,\"m\":" + String(mask);
  if (ch && ch[0]) j += ",\"ch\":\"" + String(ch) + "\"";
  j += "}";
  postEvent(j);
}

void emitKeyUp(const char* code, uint32_t mask) {
  postEvent("{\"t\":\"k\",\"c\":\"" + String(code) + "\",\"d\":0,\"m\":" + String(mask) + "}");
}

void emitText(const String& s) {
  postEvent("{\"t\":\"text\",\"s\":\"" + s + "\"}");
}

void emitMoveRel(float dx, float dy) {
  postEvent("{\"t\":\"mu\",\"dx\":" + String(dx, 2) + ",\"dy\":" + String(dy, 2) + "}");
}

void emitMoveAbs(float x, float y) {
  postEvent("{\"t\":\"mu\",\"x\":" + String(x, 2) + ",\"y\":" + String(y, 2) + "}");
}

void emitClick(uint8_t button, bool down, float x, float y) {
  String j = "{\"t\":\"mc\",\"b\":" + String(button) + ",\"d\":" + String(down ? 1 : 0);
  if (x >= 0 && y >= 0) j += ",\"x\":" + String(x, 2) + ",\"y\":" + String(y, 2);
  j += "}";
  postEvent(j);
}

// --- serial reader -------------------------------------------------------

bool readLine(String& out) {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (lineBuf.length()) { out = lineBuf; lineBuf = ""; return true; }
      continue;
    }
    lineBuf += c;
    if (lineBuf.length() > 256) lineBuf = "";
  }
  return false;
}

void handleSerialLine(const String& line) {
  char buf[line.length() + 1];
  line.toCharArray(buf, sizeof(buf));

  char* p = buf;
  char* tok = strtok_r(p, " ", &p);
  if (!tok) return;

  if (strcmp(tok, "k") == 0) {
    char* code = strtok_r(nullptr, " ", &p);
    char* down = strtok_r(nullptr, " ", &p);
    char* mask = strtok_r(nullptr, " ", &p);
    char* ch   = strtok_r(nullptr, " ", &p);
    if (!code || !down) return;
    uint32_t m = mask ? (uint32_t)strtoul(mask, nullptr, 0) : 0;
    if (strcmp(down, "0") == 0) emitKeyUp(code, m);
    else emitKeyDown(code, ch ? ch : "", m);
  }
  else if (strcmp(tok, "t") == 0) {
    String rest = p;
    if (rest.length()) emitText(rest);
  }
  else if (strcmp(tok, "mu") == 0) {
    char* dx = strtok_r(nullptr, " ", &p);
    char* dy = strtok_r(nullptr, " ", &p);
    if (dx && dy) emitMoveRel(atof(dx), atof(dy));
  }
  else if (strcmp(tok, "ma") == 0) {
    char* x = strtok_r(nullptr, " ", &p);
    char* y = strtok_r(nullptr, " ", &p);
    if (x && y) emitMoveAbs(atof(x), atof(y));
  }
  else if (strcmp(tok, "mc") == 0) {
    char* btn  = strtok_r(nullptr, " ", &p);
    char* down = strtok_r(nullptr, " ", &p);
    char* x    = strtok_r(nullptr, " ", &p);
    char* y    = strtok_r(nullptr, " ", &p);
    if (btn && down) {
      emitClick((uint8_t)atoi(btn), strcmp(down, "0") != 0,
                x ? atof(x) : -1, y ? atof(y) : -1);
    }
  }
  else if (strcmp(tok, "sc") == 0) {
    char* dy = strtok_r(nullptr, " ", &p);
    if (dy) postEvent("{\"t\":\"sc\",\"dy\":" + String(atof(dy), 2) + "}");
  }
}

// --- BT keyboard hooks ---------------------------------------------------
// Call these from your HID host callback (BLE or classic HID). `code` is a DOM
// code such as "KeyA" / "Enter" / "ShiftLeft"; `mask` is the USB modifier mask.
void onKeyDown(const char* code, const char* ch, uint32_t mask) { emitKeyDown(code, ch, mask); }
void onKeyUp(const char* code, uint32_t mask) { emitKeyUp(code, mask); }

// ------------------------------------------------------------------------

void connectWiFi() {
  Serial.printf("[wifi] connecting to %s", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
    if (millis() - start > 30000) {
      Serial.println("\n[wifi] retry");
      WiFi.disconnect();
      WiFi.begin(WIFI_SSID, WIFI_PASS);
      start = millis();
    }
  }
  Serial.printf("\n[wifi] connected, IP %s\n", WiFi.localIP().toString().c_str());
  Serial.printf("[kbd] target http://%s:%d/events\n", SERVER_HOST, SERVER_PORT);
}

void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println("\n[kbd] kbd-bridge ESP32 client");
  Serial.println("[kbd] serial: k <code> <down> <mask> [ch] | t <text> | mu <dx> <dy> | ma <x> <y> | mc <btn> <down> [x y] | sc <dy>");
  connectWiFi();
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    connectWiFi();
    return;
  }
  String line;
  while (readLine(line)) {
    handleSerialLine(line);
  }
  delay(5);
}
