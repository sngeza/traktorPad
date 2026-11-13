// TraktorPad entry point. The installer adds one instance of this to a
// controller mapping (Kontrol D2 by default), which is what makes Traktor load it.

import CSI 1.0
import QtQuick 2.0

Module {
  id: traktorPad

  // Where the TraktorPad server listens. Change it here if you run the server
  // with a different --port.
  property alias baseUrl: link.baseUrl

  PadLink {
    id: link
    onCommand: traktorPad.dispatch(cmd)
    onResyncRequested: traktorPad.publishAll()
  }

  PadDeck { id: deckA; name: "traktorpad_deck_a"; deckIdx: 1; link: link }
  PadDeck { id: deckB; name: "traktorpad_deck_b"; deckIdx: 2; link: link }
  PadDeck { id: deckC; name: "traktorpad_deck_c"; deckIdx: 3; link: link }
  PadDeck { id: deckD; name: "traktorpad_deck_d"; deckIdx: 4; link: link }

  PadMixer { id: mixer; link: link }

  function deckFor(index) {
    switch (index) {
      case 1: return deckA
      case 2: return deckB
      case 3: return deckC
      case 4: return deckD
    }
    return null
  }

  function dispatch(cmd) {
    var target = deckFor(cmd.deck)
    if (target) target.run(cmd.action, cmd.value)
  }

  function publishAll() {
    deckA.publish()
    deckB.publish()
    deckC.publish()
    deckD.publish()
    mixer.publish()
  }
}
