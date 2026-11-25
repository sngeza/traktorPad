"""A WS2812B LED matrix (8 x 32) as a "now playing" display for one deck.

The Mac does all the drawing: it reads the hub's state, draws a frame for the
deck the audience is hearing, and streams the pixels to a small board over USB
serial (see led/traktorpad_led/traktorpad_led.ino).

    track loads  ->  "Artist - Title" scrolls across once, in the deck's colour
    then         ->  scrolling waveform around the playhead, bar ticks, hotcues,
                     and a progress bar on the bottom row (flashing red when the
                     track is about to end)

Serial frame: 0xAA 0x55, brightness (0-255), then width*height*3 bytes of RGB in
the order the LEDs are wired. The board answers b"K" once the frame is shown.
"""

import glob
import math
import os
import select
import struct
import sys
import threading
import time
import unicodedata

import analyze

WIDTH = 32
HEIGHT = 8

FPS = 30
SCROLL_SPEED = 24           # text, pixels per second
TITLE_GAP = 0.6             # seconds of dark panel before the waveform appears
PLAYHEAD_COLUMN = 8         # more of the panel shows what is coming
COLUMNS_PER_BEAT = 2        # 32 columns = 4 bars at the default
FALLBACK_SECONDS = 0.25     # per column, for a track with no BPM
ENDING_SECONDS = 30         # matches web/app.js
SWITCH_HOLD = 2.0           # a new deck must stay the choice this long
LIVE_LEVEL = 0.02           # as isLive() in web/app.js

DECK_COLORS = {1: (57, 162, 255), 2: (255, 154, 46), 3: (190, 120, 255), 4: (87, 224, 90)}
CUE_COLORS = [(57, 162, 255), (255, 154, 46), (255, 154, 46), (255, 216, 58),
              (232, 237, 242), (87, 224, 90)]       # by cue type, as web/app.js
PLAYHEAD = (255, 255, 255)
PLAYHEAD_DIM = (70, 0, 0)
BAR_TICK = (22, 22, 22)
ENDING = (255, 0, 0)


# ---------------------------------------------------------------------------
# Font: classic 5x7, five columns per glyph, bit 0 is the top row.

_GLYPHS = (
    "0000000000 00005f0000 0007000700 147f147f14 242a7f2a12 2313086462 3649552250"
    " 0005030000 001c224100 0041221c00 082a1c2a08 08083e0808 0050300000 0808080808"
    " 0060600000 2010080402 3e5149453e 00427f4000 4261514946 2141454b31 1814127f10"
    " 2745454539 3c4a494930 0171090503 3649494936 064949291e 0036360000 0056360000"
    " 0814224100 1414141414 0041221408 0201510906 324979413e 7e1111117e 7f49494936"
    " 3e41414122 7f4141221c 7f49494941 7f09090101 3e41415132 7f0808087f 00417f4100"
    " 2040413f01 7f08142241 7f40404040 7f0204027f 7f0408107f 3e4141413e 7f09090906"
    " 3e4151215e 7f09192946 4649494931 01017f0101 3f4040403f 1f2040201f 7f2018207f"
    " 6314081463 0304780403 6151494543 007f414100 0204081020 0041417f00 0402010204"
    " 4040404040 0001020400 2054545478 7f48444438 3844444420 384444487f 3854545418"
    " 087e090102 0c5252523e 7f08040478 00447d4000 2040443d00 007f102844 00417f4000"
    " 7c04180478 7c08040478 3844444438 7c14141408 081414187c 7c08040408 4854545420"
    " 043f444020 3c4040207c 1c2040201c 3c4030403c 4428102844 0c5050503c 4464544c44"
    " 0008364100 00007f0000 0041360800 0201020402"
).split()
FONT = {chr(32 + i): bytes.fromhex(g) for i, g in enumerate(_GLYPHS)}

_REPLACE = {"–": "-", "—": "-", "‘": "'", "’": "'", "“": '"',
            "”": '"', "…": "...", "ß": "ss", "æ": "ae", "Æ": "AE",
            "ø": "o", "Ø": "O", "ł": "l", "Ł": "L", "Ð": "D",
            "ð": "d", "þ": "th", "Þ": "Th"}


