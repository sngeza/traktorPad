"""End-to-end tests: real server, real HTTP, simulated Traktor bridge.

    python3 -m unittest discover tests
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
import urllib.parse
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
        self.cache = tempfile.mkdtemp()
        self.server = traktorpad.Server(("127.0.0.1", 0), key=self.key, cache_dir=self.cache)
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def wait_for(self, predicate, timeout=4.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            _, body = call(self.base + "/api/state")
            if predicate(body):
                return body
            time.sleep(0.05)
        self.fail("condition not reached; last state: %r" % (body,))

    def poll(self, channel, instance, after=-1, timeout=30):
        return call(self.base + "/bridge/poll?channel=%s&id=%s&after=%d"
                    % (channel, instance, after), timeout=timeout)[1]


class WithSimulator(ServerCase):
    def setUp(self):
        super().setUp()
        self.sim = Simulator(self.base)
        self.sim.start()
        self.addCleanup(self.sim.stop)
        self.wait_for(lambda s: s["bridge"]["main"] and s["bridge"]["browser"]
                      and "1" in s["state"].get("decks", {})
                      and s["state"].get("browser", {}).get("list"))

    def deck(self, body, index):
        return body["state"]["decks"][str(index)]

    def listing(self, body):
        return body["state"]["browser"]["list"]

    def command(self, **body):
        return call(self.base + "/api/command", body)

    def test_seek_moves_the_playhead(self):
        length = self.deck(self.wait_for(lambda s: True), 2)["length"]
        self.assertEqual(self.command(action="seek", deck=2, value=0.5)[0], 200)
        self.wait_for(lambda s: abs(s["state"]["pos"]["2"]["e"] - length / 2) < 0.01)

    def test_browse_into_a_playlist_and_load_a_track(self):
        self.command(action="back")
        root = self.listing(self.wait_for(lambda s: self.listing(s)["path"] == ""))
        names = [row["name"] for row in root["rows"]]
        self.command(action="open", index=names.index("Playlists"))
        self.wait_for(lambda s: self.listing(s)["path"] == "Playlists")
        self.command(action="open", index=1)
        body = self.wait_for(lambda s: self.listing(s)["path"] == "Playlists | Peak Time")
        wanted = self.listing(body)["rows"][7]

        self.command(action="load", index=7, deck=2)
        body = self.wait_for(lambda s: self.deck(s, 2)["title"] == wanted["title"])
        self.wait_for(lambda s: self.listing(s)["rows"][7]["decks"] == ["B"])

    def test_scrolling_fetches_another_window_of_rows(self):
        first = self.listing(self.wait_for(lambda s: True))
        self.assertEqual(first["count"], 240)
        self.assertEqual(len(first["rows"]), 80)
        self.command(action="window", value=150)
        body = self.wait_for(lambda s: self.listing(s)["start"] == 150)
        self.assertEqual(self.listing(body)["rows"][0]["i"], 150)

    def test_preview_then_load_from_preview(self):
        row = self.listing(self.wait_for(lambda s: True))["rows"][12]
        self.command(action="preview", index=12)
        self.wait_for(lambda s: s["state"]["browser"]["preview"]["loaded"])
        self.command(action="load_preview", deck=2)
        self.wait_for(lambda s: self.deck(s, 2)["title"] == row["title"])
        self.command(action="preview_unload")
        self.wait_for(lambda s: not s["state"]["browser"]["preview"]["loaded"])

    def test_audio_and_waveform_cache_follow_the_loaded_track(self):
        deck = self.deck(self.wait_for(lambda s: True), 1)
        query = "?deck=1&file=" + urllib.parse.quote(deck["file"])

        with urllib.request.urlopen(self.base + "/api/audio" + query, timeout=10) as audio:
            self.assertEqual(audio.read(4), b"RIFF")
        self.assertEqual(call(self.base + "/api/waveform" + query)[0], 404)

        put = urllib.request.Request(self.base + "/api/waveform" + query,
                                     data=b"TPW1" + bytes(100), method="PUT")
        with urllib.request.urlopen(put, timeout=5) as response:
            self.assertEqual(response.status, 200)
        with urllib.request.urlopen(self.base + "/api/waveform" + query, timeout=5) as cached:
            self.assertEqual(cached.read(4), b"TPW1")

        # Only the file that is actually on the deck can be fetched.
        other = "?deck=1&file=" + urllib.parse.quote("/etc/passwd")
        self.assertEqual(call(self.base + "/api/audio" + other)[0], 404)
        self.assertEqual(call(self.base + "/api/audio?deck=7")[0], 404)

    def test_invalid_commands_are_rejected(self):
        for body in [
            {"action": "seek", "deck": 3, "value": 0.5},
            {"action": "seek", "deck": 1, "value": 1.5},
            {"action": "seek", "deck": 1},
            {"action": "load", "index": 2},
            {"action": "load", "index": -1, "deck": 1},
            {"action": "play", "deck": 1},
            {"action": "select", "index": True},
            "seek",
        ]:
            status, _ = call(self.base + "/api/command", body)
            self.assertEqual(status, 400, body)

    def test_event_stream_starts_with_full_state(self):
        with urllib.request.urlopen(self.base + "/events", timeout=5) as stream:
            self.assertEqual(stream.readline().strip(), b"event: hello")
            hello = json.loads(stream.readline()[len(b"data: "):])
        self.assertEqual(hello["bridge"], {"main": True, "browser": True})
        self.assertIn("1", hello["state"]["decks"])


class WithoutBridge(ServerCase):
    def test_commands_fail_while_traktor_is_away(self):
        status, body = call(self.base + "/api/command", {"action": "back"})
        self.assertEqual(status, 503)
        self.assertIn("not connected", body["error"])

    def test_stale_commands_are_never_replayed(self):
        # Bridge connects, then goes quiet while a command is queued.
        first = self.poll("main", "one")
        self.assertTrue(first["owner"] and first["needState"])
        self.assertEqual(call(self.base + "/api/command",
                              {"action": "seek", "deck": 1, "value": 0.1})[0], 200)
        time.sleep(traktorpad.COMMAND_TTL + 0.3)

        got = {}
        poller = threading.Thread(target=lambda: got.update(self.poll("main", "one", first["seq"])))
        poller.start()
        time.sleep(0.3)
        # A fresh command still gets through on the same poll.
        call(self.base + "/api/command", {"action": "seek", "deck": 2, "value": 0.2})
        poller.join(5)
        self.assertEqual([c["deck"] for c in got["commands"]], [2])

    def test_channels_are_independent(self):
        self.poll("main", "one")
        status, _ = call(self.base + "/api/command", {"action": "back"})
        self.assertEqual(status, 503, "the browser bridge is not connected")
        self.assertEqual(self.wait_for(lambda s: True)["bridge"], {"main": True, "browser": False})

    def test_only_one_instance_owns_a_channel(self):
        self.assertTrue(self.poll("browser", "left")["owner"])
        holder = threading.Thread(target=lambda: self.poll("browser", "left", 0))
        holder.start()                      # "left" keeps its long poll open
        time.sleep(0.2)

        accepted = call(self.base + "/bridge/state",
                        {"channel": "browser", "id": "right", "patch": {"browser": {"x": 1}}})[1]
        self.assertFalse(accepted["ok"])
        self.assertNotIn("browser", self.wait_for(lambda s: True)["state"])

        started = time.monotonic()
        self.assertFalse(self.poll("browser", "right")["owner"])
        self.assertGreater(time.monotonic() - started, traktorpad.STANDBY_HOLD - 0.5,
                           "a standby must be held, not answered in a tight loop")
        self.server.hub.close()
        holder.join(5)

    def test_a_standby_takes_over_when_the_owner_disappears(self):
        self.assertTrue(self.poll("browser", "left")["owner"])
        time.sleep(traktorpad.BRIDGE_GRACE + 0.3)       # "left" never polls again
        reply = self.poll("browser", "right")
        self.assertTrue(reply["owner"] and reply["needState"])

    def test_bridge_endpoints_need_a_channel_and_id(self):
        self.assertEqual(call(self.base + "/bridge/poll?after=-1")[0], 400)
        self.assertEqual(call(self.base + "/bridge/poll?channel=nope&id=x")[0], 400)
        self.assertEqual(call(self.base + "/bridge/state", {"patch": {}})[0], 400)

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
        self.assertEqual(call(self.base + "/api/audio?deck=1")[0], 401)
        self.assertEqual(call(self.base + "/api/command", {"action": "back"})[0], 401)


class TrackPaths(unittest.TestCase):
    def test_traktor_volume_paths_resolve_to_real_files(self):
        folder = Path(tempfile.mkdtemp())
        track = folder / "my track.mp3"
        track.write_bytes(b"x")
        self.assertEqual(traktorpad.resolve_track_path(str(track)), track)
        # "Volume:folder:file" with the boot volume first, as Traktor reports it.
        colon_form = "Macintosh HD" + str(track).replace("/", ":")
        self.assertEqual(traktorpad.resolve_track_path(colon_form), track)
        self.assertIsNone(traktorpad.resolve_track_path("Macintosh HD:nope:missing.mp3"))
        self.assertIsNone(traktorpad.resolve_track_path(""))


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


STOCK_SCREEN = """import CSI 1.0
import QtQuick 2.0

