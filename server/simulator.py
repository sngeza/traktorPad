"""A stand-in for Traktor's QML bridge.

It speaks to the server over the same HTTP endpoints the real bridge uses
(/bridge/state and /bridge/poll, on both channels), so the UI and the protocol
can be exercised without Traktor. It models two decks, a browsable library and
a preview player, and synthesises a short audio file per track so the iPad has
something real to analyse and draw.
"""

import array
import json
import math
import random
import tempfile
import threading
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

SAMPLE_RATE = 22050
KEYS = ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]

ADJECTIVES = ["Glass", "Low", "Paper", "Night", "Salt", "Hollow", "Amber", "Quiet",
              "Velvet", "Second", "Northern", "Slow", "Copper", "Distant", "Open"]
NOUNS = ["Harbour", "Signal", "Lanterns", "Bus", "Static", "Garden", "Circuit",
         "Weather", "Engine", "Rooms", "Motion", "Tide", "Atlas", "Mirror", "Ritual"]
ARTISTS = ["Mira Solheim", "Oskar Venn", "Ines Abara", "Tomas Reye", "Juno Marek",
           "Kaito Brandt", "Lea Okafor", "Nils Aranda", "Sana Whitlock", "Ravi Thorne"]


def make_library(seed=7, size=240):
    rng = random.Random(seed)
    tracks = []
    for n in range(size):
        key_index = rng.randrange(24)
        tracks.append({
            "id": n,
            "title": "%s %s" % (rng.choice(ADJECTIVES), rng.choice(NOUNS)),
            "artist": rng.choice(ARTISTS),
            "bpm": float(rng.choice([120, 122, 123, 124, 125, 126, 128, 130])),
            "keyIndex": key_index,
            "key": KEYS[key_index % 12] + ("m" if key_index >= 12 else ""),
            "rating": rng.choice([0, 0, 3, 4, 5]),
            "bars": rng.choice([48, 56, 64]),
        })
    return tracks


def folder(name, children):
    return {"folder": True, "name": name, "children": children}


def make_tree(tracks):
    return folder("", [
        folder("Track Collection", tracks),
        folder("Playlists", [
            folder("Warm Up", tracks[0:40]),
            folder("Peak Time", tracks[40:120]),
            folder("Closing", tracks[120:150]),
        ]),
        folder("Preparation", []),
        folder("History", tracks[200:215]),
    ])


def _bar(bpm, kick, bass, hats, clap, seed):
    """One bar of a simple four-on-the-floor pattern, as int16 samples."""
    rng = random.Random(seed)
    beat = 60.0 / bpm
    total = int(SAMPLE_RATE * beat * 4)
    out = [0.0] * total
    for b in range(4):
        start = int(b * beat * SAMPLE_RATE)
        if kick:
            for i in range(min(int(0.18 * SAMPLE_RATE), total - start)):
                t = i / SAMPLE_RATE
                out[start + i] += 0.85 * math.sin(2 * math.pi * (48 + 90 * math.exp(-t * 30)) * t) * math.exp(-t * 16)
        if clap and b in (1, 3):
            for i in range(min(int(0.09 * SAMPLE_RATE), total - start)):
                out[start + i] += 0.35 * (rng.random() * 2 - 1) * math.exp(-i / SAMPLE_RATE * 40)
        if hats:
            off = start + int(beat / 2 * SAMPLE_RATE)
            for i in range(min(int(0.04 * SAMPLE_RATE), total - off)):
                out[off + i] += 0.28 * (rng.random() * 2 - 1) * math.exp(-i / SAMPLE_RATE * 90)
        if bass:
            off = start + int(beat / 4 * SAMPLE_RATE)
            for i in range(min(int(beat * 0.6 * SAMPLE_RATE), total - off)):
                t = i / SAMPLE_RATE
                out[off + i] += 0.4 * math.sin(2 * math.pi * 110 * t) * math.exp(-t * 5)
    return array.array("h", [int(max(-1.0, min(1.0, v)) * 30000) for v in out]).tobytes()


def synthesize(track, directory):
    """Write a small WAV with an intro / build / drop / break / drop / outro shape."""
    path = Path(directory) / ("track-%03d.wav" % track["id"])
    if path.exists():
        return path
    bpm = track["bpm"]
    variants = {
        "intro": _bar(bpm, False, False, True, False, 1),
        "build": _bar(bpm, True, False, True, False, 2),
        "drop":  _bar(bpm, True, True, True, True, 3),
        "break": _bar(bpm, False, True, False, False, 4),
    }
    bars = track["bars"]
    plan = []
    for bar in range(bars):
        part = bar / bars
        if part < 0.125:
            plan.append("intro")
        elif part < 0.25:
            plan.append("build")
        elif part < 0.5:
            plan.append("drop")
        elif part < 0.625:
            plan.append("break")
        elif part < 0.875:
            plan.append("drop")
        else:
            plan.append("build")
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        # A little lead-in silence, so the beatgrid offset is not zero.
        out.writeframes(b"\x00\x00" * int(SAMPLE_RATE * track_offset(track)))
        out.writeframes(b"".join(variants[name] for name in plan))
    return path