def plain_text(text):
    """What the 5x7 font can show: accents dropped (Ő -> O), the rest as '?'."""
    out = []
    for ch in str(text or ""):
        if ch in FONT:
            out.append(ch)
            continue
        if ch in _REPLACE:
            out.append(_REPLACE[ch])
            continue
        base = "".join(c for c in unicodedata.normalize("NFKD", ch)
                       if not unicodedata.combining(c))
        if base and all(c in FONT for c in base):
            out.append(base)
        elif ch.isspace():
            out.append(" ")
        else:
            out.append("?")
    return "".join(out)


def text_columns(text):
    """The text as a list of 7-bit columns, one blank column between glyphs."""
    columns = []
    for ch in plain_text(text):
        columns.extend(FONT[ch])
        columns.append(0)
    return columns


# ---------------------------------------------------------------------------
# Frames and LED order


class Frame:
    def __init__(self, width=WIDTH, height=HEIGHT):
        self.width = width
        self.height = height
        self.pixels = bytearray(width * height * 3)

    def set(self, x, y, color):
        if 0 <= x < self.width and 0 <= y < self.height:
            i = (y * self.width + x) * 3
            self.pixels[i:i + 3] = bytes(color)

    def get(self, x, y):
        i = (y * self.width + x) * 3
        return tuple(self.pixels[i:i + 3])


def led_order(width, height, layout="serpentine-columns", flip=False):
    """For each LED along the strip, the (x, y) pixel it shows."""
    order = []
    if layout in ("serpentine-columns", "columns"):
        for x in range(width):
            ys = range(height)
            if layout == "serpentine-columns" and x % 2:
                ys = reversed(ys)
            order.extend((x, y) for y in ys)
    elif layout in ("serpentine-rows", "rows"):
        for y in range(height):
            xs = range(width)
            if layout == "serpentine-rows" and y % 2:
                xs = reversed(xs)
            order.extend((x, y) for x in xs)
    else:
        raise ValueError("unknown LED layout: %s" % layout)
    if flip:
        order = [(width - 1 - x, height - 1 - y) for x, y in order]
    return order


LAYOUTS = ("serpentine-columns", "columns", "serpentine-rows", "rows")


def to_strip(frame, order):
    out = bytearray(len(order) * 3)
    src = frame.pixels
    w = frame.width
    for n, (x, y) in enumerate(order):
        i = (y * w + x) * 3
        out[n * 3:n * 3 + 3] = src[i:i + 3]
    return bytes(out)


def scale(color, k):
    return tuple(max(0, min(255, int(c * k + 0.5))) for c in color)


# ---------------------------------------------------------------------------
# Which deck, and where its playhead is


def _num(value, default=0.0):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if value == value else default     # NaN


def deck_is_live(state, index):
    deck = state.get("decks", {}).get(str(index)) or {}
    level = _num((state.get("mixer", {}).get("levels") or {}).get(str(index)))
    return bool(deck.get("loaded")) and bool(deck.get("playing")) and level > LIVE_LEVEL


def preferred_deck(state):
    """The master deck if the audience hears it, else the loudest live deck."""
    decks = state.get("decks", {})
    levels = state.get("mixer", {}).get("levels") or {}
    master = int(_num((state.get("mixer", {}).get("master") or {}).get("masterDeck")))
    if master and deck_is_live(state, master):
        return master
    live = [int(i) for i in decks if deck_is_live(state, i)]
    if live:
        return max(live, key=lambda i: (_num(levels.get(str(i))), -i))
    if master and (decks.get(str(master)) or {}).get("loaded"):
        return master
    playing = [int(i) for i, d in decks.items() if d and d.get("loaded") and d.get("playing")]
    if playing:
        return min(playing)
    return None


