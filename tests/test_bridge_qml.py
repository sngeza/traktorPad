"""Runs the real QML bridge in a Qt engine against the real server.

Traktor's own `CSI` QML module is replaced by the test double in tests/qml/CSI,
so this checks the bridge's logic and wire protocol - not Traktor itself.
Needs PySide6 (pip install pyside6-essentials); skipped when it is missing.
"""

import json
import os
import sys
import threading
import time
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import traktorpad  # noqa: E402

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine
except ImportError:
    QGuiApplication = None

DECK1 = "app.traktor.decks.1."


@unittest.skipUnless(QGuiApplication, "PySide6 is not installed")
class BridgeInQt(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def setUp(self):
        self.server = traktorpad.Server(("127.0.0.1", 0))
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

        self.warnings = []
        self.engine = QQmlApplicationEngine()
        self.engine.warnings.connect(lambda items: self.warnings.extend(w.toString() for w in items))
        self.engine.addImportPath(str(ROOT / "tests" / "qml"))
        self.engine.setInitialProperties({"serverUrl": self.base})
        self.engine.load(QUrl.fromLocalFile(str(ROOT / "tests" / "qml" / "Harness.qml")))
        self.assertTrue(self.engine.rootObjects(), "bridge QML failed to load: %s" % self.warnings)
        self.harness = self.engine.rootObjects()[0]

    def tearDown(self):
        self.engine.deleteLater()
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

    def wait_for(self, predicate, timeout=4.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.pump(0.05)
            result = predicate()
            if result:
                return result
        self.fail("condition not reached. log:\n%s\nwarnings: %s"
                  % (self.harness.property("log"), self.warnings))

    def deck(self, index=1):
        return self.state()["state"].get("decks", {}).get(str(index))

    def seed(self, values):
        self.harness.seed(json.dumps(values))

    def log(self):
        return self.harness.property("log").splitlines()

    def test_state_reaches_the_server(self):
        self.seed({
            DECK1 + "is_loaded": True,
            DECK1 + "content.title": "Glass Harbour",
            DECK1 + "content.artist": "Mira Solheim",
            DECK1 + "track.content.track_length": 392.0,
            DECK1 + "tempo.base_bpm": 124.0,
            DECK1 + "tempo.tempo_for_display": 1.02,
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
        self.assertEqual(deck["length"], 392.0)
        self.assertEqual(deck["cues"][2], {"pos": 123.5, "name": "Drop", "type": 0})
        self.assertIsNone(deck["cues"][0])

        mixer = self.wait_for(lambda: self.state()["state"].get("mixer", {}).get("master") and
                              self.state()["state"]["mixer"])
        self.assertEqual(mixer["master"]["masterDeck"], 1)
        self.assertEqual(mixer["master"]["bpm"], 126.5)
        self.assertEqual(mixer["levels"]["1"], 1.0)
        self.assertTrue(self.state()["bridge"]["online"])

    def test_commands_drive_traktor_properties(self):
        self.seed({DECK1 + "is_loaded": True, DECK1 + "play": False})
        self.wait_for(lambda: self.state()["bridge"]["online"] and self.deck())

        self.post({"deck": 1, "action": "play"})
        self.wait_for(lambda: (self.deck() or {}).get("playing"))

        self.post({"deck": 1, "action": "loop_size", "value": 5})
        self.wait_for(lambda: (self.deck() or {}).get("loopSize") == 5)

        self.post({"deck": 1, "action": "jump", "value": {"size": 8, "dir": -1}})
        self.wait_for(lambda: DECK1 + "move=-1" in self.log())
        self.assertIn(DECK1 + "move.size=8", self.log())
        self.wait_for(lambda: DECK1 + "move=0" in self.log())   # released again

        self.post({"deck": 1, "action": "load"})
        self.wait_for(lambda: DECK1 + "load.selected=true" in self.log())

    def test_cue_and_hotcue_follow_finger_down_and_up(self):
        self.seed({DECK1 + "is_loaded": True})
        self.wait_for(lambda: self.state()["bridge"]["online"] and self.deck())
        pad = "mapping.state.traktorpad.deck1.pad3"

        self.post({"deck": 1, "action": "hotcue", "value": {"index": 3, "down": True}})
        self.wait_for(lambda: pad + "=true" in self.log())
        self.post({"deck": 1, "action": "hotcue", "value": {"index": 3, "down": False}})
        self.wait_for(lambda: pad + "=false" in self.log())

        self.post({"deck": 1, "action": "cue", "value": True})
        self.wait_for(lambda: DECK1 + "cue=true" in self.log())
        self.post({"deck": 1, "action": "cue", "value": False})
        self.wait_for(lambda: DECK1 + "cue=false" in self.log())

    def test_bridge_resends_state_after_a_server_restart(self):
        self.seed({DECK1 + "is_loaded": True, DECK1 + "content.title": "Low Tide Signal"})
        self.wait_for(lambda: (self.deck() or {}).get("title") == "Low Tide Signal")

        # Same port, fresh server with no memory - what happens when the Mac
        # app is restarted mid-session.
        port = self.server.server_address[1]
        self.server.shutdown()
        self.server.server_close()
        self.pump(0.3)
        self.server = traktorpad.Server(("127.0.0.1", port))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

        self.wait_for(lambda: (self.deck() or {}).get("title") == "Low Tide Signal", timeout=8)


if __name__ == "__main__":
    unittest.main()
