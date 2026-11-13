// TraktorPad entry point for deck and mixer data. The installer adds one
// instance of this to a controller mapping (Kontrol D2 by default), which is
// what makes Traktor load it. The browser lives in Screens/TraktorPad.

import CSI 1.0
import QtQuick 2.0

Module {
  id: traktorPad

  // Where the TraktorPad server listens. Change it in PadLink.qml if you run
  // the server with a different --port.
  property alias baseUrl: link.baseUrl

  PadLink {
    id: link
    channel: "main"
    onCommand: traktorPad.dispatch(cmd)
    onResyncRequested: traktorPad.publishAll()
  }

  PadDeck { id: deckA; deckIdx: 1; link: link }
  PadDeck { id: deckB; deckIdx: 2; link: link }

  PadMixer { id: mixer; link: link }

  function deckFor(index) {
    switch (index) {
      case 1: return deckA
      case 2: return deckB
    }
    return null
  }

  function dispatch(cmd) {
    var target = deckFor(cmd.deck)
    if (target) target.run(cmd.action, cmd.value)
  }

  function publishAll() {
    link.log("deck bridge active")
    deckA.publish()
    deckB.publish()
    mixer.publish()
  }
}
