#include <Adafruit_NeoPixel.h>
#include <math.h>

#define LED_PIN    15
#define LED_COUNT  150

Adafruit_NeoPixel strip(LED_COUNT, LED_PIN, NEO_GRB + NEO_KHZ800);

enum Mode { MODE_COLORWAVE = 1, MODE_DIAGONAL = 2, MODE_GRADIENT = 3, MODE_AURORA = 4, MODE_BREATHING = 5, MODE_AMBILIGHT = 6, MODE_WARMWHITE = 7 };
Mode currentMode = MODE_WARMWHITE;
bool powerOn = true;

// Physical segment map (see project doc): strip loops the monitor perimeter
// once, then partway around again along the left and top edges.
const int LEFT1_START = 0,   LEFT1_COUNT = 18;
const int TOP1_START = 18,   TOP1_COUNT = 35;
const int RIGHT_START = 53,  RIGHT_COUNT = 20;
const int BOTTOM_START = 73, BOTTOM_COUNT = 31;
const int LEFT2_START = 104, LEFT2_COUNT = 17;
const int TOP2_START = 121,  TOP2_COUNT = 29;

unsigned long lastFrameTime = 0;
const unsigned long FRAME_INTERVAL_MS = 15;

uint16_t waveHue = 0;
int waveDir = 1;
const uint16_t WARM_HUE_MIN = 0;      // red
const uint16_t WARM_HUE_MAX = 10922;  // yellow (~60 degrees)
const uint8_t WARM_SATURATION = 150;  // richer warm tone, less washed out

int diagPos = 0;
const int DIAG_HEADS = 4;

float auroraT = 0;
float breathT = 0;

uint32_t ambLeftCur = 0, ambTopCur = 0, ambRightCur = 0, ambBottomCur = 0;
uint32_t ambLeftTarget = 0, ambTopTarget = 0, ambRightTarget = 0, ambBottomTarget = 0;
const float AMBI_SMOOTH_FACTOR = 0.15; // fraction of the way to the target color eased per frame tick

String inputLine = "";

void setup() {
  Serial.begin(115200);
  strip.begin();
  strip.setBrightness(40);
  strip.clear();
  strip.show();
  randomSeed(analogRead(0));
}

void loop() {
  readSerialCommands();

  if (!powerOn) return;

  unsigned long now = millis();
  if (now - lastFrameTime >= FRAME_INTERVAL_MS) {
    lastFrameTime = now;
    switch (currentMode) {
      case MODE_COLORWAVE: stepColorWave();   break;
      case MODE_DIAGONAL:  stepDiagonalChase(); break;
      case MODE_GRADIENT:  stepGradientWave(); break;
      case MODE_AURORA:    stepAurora();      break;
      case MODE_BREATHING: stepBreathing();   break;
      case MODE_AMBILIGHT: stepAmbilight(); break;
      case MODE_WARMWHITE: stepWarmWhite(); break;
    }
  }
}

// ---------- Serial command protocol ----------
// Commands (one per line, newline-terminated):
//   MODE 1 | MODE 2 | MODE 3 | MODE 4 | MODE 5 | MODE 6 | MODE 7  -> switch effect, replies "OK"
//   SEG l_r,l_g,l_b t_r,t_g,t_b r_r,r_g,r_b b_r,b_g,b_b   -> push live colors for
//       left/top/right/bottom segments (0-255 each), switches to ambilight mode, replies "OK"
//   ON | OFF         -> power control, replies "OK"
//   STATUS           -> replies {"power":"on","mode":"colorwave"}
// Unrecognized input replies "ERR unknown command"

void readSerialCommands() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') {
      handleCommand(inputLine);
      inputLine = "";
    } else if (c != '\r') {
      inputLine += c;
    }
  }
}

const char* modeName(Mode m) {
  switch (m) {
    case MODE_COLORWAVE: return "colorwave";
    case MODE_DIAGONAL:  return "diagonal";
    case MODE_GRADIENT:  return "gradient";
    case MODE_AURORA:    return "aurora";
    case MODE_BREATHING: return "breathing";
    case MODE_AMBILIGHT: return "ambilight";
    case MODE_WARMWHITE: return "warmwhite";
  }
  return "unknown";
}

uint32_t parseRGB(String tok) {
  int c1 = tok.indexOf(',');
  int c2 = tok.indexOf(',', c1 + 1);
  if (c1 < 0 || c2 < 0) return 0;
  int r = constrain(tok.substring(0, c1).toInt(), 0, 255);
  int g = constrain(tok.substring(c1 + 1, c2).toInt(), 0, 255);
  int b = constrain(tok.substring(c2 + 1).toInt(), 0, 255);
  return strip.Color(r, g, b);
}

void handleCommand(String line) {
  line.trim();
  if (line.startsWith("MODE ")) {
    int m = line.substring(5).toInt();
    if (m >= 1 && m <= 7) {
      currentMode = (Mode)m;
      Serial.println("OK");
    } else {
      Serial.println("ERR bad mode");
    }
  } else if (line.startsWith("SEG ")) {
    String rest = line.substring(4);
    rest.trim();
    int sp1 = rest.indexOf(' ');
    int sp2 = sp1 < 0 ? -1 : rest.indexOf(' ', sp1 + 1);
    int sp3 = sp2 < 0 ? -1 : rest.indexOf(' ', sp2 + 1);
    if (sp1 < 0 || sp2 < 0 || sp3 < 0) {
      Serial.println("ERR bad seg");
    } else {
      ambLeftTarget = parseRGB(rest.substring(0, sp1));
      ambTopTarget = parseRGB(rest.substring(sp1 + 1, sp2));
      ambRightTarget = parseRGB(rest.substring(sp2 + 1, sp3));
      ambBottomTarget = parseRGB(rest.substring(sp3 + 1));

      currentMode = MODE_AMBILIGHT;
      Serial.println("OK");
    }
  } else if (line == "ON") {
    powerOn = true;
    Serial.println("OK");
  } else if (line == "OFF") {
    powerOn = false;
    strip.clear();
    strip.show();
    Serial.println("OK");
  } else if (line == "STATUS") {
    Serial.print("{\"power\":\"");
    Serial.print(powerOn ? "on" : "off");
    Serial.print("\",\"mode\":\"");
    Serial.print(modeName(currentMode));
    Serial.println("\"}");
  } else {
    Serial.println("ERR unknown command");
  }
}