class DeckChooser:
    """preferred_deck(), but a change has to hold for SWITCH_HOLD seconds."""

    def __init__(self, fixed=None, hold=SWITCH_HOLD):
        self.fixed = fixed
        self.hold = hold
        self.current = None
        self.pending = None
        self.pending_since = 0.0

    def choose(self, state, now):
        if self.fixed:
            return self.fixed
        wanted = preferred_deck(state)
        current_loaded = self.current is not None and (
            state.get("decks", {}).get(str(self.current)) or {}).get("loaded")
        if wanted == self.current or wanted is None and current_loaded:
            self.pending = None
            return self.current
        if self.current is None or not current_loaded:
            self.current, self.pending = wanted, None
            return self.current
        if wanted != self.pending:
            self.pending, self.pending_since = wanted, now
        elif now - self.pending_since >= self.hold:
            self.current, self.pending = wanted, None
        return self.current


def valid_tempo(deck):
    tempo = _num(deck.get("tempo"), 1.0)
    return tempo if 0.2 < tempo < 5 else 1.0


class Playhead:
    """Smooth position between the bridge's 10 Hz updates (as web/app.js does)."""

    def __init__(self):
        self.base = 0.0
        self.at = 0.0
        self.correction = 0.0
        self.last_reported = None

    def position(self, deck, now):
        elapsed = self.base
        if deck.get("loaded") and deck.get("playing"):
            dt = min(now - self.at, 1.0)
            elapsed += dt * valid_tempo(deck)
            elapsed += self.correction * max(0.0, 1 - dt / 0.25)
        length = _num(deck.get("length"))
        return max(0.0, min(elapsed, length)) if length > 0 else max(0.0, elapsed)

    def update(self, deck, reported, now):
        if reported == self.last_reported:
            return
        self.last_reported = reported
        error = self.position(deck, now) - reported
        self.correction = error if deck.get("playing") and abs(error) < 0.15 else 0.0
        self.base = reported
        self.at = now


# ---------------------------------------------------------------------------
# Drawing


def column_peaks(wave, t0, t1):
    """Peak low/mid/high over the track time [t0, t1)."""
    bins_per_second, data = wave
    count = len(data) // 3
    a = max(0, int(t0 * bins_per_second))
    b = min(count, max(a + 1, int(t1 * bins_per_second + 0.999)))
    lo = mi = hi = 0
    for i in range(a, b):
        k = i * 3
        if data[k] > lo:
            lo = data[k]
        if data[k + 1] > mi:
            mi = data[k + 1]
        if data[k + 2] > hi:
            hi = data[k + 2]
    return lo, mi, hi


def wave_column(peaks):
    """Height (0..1) and colour of one waveform column: red bass, green mids, blue highs."""
    lo, mi, hi = (p / 255 for p in peaks)
    # Linear height: with only seven rows, the sqrt curve of web/wave.js would
    # fill nearly every column of a mastered track to the top.
    height = min(1.0, max(lo, mi, hi * 1.3))
    if height <= 0:
        return 0.0, (0, 0, 0)
    # Colour from the band balance (gains as drawBands() in web/wave.js).
    lo, mi, hi = (v ** 0.5 * g for v, g in zip((lo, mi, hi), (1.0, 1.0, 1.28)))
    strongest = max(lo, mi, hi)
    color = tuple(int(255 * (v / strongest) ** 1.5) for v in (lo, mi, hi))
    return height, color


def draw_text(frame, columns, x0, color, rows=7):
    for i, bits in enumerate(columns):
        x = x0 + i
        if x < 0 or x >= frame.width:
            continue
        for y in range(rows):
            if bits >> y & 1:
                frame.set(x, y, color)


def draw_progress(frame, deck, pos, now):
    length = _num(deck.get("length"))
    if length <= 0:
        return
    y = frame.height - 1
    color = DECK_COLORS.get(int(_num(deck.get("_index"), 1)), DECK_COLORS[1])
    remaining = length - pos
    if deck.get("playing") and remaining < ENDING_SECONDS:
        on = (now * 2) % 1 < 0.5
        color = ENDING if on else scale(ENDING, 0.25)
    filled = pos / length * frame.width
    for x in range(frame.width):
        if x + 1 <= filled:
            frame.set(x, y, scale(color, 0.35))
        elif x < filled:
            frame.set(x, y, scale(color, 0.35 * (filled - x)))
    frame.set(min(frame.width - 1, int(filled)), y, color)