import '../Templates/Deck' as Deck

Item {
  id: screen
}
"""


class Installer(unittest.TestCase):
    def make_traktor(self, mapping):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(root))
        qml = root / "qml"
        (qml / "CSI" / "D2").mkdir(parents=True)
        (qml / "CSI" / "X1MK3").mkdir()
        (qml / "CSI" / "Common").mkdir()
        (qml / "Screens" / "S8" / "Views").mkdir(parents=True)
        (qml / "CSI" / "D2" / "D2.qml").write_text(mapping)
        (qml / "CSI" / "X1MK3" / "X1MK3.qml").write_text(mapping)
        (qml / "Screens" / "S8" / "Views" / "Screen.qml").write_text(STOCK_SCREEN)
        return qml

    def quietly(self, function, *args):
        with open(os.devnull, "w") as quiet:
            stdout, sys.stdout = sys.stdout, quiet
            try:
                return function(*args)
            finally:
                sys.stdout = stdout

    def test_install_then_uninstall_restores_the_original(self):
        for mapping in (STOCK_MAPPING_SAME_LINE, STOCK_MAPPING_NEXT_LINE):
            qml = self.make_traktor(mapping)
            target = qml / "CSI" / "D2" / "D2.qml"
            screen = qml / "Screens" / "S8" / "Views" / "Screen.qml"

            self.quietly(install.install, qml, "D2")
            patched = target.read_text()
            self.assertIn('import "../Common/TraktorPad"', patched)
            self.assertLess(patched.index("Mapping"), patched.index("TraktorPadModule"))
            self.assertLess(patched.index("TraktorPadModule"), patched.index('D2 { name'))
            hooked = screen.read_text()
            self.assertIn("import '../../TraktorPad' as TraktorPad", hooked)
            self.assertLess(hooked.index("Item {"), hooked.index("TraktorPad.PadBrowser {}"))
            self.assertLess(hooked.index("TraktorPad.PadBrowser {}"), hooked.index("id: screen"))
            for name in ("CSI/Common/TraktorPad/TraktorPadModule.qml",
                         "CSI/Common/TraktorPad/PadLink.qml",
                         "Screens/TraktorPad/PadBrowser.qml",
                         "Screens/TraktorPad/PadLink.qml"):
                self.assertTrue((qml / name).is_file(), name)
            self.assertEqual(self.quietly(install.status, qml), 0)

            self.quietly(install.install, qml, "D2")   # twice must not double-hook
            self.assertEqual(target.read_text().count("TraktorPadModule"), 1)
            self.assertEqual(screen.read_text().count("PadBrowser"), 1)

            self.quietly(install.uninstall, qml)
            self.assertEqual(target.read_text(), mapping)
            self.assertEqual(screen.read_text(), STOCK_SCREEN)
            self.assertFalse((qml / "CSI" / "Common" / "TraktorPad").exists())
            self.assertFalse((qml / "Screens" / "TraktorPad").exists())
            self.assertEqual(list(qml.rglob("*.traktorpad-backup")), [])

    def test_switching_controller_moves_the_hook(self):
        qml = self.make_traktor(STOCK_MAPPING_NEXT_LINE)
        self.quietly(install.install, qml, "D2")
        self.quietly(install.install, qml, "X1MK3")      # no screen QML: decks only
        self.assertNotIn("TraktorPad", (qml / "CSI" / "D2" / "D2.qml").read_text())
        self.assertNotIn("TraktorPad", (qml / "Screens" / "S8" / "Views" / "Screen.qml").read_text())
        self.assertIn("TraktorPadModule", (qml / "CSI" / "X1MK3" / "X1MK3.qml").read_text())

    def test_unpatchable_mapping_is_left_untouched(self):
        qml = self.make_traktor("import CSI 1.0\n\nSomethingElse {\n}\n")
        with self.assertRaises(ValueError):
            install.install(qml, "D2")
        self.assertFalse((qml / "CSI" / "Common" / "TraktorPad").exists())
        self.assertEqual((qml / "CSI" / "D2" / "D2.qml").read_text(),
                         "import CSI 1.0\n\nSomethingElse {\n}\n")
        self.assertEqual((qml / "Screens" / "S8" / "Views" / "Screen.qml").read_text(), STOCK_SCREEN)


if __name__ == "__main__":
    unittest.main()
