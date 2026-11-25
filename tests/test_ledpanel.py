"""LED panel: deck choice, drawing, the serial protocol, and analysis on the Mac.

    python3 -m unittest discover tests
"""

import math
import os
import shutil
import struct
import sys
import tempfile
import threading
import time
import unittest
import wave
from array import array
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import analyze  # noqa: E402
import ledpanel  # noqa: E402
import traktorpad  # noqa: E402
from simulator import Simulator  # noqa: E402


def state(master=1, levels=None, decks=None):
    decks = decks or {
        "1": {"loaded": True, "playing": True, "title": "One", "artist": "A"},
        "2": {"loaded": True, "playing": True, "title": "Two", "artist": "B"},
    }
    return {"decks": decks, "pos": {},
            "mixer": {"master": {"masterDeck": master},
                      "levels": levels or {"1": 1.0, "2": 0.0}}}


class DeckChoice(unittest.TestCase):
    def test_master_deck_when_audible(self):
        self.assertEqual(ledpanel.preferred_deck(state(master=1)), 1)

    def test_loudest_deck_when_master_is_silent(self):
        s = state(master=1, levels={"1": 0.0, "2": 0.8})
        self.assertEqual(ledpanel.preferred_deck(s), 2)

    def test_nothing_loaded(self):
        self.assertIsNone(ledpanel.preferred_deck(state(decks={"1": {"loaded": False}})))

    def test_a_switch_waits_for_the_hold(self):
        chooser = ledpanel.DeckChooser(hold=2.0)
        self.assertEqual(chooser.choose(state(master=1), 0.0), 1)
        crossfaded = state(master=2, levels={"1": 0.5, "2": 1.0})
        self.assertEqual(chooser.choose(crossfaded, 1.0), 1)
        self.assertEqual(chooser.choose(crossfaded, 2.5), 1)
        self.assertEqual(chooser.choose(crossfaded, 3.1), 2)

    def test_a_brief_flick_does_not_switch(self):
        chooser = ledpanel.DeckChooser(hold=2.0)
        chooser.choose(state(master=1), 0.0)
        chooser.choose(state(master=2, levels={"1": 0.0, "2": 1.0}), 1.0)
        chooser.choose(state(master=1), 2.0)
        self.assertEqual(chooser.choose(state(master=2, levels={"1": 0.0, "2": 1.0}), 3.5), 1)

    def test_unloading_the_shown_deck_switches_at_once(self):
        chooser = ledpanel.DeckChooser(hold=2.0)
        chooser.choose(state(master=1), 0.0)
        s = state(master=2, levels={"1": 0.0, "2": 1.0})
        s["decks"]["1"] = {"loaded": False}
        self.assertEqual(chooser.choose(s, 0.1), 2)

    def test_fixed_deck(self):
        self.assertEqual(ledpanel.DeckChooser(fixed=2).choose(state(master=1), 0), 2)