def draw_waveform(frame, deck, wave, pos, playhead_column=PLAYHEAD_COLUMN,
                  columns_per_beat=COLUMNS_PER_BEAT):
    rows = frame.height - 1               # the bottom row is the progress bar
    bpm = _num(deck.get("baseBpm"))
    beat = 60 / bpm if bpm > 0 else 0
    per_column = beat / columns_per_beat if beat else FALLBACK_SECONDS
    length = _num(deck.get("length"))
    offset = _num(deck.get("gridOffset")) / 1000

    for x in range(frame.width):
        t0 = pos + (x - playhead_column) * per_column
        t1 = t0 + per_column
        if t1 <= 0 or (length and t0 >= length):
            continue

        # Bar lines: the column that contains the start of a bar.
        tick = False
        if beat:
            n = -(-(t0 - offset) // beat)       # first beat at or after t0
            first = offset + n * beat
            tick = first < t1 and n % 4 == 0

        height, color = (0.0, (0, 0, 0))
        if wave:
            height, color = wave_column(column_peaks(wave, max(t0, 0), t1))
        lit = int(round(height * rows))
        is_playhead = x == playhead_column
        for y in range(rows):
            if rows - y <= lit:
                frame.set(x, y, PLAYHEAD if is_playhead else color)
            elif is_playhead:
                frame.set(x, y, PLAYHEAD_DIM)
            elif tick:
                frame.set(x, y, BAR_TICK)

        # Loop and hotcues on the top row.
        if deck.get("loopActive") and _num(deck.get("cueLength")) > 0:
            start = _num(deck.get("cueStart"))
            if start < t1 and t0 < start + _num(deck.get("cueLength")) and rows - lit > 0:
                frame.set(x, 0, (0, 90, 0))
        for i, cue in enumerate(deck.get("cues") or []):
            if not isinstance(cue, dict):
                continue
            cp = _num(cue.get("pos"), -1)
            if t0 <= cp < t1:
                kind = int(_num(cue.get("type")))
                frame.set(x, 0, CUE_COLORS[kind] if 0 <= kind < len(CUE_COLORS) else CUE_COLORS[0])


# ---------------------------------------------------------------------------
# The panel


class LedPanel:
    """Draws frames from the hub's state and hands them to an output."""

    def __init__(self, hub, output, cache_dir, analyzer=None, deck=None, retitle=0.0,
                 width=WIDTH, height=HEIGHT, fps=FPS, resolve=None, cache_file_for=None,
                 log=print):
        self.hub = hub
        self.output = output
        self.cache_dir = cache_dir
        self.analyzer = analyzer
        self.chooser = DeckChooser(fixed=deck)
        self.retitle = retitle
        self.width = width
        self.height = height
        self.fps = fps
        self.resolve = resolve
        self.cache_file_for = cache_file_for
        self.log = log

        self.playheads = {}
        self.shown = None           # (deck, file, title, artist) the panel is showing
        self.title_started = 0.0
        self.title_columns = []
        self.color = DECK_COLORS[1]
        self.waves = {}             # file -> (bins_per_second, data) or None
        self.wave_checked = {}      # file -> last time the cache was looked at
        self.stopping = threading.Event()
        self.thread = None
        self.last_frame = None

    # -- life cycle ------------------------------------------------------------

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return self

    def stop(self):
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=2)
        self.output.close()

    def _run(self):
        period = 1 / self.fps
        while not self.stopping.is_set():
            started = time.monotonic()
            try:
                frame = self.render(started)
                self.last_frame = frame
                self.output.show(frame)
            except Exception as error:      # noqa: BLE001 - the panel must never stop the server
                self.log("[led] %s: %s" % (type(error).__name__, error))
                self.stopping.wait(1)
            self.stopping.wait(max(0.0, period - (time.monotonic() - started)))

    # -- state ------------------------------------------------------------------

    def snapshot(self):
        with self.hub.lock:
            state = {
                "decks": {k: dict(v) for k, v in self.hub.state.get("decks", {}).items()
                          if isinstance(v, dict)},
                "pos": {k: dict(v) for k, v in self.hub.state.get("pos", {}).items()
                        if isinstance(v, dict)},
                "mixer": {k: (dict(v) if isinstance(v, dict) else v)
                          for k, v in self.hub.state.get("mixer", {}).items()},
            }
            online = self.hub.bridges().get("main", False)
        return state, online

    def waveform(self, file, now):
        if not file:
            return None
        if self.waves.get(file):
            return self.waves[file]
        if now - self.wave_checked.get(file, -10) < 1.0:
            return None
        self.wave_checked[file] = now
        path = self.resolve(file) if self.resolve else None
        if path is None:
            return None
        target = self.cache_file_for(path, self.cache_dir)
        if target is None:
            return None
        try:
            wave = analyze.unpack(target.read_bytes())
        except OSError:
            wave = None
        if wave:
            if len(self.waves) > 8:
                self.waves.clear()
            self.waves[file] = wave
        elif self.analyzer:
            self.analyzer.request(path, target)
        return wave

    # -- drawing ----------------------------------------------------------------

    def render(self, now):
        frame = Frame(self.width, self.height)
        state, online = self.snapshot()

        for key, entry in state["pos"].items():
            deck = state["decks"].get(key) or {}
            self.playheads.setdefault(key, Playhead()).update(deck, _num(entry.get("e")), now)

        index = self.chooser.choose(state, now)
        deck = state["decks"].get(str(index)) if index else None
        if not online or not deck or not deck.get("loaded"):
            self.shown = None
            if not online:
                # Traktor is away: one slowly breathing pixel.
                level = 0.1 + 0.1 * math.sin(now * 2)
                frame.set(0, self.height - 1, scale((255, 40, 0), level))
            return frame
        deck["_index"] = index

        identity = (index, deck.get("file"), deck.get("title"), deck.get("artist"))
        if identity != self.shown:
            self.shown = identity
            self.start_title(index, deck, now)
        elif self.retitle and now - self.title_started > self.retitle:
            self.start_title(index, deck, now)

        pos = self.playheads.setdefault(str(index), Playhead()).position(deck, now)
        if not self.draw_title(frame, now):
            wave = self.waveform(deck.get("file"), now)
            draw_waveform(frame, deck, wave, pos)
        draw_progress(frame, deck, pos, now)
        return frame

    def start_title(self, index, deck, now):
        artist = str(deck.get("artist") or "").strip()
        title = str(deck.get("title") or "").strip()
        text = " - ".join(part for part in (artist, title) if part) or "Deck %s" % "ABCD"[index - 1]
        self.title_columns = text_columns(text)
        self.title_started = now
        self.color = DECK_COLORS.get(index, DECK_COLORS[1])

    def draw_title(self, frame, now):
        """Draw the scrolling title; False once it has gone by."""
        travelled = int((now - self.title_started) * SCROLL_SPEED)
        total = frame.width + len(self.title_columns)
        if travelled >= total + TITLE_GAP * SCROLL_SPEED:
            return False
        draw_text(frame, self.title_columns, frame.width - travelled, self.color)
        return True


