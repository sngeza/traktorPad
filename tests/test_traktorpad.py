"""End-to-end tests: real server, real HTTP, simulated Traktor bridge.

    python3 -m unittest discover tests
"""

import json
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
from simulator import Simulator  # noqa: E402


def call(url, body=None, timeout=5):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data)
    if data:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


class ServerCase(unittest.TestCase):
    key = ""

    def setUp(self):
        self.server = traktorpad.Server(("127.0.0.1", 0), key=self.key)
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def wait_for(self, predicate, timeout=3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            _, body = call(self.base + "/api/state")
            if predicate(body):
                return body
            time.sleep(0.05)
        self.fail("condition not reached; last state: %r" % (body,))


class WithSimulator(ServerCase):
    def setUp(self):
        super().setUp()
        self.sim = Simulator(self.base)
        self.sim.start()
        self.addCleanup(self.sim.stop)
        self.wait_for(lambda s: s["bridge"]["online"] and "1" in s["state"].get("decks", {}))

    def deck(self, body, index):
        return body["state"]["decks"][str(index)]

    def command(self, deck, action, value=None):
        body = {"deck": deck, "action": action}
        if value is not None:
            body["value"] = value
        return call(self.base + "/api/command", body)

    def test_play_toggles_deck(self):
        self.assertFalse(self.deck(self.wait_for(lambda s: True), 2)["playing"])
        status, _ = self.command(2, "play")
        self.assertEqual(status, 200)
        self.wait_for(lambda s: self.deck(s, 2)["playing"])

    def test_hotcue_jumps_to_its_position(self):
        target = self.deck(self.wait_for(lambda s: True), 1)["cues"][3]["pos"]
        self.command(1, "play", False)
        self.command(1, "hotcue", {"index": 4, "down": True})
        self.command(1, "hotcue", {"index": 4, "down": False})
        self.wait_for(lambda s: abs(self.deck(s, 1)["elapsed"] - target) < 0.01)

    def test_loop_size_and_toggle(self):
        self.command(1, "loop_size", 5)
        self.command(1, "loop")
        body = self.wait_for(lambda s: self.deck(s, 1)["loopActive"])
        self.assertEqual(self.deck(body, 1)["loopSize"], 5)

    def test_load_puts_a_track_on_an_empty_deck(self):
        self.assertFalse(self.deck(self.wait_for(lambda s: True), 3)["loaded"])
        self.command(3, "load")
        self.wait_for(lambda s: self.deck(s, 3)["loaded"])

    def test_invalid_commands_are_rejected(self):
        for body in [
            {"deck": 5, "action": "play"},
            {"deck": 1, "action": "eject"},
            {"deck": 1, "action": "loop_size", "value": 99},
            {"deck": 1, "action": "hotcue", "value": {"index": 9, "down": True}},
            {"deck": 1, "action": "cue"},
            {"deck": True, "action": "play"},
        ]:
            status, _ = call(self.base + "/api/command", body)
            self.assertEqual(status, 400, body)

    def test_event_stream_starts_with_full_state(self):
        with urllib.request.urlopen(self.base + "/events", timeout=5) as stream:
            self.assertEqual(stream.readline().strip(), b"event: hello")
            hello = json.loads(stream.readline()[len(b"data: "):])
        self.assertTrue(hello["bridge"]["online"])
        self.assertIn("1", hello["state"]["decks"])


class WithoutBridge(ServerCase):
    def test_commands_fail_while_traktor_is_away(self):
        status, body = call(self.base + "/api/command", {"deck": 1, "action": "play"})
        self.assertEqual(status, 503)
        self.assertIn("not connected", body["error"])

    def test_stale_commands_are_never_replayed(self):
        # Bridge connects, then goes quiet while a command is queued.
        _, first = call(self.base + "/bridge/poll?after=-1")
        self.assertTrue(first["needState"])
        status, _ = call(self.base + "/api/command", {"deck": 1, "action": "play"})
        self.assertEqual(status, 200)
        time.sleep(traktorpad.COMMAND_TTL + 0.3)

        got = {}
        poller = threading.Thread(
            target=lambda: got.update(call(self.base + "/bridge/poll?after=%d" % first["seq"],
                                           timeout=30)[1]))
        poller.start()
        time.sleep(0.3)
        # A fresh command still gets through on the same poll.
        call(self.base + "/api/command", {"deck": 2, "action": "sync"})
        poller.join(5)
        self.assertEqual([c["deck"] for c in got["commands"]], [2])

    def test_a_new_bridge_starts_from_the_head(self):
        call(self.base + "/bridge/poll?after=-1")
        call(self.base + "/api/command", {"deck": 1, "action": "play"})
        _, reply = call(self.base + "/bridge/poll?after=-1")
        self.assertEqual(reply["commands"], [])
        self.assertEqual(reply["seq"], 1)

    def test_static_files_cannot_escape_the_web_folder(self):
        status, _ = call(self.base + "/../server/traktorpad.py")
        self.assertEqual(status, 404)
        with urllib.request.urlopen(self.base + "/", timeout=5) as page:
            self.assertIn(b"TraktorPad", page.read())


class WithKey(ServerCase):
    key = "secret-key"

    def test_key_is_required_for_the_ipad_api(self):
        self.assertEqual(call(self.base + "/api/state")[0], 401)
        self.assertEqual(call(self.base + "/api/state?k=wrong")[0], 401)
        self.assertEqual(call(self.base + "/api/state?k=secret-key")[0], 200)
        self.assertEqual(call(self.base + "/api/command", {"deck": 1, "action": "play"})[0], 401)


STOCK_MAPPING_SAME_LINE = """import CSI 1.0
import "../Common"

Mapping {
  D2 { name: "surface" }
}
"""

STOCK_MAPPING_NEXT_LINE = """import CSI 1.0
import QtQuick 2.0

import "../../Defines"
import "../Common"

Mapping
{
  D2 { name: "surface" }
}
"""


class Installer(unittest.TestCase):
    def make_traktor(self, mapping):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root))
        qml = root / "qml"
        (qml / "CSI" / "D2").mkdir(parents=True)
        (qml / "CSI" / "Common").mkdir()
        (qml / "CSI" / "D2" / "D2.qml").write_text(mapping)
        return qml

    def test_install_then_uninstall_restores_the_original(self):
        for mapping in (STOCK_MAPPING_SAME_LINE, STOCK_MAPPING_NEXT_LINE):
            qml = self.make_traktor(mapping)
            target = qml / "CSI" / "D2" / "D2.qml"

            install.install(qml, "D2")
            patched = target.read_text()
            self.assertIn(install.IMPORT_LINE, patched)
            self.assertLess(patched.index("Mapping"), patched.index("TraktorPadModule"))
            self.assertLess(patched.index("TraktorPadModule"), patched.index('D2 { name'))
            self.assertTrue((qml / "CSI" / "Common" / "TraktorPad" / "TraktorPadModule.qml").is_file())
            self.assertEqual(install.status(qml), 0)

            install.install(qml, "D2")   # installing twice must not double-hook
            self.assertEqual(target.read_text().count("TraktorPadModule"), 1)

            install.uninstall(qml)
            self.assertEqual(target.read_text(), mapping)
            self.assertFalse((qml / "CSI" / "Common" / "TraktorPad").exists())

    def test_unpatchable_mapping_is_left_untouched(self):
        qml = self.make_traktor("import CSI 1.0\n\nSomethingElse {\n}\n")
        with self.assertRaises(ValueError):
            install.install(qml, "D2")
        self.assertFalse((qml / "CSI" / "Common" / "TraktorPad").exists())
        self.assertEqual((qml / "CSI" / "D2" / "D2.qml").read_text(),
                         "import CSI 1.0\n\nSomethingElse {\n}\n")


if __name__ == "__main__":
    unittest.main()
