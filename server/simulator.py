"""A stand-in for Traktor's QML bridge.

It speaks to the server over the same two HTTP endpoints the real bridge uses
(/bridge/state and /bridge/poll), so the UI and the protocol can be exercised
without Traktor. It models just enough deck behaviour to make every control on
the iPad do something visible.
"""

import json
import threading
import time
import urllib.error
import urllib.request

LOOP_BEATS = [1 / 32, 1 / 16, 1 / 8, 1 / 4, 1 / 2, 1, 2, 4, 8, 16, 32]
MOVE_BEATS = [0, 1 / 32, 1 / 16, 1 / 8, 1 / 4, 1 / 2, 1, 2, 4, 8, 16, 32]

TRACKS = [
    ("Glass Harbour", "Mira Solheim", 124.0, "8m", 392.0),
    ("Low Tide Signal", "Oskar Venn", 126.0, "9m", 431.5),
    ("Paper Lanterns", "Ines Abara", 122.0, "7d", 358.2),
    ("Night Bus North", "Tomas Reye", 128.0, "10m", 405.9),
    ("Salt & Static", "Juno Marek", 125.0, "8d", 377.4),
]


class SimDeck:
    def __init__(self, index):
        self.index = index
        self.loaded = False
        self.playing = False
        self.sync = False
        self.key_lock = True
        self.flux = False
        self.loop_active = False
        self.loop_size = 7
        self.loop_start = 0.0
        self.tempo = 1.0
        self.elapsed = 0.0
        self.cue_pos = 0.0
        self.cue_held = False
        self.pads = set()
        self.track = None
        self.cues = [None] * 8

    def load(self, n):
        title, artist, bpm, key, length = TRACKS[n % len(TRACKS)]
        self.track = dict(title=title, artist=artist, bpm=bpm, key=key, length=length)
        self.loaded = True
        self.playing = False
        self.loop_active = False
        self.elapsed = 0.0
        self.cue_pos = 0.0
        beat = 60.0 / bpm
        names = ["Intro", "Build", "Drop", "Break", "Drop 2", "Outro"]
        bars = [0, 32, 64, 96, 128, 176]
        self.cues = [None] * 8
        for i, (name, bar) in enumerate(zip(names, bars)):
            pos = bar * 4 * beat
            if pos < length:
                self.cues[i] = {"pos": pos, "name": name, "type": 0}

    @property
    def beat(self):
        return 60.0 / (self.track["bpm"] * self.tempo)

    def advance(self, dt):
        if not (self.loaded and (self.playing or self.cue_held)):
            return
        self.elapsed += dt * self.tempo
        if self.loop_active:
            length = LOOP_BEATS[self.loop_size] * 60.0 / self.track["bpm"]
            if self.elapsed >= self.loop_start + length:
                self.elapsed = self.loop_start + (self.elapsed - self.loop_start) % length
        if self.elapsed >= self.track["length"]:
            self.elapsed = self.track["length"]
            self.playing = False

    def run(self, action, value, sim):
        if action == "load":
            sim.next_track += 1
            self.load(sim.next_track)
            return
        if not self.loaded:
            return

        def toggled(current):
            return value if isinstance(value, bool) else not current

        if action == "play":
            self.playing = toggled(self.playing)
        elif action == "sync":
            self.sync = toggled(self.sync)
            if self.sync:
                self.tempo = sim.master_bpm / self.track["bpm"]
        elif action == "keylock":
            self.key_lock = toggled(self.key_lock)
        elif action == "flux":
            self.flux = toggled(self.flux)
        elif action == "loop":
            self.loop_active = toggled(self.loop_active)
            self.loop_start = self.elapsed
        elif action == "loop_size":
            self.loop_size = value
        elif action == "autoloop":
            self.loop_size = value
            self.loop_active = True
            self.loop_start = self.elapsed
        elif action == "cue":
            if value and not self.playing:
                self.cue_pos = self.elapsed
            if not value and not self.playing:
                self.elapsed = self.cue_pos
            if value and self.playing:
                self.playing = False
                self.elapsed = self.cue_pos
            self.cue_held = bool(value) and not self.playing
        elif action == "jump":
            step = MOVE_BEATS[value["size"]] * 60.0 / self.track["bpm"] * value["dir"]
            self.elapsed = min(max(self.elapsed + step, 0.0), self.track["length"])
        elif action == "hotcue" and value["down"]:
            i = value["index"] - 1
            if self.cues[i]:
                self.elapsed = self.cues[i]["pos"]
                self.loop_active = False
            else:
                self.cues[i] = {"pos": self.elapsed, "name": "", "type": 0}

    def snapshot(self):
        t = self.track or {}
        return {
            "loaded": self.loaded, "type": 0,
            "title": t.get("title", ""), "artist": t.get("artist", ""),
            "key": t.get("key", ""), "legacyKey": "",
            "length": t.get("length", 0), "elapsed": self.elapsed,
            "baseBpm": t.get("bpm", 0), "tempo": self.tempo,
            "playing": self.playing or self.cue_held, "sync": self.sync,
            "keyLock": self.key_lock, "flux": self.flux,
            "loopActive": self.loop_active, "loopSize": self.loop_size,
            "cues": self.cues,
        }


class Simulator:
    def __init__(self, base_url):
        self.base_url = base_url
        self.decks = {i: SimDeck(i) for i in range(1, 5)}
        self.master_bpm = 124.0
        self.next_track = 1
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.threads = []

        self.decks[1].load(0)
        self.decks[2].load(1)
        self.decks[1].playing = True
        self.decks[1].elapsed = 96.0
        self.decks[2].sync = True
        self.decks[2].tempo = self.master_bpm / self.decks[2].track["bpm"]

    def start(self):
        for target in (self._publish_loop, self._poll_loop):
            thread = threading.Thread(target=target, daemon=True)
            thread.start()
            self.threads.append(thread)

    def stop(self):
        self.stopping.set()

    def _request(self, path, body=None, timeout=5):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base_url + path, data=data)
        if data:
            request.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())

    def _patch(self):
        return {
            "decks": {str(i): d.snapshot() for i, d in self.decks.items()},
            "mixer": {
                "master": {"masterDeck": 1, "bpm": self.master_bpm, "xfader": 0.5},
                "levels": {"1": 1.0, "2": 0.0, "3": 0.0, "4": 0.0},
            },
        }

    def _publish_loop(self):
        last = time.monotonic()
        while not self.stopping.is_set():
            now = time.monotonic()
            with self.lock:
                for deck in self.decks.values():
                    deck.advance(now - last)
                patch = self._patch()
            last = now
            try:
                self._request("/bridge/state", {"patch": patch})
            except (urllib.error.URLError, OSError, ValueError):
                pass
            self.stopping.wait(0.25)

    def _poll_loop(self):
        seq = -1
        while not self.stopping.is_set():
            try:
                reply = self._request("/bridge/poll?after=%d" % seq, timeout=30)
            except (urllib.error.URLError, OSError, ValueError):
                self.stopping.wait(1)
                continue
            seq = reply.get("seq", seq)
            commands = reply.get("commands", [])
            if not commands:
                continue
            with self.lock:
                for cmd in commands:
                    deck = self.decks.get(cmd.get("deck"))
                    if deck:
                        deck.run(cmd.get("action"), cmd.get("value"), self)
                patch = self._patch()
            try:
                self._request("/bridge/state", {"patch": patch})
            except (urllib.error.URLError, OSError, ValueError):
                pass