# ---------------------------------------------------------------------------
# Outputs


class SerialOut:
    """Streams frames to the board, one at a time, each acknowledged with b"K"."""

    IOSSIOSPEED = 0x80085402        # macOS: set any baud rate on a serial port
    ACK_TIMEOUT = 0.1
    KEEPALIVE = 1.0

    def __init__(self, port, order, brightness=0.15, baud=1000000, log=print,
                 settle=2.0):
        self.port = port
        self.order = order
        self.brightness = max(0, min(255, int(round(brightness * 255))))
        self.baud = baud
        self.log = log
        self.settle = settle
        self.fd = None
        self.device = None
        self.retry_at = 0.0
        self.ready_at = 0.0
        self.last_payload = None
        self.last_sent = 0.0
        self.awaiting_ack = False
        self.reported_missing = False

    def find_port(self):
        if self.port != "auto":
            return self.port
        found = sorted(glob.glob("/dev/cu.usbmodem*") + glob.glob("/dev/cu.usbserial*")
                       + glob.glob("/dev/cu.wchusbserial*") + glob.glob("/dev/cu.SLAB_USBtoUART*"))
        return found[0] if found else None

    def open(self, now):
        import termios
        device = self.find_port()
        if device is None:
            if not self.reported_missing:
                self.log("[led] no USB serial device found; still looking")
                self.reported_missing = True
            return False
        fd = os.open(device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            attrs = termios.tcgetattr(fd)
            attrs[0] = 0                                        # iflag: raw
            attrs[1] = 0                                        # oflag: raw
            attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
            attrs[3] = 0                                        # lflag: raw
            standard = getattr(termios, "B%d" % self.baud, None)
            attrs[4] = attrs[5] = standard if standard is not None else termios.B115200
            attrs[6][termios.VMIN] = 0
            attrs[6][termios.VTIME] = 0
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
            if standard is None and sys.platform == "darwin":
                import fcntl
                try:
                    fcntl.ioctl(fd, self.IOSSIOSPEED, struct.pack("L", self.baud))
                except OSError:
                    pass        # a pseudo-terminal, or a native-USB board: no baud rate
            termios.tcflush(fd, termios.TCIOFLUSH)
        except Exception:
            os.close(fd)
            raise
        self.fd = fd
        self.device = device
        # Opening the port resets most Arduinos; give the bootloader time to finish.
        self.ready_at = now + self.settle
        self.awaiting_ack = False
        self.last_payload = None
        self.reported_missing = False
        self.log("[led] panel connected on %s" % device)
        return True

    def close(self):
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
        self.fd = None

    def _lost(self, error, now):
        self.log("[led] lost the panel on %s: %s" % (self.device, error))
        self.close()
        self.retry_at = now + 2.0

    def _read_ack(self, timeout):
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            readable, _, _ = select.select([self.fd], [], [], max(0.0, remaining))
            if readable:
                data = os.read(self.fd, 64)
                if not data:
                    raise OSError("device closed")
                if b"K" in data:
                    return True
            if remaining <= 0:
                return False

    def _write_all(self, data):
        view = memoryview(data)
        deadline = time.monotonic() + 0.5
        while view:
            _, writable, _ = select.select([], [self.fd], [], 0.05)
            if writable:
                n = os.write(self.fd, view)
                view = view[n:]
            elif time.monotonic() > deadline:
                raise OSError("write timed out")

    def show(self, frame):
        now = time.monotonic()
        if self.fd is None:
            if now < self.retry_at:
                return
            try:
                if not self.open(now):
                    self.retry_at = now + 2.0
                    return
            except OSError as error:
                self._lost(error, now)
                return
        if now < self.ready_at:
            return
        payload = to_strip(frame, self.order)
        try:
            if self.awaiting_ack and not self._read_ack(0.0):
                if now - self.last_sent < self.ACK_TIMEOUT:
                    return              # still showing the previous frame
            self.awaiting_ack = False
            if payload == self.last_payload and now - self.last_sent < self.KEEPALIVE:
                return
            self._write_all(b"\xaa\x55" + bytes([self.brightness]) + payload)
            self.last_payload = payload
            self.last_sent = now
            self.awaiting_ack = True
        except OSError as error:
            self._lost(error, now)


class TerminalOut:
    """Shows the panel in the terminal, two pixel rows per text line."""

    def __init__(self, stream=None, fps=15):
        self.stream = stream or sys.stdout
        self.period = 1 / fps
        self.last = 0.0
        self.drawn = False

    def show(self, frame):
        now = time.monotonic()
        if now - self.last < self.period:
            return
        self.last = now
        lines = []
        for y in range(0, frame.height, 2):
            cells = []
            for x in range(frame.width):
                top = frame.get(x, y)
                bottom = frame.get(x, y + 1) if y + 1 < frame.height else (0, 0, 0)
                cells.append("\x1b[38;2;%d;%d;%dm\x1b[48;2;%d;%d;%dm▀▀" % (top + bottom))
            lines.append("".join(cells) + "\x1b[0m")
        up = "\x1b[%dA" % len(lines) if self.drawn else ""
        self.stream.write(up + "\n".join(lines) + "\n")
        self.stream.flush()
        self.drawn = True

    def close(self):
        pass


class MultiOut:
    def __init__(self, outputs):
        self.outputs = outputs

    def show(self, frame):
        for output in self.outputs:
            output.show(frame)

    def close(self):
        for output in self.outputs:
            output.close()
