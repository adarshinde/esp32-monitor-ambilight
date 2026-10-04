# esp32-monitor-ambilight

An ESP32 + WS2812B LED strip mounted behind a monitor, with several ambient lighting modes plus a real-time "Ambilight"-style screen color sync — controllable via a CLI tool and a macOS menu bar app, all talking to the ESP32 over USB serial (no WiFi).

**Note on paths:** this was built for one specific machine, so file paths throughout (`~/esp32_rgb_loop/...`, a specific pyenv Python version, `/dev/cu.usbserial-0001`) are hardcoded to that setup. If you're reusing this, adjust the paths in `ledctl`, the LaunchAgent plists, and the Python scripts to match your own machine.

## Hardware
- ESP32-D0WD-V3 dev board, USB-C, CP2102 USB-UART bridge
- WS2812B addressable LED strip, 60 LEDs/m, 2.5m = **150 LEDs total**
- Strip wire colors: **Red = 5V, White = GND, Green = Data In (DIN)**
- Data line connects to **ESP32 GPIO15**
- Mounted behind an external monitor (2560×1440), powered and data-connected through the monitor's built-in USB hub

## Wiring

| Strip wire | Connect to |
|---|---|
| Red (5V) | ESP32 **5V** or **VIN** pin |
| White (GND) | Any ESP32 **GND** pin |
| Green (Data) | ESP32 **GPIO15** |

Power comes from whatever the ESP32's USB connection provides — works via direct computer connection, a wall charger, or a monitor's USB hub.

**Gotcha:** some USB-A-to-USB-C cables are charge-only (no data lines) — look identical to a real cable but won't enumerate at all. If the ESP32 "disappears" after being unplugged/replugged, try swapping the cable before assuming a software issue.

## Physical LED segment map
The strip loops the monitor's perimeter once, then partway around again along the left and top edges (it's longer than one full loop). LED index 0 starts at the bottom-left corner.

| Segment | Edge | Direction | LED count | Index range |
|---|---|---|---|---|
| A | Left (vertical) | bottom → top | 18 | 0-17 |
| B | Top (horizontal) | left → right | 35 | 18-52 |
| C | Right (vertical) | top → bottom | 20 | 53-72 |
| D | Bottom (horizontal) | right → left | 31 | 73-103 |
| E | Left (vertical), 2nd pass | bottom → top | 17 | 104-120 |
| F | Top (horizontal), 2nd pass | left → right | 29 | 121-149 |

Total: 18+35+20+31+17+29 = **150**, matches the full strip exactly. Left edge is double-stacked (A+E = 35 LEDs), top edge is double-stacked (B+F = 64 LEDs), right (C = 20) and bottom (D = 31) are single rows. If you reuse this for a different monitor, you'll need to re-measure and update the segment constants in the firmware (`LEFT1_START`, `TOP1_COUNT`, etc.).

## Repo layout
```
firmware/esp32_rgb_loop/esp32_rgb_loop.ino   Arduino sketch (flash with arduino-cli or Arduino IDE)
mac/esp32_helper.py                           Serial bridge daemon (holds the USB connection open)
mac/screen_sync.py                            Screen capture + Ambilight color sync
mac/menubar_app.py                            macOS menu bar GUI
mac/ledctl                                    CLI control tool
launchagents/*.plist                          macOS LaunchAgents for auto-start on login
```

## Dev environment setup
- Install `arduino-cli`
- Install the `esp32:esp32` board core (classic ESP32 / "ESP32 Dev Module", FQBN `esp32:esp32:esp32`)
- On Apple Silicon: install Rosetta 2 — `ctags`, a build tool arduino-cli uses, is x86_64-only
- Install the `Adafruit NeoPixel` Arduino library
- Python side: `pip3 install pyserial mss numpy rumps`

