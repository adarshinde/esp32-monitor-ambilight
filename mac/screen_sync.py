#!/usr/bin/env python3
"""Ambilight-style screen color sync: captures only the external monitor
(auto-detected by resolution, never the MacBook's built-in display), divides
the frame into 4 triangular regions using the screen's two diagonals
(top/bottom/left/right, matching the LED strip's perimeter layout), picks
the dominant color in each region (filtering out near-black pixels so
letterbox/pillarbox bars don't wash things out), blends each edge's color
slightly with its two neighboring edges for smoother corners, and pushes
the result to the ESP32 over the existing esp32_helper socket using the
SEG serial command. The firmware itself smooths transitions over time."""

import colorsys
import socket
import time

import mss
import numpy as np

SOCK_PATH = "/tmp/esp32ctl.sock"
EXTERNAL_WIDTH = 2560
EXTERNAL_HEIGHT = 1440
TARGET_FPS = 20
STRIDE = 10            # downsample factor for speed before analysis
BLACK_THRESHOLD = 24    # pixels with max(R,G,B) below this are treated as "black bar" and excluded
BUCKET_SIZE = 24        # quantization bucket size per channel for dominant-color histogram
CORNER_BLEND = 0.15     # how much of each neighboring edge's color bleeds into this edge
MAX_BAR_FRACTION = 0.25  # cap how much of a dimension can be cropped as a "bar", so a genuinely
                         # dark full-screen scene never gets misread as letterboxing
SATURATION_BOOST = 1.3   # multiplies HSV saturation before sending, clamped to 1.0
WHITEISH_SAT_THRESHOLD = 0.6   # below this saturation, a blue/cyan-ish hue reads as washed-out white, not a real color
                               # (true vivid blues typically sit at 0.8+, so this still leaves real blue content alone)
WHITEISH_HUE_MIN = 0.45        # cyan
WHITEISH_HUE_MAX = 0.78        # blue-violet boundary
WARM_WHITE_HUE = 0.05          # fire/amber-leaning hue used to retint washed-out blue-whites
WARM_WHITE_SAT = 0.59          # matches the sweet spot found for Mode 1's warm tone (150/255), reads as warm, not washed out


def find_external_monitor(sct):
    for m in sct.monitors[1:]:  # index 0 is "all monitors combined"
        if m["width"] == EXTERNAL_WIDTH and m["height"] == EXTERNAL_HEIGHT:
            return m
    raise RuntimeError(
        f"Could not find external monitor ({EXTERNAL_WIDTH}x{EXTERNAL_HEIGHT}) "
        f"among: {sct.monitors}"
    )


def build_quadrant_masks(h, w):
    ys, xs = np.mgrid[0:h, 0:w]
    nx = (xs - w / 2) / (w / 2)
    ny = (ys - h / 2) / (h / 2)
    diag1 = ny - nx
    diag2 = ny + nx
    top = (diag1 < 0) & (diag2 < 0)
    bottom = (diag1 > 0) & (diag2 > 0)
    right = (diag1 < 0) & (diag2 > 0)
    left = (diag1 > 0) & (diag2 < 0)
    return top, bottom, left, right


BAR_DETECT_STRIDE = 8  # coarse subsample for bar detection; boundary precision within a few px doesn't matter here


def detect_content_bounds(frame_bgr):
    """Find the active content rectangle by scanning in from each edge past
    any solid black letterbox/pillarbox bars, capped at MAX_BAR_FRACTION.
    Runs on a coarse subsample since exact-pixel boundary precision isn't needed."""
    full_h, full_w = frame_bgr.shape[:2]
    small = frame_bgr[::BAR_DETECT_STRIDE, ::BAR_DETECT_STRIDE]
    h, w = small.shape[:2]
    row_bright = small.max(axis=(1, 2)) > BLACK_THRESHOLD
    col_bright = small.max(axis=(0, 2)) > BLACK_THRESHOLD

    max_rows = int(h * MAX_BAR_FRACTION)
    max_cols = int(w * MAX_BAR_FRACTION)

    top = 0
    while top < max_rows and not row_bright[top]:
        top += 1
    bottom = h
    while bottom > h - max_rows and not row_bright[bottom - 1]:
        bottom -= 1
    left = 0
    while left < max_cols and not col_bright[left]:
        left += 1
    right = w
    while right > w - max_cols and not col_bright[right - 1]:
        right -= 1

    return (
        min(top * BAR_DETECT_STRIDE, full_h),
        min(bottom * BAR_DETECT_STRIDE, full_h),
        min(left * BAR_DETECT_STRIDE, full_w),
        min(right * BAR_DETECT_STRIDE, full_w),
    )


