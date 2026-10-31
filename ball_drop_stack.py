"""Ball-drop prototype v3: Pymunk physics — balls collide and pile up.

Balls bounce off pegs AND each other, then stack on the floor.
Hand-tinker points:
  - ELASTICITY (bounciness), FRICTION (how well piles hold)
  - BALL_R, SPAWN_EVERY, DURATION_SEC
  - peg layout below

Run:  .venv/bin/python ball_drop_stack.py  -> ball_drop_stack.mp4
"""
import subprocess

import numpy as np
import pymunk
from PIL import Image, ImageDraw

W, H, FPS = 1280, 720, 30
DURATION_SEC = 16
BALL_R = 26
PEG_R = 12
SPAWN_EVERY = 0.7
ELASTICITY = 0.45   # balls are a bit dead so piles settle
FRICTION = 0.6
COLORS = [(231, 76, 60), (52, 152, 219), (241, 196, 15)]  # 🔴🔵🟡

space = pymunk.Space()
space.gravity = (0, 1800)
space.iterations = 20

static = space.static_body


def add_segment(a, b, radius=5):
    s = pymunk.Segment(static, a, b, radius)
    s.elasticity = 0.4
    s.friction = FRICTION
    space.add(s)
    return s


# floor and walls (pymunk y grows downward here because we feed it
# screen coordinates with gravity +y; rendering uses the same coords)
add_segment((0, H - 10), (W, H - 10), 5)
add_segment((10, 0), (10, H), 5)
add_segment((W - 10, 0), (W - 10, H), 5)

pegs = []
for row, y in enumerate(range(170, 560, 78)):
    offset = 64 if row % 2 else 0
    for x in range(96 + offset, W - 60, 128):
        c = pymunk.Circle(static, PEG_R, offset=(x, y))
        c.elasticity = 0.6
        c.friction = 0.3
        space.add(c)
        pegs.append((x, y))

balls = []  # (body, colour)


def spawn(i):
    x = W / 2 + ((i * 197) % 241) - 120
    body = pymunk.Body(1.0, pymunk.moment_for_circle(1.0, 0, BALL_R))
    body.position = (x, 50)
    shape = pymunk.Circle(body, BALL_R)
    shape.elasticity = ELASTICITY
    shape.friction = FRICTION
    space.add(body, shape)
    balls.append((body, COLORS[i % len(COLORS)]))


def render_frame():
    img = Image.new("RGB", (W, H), (24, 28, 36))
    d = ImageDraw.Draw(img)
    d.rectangle([0, H - 10, W, H], fill=(60, 65, 80))
    for px, py in pegs:
        d.ellipse([px - PEG_R, py - PEG_R, px + PEG_R, py + PEG_R],
                  fill=(200, 205, 215))
    for body, c in balls:
        x, y = body.position
        d.ellipse([x - BALL_R, y - BALL_R, x + BALL_R, y + BALL_R], fill=c)
    return np.asarray(img)


def main():
    cmd = ["ffmpeg", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
           "-pix_fmt", "yuv420p", "ball_drop_stack.mp4"]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    n = int(DURATION_SEC * FPS)
    spawned = 0
    for f in range(n):
        t = f / FPS
        want = int((t - 0.4) / SPAWN_EVERY) + 1 if t >= 0.4 else 0
        while spawned < want:
            spawn(spawned)
            spawned += 1
        for _ in range(3):  # substeps for stable stacking
            space.step((1.0 / FPS) / 3)
        proc.stdin.write(render_frame().tobytes())
    proc.stdin.close()
    proc.wait()
    print(f"wrote ball_drop_stack.mp4: {n} frames, {len(balls)} balls, {len(pegs)} pegs")


if __name__ == "__main__":
    main()
