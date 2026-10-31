"""Ball-drop live streamer: Pymunk physics -> PIL render -> ffmpeg.

Demo version of ball_drop_stack.py for the GCP time-boxed demo:
  - renders in real time (frame pacing to FPS)
  - auto-spawns a ball every SPAWN_EVERY seconds (comment triggers come
    in the next iteration; spawn() is the single entry point for them)
  - clears all balls when the pile reaches CLEAR_AT (default 100), the
    rule we agreed a long stream needs so the floor does not fill up
  - output is an RTMP URL (YouTube Live) when RTMP_URL is set,
    otherwise a local file for performance tests

Env:
  RTMP_URL      e.g. rtmp://a.rtmp.youtube.com/live2/<stream-key>
  OUTPUT        local file when RTMP_URL is unset (default stream_test.mp4)
  DURATION_SEC  0 = run until killed (default for live); >0 stops after N sec

Run:  .venv/bin/python stream_live.py
"""
import os
import subprocess
import sys
import time

import numpy as np
import pymunk
from PIL import Image, ImageDraw

W, H, FPS = 1280, 720, 30
BALL_R = 26
PEG_R = 12
SPAWN_EVERY = 0.7
CLEAR_AT = int(os.environ.get("CLEAR_AT", "100"))
ELASTICITY = 0.45
FRICTION = 0.6
COLORS = [(231, 76, 60), (52, 152, 219), (241, 196, 15)]  # red / blue / yellow

RTMP_URL = os.environ.get("RTMP_URL", "")
OUTPUT = os.environ.get("OUTPUT", "stream_test.mp4")
DURATION_SEC = float(os.environ.get("DURATION_SEC", "0"))

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

balls = []  # (body, shape, colour)
spawned_total = 0


def spawn():
    """Single entry point for dropping a ball (auto now, chat later)."""
    global spawned_total
    i = spawned_total
    x = W / 2 + ((i * 197) % 241) - 120
    body = pymunk.Body(1.0, pymunk.moment_for_circle(1.0, 0, BALL_R))
    body.position = (x, 50)
    shape = pymunk.Circle(body, BALL_R)
    shape.elasticity = ELASTICITY
    shape.friction = FRICTION
    space.add(body, shape)
    balls.append((body, shape, COLORS[i % len(COLORS)]))
    spawned_total += 1


def clear_balls():
    for body, shape, _ in balls:
        space.remove(body, shape)
    balls.clear()
    print(f"[stream] cleared all balls at total {spawned_total}", flush=True)


def render_frame():
    img = Image.new("RGB", (W, H), (24, 28, 36))
    d = ImageDraw.Draw(img)
    d.rectangle([0, H - 10, W, H], fill=(60, 65, 80))
    for px, py in pegs:
        d.ellipse([px - PEG_R, py - PEG_R, px + PEG_R, py + PEG_R],
                  fill=(200, 205, 215))
    for body, _shape, c in balls:
        x, y = body.position
        d.ellipse([x - BALL_R, y - BALL_R, x + BALL_R, y + BALL_R], fill=c)
    return np.asarray(img)


def ffmpeg_cmd():
    base = ["ffmpeg", "-y",
            "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
            "-pix_fmt", "yuv420p", "-g", str(FPS * 2),
            "-c:a", "aac", "-b:a", "128k", "-shortest"]
    if RTMP_URL:
        return base + ["-f", "flv", RTMP_URL]
    return base + [OUTPUT]


def main():
    proc = subprocess.Popen(ffmpeg_cmd(), stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    target = "RTMP" if RTMP_URL else OUTPUT
    print(f"[stream] start -> {target} (clear at {CLEAR_AT})", flush=True)
    start = time.monotonic()
    frame = 0
    next_spawn = 0.4
    try:
        while True:
            t = frame / FPS
            if DURATION_SEC and t >= DURATION_SEC:
                break
            while t >= next_spawn:
                spawn()
                next_spawn += SPAWN_EVERY
                if len(balls) >= CLEAR_AT:
                    clear_balls()
            for _ in range(3):
                space.step((1.0 / FPS) / 3)
            proc.stdin.write(render_frame().tobytes())
            frame += 1
            # real-time pacing: sleep until this frame's deadline
            deadline = start + frame / FPS
            delay = deadline - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            elif delay < -1.0:
                # falling behind badly; re-anchor so we do not spiral
                start = time.monotonic() - frame / FPS
                print(f"[stream] warning: behind schedule at frame {frame}",
                      flush=True)
    except (BrokenPipeError, KeyboardInterrupt):
        pass
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        proc.wait()
    elapsed = time.monotonic() - start
    print(f"[stream] done: {frame} frames in {elapsed:.1f}s "
          f"({frame / max(elapsed, 0.01):.1f} fps render+encode, "
          f"{spawned_total} balls)", flush=True)


if __name__ == "__main__":
    sys.exit(main())
