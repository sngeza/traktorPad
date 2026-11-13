# TraktorPad

Use an iPad as a second screen with touch controls for Traktor Pro on a Mac.

The iPad shows each deck (track, BPM, key, elapsed/remaining time, a track
overview with hotcue markers, what is live, who is master) and gives you touch
controls: play, cue, sync, eight hotcue pads, loop, beatjump, flux, key lock and
load. No Native Instruments hardware and no iPad app are needed — the iPad just
opens a web page served by your Mac.

> **Status: not yet run against a real Traktor.** The server, the iPad UI and
> the bridge's logic are tested (see [Testing](#testing)), but the bridge was
> developed against a copy of Traktor Pro 4.5.0's QML, not a running Traktor.
> Try it at home before relying on it, and see [If something does not
> work](#if-something-does-not-work).

## How it works

```
Traktor Pro ──(QML bridge, HTTP on 127.0.0.1)──▶ traktorpad.py ◀──(Wi-Fi/USB)── iPad Safari
```

Traktor runs QML scripts for its screen controllers (Kontrol D2, S8, S4 MK3…).
Those scripts can read and write Traktor's internal state. TraktorPad adds a
small QML module to one of those controller mappings. It reports deck state to a
server on your Mac and asks it for touch commands; the server serves the page
the iPad displays. This is the technique documented in
[traktor-kontrol-qml](https://github.com/lsmith77/traktor-kontrol-qml) and used
by [traktor-api-client](https://github.com/ErikMinekus/traktor-api-client),
extended with a channel back into Traktor.

## Setup

You need `python3` on the Mac (`python3 --version`; if it is missing, macOS
offers to install the Command Line Tools).

**1. Install the bridge** — quit Traktor first:

```sh
python3 bridge/install.py install
```

If macOS refuses to modify the app, run it with `sudo` and allow your terminal
under *System Settings › Privacy & Security › App Management*.

**2. Add the virtual controller** — start Traktor, open *Preferences ›
Controller Manager › Add… › Traktor › Kontrol D2*. You do not need the hardware;
adding the device is what makes Traktor load the bridge. (Own an S8, S4 MK3 or
X1 MK3? Hook into that instead: `install --controller S4MK3`.)

**3. Start the server:**

```sh
python3 server/traktorpad.py
```

It prints a URL such as `http://192.168.1.20:8765/?k=…`.

**4. Open that URL in Safari on the iPad**, then *Share › Add to Home Screen* to
run it full screen. The Mac and iPad must be on the same network.

To try the UI without Traktor: `python3 server/traktorpad.py --simulate`.

## Using it

- **A B / A B C D** switches between two and four decks.
- **Touch on / Touch locked** turns the iPad into a display only — useful when
  it sits where it might get brushed.
- **Cue** and the **hotcue pads** act while your finger is down, like hardware.
  Tapping an empty pad stores a hotcue, as in Traktor.
- **Loop**: the middle button toggles the loop, −/+ halve and double it.
- **Jump**: ◀ ▶ beatjump; the middle button cycles the size (1–32 beats).
- **Load** loads the track selected in Traktor's browser. It needs a 0.7 s hold
  and is disabled while that deck is playing and audible.
- A deck's **Remaining** time flashes red in its last 30 seconds.

Set the iPad's *Auto-Lock* to *Never* while playing; a web page cannot keep the
screen awake over plain HTTP.

## Good to know

- **No waveforms.** Traktor draws waveforms with a native component that QML
  cannot export, so the iPad shows a track overview with hotcues instead. Track
  browsing is likewise not available yet (the browser list is only exposed to
  hardware screens).
- **Traktor updates** replace the app, which removes the bridge. Run
  `install.py install` again afterwards.
- **The pairing key** (`?k=…`) keeps other people on the same network from
  controlling your decks. It is stored in `~/.traktorpad/key`. At a venue,
  prefer a private link (iPad hotspot or a travel router) over shared Wi-Fi.
- **Stale touches are dropped.** If Traktor is not connected a tap is refused,
  and a command not picked up within 1.5 s is discarded rather than replayed.
- **Port**: the default is 8765. If you change it with `--port`, also change
  `baseUrl` in `bridge/qml/TraktorPad/PadLink.qml` and reinstall.

Remove everything from Traktor with `python3 bridge/install.py uninstall`.

## If something does not work

The iPad's status dot tells you where to look:

| Status | Meaning |
| --- | --- |
| No connection to the Mac | The iPad cannot reach the server: check the URL, the network and the macOS firewall prompt. |
| Waiting for Traktor | The server runs but the bridge is silent: check `install.py status`, that the Kontrol D2 device is added, and restart Traktor. |
| Traktor connected | The bridge is talking. |

If the display works but a control does nothing, the property path behind it
likely differs in your Traktor version. Each control maps to one line in
`bridge/qml/TraktorPad/PadDeck.qml` (`run()`); the hotcue pads are the least
certain part, since they drive Traktor's `Hotcues` module through virtual
buttons rather than a plain property.
[traktor-logger](https://github.com/lsmith77/traktor-logger) is the tool for
seeing what Traktor's QML is doing.

## Testing

```sh
python3 -m unittest discover tests
```

- `tests/test_traktorpad.py` — server, protocol and installer, end to end over
  HTTP with a simulated Traktor.
- `tests/test_bridge_qml.py` — loads the real bridge QML in a Qt engine against
  a stand-in for Traktor's `CSI` module (`tests/qml/CSI`) and the real server.
  Needs `pip install pyside6-essentials`; skipped otherwise.

## Layout

```
bridge/qml/TraktorPad/   QML that runs inside Traktor
bridge/install.py        adds/removes it from Traktor's app bundle
server/traktorpad.py     the hub on the Mac (standard library only)
server/simulator.py      fake Traktor for --simulate and tests
web/                     the iPad UI
```

## Credits

Built on what the Traktor modding community worked out, in particular
[traktor-kontrol-qml](https://github.com/lsmith77/traktor-kontrol-qml) by
lsmith77 and [traktor-api-client](https://github.com/ErikMinekus/traktor-api-client)
by Erik Minekus. Not affiliated with Native Instruments.
