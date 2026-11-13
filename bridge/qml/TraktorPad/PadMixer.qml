// Mixer + master clock state (read-only): what is audible, and who is master.

import CSI 1.0
import QtQuick 2.0

Item {
  id: mixer

  property var link: null

  AppProperty { id: masterId;  path: "app.traktor.masterclock.source_id"; onValueChanged: mixer.touch() }
  AppProperty { id: masterBpm; path: "app.traktor.masterclock.tempo";     onValueChanged: mixer.touch() }
  AppProperty { id: xfader;    path: "app.traktor.mixer.xfader.adjust";   onValueChanged: mixer.touch() }

  AppProperty { id: vol1; path: "app.traktor.mixer.channels.1.volume"; onValueChanged: mixer.touch() }
  AppProperty { id: vol2; path: "app.traktor.mixer.channels.2.volume"; onValueChanged: mixer.touch() }
  AppProperty { id: vol3; path: "app.traktor.mixer.channels.3.volume"; onValueChanged: mixer.touch() }
  AppProperty { id: vol4; path: "app.traktor.mixer.channels.4.volume"; onValueChanged: mixer.touch() }

  AppProperty { id: left1;  path: "app.traktor.mixer.channels.1.xfader_assign.left";  onValueChanged: mixer.touch() }
  AppProperty { id: left2;  path: "app.traktor.mixer.channels.2.xfader_assign.left";  onValueChanged: mixer.touch() }
  AppProperty { id: left3;  path: "app.traktor.mixer.channels.3.xfader_assign.left";  onValueChanged: mixer.touch() }
  AppProperty { id: left4;  path: "app.traktor.mixer.channels.4.xfader_assign.left";  onValueChanged: mixer.touch() }
  AppProperty { id: right1; path: "app.traktor.mixer.channels.1.xfader_assign.right"; onValueChanged: mixer.touch() }
  AppProperty { id: right2; path: "app.traktor.mixer.channels.2.xfader_assign.right"; onValueChanged: mixer.touch() }
  AppProperty { id: right3; path: "app.traktor.mixer.channels.3.xfader_assign.right"; onValueChanged: mixer.touch() }
  AppProperty { id: right4; path: "app.traktor.mixer.channels.4.xfader_assign.right"; onValueChanged: mixer.touch() }

  function touch() { if (!throttle.running) throttle.start() }

  // How much of a channel reaches the master: its fader, scaled by the
  // crossfader when the channel is assigned to one side.
  function level(volume, left, right) {
    var out = volume.value
    var x = xfader.value
    if (left.value && x > 0.5)  out *= 1 - (x * 2 - 1)
    if (right.value && x < 0.5) out *= 1 - (1 - x * 2)
    return out
  }

  function publish() {
    if (!link) return

    link.push("mixer", "master", {
      // source_id is 0-based, -1 when the clock is not following a deck
      masterDeck: masterId.value >= 0 ? masterId.value + 1 : 0,
      bpm:        masterBpm.value,
      xfader:     xfader.value
    })
    link.push("mixer", "levels", {
      "1": level(vol1, left1, right1),
      "2": level(vol2, left2, right2),
      "3": level(vol3, left3, right3),
      "4": level(vol4, left4, right4)
    })
  }

  Timer { id: throttle; interval: 60; onTriggered: mixer.publish() }
}
