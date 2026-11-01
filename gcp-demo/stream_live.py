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
  CHAT_MODE     off (default, auto-spawn) / mock / youtube (gRPC
                streamList) / youtube-poll (legacy list polling)
                In chat modes balls come from chat triggers
                (see chat_triggers.py); a few welcome balls spawn first.
  GOAL_START    co-op goal for round 1 (default 50 chat balls)
  GOAL_STEP     goal increase per round (default 25)

Run:  .venv/bin/python stream_live.py
"""
import os
import subprocess
import sys
import time

import numpy as np
import pymunk
from PIL import Image, ImageDraw, ImageFont

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

balls = []  # (body, shape, colour, radius)
spawned_total = 0

# --- co-op goal game -------------------------------------------------
GOAL_START = int(os.environ.get("GOAL_START", "50"))
GOAL_STEP = int(os.environ.get("GOAL_STEP", "25"))
GOLD = (255, 195, 0)
game = {
    "round": 1,
    "goal": GOAL_START,
    "progress": 0,          # chat balls this round
    "last_author": "",
    "banner_author": "",
    "banner_until": 0.0,    # monotonic time; celebration banner
}


def spawn(colour_idx=-1, radius=BALL_R, colour=None, x=None):
    """Single entry point for dropping a ball.

    colour_idx 0..2 picks a colour (chat requests); -1 uses the
    default cycle (auto-spawn and colour-less chat requests).
    radius/colour/x override for special balls (gold, mini burst).
    """
    global spawned_total
    i = spawned_total
    if x is None:
        x = W / 2 + ((i * 197) % 241) - 120
    body = pymunk.Body(1.0, pymunk.moment_for_circle(1.0, 0, radius))
    body.position = (x, 50)
    shape = pymunk.Circle(body, radius)
    shape.elasticity = ELASTICITY
    shape.friction = FRICTION
    space.add(body, shape)
    if colour is None:
        colour = COLORS[colour_idx] if 0 <= colour_idx < len(COLORS) \
            else COLORS[i % len(COLORS)]
    balls.append((body, shape, colour, radius))
    spawned_total += 1


def clear_balls():
    for body, shape, _c, _r in balls:
        space.remove(body, shape)
    balls.clear()
    print(f"[stream] cleared all balls at total {spawned_total}", flush=True)


def celebrate(author):
    """Goal reached: gold ball + mini-ball burst + banner."""
    game["banner_until"] = time.monotonic() + 5.0
    game["banner_author"] = author
    spawn(colour=GOLD, radius=44)  # < peg gap (~104px) so it can fall
    for k in range(10):
        spawn(radius=12, x=W / 2 + (k - 5) * 36)
    print(f"[game] GOAL! round {game['round']} reached by {author} "
          f"(next goal {game['goal'] + GOAL_STEP})", flush=True)
    game["round"] += 1
    game["goal"] += GOAL_STEP
    game["progress"] = 0


def _load_font(size):
    for path in ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


FONT_HUD = _load_font(26)
FONT_BANNER = _load_font(44)


def render_frame():
    img = Image.new("RGB", (W, H), (24, 28, 36))
    d = ImageDraw.Draw(img)
    d.rectangle([0, H - 10, W, H], fill=(60, 65, 80))
    for px, py in pegs:
        d.ellipse([px - PEG_R, py - PEG_R, px + PEG_R, py + PEG_R],
                  fill=(200, 205, 215))
    for body, _shape, c, r in balls:
        x, y = body.position
        d.ellipse([x - r, y - r, x + r, y + r], fill=c)
    _draw_hud(d)
    return np.asarray(img)


def _draw_hud(d):
    # co-op progress: label + bar along the top
    goal = max(game["goal"], 1)
    frac = min(game["progress"] / goal, 1.0)
    d.rectangle([0, 0, W, 46], fill=(16, 19, 25))
    d.rectangle([330, 12, W - 24, 34], outline=(120, 128, 145), width=2)
    if frac > 0:
        d.rectangle([332, 14, 332 + int((W - 358) * frac), 32], fill=GOLD)
    d.text((16, 8), f"ROUND {game['round']}  みんなで {game['progress']}"
           f" / {game['goal']}", font=FONT_HUD, fill=(235, 238, 245))
    if time.monotonic() < game["banner_until"]:
        msg = f"GOAL! 最後の一押し: {game['banner_author'][:16]}"
        bbox = d.textbbox((0, 0), msg, font=FONT_BANNER)
        tw = bbox[2] - bbox[0]
        # dark stroke: the gold text must stay readable over gold balls
        d.text(((W - tw) / 2, 64), msg, font=FONT_BANNER, fill=GOLD,
               stroke_width=3, stroke_fill=(24, 28, 36))


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
    chat_mode = os.environ.get("CHAT_MODE", "off").lower()
    spawn_queue = None
    if chat_mode in ("mock", "youtube", "youtube-poll"):
        import chat_triggers
        spawn_queue = chat_triggers.SpawnQueue()
        if chat_mode == "mock":
            source = chat_triggers.MockChat(spawn_queue)
        elif chat_mode == "youtube-poll":
            source = chat_triggers.YouTubeChatPoller(spawn_queue)
        else:
            source = chat_triggers.YouTubeChatStreamer(spawn_queue)
        source.start()
        print(f"[stream] chat mode: {chat_mode}", flush=True)
        for _ in range(3):  # welcome balls so the scene is never empty
            spawn()
    start = time.monotonic()
    frame = 0
    next_spawn = 0.4
    try:
        while True:
            t = frame / FPS
            if DURATION_SEC and t >= DURATION_SEC:
                break
            if spawn_queue is not None:
                for colour_idx, author in spawn_queue.drain(limit=5):
                    spawn(colour_idx)
                    game["progress"] += 1
                    game["last_author"] = author
                    print(f"[stream] chat ball for {author} "
                          f"(total {spawned_total}, round "
                          f"{game['round']}: {game['progress']}/"
                          f"{game['goal']})", flush=True)
                    if game["progress"] >= game["goal"]:
                        celebrate(author)
                    if len(balls) >= CLEAR_AT:
                        clear_balls()
            else:
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
    if spawn_queue is not None:
        print(f"[chat] stats: {spawn_queue.stats}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