class Drawing(unittest.TestCase):
    def test_serpentine_columns_corners(self):
        order = ledpanel.led_order(32, 8)
        self.assertEqual(len(order), 256)
        self.assertEqual(order[0], (0, 0))
        self.assertEqual(order[7], (0, 7))
        self.assertEqual(order[8], (1, 7))
        self.assertEqual(order[255], (31, 0))
        self.assertEqual(len(set(order)), 256)

    def test_flip_and_other_layouts_cover_every_pixel(self):
        for layout in ledpanel.LAYOUTS:
            for flip in (False, True):
                self.assertEqual(len(set(ledpanel.led_order(32, 8, layout, flip))), 256)
        self.assertEqual(ledpanel.led_order(32, 8, flip=True)[0], (31, 7))

    def test_strip_order_follows_the_mapping(self):
        frame = ledpanel.Frame()
        frame.set(1, 7, (1, 2, 3))
        strip = ledpanel.to_strip(frame, ledpanel.led_order(32, 8))
        self.assertEqual(strip[8 * 3:8 * 3 + 3], b"\x01\x02\x03")

    def test_font_has_every_printable_ascii_glyph(self):
        for code in range(32, 127):
            self.assertEqual(len(ledpanel.FONT[chr(code)]), 5)

    def test_accents_fall_back_to_their_letter(self):
        self.assertEqual(ledpanel.plain_text("Őrült Bálint – Ünnep"), "Orult Balint - Unnep")
        self.assertEqual(ledpanel.text_columns("ő"), ledpanel.text_columns("o"))
        self.assertEqual(ledpanel.plain_text("東京"), "??")

    def test_waveform_view(self):
        deck = {"loaded": True, "playing": True, "length": 200.0, "baseBpm": 120.0,
                "gridOffset": 0, "_index": 1,
                "cues": [{"pos": 60.25, "type": 0, "length": 0}]}
        # A steady full-scale kick everywhere.
        wave = (150, bytes([255, 40, 10]) * (200 * 150))
        frame = ledpanel.Frame()
        ledpanel.draw_waveform(frame, deck, wave, 60.0)
        ledpanel.draw_progress(frame, deck, 60.0, 0.0)
        x = ledpanel.PLAYHEAD_COLUMN
        self.assertEqual(frame.get(x, 3), ledpanel.PLAYHEAD)
        self.assertEqual(frame.get(x + 1, 0), ledpanel.CUE_COLORS[0])   # hotcue just ahead
        red, green, blue = frame.get(x + 3, 6)
        self.assertGreater(red, green + blue)                            # bass is red
        lit = [frame.get(c, 7) != (0, 0, 0) for c in range(32)]
        self.assertEqual(lit.index(False), 10)                           # 60 s of 200 s

    def test_progress_bar_flashes_red_near_the_end(self):
        deck = {"loaded": True, "playing": True, "length": 100.0, "_index": 1}
        frames = []
        for now in (0.0, 0.3):
            frame = ledpanel.Frame()
            ledpanel.draw_progress(frame, deck, 90.0, now)
            frames.append(frame.get(28, 7))
        self.assertEqual(frames[0], ledpanel.ENDING)
        self.assertNotEqual(frames[0], frames[1])

    def test_playhead_moves_between_updates(self):
        deck = {"loaded": True, "playing": True, "tempo": 1.0, "length": 300}
        playhead = ledpanel.Playhead()
        playhead.update(deck, 10.0, 100.0)
        self.assertAlmostEqual(playhead.position(deck, 100.5), 10.5, places=3)
        deck["playing"] = False
        self.assertAlmostEqual(playhead.position(deck, 100.5), 10.0, places=3)


def write_wav(path, seconds, tone_hz, rate=22050):
    samples = array("h", (int(20000 * math.sin(2 * math.pi * tone_hz * i / rate))
                          for i in range(int(seconds * rate))))
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(samples.tobytes())


