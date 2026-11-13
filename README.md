# TraktorPad

An iPad as a second screen for Traktor Pro 4 on a Mac: two deck displays with
scrolling waveforms, stacked above a touch browser for your library.

It is built for a setup where the hardware already covers transport, cues,
loops, stems and effects. The iPad shows what the laptop screen is too far away
or too crowded for, and adds the things touch is good at.

**Decks (A and B)**

- scrolling three-band waveform with beatgrid, hotcues, loops and playhead
- whole-track overview with cue positions; tap it to seek
- BPM now and original, tempo %, key
- elapsed and remaining time, bar.beat position, bars left
- phase meter against the master, plus Live / Master / Sync / Loop badges
- the remaining time flashes red in the last 30 seconds

**Browser**

- Traktor's own browser: folders, playlists, the list you are in, its sort order
- scroll freely by touch; it also follows the selection when you turn the
  browse knob on your controller
- load to A or B, load to the preview player, scrub the preview, load from the
  preview into a deck, add to the preparation list
- tap a column header to sort
- a green dot marks keys that are harmonically compatible with the master deck

No Native Instruments hardware and no iPad app are needed — the iPad opens a
web page served by your Mac.

> **Status: not yet run against a real Traktor.** Everything here is tested
> against stand-ins (see [Testing](#testing)) and written against the stock QML
> of Traktor Pro 4.5.0, but nobody has run it inside Traktor yet. Expect a round
> of fixes after the first real try — see [First run](#first-run-what-to-check).

## How it works

```
Traktor Pro ──(QML bridge, HTTP on 127.0.0.1)──▶ traktorpad.py ◀──(Wi-Fi)── iPad Safari
```

Traktor runs QML scripts for its screen controllers (Kontrol D2, S8, S4 MK3…),
and those scripts can read and write Traktor's state. TraktorPad adds two small
QML components to one of those controllers:

- a **deck bridge** in the controller mapping (deck, mixer and clock state, seek)
- a **browser bridge** in the controller's screen (Traktor's browser is only
  reachable from there)

Both talk to a server on your Mac, which serves the page the iPad displays.
The technique is documented in
[traktor-kontrol-qml](https://github.com/lsmith77/traktor-kontrol-qml) and used
by [traktor-api-client](https://github.com/ErikMinekus/traktor-api-client) and
[traktor-logger](https://github.com/lsmith77/traktor-logger).

**Waveforms** cannot be exported from Traktor. Instead the iPad downloads the
audio file of each loaded track from the Mac, analyses it once (a few seconds),
and the result is cached on the Mac in `~/.traktorpad/waveforms`, so the next
load of that track is instant. This needs local files; a streaming track gets
the full deck display without a waveform. The colours will not match Traktor's.

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
adding the device is what makes Traktor load the bridge. It does not interfere
with your S2 MK3 or MIDI controllers.

**3. Start the server:**

```sh
python3 server/traktorpad.py
```

It prints a URL such as `http://192.168.1.20:8765/?k=…`.

**4. Open that URL in Safari on the iPad**, then *Share › Add to Home Screen* to
run it full screen. The Mac and iPad must be on the same network.

To see the UI without Traktor: `python3 server/traktorpad.py --simulate`.

## Using it

- **Browser**: tap a folder to open it, **‹** to go back. Tap a track to select
  it, then **Load A**, **Load B**, **Preview** or **+ Prep**.
- **Seek**: tap the overview strip under a waveform. On a stopped or inaudible
  deck it seeks at once and you can drag to scrub. On a deck that is playing to
  the audience it needs a half-second hold.
- **Load** is disabled for a deck that is playing and audible.
- **− / +** in the top bar zooms both waveforms (4 to 32 seconds across).
- **Touch on / Touch locked** turns the iPad into a display only.

Set the iPad's *Auto-Lock* to *Never* while playing; a web page cannot keep the
screen awake over plain HTTP.

## First run: what to check

The status text at the top of the iPad tells you which half is alive:

| Status | Meaning |
| --- | --- |
| No connection to the Mac | The iPad cannot reach the server: check the URL, the network and the macOS firewall prompt. |
| Waiting for Traktor | Neither bridge is talking: check `python3 bridge/install.py status`, that the Kontrol D2 device is added, and restart Traktor. |
| Decks connected · browser not available | The deck bridge works but Traktor is not running the D2's screen QML. This is the one assumption that could not be verified without Traktor. |
| Traktor connected | Both bridges are talking. |

The terminal running the server prints a line when each bridge starts
(`[traktor:main] deck bridge active`, `[traktor:browser] browser bridge
active`) and prints any error a command raises inside Traktor.

Things most likely to need adjusting, in order:

1. **Browser not available** with a virtual D2. Try `install --controller S8`
   and add a Kontrol S8 instead.
2. **Seek** lands in the wrong place: `seek` in
   `bridge/qml/CSI/Common/TraktorPad/PadDeck.qml` assumes Traktor's `seek`
   property takes a position from 0 to 1.
3. **Waveform says the Mac could not read the file**: the path Traktor reports
   was not understood by `resolve_track_path()` in `server/traktorpad.py`.
4. **Beatgrid lines are offset** from the beats: `gridOffset` is assumed to be
   in milliseconds.

When reporting a problem, the status text, the terminal output and what you
tapped are the useful parts.

## Good to know

- **Traktor updates** replace the app, which removes the bridge. Run
  `install.py install` again afterwards.
- **The pairing key** (`?k=…`) keeps other people on the same network from
  loading tracks into your decks. It is stored in `~/.traktorpad/key`. At a
  venue, prefer a private link (iPad hotspot or a travel router) over shared
  Wi-Fi.
- **Stale touches are dropped.** If Traktor is not connected a tap is refused,
  and a command not picked up within 1.5 s is discarded rather than replayed.
- **Favourites** are not shown as a separate bar; reach them through the folder
  tree.
- **Port**: the default is 8765. If you change it with `--port`, also change
  `baseUrl` in `bridge/qml/CSI/Common/TraktorPad/PadLink.qml` and reinstall.

Remove everything from Traktor with `python3 bridge/install.py uninstall`.

## Testing

```sh
python3 -m unittest discover tests
```

- `tests/test_traktorpad.py` — server, protocol and installer, end to end over
  HTTP with a simulated Traktor.
- `tests/test_bridge_qml.py` — installs the bridge into a scratch qml folder and
  runs the real QML in a Qt engine, against stand-ins for Traktor's `CSI` and
  `Traktor.Gui` modules (`tests/qml`) and the real server. Needs
  `pip install pyside6-essentials`; skipped otherwise.

## Layout

```
bridge/qml/CSI/Common/TraktorPad/   deck bridge (runs inside Traktor)
bridge/qml/Screens/TraktorPad/      browser bridge (runs inside Traktor)
bridge/install.py                   adds/removes both from Traktor's app bundle
server/traktorpad.py                the hub on the Mac (standard library only)
server/simulator.py                 fake Traktor for --simulate and tests
web/                                the iPad UI
```

## Credits

Built on what the Traktor modding community worked out, in particular
[traktor-kontrol-qml](https://github.com/lsmith77/traktor-kontrol-qml) and
[traktor-logger](https://github.com/lsmith77/traktor-logger) by lsmith77,
[traktor-api-client](https://github.com/ErikMinekus/traktor-api-client) by Erik
Minekus, and the browser approach credited there to DJMirror. Not affiliated
with Native Instruments.
