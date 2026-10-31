"""Ball-drop prototype (offline demo, no streaming, no cloud).

Simulates the idea from the YouTube side chat: a viewer comment
("ボール") spawns one ball a few seconds later, and it falls into
the video. Here the comment queue is simulated (one spawn per second)
so the physics + rendering loop can be tried by hand, without any
YouTube key, GCP VM, or RTMP endpoint.

Hand-tinker points (this is the fun part to edit yourself):
  - GRAVITY, BOUNCE: how the balls fall and bounce
  - COLORS: the 🔴🔵🟡 cycle, i.e. "comment emoji -> ball colour"
  - SPAWN_EVERY: how often a new ball drops
  - DURATION_SEC: how long the demo runs

Next steps, when you want them (not wired in yet):
  1. Replace simulated_spawns() with YouTube liveChatMessages.streamList
  2. Swap this simple bounce physics for Pymunk if you want balls to
     pile up and stack realistically instead of passing through each other
  3. Point the ffmpeg pipe at an RTMP URL instead of an .mp4 file
     (same encoder flags: ultrafast + zerolatency, keyframe every 1-2s)

Run:  python3 ball_drop.py   -> writes ball_drop_demo.mp4 (720p/30fps)
Requires: python3 + numpy + Pillow + ffmpeg (all already in this VM).
"""
import math
import subprocess

import numpy as np
from PIL import Image, ImageDraw

W, H, FPS = 1280, 720, 30
DURATION_SEC = 6
GRAVITY = 1800.0          # px/s^2 — tweak me
BOUNCE = 0.72             # floor bounce energy kept (0..1) — tweak me
BALL_R = 34
SPAWN_EVERY = 1.0         # seconds between simulated "ボール" comments
COLORS = [(231, 76, 60), (52, 152, 219), (241, 196, 15)]  # 🔴🔵🟡

balls = []  # each: [x, y, vx, vy, colour]

_spawn_count = [0]

def simulated_spawns(t):
    """True once per SPAWN_EVERY seconds, from t = 0.5s (stand-in for chat)."""
    want = int((t - 0.5) / SPAWN_EVERY) + 1 if t >= 0.5 else 0
    if want > _spawn_count[0]:
        _spawn_count[0] = want
        return True
    return False

def step(dt, t):
    if simulated_spawns(t):
        i = len(balls) % len(COLORS)
        balls.append([W * (0.25 + 0.5 * ((len(balls) % 3) / 2.0)), 60.0, 0.0, 0.0, COLORS[i]])
    for b in balls:
        b[3] += GRAVITY * dt
        b[0] += b[1] * dt
        b[1] += b[3] * dt
        if b[1] + BALL_R > H - 10:  # floor
            b[1] = H - 10 - BALL_R
            b[3] = -b[3] * BOUNCE
        if b[0] - BALL_R < 10 or b[0] + BALL_R > W - 10:  # walls
            b[1] = -b[1] * 0.8
            b[0] = max(BALL_R + 10, min(W - 10 - BALL_R, b[0]))

def render_frame():
    img = Image.new("RGB", (W, H), (24, 28, 36))
    d = ImageDraw.Draw(img)
    d.rectangle([0, H - 10, W, H], fill=(60, 65, 80))  # floor
    for x, y, vx, vy, c in balls:
        d.ellipse([x - BALL_R, y - BALL_R, x + BALL_R, y + BALL_R], fill=c)
    return np.asarray(img)

def main():
    cmd = ["ffmpeg", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
           "-pix_fmt", "yuv420p", "ball_drop_demo.mp4"]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    n = int(DURATION_SEC * FPS)
    for f in range(n):
        step(1.0 / FPS, f / FPS)
        proc.stdin.write(render_frame().tobytes())
    proc.stdin.close()
    proc.wait()
    print(f"wrote ball_drop_demo.mp4: {n} frames, {len(balls)} balls dropped")

if __name__ == "__main__":
    main()