Compile/upload (stop the helper first to free the serial port):
```
launchctl unload ~/Library/LaunchAgents/com.adarsh.esp32helper.plist
pkill -f esp32_helper.py
arduino-cli compile --fqbn esp32:esp32:esp32 firmware/esp32_rgb_loop
arduino-cli upload -p /dev/cu.usbserial-0001 --fqbn esp32:esp32:esp32 firmware/esp32_rgb_loop
launchctl load ~/Library/LaunchAgents/com.adarsh.esp32helper.plist
```

## Firmware evolution
1. Simple solid Red → Green → Blue loop (first test)
2. Theater-chase rainbow, then sparkle/twinkle
3. Confetti, dual comet, dimmed strobe
4. Fire simulation (heat-diffusion algorithm) and random lightning-storm flashes
5. Brightness ceiling testing — solid white across all 150 LEDs is safe up to ~30-40% brightness over USB power (no flicker/brownout observed); ~40% white ≈ 3.6-3.7A estimated, pushing USB limits — an external 5V 10A+ supply is recommended before going higher (see below)
6. **WiFi control version** (superseded) — HTTP server with `/mode`, `/power`, `/status`, controlled via curl. Debugged: unreliable mDNS (switched to static IP), and a bug where WiFi never reconnected after a drop. Eventually dropped entirely in favor of USB
7. **USB serial control** — WiFi removed entirely (lower flash usage, no WiFi chip load). Plain-text commands over USB serial
8. Removed all "demo" effects once the segment map was verified — replaced with ambient modes meant to look good as a permanent monitor backlight
9. Added **Ambilight mode** — real-time screen color sync, including temporal smoothing to eliminate flicker

## Firmware modes
1. **Color Wave** — whole strip, one color continuously sweeping (ping-pong) through only the warm range: red → orange → yellow (hue 0-60°), at a tuned pastel-but-not-washed-out saturation
2. **Diagonal Chase** — short colored bands circling the loop, cutting diagonally through corners
3. **Gradient Wave** — multi-color gradient across the strip within a harmonious hue range, slowly rotating
4. **Aurora** — organic flowing multi-color blobs (layered sine waves)
5. **Breathing Pulse** — slow color rotation with gentle brightness breathing
6. **Ambilight** — driven externally by `screen_sync.py`; activates automatically when a `SEG` command arrives
7. **Warm White** — solid warm white across the whole strip (~1500K), static. **Default boot mode.**

Serial protocol (one command per line, newline-terminated, 115200 baud):
- `MODE 1` .. `MODE 7` → switch effect, replies `OK`
- `SEG l_r,l_g,l_b t_r,t_g,t_b r_r,r_g,r_b b_r,b_g,b_b` → push live target colors (0-255 each) for left/top/right/bottom edges, switches to Ambilight mode, replies `OK`
- `ON` / `OFF` → power control, replies `OK`
- `STATUS` → replies `{"power":"on","mode":"colorwave"}`
- Unrecognized input → `ERR unknown command`

**Ambilight smoothing (anti-flicker):** the firmware does not snap to each `SEG` color immediately. It stores a target color per edge and continuously eases the currently-displayed color toward that target every frame tick, closing ~15% of the remaining gap per 15ms tick. This is the same trick Philips Hue Sync uses — smoothing happens on the receiving device continuously, independent of how often or irregularly the source sends updates.

## Mac-side control stack
**Why a helper process:** CP2102-based boards hardware-reset the ESP32 every time a program freshly opens the serial port (same mechanism used for flashing). A background helper holds the serial connection open permanently so the strip doesn't blip/reboot on every command.

- **`mac/esp32_helper.py`** — Python daemon using `pyserial`. Opens the serial port once, keeps it open, listens on a local Unix socket for commands and relays them. **Self-healing:** if a write/read ever fails (device unplugged/replugged), it automatically closes the stale connection and retries opening every 2 seconds until the device reappears.
- **`mac/ledctl`** — Bash CLI tool, sends commands via the Unix socket:
  ```
  ledctl mode 1..7
  ledctl on
  ledctl off
  ledctl status
  ```
  **Fully manages `screen_sync.py`'s lifecycle:** switching to mode 6 auto-starts it if not already running; switching to any other mode stops it first. This matters because `screen_sync.py` runs in an infinite loop and every `SEG` push forces the firmware back into Ambilight mode — without this, switching away from mode 6 while the script was still running would get overridden within ~50ms.
