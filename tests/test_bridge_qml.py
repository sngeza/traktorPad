"""Runs the real QML bridge in a Qt engine against the real server.

The bridge is first installed into a scratch copy of a Traktor qml folder, then
loaded from there. Traktor's own `CSI` and `Traktor.Gui` QML modules are
replaced by the test doubles in tests/qml, so this checks the bridge's logic,
its install layout and the wire protocol - not Traktor itself.
Needs PySide6 (pip install pyside6-essentials); skipped when it is missing.
"""

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "bridge"))

import install  # noqa: E402
import traktorpad  # noqa: E402

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QT_QUICK_BACKEND", "software")
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQuick import QQuickView
except ImportError:
    QGuiApplication = None

DECK1 = "app.traktor.decks.1."
PREVIEW = "app.traktor.browser.preview_player."


@unittest.skipUnless(QGuiApplication, "PySide6 is not installed")
class BridgeInQt(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def setUp(self):
        self.server = traktorpad.Server(("127.0.0.1", 0), cache_dir=tempfile.mkdtemp())
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

        # A minimal Traktor qml folder, with the bridge installed into it.
        self.qml = Path(tempfile.mkdtemp()) / "qml"
        (self.qml / "CSI" / "D2").mkdir(parents=True)
        (self.qml / "CSI" / "Common").mkdir()
        (self.qml / "Screens" / "S8" / "Views").mkdir(parents=True)
        (self.qml / "CSI" / "D2" / "D2.qml").write_text("import CSI 1.0\n\nMapping\n{\n}\n")
        (self.qml / "Screens" / "S8" / "Views" / "Screen.qml").write_text(
            "import QtQuick 2.0\n\nItem {\n  id: screen\n}\n")
        with open(os.devnull, "w") as quiet:
            stdout, sys.stdout = sys.stdout, quiet
            try:
                install.install(self.qml, "D2")
            finally:
                sys.stdout = stdout
        shutil.copy(ROOT / "tests" / "qml" / "Harness.qml", self.qml / "Harness.qml")

        self.warnings = []
        self.view = QQuickView()
        self.view.engine().warnings.connect(
            lambda items: self.warnings.extend(w.toString() for w in items))
        self.view.engine().addImportPath(str(ROOT / "tests" / "qml"))
        self.view.setInitialProperties({"serverUrl": self.base})
        self.view.setSource(QUrl.fromLocalFile(str(self.qml / "Harness.qml")))
        self.harness = self.view.rootObject()
        self.assertIsNotNone(self.harness, "bridge QML failed to load: %s"
                             % [error.toString() for error in self.view.errors()])
        self.view.show()

    def tearDown(self):
        self.view.deleteLater()
        self.pump(0.1)
        self.server.shutdown()
        self.server.server_close()

    def pump(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)

    def state(self):
        with urllib.request.urlopen(self.base + "/api/state", timeout=5) as response:
            return json.loads(response.read())

    def post(self, body):
        request = urllib.request.Request(self.base + "/api/command", data=json.dumps(body).encode())
        request.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status

    def wait_for(self, predicate, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.pump(0.05)
            result = predicate()
            if result:
                return result
        self.fail("condition not reached. state: %s\nlog:\n%s\nwarnings: %s"
                  % (json.dumps(self.state())[:1500], self.harness.property("log"), self.warnings))

    def deck(self, index=1):
        return self.state()["state"].get("decks", {}).get(str(index))

    def browser(self):
        return self.state()["state"].get("browser", {}).get("list")

    def online(self):
        bridges = self.state()["bridge"]
        return bridges["main"] and bridges["browser"]

    def seed(self, values):
        self.harness.seed(json.dumps(values))

    def log(self):
        return self.harness.property("log").splitlines()

    def test_deck_state_reaches_the_server(self):
        self.seed({
            DECK1 + "is_loaded": True,
            DECK1 + "content.title": "Glass Harbour",
            DECK1 + "track.content.file_path": "Macintosh HD:Users:dj:Music:glass.mp3",
            DECK1 + "track.content.track_length": 392.0,
            DECK1 + "content.grid_offset": 140.0,
            DECK1 + "tempo.base_bpm": 124.0,
            DECK1 + "tempo.tempo_for_display": 1.02,
            DECK1 + "tempo.phase": -0.2,
            DECK1 + "track.player.elapsed_time": 61.5,
            DECK1 + "track.cue.hotcues.3.exists": True,
            DECK1 + "track.cue.hotcues.3.start_pos": 123.5,
            DECK1 + "track.cue.hotcues.3.name": "Drop",
            DECK1 + "track.cue.hotcues.3.type": 0,
            "app.traktor.masterclock.source_id": 0,
            "app.traktor.masterclock.tempo": 126.5,
            "app.traktor.mixer.channels.1.volume": 1.0,
            "app.traktor.mixer.xfader.adjust": 0.5,
        })
        deck = self.wait_for(lambda: (self.deck() or {}).get("title") == "Glass Harbour" and self.deck())
        self.assertTrue(deck["loaded"])
        self.assertEqual(deck["file"], "Macintosh HD:Users:dj:Music:glass.mp3")
        self.assertEqual(deck["gridOffset"], 140.0)
        self.assertEqual(deck["cues"][2]["pos"], 123.5)
        self.assertEqual(deck["cues"][2]["name"], "Drop")
        self.assertIsNone(deck["cues"][0])

        position = self.wait_for(lambda: self.state()["state"].get("pos", {}).get("1"))
        self.assertEqual(position, {"e": 61.5, "p": -0.2})

        mixer = self.wait_for(lambda: self.state()["state"].get("mixer", {}).get("master") and
                              self.state()["state"]["mixer"])
        self.assertEqual(mixer["master"]["masterDeck"], 1)
        self.assertEqual(mixer["levels"]["1"], 1.0)
        self.assertEqual(self.warnings, [])

    def test_seek_and_load_from_preview(self):
        self.seed({DECK1 + "is_loaded": True})
        self.wait_for(lambda: self.online() and self.deck())

        self.post({"action": "seek", "deck": 1, "value": 0.25})
        self.wait_for(lambda: DECK1 + "seek=0.25" in self.log())
        self.post({"action": "load_preview", "deck": 2})
        self.wait_for(lambda: "app.traktor.decks.2.load.from_preview_player=true" in self.log())

    def test_browser_navigation_scrolling_and_loading(self):
        root = self.wait_for(lambda: self.online() and (self.browser() or {}).get("count") == 2
                             and self.browser())
        self.assertEqual([r["name"] for r in root["rows"]], ["Track Collection", "Playlists"])
        self.assertFalse(root["content"])

        self.post({"action": "open", "index": 0})
        tracks = self.wait_for(lambda: (self.browser() or {}).get("count") == 400 and
                               len(self.browser()["rows"]) == 80 and self.browser())
        self.assertEqual(tracks["path"], "Track Collection")
        self.assertTrue(tracks["content"])
        self.assertEqual(tracks["rows"][5], {
            "i": 5, "title": "Track 5", "artist": "Artist 5", "bpm": 125, "key": "8m",
            "keyIndex": 5, "rating": 5, "prepared": False, "played": False,
            "preview": False, "decks": []})

        # The iPad scrolls far down: only that slice is read and sent.
        self.post({"action": "window", "value": 300})
        window = self.wait_for(lambda: self.browser()["start"] == 300 and
                               len(self.browser()["rows"]) == 80 and self.browser())
        self.assertEqual([window["rows"][0]["i"], window["rows"][-1]["i"]], [300, 379])
        self.assertEqual(window["rows"][0]["title"], "Track 300")

        self.post({"action": "select", "index": 310})
        self.wait_for(lambda: self.browser()["selected"] == 310)

        self.post({"action": "load", "index": 312, "deck": 2})
        self.wait_for(lambda: 'test.loaded.deck2="Track 312"' in self.log())
        self.assertEqual(sum("test.loaded" in line for line in self.log()), 1,
                         "the second browser instance must not also act")

        self.post({"action": "preview", "index": 320})
        self.wait_for(lambda: PREVIEW + "load=true" in self.log())
        self.assertEqual(self.browser()["selected"], 320)

        self.post({"action": "preview_seek", "value": 0.5})
        self.wait_for(lambda: PREVIEW + "seek=0.5" in self.log())

        self.post({"action": "sort", "value": 5})
        self.wait_for(lambda: self.browser()["sort"] == 5)

        self.post({"action": "back"})
        self.wait_for(lambda: self.browser()["count"] == 2 and self.browser()["path"] == "")
        self.assertEqual(self.warnings, [])

    def test_selection_moved_in_traktor_is_followed(self):
        self.wait_for(lambda: self.online() and self.browser())
        self.post({"action": "open", "index": 0})
        self.wait_for(lambda: (self.browser() or {}).get("count") == 400)
        # As if the browse encoder on a hardware controller jumped far down.
        self.post({"action": "select", "index": 250})
        moved = self.wait_for(lambda: self.browser()["selected"] == 250 and
                              self.browser()["start"] <= 250 < self.browser()["start"] + 80 and
                              len(self.browser()["rows"]) > 0 and self.browser())
        self.assertIn(250, [row["i"] for row in moved["rows"]])

    def test_bridge_resends_state_after_a_server_restart(self):
        self.seed({DECK1 + "is_loaded": True, DECK1 + "content.title": "Low Tide Signal"})
        self.wait_for(lambda: (self.deck() or {}).get("title") == "Low Tide Signal")

        # Same port, fresh server with no memory - what happens when the Mac
        # app is restarted mid-session.
        port = self.server.server_address[1]
        self.server.shutdown()
        self.server.server_close()
        self.pump(0.3)
        self.server = traktorpad.Server(("127.0.0.1", port), cache_dir=tempfile.mkdtemp())
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

        self.wait_for(lambda: (self.deck() or {}).get("title") == "Low Tide Signal", timeout=8)
        self.wait_for(lambda: (self.browser() or {}).get("count") == 2, timeout=8)


if __name__ == "__main__":
    unittest.main()
