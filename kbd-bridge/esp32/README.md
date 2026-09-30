# ESP32 client

Posts key/mouse events to the kbd-bridge server over WiFi.

## Two input backends

### 1. Serial (works now — test the whole pipeline)

Flash `esp32_kbd_bridge.ino` (Arduino core / PlatformIO). Set WiFi + server in
the CONFIG block, then open the serial monitor at 115200 and type:

```
k KeyA 1 0 a        # KeyA down, mask 0, char 'a'
k KeyA 0 0          # KeyA up
k ShiftLeft 1 2     # Shift down (mask bit1)
t hello             # bulk insert "hello"
mu 5 -3             # mouse move +5,-3
ma 640 300          # absolute move
mc 0 1 640 300      # left button down at 640,300
mc 0 0              # left button up
sc -120             # wheel
```

Each line is POSTed as JSON to `http://<SERVER_HOST>:8766/events`.

### 2. BT keyboard

Wire your HID host into the `onKeyDown()/onKeyUp()` hooks (bottom of the
sketch). They already build the right JSON. The one thing that is
keyboard-specific is getting the HID reports:

- **BLE HID keyboard** → use ESP-IDF's `esp_hid_host` component (official
  `bluetooth/esp_hid_host` example is the known-good base; it gives you an
  `ESP_HIDH_INPUT_EVENT` callback — map the report's usage to a DOM `code`
  string + modifier mask and call `onKeyDown`).
- **BT Classic HID keyboard** → Bluedroid HID host (`esp_hidh`), same mapping.

Tell me the exact keyboard model + BT flavour and I'll finish that leg (it
needs a compile/flash cycle on hardware, which I can't do from here).

## Modifier mask (USB HID)

bit0 LCtrl · bit1 LShift · bit2 LAlt · bit3 LGui · bit4 RCtrl · bit5 RShift ·
bit6 RAlt · bit7 RGui