class Analysis(unittest.TestCase):
    def test_pack_round_trip(self):
        blob = analyze.pack(150, bytes(range(9)))
        self.assertEqual(blob[:4], struct.pack("<I", analyze.MAGIC))
        self.assertEqual(analyze.unpack(blob), (150, bytes(range(9))))
        self.assertIsNone(analyze.unpack(b"nope"))

    def test_bands_follow_the_frequency(self):
        rate = 22050
        for hz, band in ((60, 0), (8000, 2)):
            samples = array("h", (int(20000 * math.sin(2 * math.pi * hz * i / rate))
                                  for i in range(rate)))
            bps, data = analyze.unpack(analyze.analyze_samples(samples, rate))
            self.assertEqual(bps, 150)
            self.assertEqual(len(data), 150 * 3)
            middle = data[75 * 3:75 * 3 + 3]
            self.assertEqual(max(range(3), key=lambda b: middle[b]), band, (hz, list(middle)))

    @unittest.skipUnless(shutil.which("afconvert"), "needs macOS afconvert")
    def test_analyzer_fills_the_cache(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, True)
        audio = folder / "tone.wav"
        write_wav(audio, 2.0, 440)
        target = traktorpad.cache_file_for(audio, folder / "cache")
        analyzer = analyze.Analyzer(log=lambda *_: None)
        self.addCleanup(analyzer.stop)
        analyzer.request(audio, target)
        deadline = time.monotonic() + 20
        while not target.is_file() and time.monotonic() < deadline:
            time.sleep(0.05)
        bps, data = analyze.unpack(target.read_bytes())
        self.assertEqual(len(data) // 3, 300)

    @unittest.skipUnless(shutil.which("afconvert"), "needs macOS afconvert")
    def test_unreadable_file_is_reported_once(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, True)
        audio = folder / "broken.mp3"
        audio.write_bytes(b"not audio at all" * 100)
        messages = []
        analyzer = analyze.Analyzer(log=messages.append)
        self.addCleanup(analyzer.stop)
        target = folder / "x.wave"
        analyzer.request(audio, target)
        deadline = time.monotonic() + 10
        while not messages and time.monotonic() < deadline:
            time.sleep(0.05)
        analyzer.request(audio, target)
        time.sleep(0.3)
        self.assertEqual(len(messages), 1)
        self.assertFalse(target.exists())


class FakeBoard(threading.Thread):
    """The sketch's protocol, on the other end of a pseudo-terminal."""

    def __init__(self, fd, size):
        super().__init__(daemon=True)
        self.fd = fd
        self.size = size
        self.frames = []
        self.stopping = False

    def run(self):
        buffer = b""
        while not self.stopping:
            try:
                buffer += os.read(self.fd, 4096)
            except BlockingIOError:
                time.sleep(0.005)
                continue
            except OSError:
                return
            while True:
                start = buffer.find(b"\xaa\x55")
                if start < 0 or len(buffer) < start + 3 + self.size:
                    break
                self.frames.append((buffer[start + 2], buffer[start + 3:start + 3 + self.size]))
                buffer = buffer[start + 3 + self.size:]
                os.write(self.fd, b"K")


class Serial(unittest.TestCase):
    def test_frames_reach_the_board_in_strip_order(self):
        import tty
        board_fd, host_fd = os.openpty()
        tty.setraw(board_fd)
        os.set_blocking(board_fd, False)
        host_name = os.ttyname(host_fd)
        board = FakeBoard(board_fd, 256 * 3)
        board.start()
        self.addCleanup(os.close, board_fd)
        self.addCleanup(setattr, board, "stopping", True)

        order = ledpanel.led_order(32, 8)
        out = ledpanel.SerialOut(host_name, order, brightness=0.5, log=lambda *_: None,
                                 settle=0)
        self.addCleanup(out.close)
        frame = ledpanel.Frame()
        frame.set(0, 7, (9, 8, 7))
        for _ in range(3):
            out.show(frame)
            time.sleep(0.05)
        frame.set(31, 0, (1, 1, 1))
        out.show(frame)
        deadline = time.monotonic() + 3
        while len(board.frames) < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        os.close(host_fd)

        self.assertEqual(len(board.frames), 2)        # the unchanged frame was not resent
        brightness, pixels = board.frames[-1]
        self.assertEqual(brightness, 128)
        self.assertEqual(pixels[7 * 3:7 * 3 + 3], b"\x09\x08\x07")
        self.assertEqual(pixels[255 * 3:], b"\x01\x01\x01")


class Capture:
    def __init__(self):
        self.frame = None

    def show(self, frame):
        self.frame = frame

    def close(self):
        pass


@unittest.skipUnless(shutil.which("afconvert"), "needs macOS afconvert")
class WithSimulator(unittest.TestCase):
    def test_title_then_waveform_for_the_master_deck(self):
        cache = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, cache, True)
        server = traktorpad.Server(("127.0.0.1", 0), cache_dir=cache)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        simulator = Simulator("http://127.0.0.1:%d" % server.server_address[1])
        simulator.start()
        self.addCleanup(simulator.stop)

        capture = Capture()
        analyzer = analyze.Analyzer(log=lambda *_: None)
        self.addCleanup(analyzer.stop)
        panel = ledpanel.LedPanel(server.hub, capture, cache, analyzer=analyzer,
                                  resolve=traktorpad.resolve_track_path,
                                  cache_file_for=traktorpad.cache_file_for,
                                  log=lambda *_: None)
        panel.start()
        self.addCleanup(panel.stop)

        deadline = time.monotonic() + 5
        while panel.shown is None and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertIsNotNone(panel.shown)
        self.assertEqual(panel.shown[0], 1)                    # the master deck
        deck = server.hub.state["decks"]["1"]
        self.assertEqual(panel.shown[2], deck["title"])

        deadline = time.monotonic() + 60
        while not panel.waves and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertTrue(panel.waves, "the waveform was never analysed")
        panel.title_started -= 1000                            # skip the title
        time.sleep(0.2)
        frame = capture.frame
        column = [frame.get(ledpanel.PLAYHEAD_COLUMN, y) for y in range(7)]
        self.assertTrue(all(c in (ledpanel.PLAYHEAD, ledpanel.PLAYHEAD_DIM) for c in column))


if __name__ == "__main__":
    unittest.main()
