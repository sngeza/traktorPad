#!/usr/bin/env python3
"""TraktorPad server: the hub between Traktor's QML bridge and the iPad.

    Traktor (QML bridge) --HTTP, loopback--> this server <--HTTP, LAN--> iPad

Standard library only, so it runs on any Mac with python3.

    python3 server/traktorpad.py              # normal use
    python3 server/traktorpad.py --simulate   # fake Traktor, to try the UI
"""

import argparse
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
MAX_BODY = 256 * 1024

LOOPBACK = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}


def _is_bool(v):
    return isinstance(v, bool)


def _is_index(lo, hi):
    return lambda v: isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


def _is_jump(v):
    return (isinstance(v, dict) and _is_index(1, 11)(v.get("size"))
            and v.get("dir") in (-1, 1))


def _is_hotcue(v):
    return (isinstance(v, dict) and _is_index(1, 8)(v.get("index"))
            and _is_bool(v.get("down")))


# action -> validator for its value. Anything else is rejected, so the iPad can
# only ever ask Traktor for what the bridge knows how to do.
ACTIONS = {
    "play": lambda v: v is None or _is_bool(v),
    "sync": lambda v: v is None or _is_bool(v),
    "keylock": lambda v: v is None or _is_bool(v),
    "flux": lambda v: v is None or _is_bool(v),
    "loop": lambda v: v is None or _is_bool(v),
    "cue": _is_bool,
    "loop_size": _is_index(0, 10),
    "autoloop": _is_index(0, 10),
    "jump": _is_jump,
    "hotcue": _is_hotcue,
    "load": lambda v: v is None,
}


class Hub:
    """Shared state: latest Traktor snapshot, command queue, SSE subscribers."""

    def __init__(self):
        self.lock = threading.Condition()
        self.state = {}
        self.seq = 0
        self.commands = []          # (seq, monotonic time, command)
        self.subscribers = set()
        self.open_polls = 0
        self.last_bridge_contact = 0.0
        self.bridge_online = False
        self.has_state = False
        self.state_requested = False
        self.closing = False

    # -- bridge side ---------------------------------------------------------

    def apply_patch(self, patch):
        with self.lock:
            for section, entries in patch.items():
                if isinstance(entries, dict):
                    self.state.setdefault(section, {}).update(entries)
            self.has_state = True
            self._bridge_seen()
            self._broadcast("patch", patch)

    def poll(self, after):
        """Block until there are commands newer than `after` (or the hold expires)."""
        deadline = time.monotonic() + POLL_HOLD
        with self.lock:
            self.open_polls += 1
            self._bridge_seen()
            try:
                if after < 0 or after > self.seq:
                    # Fresh bridge (or a restarted server): start from the head.
                    self.state_requested = True
                    return {"seq": self.seq, "commands": [], "needState": True}
                if not self.has_state and not self.state_requested:
                    # This server restarted under a running bridge and knows
                    # nothing yet: ask once, right away, instead of leaving the
                    # iPad blank until the next deck change.
                    self.state_requested = True
                    return {"seq": self.seq, "commands": [], "needState": True}
                while not self.closing:
                    fresh = self._fresh_commands(after)
                    if fresh:
                        return {"seq": self.seq, "commands": fresh,
                                "needState": not self.has_state}
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    self.lock.wait(remaining)
                return {"seq": self.seq, "commands": [],
                        "needState": not self.has_state}
            finally:
                self.open_polls -= 1
                self.last_bridge_contact = time.monotonic()

    def _fresh_commands(self, after):
        now = time.monotonic()
        self.commands = [c for c in self.commands if now - c[1] <= COMMAND_TTL]
        return [c[2] for c in self.commands if c[0] > after]

    def _bridge_seen(self):
        self.last_bridge_contact = time.monotonic()
        self._set_bridge(True)

    def _set_bridge(self, online):
        if online != self.bridge_online:
            self.bridge_online = online
            self._broadcast("bridge", {"online": online})

    def check_bridge(self):
        with self.lock:
            idle = time.monotonic() - self.last_bridge_contact
            if self.open_polls == 0 and idle > BRIDGE_GRACE:
                self._set_bridge(False)

    # -- iPad side -----------------------------------------------------------

    def send_command(self, command):
        with self.lock:
            if not self.bridge_online:
                return False
            self.seq += 1
            self.commands.append((self.seq, time.monotonic(), command))
            self.lock.notify_all()
            return True

    def subscribe(self):
        q = queue.Queue(maxsize=256)
        with self.lock:
            self.subscribers.add(q)
            q.put(("hello", {"state": self.state,
                             "bridge": {"online": self.bridge_online}}))
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

    def do_GET(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)

        if url.path == "/bridge/poll":
            if not self.is_local():
                return self.send_json(403, {"error": "bridge is loopback-only"})
            try:
                after = int((query.get("after") or ["-1"])[0])
            except ValueError:
                after = -1
            return self.send_json(200, self.hub.poll(after))

        if url.path == "/events":
            if not self.authorized(query):
                return self.send_json(401, {"error": "bad key"})
            return self.stream_events()

        if url.path == "/api/state":
            if not self.authorized(query):
                return self.send_json(401, {"error": "bad key"})
            with self.hub.lock:
                return self.send_json(200, {
                    "state": self.hub.state,
                    "bridge": {"online": self.hub.bridge_online}})

        return self.serve_static(url.path)

    def do_POST(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)

        if url.path == "/bridge/state":
            if not self.is_local():
                return self.send_json(403, {"error": "bridge is loopback-only"})
            body = self.read_json()
            if not isinstance(body, dict) or not isinstance(body.get("patch"), dict):
                return self.send_json(400, {"error": "expected {patch: {...}}"})
            self.hub.apply_patch(body["patch"])
            return self.send_json(200, {"ok": True})

        if url.path == "/api/command":
            if not self.authorized(query):
                return self.send_json(401, {"error": "bad key"})
            body = self.read_json()
            error = validate_command(body)
            if error:
                return self.send_json(400, {"error": error})
            command = {"deck": body["deck"], "action": body["action"],
                       "value": body.get("value")}
            if not self.hub.send_command(command):
                return self.send_json(503, {"error": "Traktor is not connected"})
            return self.send_json(200, {"ok": True})

        return self.send_json(404, {"error": "not found"})

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


def validate_command(body):
    if not isinstance(body, dict):
        return "expected a JSON object"
    if not _is_index(1, 4)(body.get("deck")):
        return "deck must be 1..4"
    check = ACTIONS.get(body.get("action"))
    if check is None:
        return "unknown action"
    if not check(body.get("value")):
        return "bad value for action"
    return None


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, key="", verbose=False):
        super().__init__(address, Handler)
        self.hub = Hub()
        self.key = key
        self.verbose = verbose
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
