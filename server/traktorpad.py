#!/usr/bin/env python3
"""TraktorPad server: the hub between Traktor's QML bridge and the iPad.

    Traktor (QML bridge) --HTTP, loopback--> this server <--HTTP, LAN--> iPad

Standard library only, so it runs on any Mac with python3.

    python3 server/traktorpad.py              # normal use
    python3 server/traktorpad.py --simulate   # fake Traktor, to try the UI
"""

import argparse
import hashlib
import json
import mimetypes
import os
import queue
import secrets
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
DEFAULT_PORT = 8765

# A command the bridge has not picked up within this window is dropped. Traktor
# reconnecting must never replay an old "play" into a running set.
COMMAND_TTL = 1.5
POLL_HOLD = 15.0
BRIDGE_GRACE = 3.0
# A bridge instance that is not the owner of its channel is told so after this
# long, then asks again - that is how a standby takes over from a dead owner.
STANDBY_HOLD = 4.0
MAX_BODY = 256 * 1024
MAX_WAVEFORM = 8 * 1024 * 1024
MAX_AUDIO = 600 * 1024 * 1024
CHANNELS = ("main", "browser")
CACHE_DIR = Path.home() / ".traktorpad" / "waveforms"

LOOPBACK = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}


def _is_bool(v):
    return isinstance(v, bool)


def _is_index(lo, hi):
    return lambda v: isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


