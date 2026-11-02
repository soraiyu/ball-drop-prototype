"""Comment triggers for the ball-drop stream.

Turns YouTube live-chat messages into ball spawns:

- A message containing the trigger word (「ボール」 or "ball") asks for
  a ball. Colour can be chosen with 🔴/🔵/🟡 or 赤/青/黄 (red/blue/yellow
  in English works too); no colour means the stream's default cycle.
- One spawn per author per COOLDOWN_SEC, queue capped at QUEUE_MAX;
  extra requests are dropped (and logged), never queued silently.
- Sources feeding the same queue: the gRPC streamer
  (liveChatMessages.streamList, low latency; CHAT_MODE=youtube), the
  legacy REST poller (liveChatMessages.list; CHAT_MODE=youtube-poll),
  and a mock source for offline tests (CHAT_MODE=mock).

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
_TRIGGERS = ("ボール", "ball", "bola", "球", "공")


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


def find_live_chat_id(yt, wait_sec=600):
    """Return the liveChatId of the channel's currently-live broadcast.

    The broadcast usually goes active a little after the stream process
    starts, so poll for it instead of failing fast. NOTE: mine=true
    cannot be combined with broadcastStatus (the API returns 400
    incompatibleParameters), so fetch the channel's broadcasts and
    filter by lifecycle status here.
    """
    deadline = time.monotonic() + wait_sec
    while True:
        resp = yt.liveBroadcasts().list(
            part="snippet,status", mine=True, maxResults=10).execute()
        for item in resp.get("items", []):
            if item.get("status", {}).get("lifeCycleStatus") == "live":
                return item["snippet"]["liveChatId"]
        if time.monotonic() > deadline:
            raise RuntimeError("No active broadcast appeared within "
                               f"{wait_sec}s.")
        print("[chat] waiting for the broadcast to go active...",
              flush=True)
        time.sleep(10)


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
        chat_id = find_live_chat_id(yt)
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


class YouTubeChatStreamer(threading.Thread):
    """Receives live chat via liveChatMessages.streamList (gRPC).

    Server-streaming: messages are pushed as they arrive, so trigger
    latency is ~1s instead of the polling interval. Reconnects with
    the last next_page_token when the server closes the stream;
    stops when the response reports offline_at (chat ended).
    Auth follows Google's demo: the OAuth access token is passed as
    call metadata and refreshed before each (re)connect.
    """

    def __init__(self, spawn_queue):
        super().__init__(daemon=True, name="yt-chat-stream")
        self.spawn_queue = spawn_queue
        self._seen = set()
        self._creds = None

    def _access_token(self):
        from google.auth.transport.requests import Request

        if self._creds is None:
            self._creds = _load_credentials()
        if not self._creds.valid:
            self._creds.refresh(Request())
        return self._creds.token

    def run(self):
        import grpc
        from googleapiclient.discovery import build

        import stream_list_pb2
        import stream_list_pb2_grpc

        self._creds = _load_credentials()
        yt = build("youtube", "v3", credentials=self._creds,
                   cache_discovery=False)
        chat_id = find_live_chat_id(yt)
        print(f"[chat] streaming live chat {chat_id}", flush=True)
        page_token = ""
        backoff = 1.0
        while True:
            try:
                metadata = (("authorization",
                             "Bearer " + self._access_token()),)
                channel = grpc.secure_channel(
                    "dns:///youtube.googleapis.com:443",
                    grpc.ssl_channel_credentials())
                stub = stream_list_pb2_grpc \
                    .V3DataLiveChatMessageServiceStub(channel)
                request = stream_list_pb2.LiveChatMessageListRequest(
                    part=["snippet", "authorDetails"],
                    live_chat_id=chat_id,
                    page_token=page_token)
                for response in stub.StreamList(request,
                                                metadata=metadata):
                    backoff = 1.0
                    if response.next_page_token:
                        page_token = response.next_page_token
                    for item in response.items:
                        self._handle(item)
                    if response.offline_at:
                        print("[chat] chat ended (offline_at set)",
                              flush=True)
                        return
                print("[chat] stream closed by server; reconnecting",
                      flush=True)
            except grpc.RpcError as e:
                print(f"[chat] stream error {e.code()}: {e.details()}",
                      flush=True)
                if e.code() == grpc.StatusCode.UNAUTHENTICATED:
                    self._creds = None  # force reload+refresh next loop
            time.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    def _handle(self, item):
        import stream_list_pb2

        if item.id in self._seen:
            return
        self._seen.add(item.id)
        sn = item.snippet
        text_type = stream_list_pb2.LiveChatMessageSnippet.TypeWrapper \
            .Type.TEXT_MESSAGE_EVENT
        if sn.type != text_type:
            return
        text = sn.display_message
        if not text and sn.HasField("text_message_details"):
            text = sn.text_message_details.message_text
        author = item.author_details
        status = self.spawn_queue.offer(
            author.channel_id or sn.author_channel_id or "?",
            author.display_name or "?",
            text)
        if status != "ignored":
            print(f"[chat] {status}: {author.display_name or '?'}: "
                  f"{text[:40]}", flush=True)


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
