"""Comment triggers for the ball-drop stream.

Turns YouTube live-chat messages into ball spawns:

- A message containing the trigger word (「ボール」 or "ball") asks for
  a ball. Colour can be chosen with 🔴/🔵/🟡 or 赤/青/黄 (red/blue/yellow
  in English works too); no colour means the stream's default cycle.
- One spawn per author per COOLDOWN_SEC, queue capped at QUEUE_MAX;
  extra requests are dropped (and logged), never queued silently.
- Two sources feed the same queue: the real YouTube poller
  (YouTube Data API v3, liveChatMessages.list polling) and a mock
  source for offline tests (CHAT_MODE=mock).

Auth (real source only): OAuth user credentials for the channel owner.
  YT_CLIENT_SECRET_FILE  OAuth client JSON (Desktop app) from GCP
  YT_TOKEN_FILE          saved user token (created on first auth,
                         default: ./yt_token.json next to this file)
Scope: https://www.googleapis.com/auth/youtube.readonly
Token and client-secret files must never be committed (see .gitignore).
"""
import os
import queue
import threading
import time

COOLDOWN_SEC = float(os.environ.get("CHAT_COOLDOWN_SEC", "30"))
QUEUE_MAX = int(os.environ.get("CHAT_QUEUE_MAX", "20"))
SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]

# colour indexes match stream_live.COLORS: 0 red / 1 blue / 2 yellow
_COLOUR_WORDS = [
    (0, ("🔴", "赤", "red")),
    (1, ("🔵", "青", "blue")),
    (2, ("🟡", "黄", "yellow")),
]
_TRIGGERS = ("ボール", "ball")


def parse_trigger(text):
    """Return a colour index, -1 for 'default cycle', or None."""
    if not text:
        return None
    low = text.lower()
    if not any(t in low for t in _TRIGGERS):
        return None
    for idx, words in _COLOUR_WORDS:
        if any(w in low for w in words):
            return idx
    return -1


class SpawnQueue:
    """Thread-safe spawn requests with per-author cooldown."""

    def __init__(self, cooldown=COOLDOWN_SEC, maxsize=QUEUE_MAX):
        self.q = queue.Queue(maxsize=maxsize)
        self.cooldown = cooldown
        self._last = {}
        self._lock = threading.Lock()
        self.stats = {"queued": 0, "cooldown": 0, "full": 0, "ignored": 0}

    def offer(self, author_id, author_name, text, now=None):
        """Offer one chat message. Returns a status string."""
        colour = parse_trigger(text)
        if colour is None:
            with self._lock:
                self.stats["ignored"] += 1
            return "ignored"
        now = time.monotonic() if now is None else now
        with self._lock:
            last = self._last.get(author_id, -1e9)
            if now - last < self.cooldown:
                self.stats["cooldown"] += 1
                return "cooldown"
            try:
                self.q.put_nowait((colour, author_name))
            except queue.Full:
                self.stats["full"] += 1
                return "full"
            self._last[author_id] = now
            self.stats["queued"] += 1
            return "queued"

    def drain(self, limit=5):
        out = []
        while len(out) < limit:
            try:
                out.append(self.q.get_nowait())
            except queue.Empty:
                break
        return out


def _load_credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    here = os.path.dirname(os.path.abspath(__file__))
    token_file = os.environ.get("YT_TOKEN_FILE",
                                os.path.join(here, "yt_token.json"))
    creds = None
    if os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds or not creds.valid:
        secret = os.environ.get("YT_CLIENT_SECRET_FILE", "")
        if not secret or not os.path.exists(secret):
            raise RuntimeError(
                "No valid YouTube token and YT_CLIENT_SECRET_FILE is not "
                "set to an existing OAuth client JSON. Run the one-time "
                "authorisation first (see RUNBOOK.md).")
        flow = InstalledAppFlow.from_client_secrets_file(secret, SCOPES)
        creds = flow.run_local_server(port=0)
        with open(token_file, "w") as fh:
            fh.write(creds.to_json())
        print(f"[chat] saved user token to {token_file}", flush=True)
    return creds


class YouTubeChatPoller(threading.Thread):
    """Polls liveChatMessages.list for the channel's active broadcast."""

    def __init__(self, spawn_queue):
        super().__init__(daemon=True, name="yt-chat")
        self.spawn_queue = spawn_queue
        self._seen = set()

    def run(self):
        from googleapiclient.discovery import build

        yt = build("youtube", "v3", credentials=_load_credentials(),
                   cache_discovery=False)
        chat_id = self._active_chat_id(yt)
        print(f"[chat] polling live chat {chat_id}", flush=True)
        page = None
        while True:
            resp = yt.liveChatMessages().list(
                liveChatId=chat_id, part="snippet,authorDetails",
                pageToken=page).execute()
            for item in resp.get("items", []):
                self._handle(item)
            page = resp.get("nextPageToken")
            wait_ms = resp.get("pollingIntervalMillis", 3000)
            time.sleep(max(wait_ms, 1000) / 1000.0)

    @staticmethod
    def _active_chat_id(yt):
        resp = yt.liveBroadcasts().list(
            part="snippet", mine=True, broadcastStatus="active").execute()
        items = resp.get("items", [])
        if not items:
            raise RuntimeError("No active broadcast found for this "
                               "channel; start the stream first.")
        return items[0]["snippet"]["liveChatId"]

    def _handle(self, item):
        mid = item.get("id")
        if mid in self._seen:
            return
        self._seen.add(mid)
        sn = item.get("snippet", {})
        if sn.get("type") != "textMessageEvent":
            return
        author = item.get("authorDetails", {})
        status = self.spawn_queue.offer(
            author.get("channelId", "?"),
            author.get("displayName", "?"),
            sn.get("displayMessage", ""))
        if status != "ignored":
            print(f"[chat] {status}: {author.get('displayName', '?')}: "
                  f"{sn.get('displayMessage', '')[:40]}", flush=True)


class MockChat(threading.Thread):
    """Offline comment source for tests (CHAT_MODE=mock)."""

    SCRIPT = [
        (1.0, "alice", "user-alice", "ボール！"),
        (2.0, "bob", "user-bob", "ボール🔵"),
        (3.0, "alice", "user-alice", "ボール"),       # cooldown -> dropped
        (5.0, "carol", "user-carol", "こんにちは"),      # no trigger -> ignored
        (6.0, "dave", "user-dave", "ボールの黄色いの！"),
        (8.0, "erin", "user-erin", "ball please, red"),
    ]

    def __init__(self, spawn_queue):
        super().__init__(daemon=True, name="mock-chat")
        self.spawn_queue = spawn_queue

    def run(self):
        start = time.monotonic()
        for at, name, uid, text in self.SCRIPT:
            delay = start + at - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            status = self.spawn_queue.offer(uid, name, text)
            print(f"[chat] mock {status}: {name}: {text}", flush=True)
