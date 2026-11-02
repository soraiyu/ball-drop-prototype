"""New-subscriber watcher for the ball-drop stream.

Polls the channel's subscriber count and the public subscriber list
(YouTube Data API v3, youtube.readonly — the same token as chat).
When a new subscriber appears, an event is queued for stream_live.py,
which celebrates on screen (gold ball + banner + duck bubble).

Honesty rules (decided with the user, 2026-11-02):
- Numbers are approximate. The API cannot say "this person is the
  Nth subscriber"; we display a ROUNDED label based on the current
  total ("およそ100番前後") and never pretend to be exact.
- Names are only visible for subscribers whose subscription is
  public. A count increase with no new public name is celebrated as
  an anonymous new member.
- Each channel id is celebrated at most once, ever (seen-ids persist
  in a JSON file), so unsubscribe/resubscribe cycles — and trolls
  farming the celebration — stay silent. Departures are not shown.

Events are dicts: {"name": str | None, "approx": int, "count": int}.
The shared state dict gets {"count": int} updates for the HUD.
"""

import json
import os
import queue
import threading
import time

POLL_SEC = int(os.environ.get("SUBS_POLL_SEC", "120"))
SEEN_FILE = os.environ.get(
    "SUBS_SEEN_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "seen_subs.json"))


def approx_label(n):
    """Round a subscriber ordinal for display ('およそN番前後')."""
    if n < 20:
        step = 1
    elif n < 100:
        step = 5
    elif n < 500:
        step = 10
    elif n < 2000:
        step = 50
    else:
        step = 100
    return max(step, int(round(n / step)) * step)


class SubscriberWatcher(threading.Thread):
    """Real watcher (SUBS_MODE=on)."""

    def __init__(self, events, state):
        super().__init__(daemon=True, name="subs-watch")
        self.events = events
        self.state = state
        self._seen = self._load_seen()
        self._count = None

    @staticmethod
    def _load_seen():
        try:
            with open(SEEN_FILE) as fh:
                return set(json.load(fh))
        except Exception:
            return set()

    def _save_seen(self):
        try:
            with open(SEEN_FILE, "w") as fh:
                json.dump(sorted(self._seen), fh)
        except Exception as e:
            print(f"[subs] could not persist seen ids: {e}", flush=True)

    def run(self):
        from googleapiclient.discovery import build

        from chat_triggers import _load_credentials
        yt = build("youtube", "v3", credentials=_load_credentials(),
                   cache_discovery=False)
        while True:
            try:
                self._poll(yt)
            except Exception as e:
                print(f"[subs] poll failed: {e}", flush=True)
            time.sleep(POLL_SEC)

    def _poll(self, yt):
        stats = yt.channels().list(part="statistics",
                                   mine=True).execute()
        items = stats.get("items", [])
        if not items:
            return
        count = int(items[0]["statistics"].get("subscriberCount", 0))
        self.state["count"] = count
        resp = yt.subscriptions().list(
            part="subscriberSnippet", mySubscribers=True,
            maxResults=50).execute()
        public = []  # (channelId, title), newest first
        for it in resp.get("items", []):
            sn = it.get("subscriberSnippet", {})
            if sn.get("channelId"):
                public.append((sn["channelId"], sn.get("title", "?")))

        if self._count is None:
            # First poll: adopt the current world silently.
            self._count = count
            self._seen.update(cid for cid, _ in public)
            self._save_seen()
            print(f"[subs] watching: {count} subscribers, "
                  f"{len(public)} public", flush=True)
            return

        new_public = [(cid, name) for cid, name in public
                      if cid not in self._seen]
        delta = count - self._count
        for cid, name in new_public[:3]:
            self._seen.add(cid)
            self.events.put({"name": name,
                             "approx": approx_label(count),
                             "count": count})
            print(f"[subs] new public subscriber: {name} "
                  f"(~{approx_label(count)})", flush=True)
        anonymous = delta - len(new_public)
        for _ in range(min(max(anonymous, 0), 3)):
            self.events.put({"name": None,
                             "approx": approx_label(count),
                             "count": count})
            print(f"[subs] new private subscriber "
                  f"(~{approx_label(count)})", flush=True)
        if new_public or anonymous > 0:
            self._save_seen()
        self._count = count


class MockSubscriberWatcher(threading.Thread):
    """Offline source for tests (SUBS_MODE=mock)."""

    def __init__(self, events, state):
        super().__init__(daemon=True, name="mock-subs")
        self.events = events
        self.state = state

    def run(self):
        self.state["count"] = 97
        time.sleep(6.0)
        self.state["count"] = 98
        self.events.put({"name": "モックさん",
                         "approx": approx_label(98), "count": 98})
        print("[subs] mock new subscriber: モックさん (~100)",
              flush=True)


def make_watcher(mode, state):
    """Return (thread, events queue) or (None, None) when off."""
    if mode == "off":
        return None, None
    events = queue.Queue()
    watcher = (MockSubscriberWatcher(events, state) if mode == "mock"
               else SubscriberWatcher(events, state))
    return watcher, events


if __name__ == "__main__":
    # quick self-test of the rounding rules
    cases = {3: 3, 19: 19, 42: 40, 98: 100, 137: 140, 480: 480,
             1234: 1250, 9876: 9900}
    for n, want in cases.items():
        got = approx_label(n)
        assert got == want, (n, got, want)
    print("approx_label OK")