def dominant_color(pixels_rgb):
    if pixels_rgb.shape[0] == 0:
        return np.array([0, 0, 0], dtype=np.float64)

    bright_mask = pixels_rgb.max(axis=1) > BLACK_THRESHOLD
    filtered = pixels_rgb[bright_mask]
    if filtered.shape[0] == 0:
        filtered = pixels_rgb  # region is genuinely all black

    buckets = (filtered // BUCKET_SIZE).astype(np.int32)
    keys = buckets[:, 0] * 10000 + buckets[:, 1] * 100 + buckets[:, 2]
    uniq, counts = np.unique(keys, return_counts=True)
    top_key = uniq[np.argmax(counts)]
    return filtered[keys == top_key].mean(axis=0)


def clamp_rgb(c):
    return tuple(int(max(0, min(255, v))) for v in c)


def boost_saturation(rgb, factor=SATURATION_BOOST):
    r, g, b = (v / 255.0 for v in rgb)
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    s = min(1.0, s * factor)
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return (int(r * 255), int(g * 255), int(b * 255))


def fix_washed_out_blue_white(rgb):
    """Washed-out, low-saturation blue/cyan-ish near-whites (a common averaging
    artifact in busy scenes) get retinted to a warm white, preserving brightness.
    Genuinely saturated blues (real color content) pass through untouched."""
    r, g, b = (v / 255.0 for v in rgb)
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    if s < WHITEISH_SAT_THRESHOLD and WHITEISH_HUE_MIN <= h <= WHITEISH_HUE_MAX:
        h = WARM_WHITE_HUE
        s = WARM_WHITE_SAT
        r, g, b = colorsys.hsv_to_rgb(h, s, v)
        return (int(r * 255), int(g * 255), int(b * 255))
    return rgb


def send_seg(left, top, right, bottom):
    msg = (
        f"SEG {left[0]},{left[1]},{left[2]} "
        f"{top[0]},{top[1]},{top[2]} "
        f"{right[0]},{right[1]},{right[2]} "
        f"{bottom[0]},{bottom[1]},{bottom[2]}\n"
    )
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        s.connect(SOCK_PATH)
        s.sendall(msg.encode())
        s.recv(256)


def main():
    frame_interval = 1.0 / TARGET_FPS
    mask_cache = {}

    with mss.mss() as sct:
        monitor = find_external_monitor(sct)
        print(f"screen_sync: capturing external monitor {monitor}")

        frame_count = 0
        last_report = time.monotonic()

        while True:
            t0 = time.monotonic()

            bgr = np.array(sct.grab(monitor))[:, :, :3]  # view, BGR order (channel order doesn't matter for max())

            y0, y1, x0, x1 = detect_content_bounds(bgr)
            content_bgr = bgr[y0:y1, x0:x1]

            ds_rgb = content_bgr[::STRIDE, ::STRIDE, ::-1]  # downsample first, then reverse channels on the small array

            shape_key = ds_rgb.shape[:2]
            if shape_key not in mask_cache:
                mask_cache[shape_key] = build_quadrant_masks(*shape_key)
            top_mask, bottom_mask, left_mask, right_mask = mask_cache[shape_key]

            top_c = dominant_color(ds_rgb[top_mask])
            bottom_c = dominant_color(ds_rgb[bottom_mask])
            left_c = dominant_color(ds_rgb[left_mask])
            right_c = dominant_color(ds_rgb[right_mask])

            final_top = top_c * (1 - 2 * CORNER_BLEND) + left_c * CORNER_BLEND + right_c * CORNER_BLEND
            final_bottom = bottom_c * (1 - 2 * CORNER_BLEND) + left_c * CORNER_BLEND + right_c * CORNER_BLEND
            final_left = left_c * (1 - 2 * CORNER_BLEND) + top_c * CORNER_BLEND + bottom_c * CORNER_BLEND
            final_right = right_c * (1 - 2 * CORNER_BLEND) + top_c * CORNER_BLEND + bottom_c * CORNER_BLEND

            left = boost_saturation(fix_washed_out_blue_white(clamp_rgb(final_left)))
            top = boost_saturation(fix_washed_out_blue_white(clamp_rgb(final_top)))
            right = boost_saturation(fix_washed_out_blue_white(clamp_rgb(final_right)))
            bottom = boost_saturation(fix_washed_out_blue_white(clamp_rgb(final_bottom)))

            try:
                send_seg(left, top, right, bottom)
            except (ConnectionRefusedError, FileNotFoundError, socket.timeout) as e:
                print(f"screen_sync: couldn't reach esp32_helper ({e}), retrying")
                time.sleep(1)
                continue

            frame_count += 1
            now = time.monotonic()
            if now - last_report >= 5:
                print(f"screen_sync: ~{frame_count / (now - last_report):.1f} fps")
                frame_count = 0
                last_report = now

            elapsed = time.monotonic() - t0
            sleep_time = frame_interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)


if __name__ == "__main__":
    main()
