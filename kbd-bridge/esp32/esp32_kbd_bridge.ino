/*
 * kbd-bridge ESP32 client (Arduino / PlatformIO).
 *
 * Posts key/mouse events to the kbd-bridge server over WiFi.
 *
 * Input backends:
 *   1. USB serial  — a tiny line protocol for testing the whole pipeline
 *      (works today, no extra hardware). See SERIAL PROTOCOL below.
 *   2. BT keyboard — wire your HID host into the readKeyReport()/onKey*()
 *      hooks at the bottom. Exact BT flavour (BLE vs classic HID) depends on
 *      your keyboard model; the hooks are transport-agnostic.
 *
 * SERIAL PROTOCOL (one line per event):
 *   k <code> <down> <mask> [ch]      key     e.g.  k KeyA 1 0 a
 *   t <text...>                      bulk text      t hello world
 *   mu <dx> <dy>                     relative move  mu 5 -3
 *   ma <x> <y>                       absolute move  ma 640 300
 *   mc <button> <down> [x] [y]       click          mc 0 1 640 300
 *   sc <dy>                          wheel          sc -120
 */

#include <WiFi.h>
#include <HTTPClient.h>

// --------------------------- CONFIG -------------------------------------
const char* WIFI_SSID   = "your-ssid";
const char* WIFI_PASS   = "your-pass";
const char* SERVER_HOST = "tesla.aijy.com";   // or your LAN IP while testing
const uint16_t SERVER_PORT = 8766;
const String  KBD_TOKEN   = "";               // set if server requires KBD_TOKEN
// ------------------------------------------------------------------------

static String lineBuf;

void postEvent(const String& json) {
  HTTPClient http;
  String url = "http://" + String(SERVER_HOST) + ":" + String(SERVER_PORT) + "/events";
  if (KBD_TOKEN.length()) url += "?token=" + KBD_TOKEN;

  http.begin(url);
  http.addHeader("Content-Type", "application/json");
  if (KBD_TOKEN.length()) http.addHeader("Authorization", "Bearer " + KBD_TOKEN);
  http.setTimeout(4000);
  int code = http.POST(json);
  if (code > 0) {
    Serial.printf("[kbd] POST %d  %s\n", code, json.c_str());
  } else {
    Serial.printf("[kbd] POST failed: %s\n", http.errorToString(code).c_str());
  }
  http.end();
}

// --- event emitters (shared by serial + BT backends) --------------------

void emitKeyDown(const char* code, const char* ch, uint32_t mask) {
  String j = "{\"t\":\"k\",\"c\":\"" + String(code) + "\",\"d\":1,\"m\":" + String(mask);
  if (ch && ch[0]) j += ",\"ch\":\"" + String(ch) + "\"";
  j += "}";
  postEvent(j);
}

void emitKeyUp(const char* code, uint32_t mask) {
  String j = "{\"t\":\"k\",\"c\":\"" + String(code) + "\",\"d\":0,\"m\":" + String(mask) + "}";
  postEvent(j);
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

// --- serial line reader ---------------------------------------------------

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

// --- serial protocol handler ----------------------------------------------

void handleSerialLine(const String& line) {
  // split on spaces
  char buf[line.length() + 1];
  line.toCharArray(buf, sizeof(buf));

  char* p = buf;
  // first token
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
    String rest = p;                    // remainder of line = text
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
    char* btn = strtok_r(nullptr, " ", &p);
    char* down = strtok_r(nullptr, " ", &p);
    char* x = strtok_r(nullptr, " ", &p);
    char* y = strtok_r(nullptr, " ", &p);
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

// --------------------------------------------------------------------------
// BT keyboard hooks. Wire your HID host in here: call emitKeyDown/Up with a
// DOM `code` (e.g. "KeyA", "Enter", "ShiftLeft") plus the USB modifier mask.
// --------------------------------------------------------------------------

// Called when a key goes down. `code` = DOM code, `ch` = resulting char or "".
void onKeyDown(const char* code, const char* ch, uint32_t mask) {
  emitKeyDown(code, ch, mask);
}

// Called when a key is released.
void onKeyUp(const char* code, uint32_t mask) {
  emitKeyUp(code, mask);
}

// Placeholder: replace with your BT HID host's report callback.
// void readKeyReport(...) { ... onKeyDown("KeyA", "a", 0); ... }

// --------------------------------------------------------------------------

void connectWiFi() {
  Serial.printf("[wifi] connecting to %s", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.printf("\n[wifi] connected, IP %s\n", WiFi.localIP().toString().c_str());
}

void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println("\n[kbd] kbd-bridge ESP32 client");
  Serial.println("[kbd] serial protocol: k <code> <down> <mask> [ch] | t <text> | mu <dx> <dy> | ma <x> <y> | mc <btn> <down> [x y] | sc <dy>");
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
