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
  SUBS_MODE     off (default) / on / mock — new-subscriber watcher
                (see subscriber_watch.py); on = YouTube API polling
  THEME         dark (default) / light — colour palette

Run:  .venv/bin/python stream_live.py
"""
import math
import os
import random
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

THEMES = {
    "dark": {
        "bg": (24, 28, 36), "floor": (60, 65, 80),
        "peg": (200, 205, 215), "hud_bg": (16, 19, 25),
        "hud_text": (235, 238, 245), "hud_bar_bg": None,
        "ball_outline": None, "duck_outline": (120, 120, 115),
        "sub_text": (200, 205, 215),
    },
    "light": {  # pale sky + pond floor; balls get a dark outline
        "bg": (236, 243, 248), "floor": (168, 208, 220),
        "peg": (110, 124, 140), "hud_bg": (252, 250, 243),
        "hud_text": (40, 45, 55), "hud_bar_bg": (255, 255, 255),
        "ball_outline": (38, 42, 52), "duck_outline": (105, 100, 90),
        "sub_text": (70, 80, 95),
    },
}
THEME = THEMES[os.environ.get("THEME", "dark").lower()] \
    if os.environ.get("THEME", "dark").lower() in THEMES \
    else THEMES["dark"]

balls = []  # (body, shape, colour, radius)
spawned_total = 0
_born = {}  # id(body) -> spawn monotonic time (for the unstick rule)
_stuck_dir = {}  # id(body) -> escape direction while unsticking

# --- co-op goal game -------------------------------------------------
GOAL_START = int(os.environ.get("GOAL_START", "50"))
GOAL_STEP = int(os.environ.get("GOAL_STEP", "25"))
GOLD = (255, 195, 0)
game = {
    "round": 1,
    "goal": GOAL_START,
    "progress": 0,          # chat balls this round
    "last_author": "",
}

# generic centre banner queue (goal / new subscriber / ...):
# banners display one at a time, in order, so simultaneous events
# do not erase each other.
banner = {"text": "", "until": 0.0, "queue": []}
subs_state = {"count": None}


def set_banner(text, seconds=5.0):
    now = time.monotonic()
    if now >= banner["until"] and not banner["queue"]:
        banner["text"] = text
        banner["until"] = now + seconds
    else:
        banner["queue"].append((text, seconds))


def _advance_banner():
    now = time.monotonic()
    if now >= banner["until"] and banner["queue"]:
        text, seconds = banner["queue"].pop(0)
        banner["text"] = text
        banner["until"] = now + seconds


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
    _born[id(body)] = time.monotonic()
    spawned_total += 1


def clear_balls():
    for body, shape, _c, _r in balls:
        space.remove(body, shape)
    balls.clear()
    _born.clear()
    _stuck_dir.clear()
    print(f"[stream] cleared all balls at total {spawned_total}", flush=True)


def celebrate(author):
    """Goal reached: gold ball + mini-ball burst + banner."""
    set_banner(f"GOAL! 最後の一押し: {author[:16]}")
    # radius 34: diameter 68 stays clear of the peg-row wedge
    # (row pitch 90 - pegs 24 = 66..72 is the danger zone; the
    # earlier radius 44 wedged between rows and never fell)
    spawn(colour=GOLD, radius=34)
    for k in range(10):
        spawn(radius=12, x=W / 2 + (k - 5) * 36)
    duck.celebrate()
    print(f"[game] GOAL! round {game['round']} reached by {author} "
          f"(next goal {game['goal'] + GOAL_STEP})", flush=True)
    game["round"] += 1
    game["goal"] += GOAL_STEP
    game["progress"] = 0


def celebrate_subscriber(ev):
    """New subscriber: gold ball + welcome banner + duck bubble."""
    name = ev.get("name") or "非公開の新しい仲間"
    set_banner(f"ようこそ！ {name[:16]}"
               f"（およそ{ev['approx']}番前後の仲間）", seconds=6.0)
    spawn(colour=GOLD, radius=34)
    duck.say("ようこそ！")
    print(f"[subs] celebrating {name} (~{ev['approx']})", flush=True)


def _load_font(size):
    for path in ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


FONT_HUD = _load_font(26)
FONT_SMALL = _load_font(20)
FONT_BANNER = _load_font(44)
FONT_BANNER_S = _load_font(30)


# --- the resident duck ----------------------------------------------
class Duck:
    """A white duck living on the floor of the board.

    Visual only (no physics body): wanders left/right, hops when a
    chat ball drops, celebrates goals, and speaks via speech bubbles
    ("ようこそ！" etc.). Drawn with PIL shapes so it needs no assets;
    swapping in real artwork later only changes draw().
    """

    def __init__(self):
        self.x = W * 0.3
        self.dir = 1
        self.speed = 26.0        # px/s
        self.hop_phase = None    # 0..1 while hopping
        self.hop_height = 16
        self.bubble = ""
        self.bubble_until = 0.0
        self.last_active = time.monotonic()
        self.napping = False
        self.flee_until = 0.0
        self.flee_dir = 1
        self._gaa_until = 0.0    # ガー！ cooldown

    def _wake(self):
        self.last_active = time.monotonic()
        self.napping = False

    def hop(self, height=16):
        self._wake()
        self.hop_height = height
        self.hop_phase = 0.0

    def celebrate(self):
        self.hop(30)
        self.say("やった！")

    def say(self, text, seconds=2.5):
        self._wake()
        self.bubble = text
        self.bubble_until = time.monotonic() + seconds

    def update(self, dt, balls=()):
        now = time.monotonic()
        # --- reactions to nearby balls ------------------------------
        head_x = self.x + 24 * self.dir
        head_y = H - 62 + self._y_offset()
        for body, _s, _c, _r in balls:
            bx, by = body.position
            vx, vy = body.velocity
            # ball about to land where the duck stands -> dodge
            if (vy > 200 and 260 < by < H - 130
                    and abs(bx - self.x) < 70
                    and now >= self.flee_until):
                self.flee_dir = 1 if self.x >= bx else -1
                self.flee_until = now + 0.55
                self._wake()
                print("[duck] dodged!", flush=True)
            # ball whizzing past the head -> ガー！
            if (vx * vx + vy * vy > 150 * 150
                    and abs(bx - head_x) < 58 and abs(by - head_y) < 58
                    and now >= self._gaa_until):
                self._gaa_until = now + 3.0
                self.say("ガー！", seconds=1.2)
                self.hop_phase = 0.0
                self.hop_height = 10
                print("[duck] ガー！", flush=True)
        # --- locomotion ----------------------------------------------
        if self.hop_phase is not None:
            self.hop_phase += dt / 0.5
            if self.hop_phase >= 1.0:
                self.hop_phase = None
        if now < self.flee_until:
            self.dir = self.flee_dir
            self.x += self.dir * 250.0 * dt
        elif self.napping:
            pass  # standing still, asleep
        else:
            if now - self.last_active > 45:
                self.napping = True
            self.x += self.dir * self.speed * dt
            if random.random() < 0.002:
                self.dir *= -1
        if self.x < 90:
            self.x, self.dir = 90, 1
        elif self.x > W - 90:
            self.x, self.dir = W - 90, -1

    def _y_offset(self):
        if self.hop_phase is None:
            return 0
        return -int(abs(math.sin(math.pi * self.hop_phase))
                    * self.hop_height)

    def draw(self, d):
        y_base = H - 10 + self._y_offset()
        x = self.x
        f = self.dir  # facing
        white = (245, 245, 240)
        outline = THEME["duck_outline"]
        # tail
        d.polygon([(x - 34 * f, y_base - 34), (x - 48 * f, y_base - 48),
                   (x - 26 * f, y_base - 46)], fill=white, outline=outline)
        # body
        d.ellipse([x - 36, y_base - 40, x + 36, y_base - 6],
                  fill=white, outline=outline)
        # head
        hx, hy = x + 24 * f, y_base - 52
        d.ellipse([hx - 15, hy - 15, hx + 15, hy + 15],
                  fill=white, outline=outline)
        # beak
        bx = hx + 14 * f
        d.ellipse([bx - 8, hy - 4, bx + 8, hy + 5], fill=(245, 166, 35))
        # eye
        d.ellipse([hx + 5 * f - 2, hy - 6, hx + 5 * f + 2, hy - 2],
                  fill=(30, 30, 30))
        # feet
        d.line([x - 10, y_base - 4, x - 10, y_base], fill=(245, 166, 35),
               width=3)
        d.line([x + 10, y_base - 4, x + 10, y_base], fill=(245, 166, 35),
               width=3)
        # nap indicator: floating Z's
        if self.napping:
            phase = int(time.monotonic() * 2) % 2
            d.text((x + 26, y_base - 108), "Z", font=FONT_SMALL,
                   fill=THEME["sub_text"])
            if phase:
                d.text((x + 40, y_base - 122), "Z", font=FONT_HUD,
                       fill=THEME["sub_text"])
        # speech bubble
        if self.bubble and time.monotonic() < self.bubble_until:
            text = self.bubble
            bbox = d.textbbox((0, 0), text, font=FONT_SMALL)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            bx, by = x - tw / 2 - 10, y_base - 118
            d.rounded_rectangle([bx, by, bx + tw + 20, by + th + 14],
                                radius=10, fill=(255, 255, 255))
            d.polygon([(x - 5, by + th + 14), (x + 5, by + th + 14),
                       (x, by + th + 24)], fill=(255, 255, 255))
            d.text((bx + 10, by + 5), text, font=FONT_SMALL,
                   fill=(30, 30, 30))


duck = Duck()


def render_frame():
    img = Image.new("RGB", (W, H), THEME["bg"])
    d = ImageDraw.Draw(img)
    d.rectangle([0, H - 10, W, H], fill=THEME["floor"])
    for px, py in pegs:
        d.ellipse([px - PEG_R, py - PEG_R, px + PEG_R, py + PEG_R],
                  fill=THEME["peg"])
    for body, _shape, c, r in balls:
        x, y = body.position
        d.ellipse([x - r, y - r, x + r, y + r], fill=c,
                  outline=THEME["ball_outline"],
                  width=2 if THEME["ball_outline"] else 1)
    duck.draw(d)
    _draw_hud(d)
    return np.asarray(img)


def _draw_hud(d):
    # co-op progress: label + bar along the top
    goal = max(game["goal"], 1)
    frac = min(game["progress"] / goal, 1.0)
    d.rectangle([0, 0, W, 46], fill=THEME["hud_bg"])
    if THEME["hud_bar_bg"]:
        d.rectangle([330, 12, W - 24, 34], fill=THEME["hud_bar_bg"])
    d.rectangle([330, 12, W - 24, 34], outline=(120, 128, 145), width=2)
    if frac > 0:
        d.rectangle([332, 14, 332 + int((W - 358) * frac), 32], fill=GOLD)
    d.text((16, 8), f"ROUND {game['round']}  みんなで {game['progress']}"
           f" / {game['goal']}", font=FONT_HUD, fill=THEME["hud_text"])
    if subs_state.get("count") is not None:
        label = f"登録者 {subs_state['count']}人"
        bbox = d.textbbox((0, 0), label, font=FONT_SMALL)
        d.text((W - (bbox[2] - bbox[0]) - 14, H - 34), label,
               font=FONT_SMALL, fill=THEME["sub_text"])
    _advance_banner()
    if banner["text"] and time.monotonic() < banner["until"]:
        msg = banner["text"]
        font = FONT_BANNER if len(msg) <= 22 else FONT_BANNER_S
        bbox = d.textbbox((0, 0), msg, font=font)
        tw = bbox[2] - bbox[0]
        # dark stroke: the gold text must stay readable over gold balls
        d.text(((W - tw) / 2, 64), msg, font=font, fill=GOLD,
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

    subs_mode = os.environ.get("SUBS_MODE", "off").lower()
    subs_events = None
    if subs_mode in ("on", "mock"):
        import subscriber_watch
        watcher, subs_events = subscriber_watch.make_watcher(
            "mock" if subs_mode == "mock" else "on", subs_state)
        watcher.start()
        print(f"[stream] subscriber watch: {subs_mode}", flush=True)
    start = time.monotonic()
    frame = 0
    next_spawn = 0.4
    try:
        while True:
            t = frame / FPS
            if DURATION_SEC and t >= DURATION_SEC:
                break
            if subs_events is not None:
                while True:
                    try:
                        ev = subs_events.get_nowait()
                    except Exception:
                        break
                    celebrate_subscriber(ev)
            duck.update(1.0 / FPS, balls)
            if spawn_queue is not None:
                for colour_idx, author in spawn_queue.drain(limit=5):
                    spawn(colour_idx)
                    duck.hop()
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
            # Unstick special (oversize) balls wedged on peg tops:
            # two gold balls can stabilise each other in a pocket.
            # A big ball still away from the floor and barely
            # falling 5s after spawn gets a SUSTAINED sideways push
            # (one set velocity is not enough to climb out of the
            # pocket) until it is falling again.
            now_m = time.monotonic()
            for body, _s, _c, _r in balls:
                key = id(body)
                if _r <= BALL_R or body.position.y >= H - 120:
                    _stuck_dir.pop(key, None)
                    continue
                if abs(body.velocity.y) >= 60:
                    if key in _stuck_dir:
                        _stuck_dir.pop(key, None)
                        _born[key] = now_m  # re-arm
                    continue
                if now_m - _born.get(key, now_m) > 5:
                    d = _stuck_dir.setdefault(
                        key, random.choice([-1, 1]))
                    body.velocity = (d * 190, body.velocity.y)
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