// ---------- LED effect steps (one frame per call, non-blocking) ----------

void fadeAll(uint8_t fadeBy) {
  for (int p = 0; p < strip.numPixels(); p++) {
    uint32_t c = strip.getPixelColor(p);
    uint8_t r = (uint8_t)(c >> 16);
    uint8_t g = (uint8_t)(c >> 8);
    uint8_t b = (uint8_t)c;
    r = (uint8_t)((r * (255 - fadeBy)) / 255);
    g = (uint8_t)((g * (255 - fadeBy)) / 255);
    b = (uint8_t)((b * (255 - fadeBy)) / 255);
    strip.setPixelColor(p, strip.Color(r, g, b));
  }
}

void stepColorWave() {
  uint32_t color = strip.gamma32(strip.ColorHSV(waveHue, WARM_SATURATION, 255));
  strip.fill(color);
  strip.show();

  int next = (int)waveHue + waveDir * 60;
  if (next >= WARM_HUE_MAX) { next = WARM_HUE_MAX; waveDir = -1; }
  else if (next <= WARM_HUE_MIN) { next = WARM_HUE_MIN; waveDir = 1; }
  waveHue = (uint16_t)next;
}

void stepDiagonalChase() {
  int n = strip.numPixels();
  fadeAll(30);
  for (int h = 0; h < DIAG_HEADS; h++) {
    int pos = (diagPos + h * (n / DIAG_HEADS)) % n;
    uint16_t hue = (uint16_t)((uint32_t)h * 65536 / DIAG_HEADS);
    strip.setPixelColor(pos, strip.gamma32(strip.ColorHSV(hue)));
  }
  strip.show();
  diagPos = (diagPos + 1) % n;
}

void stepGradientWave() {
  int n = strip.numPixels();
  const uint16_t HUE_SPREAD = 6000; // keeps the whole strip within a similar ~33-degree hue range at any instant
  for (int i = 0; i < n; i++) {
    uint16_t hue = waveHue + (uint32_t)i * HUE_SPREAD / n;
    strip.setPixelColor(i, strip.gamma32(strip.ColorHSV(hue)));
  }
  strip.show();
  waveHue += 40;
}

void stepAurora() {
  int n = strip.numPixels();
  for (int i = 0; i < n; i++) {
    float v = sin(i * 0.15 + auroraT) + sin(i * 0.07 - auroraT * 0.6);
    uint16_t hue = (uint16_t)(32768 + v * 8000);
    uint8_t val = 150 + (uint8_t)(sin(i * 0.1 + auroraT * 1.3) * 60);
    strip.setPixelColor(i, strip.gamma32(strip.ColorHSV(hue, 255, val)));
  }
  strip.show();
  auroraT += 0.03;
}

void stepBreathing() {
  float b = (sin(breathT) + 1.0) / 2.0;
  uint8_t level = (uint8_t)(b * 255);
  uint32_t color = strip.gamma32(strip.ColorHSV(waveHue, 255, level));
  strip.fill(color);
  strip.show();
  waveHue += 15;
  breathT += 0.03;
}

void stepWarmWhite() {
  strip.fill(strip.Color(255, 109, 0)); // ~1500K, 2x warmer than the original 3000K
  strip.show();
}

uint32_t lerpColor(uint32_t cur, uint32_t target, float factor) {
  uint8_t cr = (cur >> 16) & 0xFF, cg = (cur >> 8) & 0xFF, cb = cur & 0xFF;
  uint8_t tr = (target >> 16) & 0xFF, tg = (target >> 8) & 0xFF, tb = target & 0xFF;
  uint8_t nr = cr + (int)((tr - cr) * factor);
  uint8_t ng = cg + (int)((tg - cg) * factor);
  uint8_t nb = cb + (int)((tb - cb) * factor);
  return strip.Color(nr, ng, nb);
}

void stepAmbilight() {
  ambLeftCur = lerpColor(ambLeftCur, ambLeftTarget, AMBI_SMOOTH_FACTOR);
  ambTopCur = lerpColor(ambTopCur, ambTopTarget, AMBI_SMOOTH_FACTOR);
  ambRightCur = lerpColor(ambRightCur, ambRightTarget, AMBI_SMOOTH_FACTOR);
  ambBottomCur = lerpColor(ambBottomCur, ambBottomTarget, AMBI_SMOOTH_FACTOR);

  strip.fill(ambLeftCur, LEFT1_START, LEFT1_COUNT);
  strip.fill(ambLeftCur, LEFT2_START, LEFT2_COUNT);
  strip.fill(ambTopCur, TOP1_START, TOP1_COUNT);
  strip.fill(ambTopCur, TOP2_START, TOP2_COUNT);
  strip.fill(ambRightCur, RIGHT_START, RIGHT_COUNT);
  strip.fill(ambBottomCur, BOTTOM_START, BOTTOM_COUNT);
  strip.show();
}
