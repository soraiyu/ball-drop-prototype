"""Ball-drop prototype v2: peg obstacles (Galton-board style).

Balls drop from the top, collide with static round pegs, bounce off
each other is NOT handled (they pass through each other) — pegs and
floor/walls only. Hand-tinker points:
  - GRAVITY, BOUNCE: fall/bounce feel
  - PEG_ROWS, PEG_GAP: obstacle layout (more rows = more chaos)
  - SPAWN_EVERY, DURATION_SEC

Run:  python3 ball_drop_obstacles.py  -> ball_drop_obstacles.mp4
"""
import subprocess

import numpy as np
from PIL import Image, ImageDraw

W, H, FPS = 1280, 720, 30
DURATION_SEC = 10
GRAVITY = 1800.0
BOUNCE = 0.72
BALL_R = 26
PEG_R = 12
SPAWN_EVERY = 0.8
COLORS = [(231, 76, 60), (52, 152, 219), (241, 196, 15)]  # 🔴🔵🟡

# staggered peg field (Galton board)
pegs = []
PEG_GAP = 128
for row, y in enumerate(range(190, 620, 86)):
    offset = PEG_GAP // 2 if row % 2 else 0
    for x in range(96 + offset, W - 60, PEG_GAP):
        pegs.append((float(x), float(y)))

balls = []  # [x, y, vx, vy, colour]
_spawned = [0]


def step(dt, t):
    want = int((t - 0.4) / SPAWN_EVERY) + 1 if t >= 0.4 else 0
    while _spawned[0] < want:
        i = _spawned[0]
        _spawned[0] += 1
        x = W / 2 + ((i * 137) % 161) - 80  # deterministic jitter
        balls.append([x, 50.0, 0.0, 0.0, COLORS[i % len(COLORS)]])
    for b in balls:
        b[3] += GRAVITY * dt
        b[0] += b[2] * dt
        b[1] += b[3] * dt
        # peg collisions: push out along normal, reflect velocity
        for px, py in pegs:
            dx, dy = b[0] - px, b[1] - py
            dist = (dx * dx + dy * dy) ** 0.5
            min_d = BALL_R + PEG_R
            if 0 < dist < min_d:
                nx, ny = dx / dist, dy / dist
                b[0], b[1] = px + nx * min_d, py + ny * min_d
                vn = b[2] * nx + b[3] * ny
                if vn < 0:
                    b[2] -= (1 + BOUNCE) * vn * nx
                    b[3] -= (1 + BOUNCE) * vn * ny
        if b[1] + BALL_R > H - 10:  # floor
            b[1] = H - 10 - BALL_R
            b[3] = -b[3] * BOUNCE
            b[2] *= 0.98
            if abs(b[3]) < 40:
                b[3] = 0.0
        if b[0] - BALL_R < 10:
            b[0] = 10 + BALL_R
            b[2] = -b[2] * 0.8
        if b[0] + BALL_R > W - 10:
            b[0] = W - 10 - BALL_R
            b[2] = -b[2] * 0.8


def render_frame():
    img = Image.new("RGB", (W, H), (24, 28, 36))
    d = ImageDraw.Draw(img)
    d.rectangle([0, H - 10, W, H], fill=(60, 65, 80))
    for px, py in pegs:
        d.ellipse([px - PEG_R, py - PEG_R, px + PEG_R, py + PEG_R],
                  fill=(200, 205, 215))
    for x, y, vx, vy, c in balls:
        d.ellipse([x - BALL_R, y - BALL_R, x + BALL_R, y + BALL_R], fill=c)
    return np.asarray(img)


def main():
    cmd = ["ffmpeg", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
           "-pix_fmt", "yuv420p", "ball_drop_obstacles.mp4"]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    n = int(DURATION_SEC * FPS)
    for f in range(n):
        step(1.0 / FPS, f / FPS)
        proc.stdin.write(render_frame().tobytes())
    proc.stdin.close()
    proc.wait()
    print(f"wrote ball_drop_obstacles.mp4: {n} frames, {len(balls)} balls, {len(pegs)} pegs")


if __name__ == "__main__":
    main()