- **`launchagents/com.adarsh.esp32helper.plist`** — macOS LaunchAgent, auto-starts the helper on login and auto-restarts it if it crashes.

## Menu bar app — `mac/menubar_app.py`
A macOS menu bar GUI built with `rumps`. It's a **thin wrapper around `ledctl`** — every action (mode switch, on/off, status) shells out to the same CLI rather than duplicating any logic, so there's exactly one source of truth for mode-switching and `screen_sync.py` lifecycle management.

Menu contents: all 7 modes (checkmark shows the currently active one), Power On/Off, a live status line, and Quit. The CLI still works independently alongside it.

Registered as a LaunchAgent (`launchagents/com.adarsh.esp32menubar.plist`), same auto-start/auto-restart pattern as the serial helper.

**Gotcha:** launching Python subprocesses from a LaunchAgent (minimal PATH, no pyenv shims) can resolve `python3` to the wrong interpreter — one missing the packages installed for this project. Fixed by hardcoding the full interpreter path in `ledctl` rather than relying on PATH.

## Screen color sync ("Ambilight") — `mac/screen_sync.py`
Captures **only the external monitor** (auto-detected by resolution, excluding a laptop's built-in display if present), analyzes it, and pushes colors to the ESP32 using `SEG`.

Requires macOS Screen Recording permission granted to the terminal app running it, plus a one-time "bypass window picker" consent dialog the first time it runs.

Pipeline per frame:
1. Grab the external monitor's raw frame.
2. **Letterbox/pillarbox bar detection:** scan inward from each edge (on a coarse subsample for speed) for solid black rows/columns and crop them out before any further analysis — capped at 25% of a dimension so a genuinely dark full-screen scene is never misread as a bar.
3. Divide the remaining content area into **4 triangular regions using the screen's two diagonals** (top/bottom/left/right wedges meeting at the center) — matches the LED strip's perimeter layout better than rectangular quadrants.
4. For each region, compute the **dominant color** (quantized-histogram mode) rather than a plain average, after filtering out near-black pixels — avoids washing out to grey/brown and keeps genuinely dark scenes dark without bar interference.
5. **Corner blending:** each edge's final color is 70% its own dominant color + 15% + 15% from its two perpendicular neighbors — smooths the transition at corners instead of a hard cut between flat colors.
6. **Saturation boost** so colors pop a bit more instead of reading flat.
7. **Washed-out blue-white fix:** busy/colorful scenes sometimes average out to a desaturated, cool-tinted near-white rather than a real color — a common Ambilight artifact. Detected specifically (low saturation + cyan/blue hue range) and retinted to a warm tone while preserving brightness. Genuinely saturated blues pass through untouched.
8. Send via `SEG` to the ESP32, which does the temporal smoothing described above.
9. Target ~20 FPS, downsampled before analysis for speed.

Performance note: the bar-detection scan originally ran on the full-resolution frame every frame and spiked CPU to ~70%; fixed by running it on a coarse subsample instead (boundary precision within a few pixels doesn't matter) — now runs at ~12-13% CPU.

Managed automatically by `ledctl` (see above) — not wired up as its own LaunchAgent, started/stopped on demand instead of running permanently.

## Future upgrade: external 5V supply
For running the whole strip bright (beyond ~30-40% brightness) without USB current limits:
- Get a 5V DC power supply rated **≥10A (50W)**, enclosed wall-plug style, not bare open-frame
- Wire: PSU **+** → strip Red; PSU **−** → strip White **and** ESP32 GND (common ground); ESP32 GPIO15 → strip Green (unchanged); ESP32 still powered separately via USB
- Do not connect PSU 5V to the ESP32's 5V/VIN pin — only share ground