def track_offset(track):
    return 0.05 + (track["id"] % 5) * 0.03


def track_length(track):
    return track_offset(track) + track["bars"] * 4 * 60.0 / track["bpm"]


class SimDeck:
    def __init__(self, index):
        self.index = index
        self.track = None
        self.file = ""
        self.playing = False
        self.sync = False
        self.tempo = 1.0
        self.elapsed = 0.0
        self.phase = 0.0
        self.cues = [None] * 8

    def load(self, track, directory):
        self.track = track
        self.file = str(synthesize(track, directory))
        self.playing = False
        self.elapsed = 0.0
        beat = 60.0 / track["bpm"]
        names = ["Intro", "Build", "Drop", "Break", "Drop 2", "Outro"]
        parts = [0, 0.125, 0.25, 0.5, 0.625, 0.875]
        self.cues = [None] * 8
        for i, (name, part) in enumerate(zip(names, parts)):
            bar = round(track["bars"] * part)
            self.cues[i] = {"pos": track_offset(track) + bar * 4 * beat, "length": 0,
                            "name": name, "type": 5 if name == "Break" else 0}

    @property
    def length(self):
        return track_length(self.track) if self.track else 0.0

    def advance(self, dt):
        if self.track and self.playing:
            self.elapsed += dt * self.tempo
            if self.elapsed >= self.length:
                self.elapsed = self.length
                self.playing = False

    def snapshot(self):
        t = self.track or {}
        return {
            "loaded": bool(self.track), "type": 0,
            "title": t.get("title", ""), "artist": t.get("artist", ""),
            "key": t.get("key", ""), "legacyKey": "",
            "keyIndex": t.get("keyIndex", -1), "keyNow": t.get("keyIndex", -1),
            "file": self.file, "length": self.length,
            "gridOffset": track_offset(t) * 1000 if t else 0,
            "baseBpm": t.get("bpm", 0), "tempo": self.tempo,
            "playing": self.playing, "sync": self.sync, "keyLock": True,
            "loopActive": False, "loopSize": 7, "cueStart": 0, "cueLength": 0,
            "cues": self.cues,
        }

    def position(self):
        return {"e": self.elapsed, "p": self.phase}


