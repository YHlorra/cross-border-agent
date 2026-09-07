"""Assemble recorded demo frames into docs/screenshots/04-demo.gif .

Input : frame PNGs from scripts/record_demo.mjs (default %TEMP%/demo-frames)
Output: docs/screenshots/04-demo.gif — width 880, ≤96 colors, ~2.9 fps playback.
Near-duplicate frames are dropped (idle stretches like the thinking ring) to
keep the GIF small; at least one frame per second is always kept.

Usage: .venv/Scripts/python.exe scripts/make_gif.py [frames_dir] [out_gif]
"""
from __future__ import annotations

import glob
import os
import sys
import tempfile

from PIL import Image

TARGET_W = 880
COLORS = 96
FRAME_MS = 350  # playback ms per kept frame (~2.9 fps)
KEEP_EVERY_MS = 1000  # force-keep at least one frame per second of wall time

THUMB = (32, 20)
DIFF_THRESHOLD = 6  # mean per-channel delta below which a frame is "duplicate"


def thumb_mean_delta(a: Image.Image, b: Image.Image) -> float:
    ta, tb = a.resize(THUMB), b.resize(THUMB)
    pa, pb = list(ta.getdata()), list(tb.getdata())
    total = 0
    for ra, rb in zip(pa, pb):
        total += abs(ra[0] - rb[0]) + abs(ra[1] - rb[1]) + abs(ra[2] - rb[2])
    return total / (len(pa) * 3)


def main() -> int:
    frames_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(tempfile.gettempdir(), "demo-frames")
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join("docs", "screenshots", "04-demo.gif")

    files = sorted(glob.glob(os.path.join(frames_dir, "frame-*.png")))
    if not files:
        print(f"no frames in {frames_dir}")
        return 1

    imgs = [Image.open(f).convert("RGB") for f in files]
    imgs = [im.resize((TARGET_W, round(im.height * TARGET_W / im.width)), Image.LANCZOS) for im in imgs]

    kept: list[tuple[int, Image.Image]] = []  # (capture_ms_at_start, image)
    last_thumb = None
    for idx, im in enumerate(imgs):
        capture_ms = idx * 500  # record_demo captures every 500ms
        force = not kept or capture_ms - kept[-1][0] >= KEEP_EVERY_MS
        if not force and last_thumb is not None:
            delta = thumb_mean_delta(last_thumb, im)
            if delta < DIFF_THRESHOLD:
                continue
        kept.append((capture_ms, im))
        last_thumb = im

    quantized = [
        im.quantize(colors=COLORS, method=Image.MEDIANCUT, dither=Image.FLOYDSTEINBERG)
        for _, im in kept
    ]
    durations = []
    for i in range(len(kept)):
        start = kept[i][0]
        end = kept[i + 1][0] if i + 1 < len(kept) else kept[i][0] + FRAME_MS
        durations.append(max(FRAME_MS, min(end - start, KEEP_EVERY_MS)))

    os.makedirs(os.path.dirname(out), exist_ok=True)
    quantized[0].save(out, save_all=True, append_images=quantized[1:], duration=durations, loop=0, optimize=True)
    size_mb = os.path.getsize(out) / 1e6
    print(f"{len(quantized)} frames (of {len(imgs)}) -> {out} ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