def _is_unit(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1


_is_deck = _is_index(1, 2)
_is_row = _is_index(0, 10_000_000)
_none = lambda v: v is None  # noqa: E731

# action -> (bridge channel, fields it needs: name -> validator).
# Anything else is rejected, so the iPad can only ever ask Traktor for what the
# bridge knows how to do.
ACTIONS = {
    "seek":           ("main",    {"deck": _is_deck, "value": _is_unit}),
    "load_preview":   ("main",    {"deck": _is_deck}),
    "window":         ("browser", {"value": _is_row}),
    "select":         ("browser", {"index": _is_row}),
    "open":           ("browser", {"index": _is_row}),
    "load":           ("browser", {"index": _is_row, "deck": _is_deck}),
    "back":           ("browser", {}),
    "sort":           ("browser", {"value": _is_index(0, 64)}),
    "preview":        ("browser", {"index": _is_row}),
    "prep":           ("browser", {"index": _is_row}),
    "preview_toggle": ("browser", {}),
    "preview_unload": ("browser", {}),
    "preview_seek":   ("browser", {"value": _is_unit}),
}


def validate_command(body):
    """Return (channel, clean command) or (None, error message)."""
    if not isinstance(body, dict):
        return None, "expected a JSON object"
    spec = ACTIONS.get(body.get("action"))
    if spec is None:
        return None, "unknown action"
    channel, fields = spec
    command = {"action": body["action"]}
    for name, check in fields.items():
        if not check(body.get(name)):
            return None, "bad or missing %r" % name
        command[name] = body[name]
    return channel, command


def resolve_track_path(raw):
    """Turn Traktor's file path into one this Mac can open.

    Traktor reports "Volume:folder:file.mp3" (old Mac style, volume first).
    """
    if not isinstance(raw, str) or not raw:
        return None
    candidates = [raw]
    if not raw.startswith("/"):
        candidates.append("/Volumes/" + raw.replace(":", "/"))
        # The boot volume is also reachable without its /Volumes entry.
        candidates.append("/" + raw.split(":", 1)[-1].replace(":", "/"))
    for candidate in candidates:
        path = Path(candidate)
        try:
            if path.is_file():
                return path
        except OSError:
            pass
    return None


class Channel:
    """One bridge channel: its command queue and which instance owns it."""

    def __init__(self):
        self.seq = 0
        self.commands = []          # (seq, monotonic time, command)
        self.owner = None
        self.owner_seen = 0.0
        self.open_polls = 0
        self.has_state = False
        self.state_requested = False

    def online(self, now):
        return self.owner is not None and (
            self.open_polls > 0 or now - self.owner_seen <= BRIDGE_GRACE)


class Hub:
    """Shared state: latest Traktor snapshot, command queues, SSE subscribers."""

    def __init__(self):
        self.lock = threading.Condition()
        self.state = {}
        self.channels = {name: Channel() for name in CHANNELS}
        self.subscribers = set()
        self.reported = {}
        self.closing = False

    # -- bridge side ---------------------------------------------------------

    def apply_patch(self, channel_name, instance, patch):
        with self.lock:
            channel = self.channels[channel_name]
            if channel.owner != instance:
                return False            # a duplicate instance; ignore it
            for section, entries in patch.items():
                if isinstance(entries, dict):
                    self.state.setdefault(section, {}).update(entries)
            channel.has_state = True
            channel.owner_seen = time.monotonic()
            self._report_bridges()
            self._broadcast("patch", patch)
            return True

    def poll(self, channel_name, instance, after):
        """Block until there are commands newer than `after` (or the hold expires)."""
        with self.lock:
            channel = self.channels[channel_name]
            now = time.monotonic()
            if channel.owner != instance:
                if channel.owner is not None and channel.online(now):
                    # Someone else speaks for this channel. Hold, then say so.
                    deadline = now + STANDBY_HOLD
                    while not self.closing and channel.owner != instance:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0 or not channel.online(time.monotonic()):
                            break
                        self.lock.wait(min(remaining, 1.0))
                    if channel.owner != instance and channel.online(time.monotonic()):
                        return {"owner": False, "seq": -1, "commands": []}
                channel.owner = instance
                channel.has_state = False
                channel.state_requested = False
                after = -1

            channel.owner_seen = time.monotonic()
            channel.open_polls += 1
            self._report_bridges()
            deadline = time.monotonic() + POLL_HOLD
            try:
                if after < 0 or after > channel.seq or (
                        not channel.has_state and not channel.state_requested):
                    # A fresh bridge, or a server that restarted under a running
                    # one: start from the head and ask for a full snapshot now.
                    channel.state_requested = True
                    return {"owner": True, "seq": channel.seq, "commands": [],
                            "needState": True}
                while not self.closing and channel.owner == instance:
                    fresh = self._fresh_commands(channel, after)
                    if fresh:
                        return {"owner": True, "seq": channel.seq, "commands": fresh,
                                "needState": False}
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    self.lock.wait(remaining)
                return {"owner": channel.owner == instance, "seq": channel.seq,
                        "commands": [], "needState": False}
            finally:
                channel.open_polls -= 1
                if channel.owner == instance:
                    channel.owner_seen = time.monotonic()

    @staticmethod
    def _fresh_commands(channel, after):
        now = time.monotonic()
        channel.commands = [c for c in channel.commands if now - c[1] <= COMMAND_TTL]
        return [c[2] for c in channel.commands if c[0] > after]

    def bridges(self):
        now = time.monotonic()
        return {name: channel.online(now) for name, channel in self.channels.items()}

    def _report_bridges(self):
        current = self.bridges()
        if current != self.reported:
            self.reported = current
            self._broadcast("bridge", current)

    def check_bridge(self):
        with self.lock:
            self._report_bridges()

    # -- iPad side -----------------------------------------------------------

    def send_command(self, channel_name, command):
        with self.lock:
            channel = self.channels[channel_name]
            if not channel.online(time.monotonic()):
                return False
            channel.seq += 1
            channel.commands.append((channel.seq, time.monotonic(), command))
            self.lock.notify_all()
            return True

    def deck_file(self, deck):
        with self.lock:
            entry = self.state.get("decks", {}).get(str(deck)) or {}
            return entry.get("file") if entry.get("loaded") else None

    def subscribe(self):
        q = queue.Queue(maxsize=512)
        with self.lock:
            self.subscribers.add(q)
            q.put(("hello", {"state": self.state, "bridge": self.bridges()}))
        return q

    def unsubscribe(self, q):
        with self.lock:
            self.subscribers.discard(q)

    def _broadcast(self, event, data):
        for q in list(self.subscribers):
            try:
                q.put_nowait((event, data))
            except queue.Full:
                # A stalled client: drop it, it will reconnect and get "hello".
                self.subscribers.discard(q)
                try:
                    q.get_nowait()
                    q.put_nowait(("close", None))
                except (queue.Empty, queue.Full):
                    pass

    def close(self):
        with self.lock:
            self.closing = True
            self.lock.notify_all()
            self._broadcast("close", None)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "TraktorPad/1"

    @property
    def hub(self):
        return self.server.hub

    def log_message(self, fmt, *args):
        if self.server.verbose:
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    # -- helpers -------------------------------------------------------------

    def send_json(self, status, payload):
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if self.hub.closing:
            # Do not keep serving a kept-alive connection after shutdown.
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None
        if length <= 0 or length > MAX_BODY:
            return None
        try:
            return json.loads(self.rfile.read(length))
        except ValueError:
            return None

    def is_local(self):
        return self.client_address[0] in LOOPBACK

    def authorized(self, query):
        key = self.server.key
        if not key:
            return True
        given = (query.get("k") or [""])[0]
        return secrets.compare_digest(given, key)

    # -- routing -------------------------------------------------------------

    def bridge_identity(self, source):
        channel = source.get("channel")
        instance = source.get("id")
        if isinstance(channel, list):
            channel = channel[0] if channel else None
        if isinstance(instance, list):
            instance = instance[0] if instance else None
        if channel not in CHANNELS or not isinstance(instance, str) or not instance:
            return None, None
        return channel, instance[:64]

    def do_GET(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)

        if url.path == "/bridge/poll":
            if not self.is_local():
                return self.send_json(403, {"error": "bridge is loopback-only"})
            channel, instance = self.bridge_identity(query)
            if not channel:
                return self.send_json(400, {"error": "channel and id are required"})
            try:
                after = int((query.get("after") or ["-1"])[0])
            except ValueError:
                after = -1
            return self.send_json(200, self.hub.poll(channel, instance, after))

        if url.path in ("/events", "/api/state", "/api/audio", "/api/waveform"):
            if not self.authorized(query):
                return self.send_json(401, {"error": "bad key"})
            if url.path == "/events":
                return self.stream_events()
            if url.path == "/api/audio":
                return self.serve_audio(query)
            if url.path == "/api/waveform":
                return self.serve_waveform(query)
            with self.hub.lock:
                return self.send_json(200, {"state": self.hub.state,
                                            "bridge": self.hub.bridges()})

        return self.serve_static(url.path)

    def do_POST(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)

        if url.path in ("/bridge/state", "/bridge/log"):
            if not self.is_local():
                return self.send_json(403, {"error": "bridge is loopback-only"})
            body = self.read_json()
            channel, instance = self.bridge_identity(body if isinstance(body, dict) else {})
            if not channel:
                return self.send_json(400, {"error": "channel and id are required"})
            if url.path == "/bridge/log":
                print("[traktor:%s] %s" % (channel, str(body.get("message"))[:500]), flush=True)
                return self.send_json(200, {"ok": True})
            if not isinstance(body.get("patch"), dict):
                return self.send_json(400, {"error": "expected {patch: {...}}"})
            accepted = self.hub.apply_patch(channel, instance, body["patch"])
            return self.send_json(200, {"ok": accepted})

        if url.path == "/api/command":
            if not self.authorized(query):
                return self.send_json(401, {"error": "bad key"})
            channel, command = validate_command(self.read_json())
            if channel is None:
                return self.send_json(400, {"error": command})
            if not self.hub.send_command(channel, command):
                return self.send_json(503, {"error": "Traktor is not connected"})
            return self.send_json(200, {"ok": True})

        return self.send_json(404, {"error": "not found"})

    def do_PUT(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if url.path != "/api/waveform":
            return self.send_json(404, {"error": "not found"})
        if not self.authorized(query):
            return self.send_json(401, {"error": "bad key"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_WAVEFORM:
            return self.send_json(400, {"error": "bad waveform size"})
        data = self.rfile.read(length)
        target = self.waveform_cache_file(query)
        if target is None:
            return self.send_json(409, {"error": "that track is not on the deck any more"})
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(".tmp%d" % threading.get_ident())
            temporary.write_bytes(data)
            temporary.replace(target)
        except OSError as error:
            return self.send_json(500, {"error": "could not cache: %s" % error})
        return self.send_json(200, {"ok": True})

    # -- track audio and waveform cache ---------------------------------------
    #
    # The iPad draws waveforms itself: it downloads the file loaded on a deck,
    # analyses it, and hands the (small) result back to be cached here. Only
    # files currently loaded on a deck can be fetched.

    def deck_track(self, query):
        """The file on the deck named in the query, if it is still the one asked for."""
        try:
            deck = int((query.get("deck") or [""])[0])
        except ValueError:
            return None
        raw = self.hub.deck_file(deck)
        wanted = (query.get("file") or [None])[0]
        if not raw or (wanted is not None and wanted != raw):
            return None
        return resolve_track_path(raw)

    def waveform_cache_file(self, query):
        path = self.deck_track(query)
        if path is None:
            return None
        try:
            stat = path.stat()
        except OSError:
            return None
        identity = "%s|%d|%d|v1" % (path, stat.st_size, int(stat.st_mtime))
        return self.server.cache_dir / (hashlib.sha1(identity.encode()).hexdigest() + ".wave")

    def send_bytes(self, body, content_type):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def serve_waveform(self, query):
        target = self.waveform_cache_file(query)
        if target is None or not target.is_file():
            return self.send_json(404, {"error": "no cached waveform"})
        return self.send_bytes(target.read_bytes(), "application/octet-stream")

    def serve_audio(self, query):
        path = self.deck_track(query)
        if path is None:
            return self.send_json(404, {"error": "no readable file on that deck"})
        try:
            size = path.stat().st_size
            if size > MAX_AUDIO:
                return self.send_json(413, {"error": "file too large to analyse"})
            with path.open("rb") as source:
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(size))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                while True:
                    chunk = source.read(256 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except OSError as error:
            return self.send_json(500, {"error": str(error)})

    # -- server-sent events ---------------------------------------------------

    def stream_events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.close_connection = True

        q = self.hub.subscribe()
        try:
            while True:
                try:
                    event, data = q.get(timeout=10)
                except queue.Empty:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                    continue
                if event == "close":
                    break
                payload = json.dumps(data, separators=(",", ":"))
                self.wfile.write(("event: %s\ndata: %s\n\n" % (event, payload)).encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.hub.unsubscribe(q)

    # -- static files ----------------------------------------------------------

    def serve_static(self, path):
        if path == "/":
            path = "/index.html"
        target = (WEB_DIR / path.lstrip("/")).resolve()
        if WEB_DIR not in target.parents or not target.is_file():
            return self.send_json(404, {"error": "not found"})
        body = target.read_bytes()
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, key="", verbose=False, cache_dir=CACHE_DIR):
        super().__init__(address, Handler)
        self.hub = Hub()
        self.key = key
        self.verbose = verbose
        self.cache_dir = Path(cache_dir)
        self._monitor = threading.Thread(target=self._watch_bridge, daemon=True)
        self._monitor.start()

    def _watch_bridge(self):
        while not self.hub.closing:
            time.sleep(1)
            self.hub.check_bridge()

    def shutdown(self):
        self.hub.close()
        super().shutdown()


# ---------------------------------------------------------------------------


def load_key():
    """A stable pairing key, so the iPad's home-screen bookmark keeps working."""
    path = Path.home() / ".traktorpad" / "key"
    try:
        key = path.read_text().strip()
        if key:
            return key
    except OSError:
        pass
    key = secrets.token_urlsafe(9)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(key + "\n")
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


def lan_addresses():
    found = []
    try:
        # No packet is sent; this only asks the OS which interface it would use.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            found.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in found and not ip.startswith("127."):
                found.append(ip)
    except OSError:
        pass
    return found


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default="0.0.0.0",
                        help="address to listen on (default: all interfaces)")
    parser.add_argument("--simulate", action="store_true",
                        help="run a fake Traktor so the UI can be tried without it")
    parser.add_argument("--no-key", action="store_true",
                        help="do not require the pairing key (trusted networks only)")
    parser.add_argument("--verbose", action="store_true", help="log every request")
    args = parser.parse_args(argv)

    key = "" if args.no_key else load_key()
    server = Server((args.host, args.port), key=key, verbose=args.verbose)
    suffix = "/?k=" + key if key else "/"

    print("TraktorPad is running. Open this on the iPad:\n")
    for ip in lan_addresses() or ["<this Mac's IP>"]:
        print("    http://%s:%d%s" % (ip, args.port, suffix))
    print("\n  (on this Mac: http://127.0.0.1:%d%s)" % (args.port, suffix))

    simulator = None
    if args.simulate:
        from simulator import Simulator
        simulator = Simulator("http://127.0.0.1:%d" % args.port)
        simulator.start()
        print("\nSimulating Traktor - no real decks are being controlled.")
    else:
        print("\nWaiting for Traktor...")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBye.")
    finally:
        if simulator:
            simulator.stop()
        server.hub.close()
        server.server_close()


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    main()
