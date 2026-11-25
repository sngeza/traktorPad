// TraktorPad LED panel: shows frames drawn by traktorpad.py on a WS2812B matrix.
//
//   python3 server/traktorpad.py --led auto
//
// The Mac does all the drawing. This sketch only receives frames over USB
// serial and pushes them to the LEDs:
//
//   0xAA 0x55 <brightness> <NUM_LEDS * 3 bytes RGB, in strip order>
//
// and answers 'K' once a frame is on the LEDs. The Mac waits for that 'K'
// before it sends the next frame, so no byte arrives while show() has
// interrupts off (that would lose data on a Nano's UART).
//
// Boards: a 5V Pro Micro / Leonardo (ATmega32U4, native USB, no level shifter),
// an Arduino Nano/Uno, or an ESP32 (3.3V data: add a 74AHCT125 level shifter if
// the first LED misbehaves). Needs the FastLED library.
//
// Wiring: 5V supply (3-4 A) straight to the panel, 1000 uF across it, 330 ohm
// in the data line, and the panel's ground joined to the board's ground.

#include <FastLED.h>

#ifndef DATA_PIN
#define DATA_PIN     6          // Pro Micro / Nano: D6.  ESP32: e.g. 16
#endif
#define NUM_LEDS     256        // 8 x 32
#define MAX_MILLIAMPS 2500      // FastLED dims frames that would draw more
#define BAUD         1000000    // ignored by native-USB boards

CRGB leds[NUM_LEDS];

enum { WAIT_SYNC1, WAIT_SYNC2, WAIT_BRIGHTNESS, READ_PIXELS } state = WAIT_SYNC1;
uint16_t received = 0;
uint32_t lastByte = 0;
uint32_t lastFrame = 0;
uint32_t lastIdle = 0;

void setup() {
  FastLED.addLeds<WS2812B, DATA_PIN, GRB>(leds, NUM_LEDS);
  FastLED.setMaxPowerInVoltsAndMilliamps(5, MAX_MILLIAMPS);
  FastLED.setBrightness(40);
  FastLED.clear(true);
  Serial.begin(BAUD);
}

// No frames from the Mac: one dim pixel, breathing.
void idle(uint32_t now) {
  if (now - lastIdle < 30) return;
  lastIdle = now;
  FastLED.clear();
  leds[0] = CRGB(sin8(now / 8) / 6 + 4, 0, 0);
  FastLED.setBrightness(255);
  FastLED.show();
}

void loop() {
  uint32_t now = millis();

  // A frame that stopped half way through is dropped.
  if (state != WAIT_SYNC1 && now - lastByte > 50) state = WAIT_SYNC1;

  while (Serial.available() > 0) {
    uint8_t b = Serial.read();
    lastByte = now;
    switch (state) {
      case WAIT_SYNC1:
        if (b == 0xAA) state = WAIT_SYNC2;
        break;
      case WAIT_SYNC2:
        state = (b == 0x55) ? WAIT_BRIGHTNESS : (b == 0xAA ? WAIT_SYNC2 : WAIT_SYNC1);
        break;
      case WAIT_BRIGHTNESS:
        FastLED.setBrightness(b);
        received = 0;
        state = READ_PIXELS;
        break;
      case READ_PIXELS:
        ((uint8_t *)leds)[received++] = b;
        if (received == NUM_LEDS * 3) {
          FastLED.show();
          Serial.write('K');
          lastFrame = now;
          state = WAIT_SYNC1;
        }
        break;
    }
  }

  if (now - lastFrame > 3000) idle(now);
}