class Simulator:
    def __init__(self, base_url):
        self.base_url = base_url
        self.directory = tempfile.mkdtemp(prefix="traktorpad-sim-")
        self.library = make_library()
        self.tree = make_tree(self.library)
        self.stack = [self.tree]           # folders from the root to the open one
        self.selected = 0
        self.window = 0
        self.sort = 0
        self.sort_dir = 1
        self.preview = None
        self.preview_elapsed = 0.0
        self.preview_playing = False
        self.prepared = set()
        self.decks = {1: SimDeck(1), 2: SimDeck(2)}
        self.master_bpm = 124.0
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.started = time.monotonic()

        self.decks[1].load(self.library[3], self.directory)
        self.decks[2].load(self.library[8], self.directory)
        self.master_bpm = self.decks[1].track["bpm"]
        self.decks[1].playing = True
        self.decks[1].elapsed = 31.0
        self.decks[2].sync = True
        self.decks[2].tempo = self.master_bpm / self.decks[2].track["bpm"]
        self.stack = [self.tree, self.tree["children"][0]]

    # -- lifecycle -----------------------------------------------------------

    def start(self):
        for target, args in ((self._publish_loop, ()),
                             (self._poll_loop, ("main",)),
                             (self._poll_loop, ("browser",))):
            threading.Thread(target=target, args=args, daemon=True).start()

    def stop(self):
        self.stopping.set()

    def _request(self, path, body=None, timeout=5):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base_url + path, data=data)
        if data:
            request.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())

    def _send(self, channel, patch):
        try:
            self._request("/bridge/state", {"channel": channel, "id": "sim-" + channel,
                                            "patch": patch})
        except (urllib.error.URLError, OSError, ValueError):
            pass

    # -- browser model -------------------------------------------------------

    def rows(self):
        items = list(self.stack[-1]["children"])
        tracks_only = all(not item.get("folder") for item in items)
        if tracks_only and self.sort:
            field = {2: "title", 3: "artist", 5: "bpm", 28: "keyIndex", 22: "rating"}.get(self.sort)
            if field:
                items.sort(key=lambda t: t[field], reverse=self.sort_dir != 1)
        return items

    def describe(self, index, item):
        if item.get("folder"):
            return {"i": index, "folder": True, "name": item["name"]}
        decks = [letter for number, letter in ((1, "A"), (2, "B"))
                 if self.decks[number].track is item]
        return {"i": index, "title": item["title"], "artist": item["artist"],
                "bpm": item["bpm"], "key": item["key"], "keyIndex": item["keyIndex"],
                "rating": item["rating"], "prepared": item["id"] in self.prepared,
                "played": False, "preview": self.preview is item, "decks": decks}

    def browser_patch(self):
        rows = self.rows()
        self.selected = max(0, min(self.selected, len(rows) - 1)) if rows else 0
        self.window = max(0, min(self.window, max(0, len(rows) - 1)))
        visible = rows[self.window:self.window + 80]
        return {"browser": {
            "list": {
                "path": " | ".join(f["name"] for f in self.stack[1:]),
                "count": len(rows), "selected": self.selected,
                "content": all(not r.get("folder") for r in rows) and bool(rows),
                "sort": self.sort, "sortDir": self.sort_dir, "start": self.window,
                "rows": [self.describe(self.window + n, r) for n, r in enumerate(visible)],
            },
            "preview": {
                "loaded": self.preview is not None,
                "elapsed": self.preview_elapsed,
                "length": track_length(self.preview) if self.preview else 0,
            },
        }}

    def run_browser(self, cmd):
        action = cmd.get("action")
        rows = self.rows()
        index = cmd.get("index")
        item = rows[index] if isinstance(index, int) and 0 <= index < len(rows) else None

        if action == "window":
            self.window = cmd["value"]
        elif action == "back":
            if len(self.stack) > 1:
                closed = self.stack.pop()
                self.selected = self.stack[-1]["children"].index(closed)
                self.window = 0
        elif action == "sort":
            if self.sort == cmd["value"]:
                self.sort_dir = 0 if self.sort_dir == 1 else 1
            else:
                self.sort = cmd["value"]
        elif action == "preview_toggle":
            self.preview_playing = self.preview is not None and not self.preview_playing
        elif action == "preview_unload":
            self.preview, self.preview_playing = None, False
        elif action == "preview_seek" and self.preview:
            self.preview_elapsed = cmd["value"] * track_length(self.preview)
        elif item is None:
            return
        elif action == "select":
            self.selected = index
        elif action in ("open", "load"):
            self.selected = index
            if item.get("folder"):
                self.stack.append(item)
                self.selected = 0
                self.window = 0
            elif action == "load":
                self.decks[cmd["deck"]].load(item, self.directory)
        elif action == "preview" and not item.get("folder"):
            self.selected = index
            self.preview, self.preview_elapsed, self.preview_playing = item, 0.0, True
        elif action == "prep" and not item.get("folder"):
            self.prepared.add(item["id"])

    def run_deck(self, cmd):
        deck = self.decks.get(cmd.get("deck"))
        if not deck:
            return
        if cmd.get("action") == "seek" and deck.track:
            deck.elapsed = cmd["value"] * deck.length
        elif cmd.get("action") == "load_preview" and self.preview:
            deck.load(self.preview, self.directory)

    # -- loops ---------------------------------------------------------------

    def main_patch(self, full):
        patch = {"pos": {str(i): d.position() for i, d in self.decks.items()}}
        if full:
            patch["decks"] = {str(i): d.snapshot() for i, d in self.decks.items()}
            patch["mixer"] = {
                "master": {"masterDeck": 1, "bpm": self.master_bpm, "xfader": 0.5},
                "levels": {"1": 1.0, "2": 0.0},
            }
        return patch

    def _publish_loop(self):
        last = time.monotonic()
        tick = 0
        while not self.stopping.is_set():
            now = time.monotonic()
            with self.lock:
                for deck in self.decks.values():
                    deck.advance(now - last)
                # Deck B drifts slowly against the master, to exercise the phase meter.
                self.decks[2].phase = 0.18 * math.sin((now - self.started) / 4.0)
                if self.preview and self.preview_playing:
                    self.preview_elapsed = min(self.preview_elapsed + (now - last),
                                               track_length(self.preview))
                main = self.main_patch(full=tick % 10 == 0)
                browser = self.browser_patch() if tick % 2 == 0 else None
            last = now
            self._send("main", main)
            if browser:
                self._send("browser", browser)
            tick += 1
            self.stopping.wait(0.1)

    def _poll_loop(self, channel):
        seq = -1
        while not self.stopping.is_set():
            try:
                reply = self._request("/bridge/poll?channel=%s&id=sim-%s&after=%d"
                                      % (channel, channel, seq), timeout=30)
            except (urllib.error.URLError, OSError, ValueError):
                self.stopping.wait(1)
                continue
            seq = reply.get("seq", seq)
            commands = reply.get("commands", [])
            if not commands:
                continue
            with self.lock:
                for cmd in commands:
                    (self.run_browser if channel == "browser" else self.run_deck)(cmd)
                main = self.main_patch(full=True)
                browser = self.browser_patch()
            self._send("main", main)
            self._send("browser", browser)
