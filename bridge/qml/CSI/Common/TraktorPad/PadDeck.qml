// One Traktor deck: publishes what the iPad displays and runs the few touch
// actions the iPad offers (seek, load from the preview player).
//
// Property paths are the ones Traktor's own controller and screen QML use
// (checked against the stock QML of Traktor Pro 4.5.0).

import CSI 1.0
import QtQuick 2.0

Item {
  id: deck

  property int deckIdx: 1          // 1..4 = A..D
  property var link: null          // PadLink

  readonly property string pathPrefix: "app.traktor.decks." + deckIdx + "."

  property var hotcueExists: [false, false, false, false, false, false, false, false]
  property var hotcuePos:    [0, 0, 0, 0, 0, 0, 0, 0]
  property var hotcueLength: [0, 0, 0, 0, 0, 0, 0, 0]
  property var hotcueName:   ["", "", "", "", "", "", "", ""]
  property var hotcueType:   [0, 0, 0, 0, 0, 0, 0, 0]

  // --- state ----------------------------------------------------------------

  AppProperty { id: propLoaded;      path: pathPrefix + "is_loaded";                  onValueChanged: deck.touch() }
  AppProperty {                      path: pathPrefix + "is_loaded_signal";           onValueChanged: deck.touch() }
  AppProperty { id: propType;        path: pathPrefix + "type";                       onValueChanged: deck.touch() }
  AppProperty { id: propTitle;       path: pathPrefix + "content.title";              onValueChanged: deck.touch() }
  AppProperty { id: propArtist;      path: pathPrefix + "content.artist";             onValueChanged: deck.touch() }
  AppProperty { id: propKey;         path: pathPrefix + "content.musical_key";        onValueChanged: deck.touch() }
  AppProperty { id: propLegacyKey;   path: pathPrefix + "content.legacy_key";         onValueChanged: deck.touch() }
  AppProperty { id: propKeyIndex;    path: pathPrefix + "content.key_index";          onValueChanged: deck.touch() }
  AppProperty { id: propKeyNow;      path: pathPrefix + "track.key.final_id";         onValueChanged: deck.touch() }
  AppProperty { id: propGridOffset;  path: pathPrefix + "content.grid_offset";        onValueChanged: deck.touch() }
  AppProperty { id: propFilePath;    path: pathPrefix + "track.content.file_path";    onValueChanged: deck.touch() }
  AppProperty { id: propLength;      path: pathPrefix + "track.content.track_length"; onValueChanged: deck.touch() }
  AppProperty { id: propBaseBpm;     path: pathPrefix + "tempo.base_bpm";             onValueChanged: deck.touch() }
  AppProperty { id: propTempo;       path: pathPrefix + "tempo.tempo_for_display";    onValueChanged: deck.touch() }

  AppProperty { id: propPlay;        path: pathPrefix + "play";                       onValueChanged: deck.touch() }
  AppProperty { id: propSync;        path: pathPrefix + "sync.enabled";               onValueChanged: deck.touch() }
  AppProperty { id: propKeyLock;     path: pathPrefix + "track.key.lock_enabled";     onValueChanged: deck.touch() }
  AppProperty { id: propLoopActive;  path: pathPrefix + "loop.active";                onValueChanged: deck.touch() }
  AppProperty { id: propLoopSize;    path: pathPrefix + "loop.size";                  onValueChanged: deck.touch() }
  AppProperty { id: propCueStart;    path: pathPrefix + "track.cue.active.start_pos"; onValueChanged: deck.touch() }
  AppProperty { id: propCueLength;   path: pathPrefix + "track.cue.active.length";    onValueChanged: deck.touch() }

  // Read on a timer only: these change every frame while a deck plays.
  AppProperty { id: propElapsed;     path: pathPrefix + "track.player.elapsed_time" }
  AppProperty { id: propPhase;       path: pathPrefix + "tempo.phase" }

  // Write-only controls.
  AppProperty { id: propSeek;        path: pathPrefix + "seek" }
  AppProperty { id: propFromPreview; path: pathPrefix + "load.from_preview_player" }

  AppProperty { path: pathPrefix + "track.cue.hotcues.1.exists";    onValueChanged: deck.setCue(hotcueExists, 0, value) }
  AppProperty { path: pathPrefix + "track.cue.hotcues.2.exists";    onValueChanged: deck.setCue(hotcueExists, 1, value) }
  AppProperty { path: pathPrefix + "track.cue.hotcues.3.exists";    onValueChanged: deck.setCue(hotcueExists, 2, value) }
  AppProperty { path: pathPrefix + "track.cue.hotcues.4.exists";    onValueChanged: deck.setCue(hotcueExists, 3, value) }
  AppProperty { path: pathPrefix + "track.cue.hotcues.5.exists";    onValueChanged: deck.setCue(hotcueExists, 4, value) }
  AppProperty { path: pathPrefix + "track.cue.hotcues.6.exists";    onValueChanged: deck.setCue(hotcueExists, 5, value) }
  AppProperty { path: pathPrefix + "track.cue.hotcues.7.exists";    onValueChanged: deck.setCue(hotcueExists, 6, value) }
  AppProperty { path: pathPrefix + "track.cue.hotcues.8.exists";    onValueChanged: deck.setCue(hotcueExists, 7, value) }

  AppProperty { path: pathPrefix + "track.cue.hotcues.1.start_pos"; onValueChanged: deck.setCue(hotcuePos, 0, value) }
  AppProperty { path: pathPrefix + "track.cue.hotcues.2.start_pos"; onValueChanged: deck.setCue(hotcuePos, 1, value) }
  AppProperty { path: pathPrefix + "track.cue.hotcues.3.start_pos"; onValueChanged: deck.setCue(hotcuePos, 2, value) }
  AppProperty { path: pathPrefix + "track.cue.hotcues.4.start_pos"; onValueChanged: deck.setCue(hotcuePos, 3, value) }
  AppProperty { path: pathPrefix + "track.cue.hotcues.5.start_pos"; onValueChanged: deck.setCue(hotcuePos, 4, value) }
  AppProperty { path: pathPrefix + "track.cue.hotcues.6.start_pos"; onValueChanged: deck.setCue(hotcuePos, 5, value) }
  AppProperty { path: pathPrefix + "track.cue.hotcues.7.start_pos"; onValueChanged: deck.setCue(hotcuePos, 6, value) }
  AppProperty { path: pathPrefix + "track.cue.hotcues.8.start_pos"; onValueChanged: deck.setCue(hotcuePos, 7, value) }

  AppProperty { path: pathPrefix + "track.cue.hotcues.1.length";    onValueChanged: deck.setCue(hotcueLength, 0, value) }
  AppProperty { path: pathPrefix + "track.cue.hotcues.2.length";    onValueChanged: deck.setCue(hotcueLength, 1, value) }
  AppProperty { path: pathPrefix + "track.cue.hotcues.3.length";    onValueChanged: deck.setCue(hotcueLength, 2, value) }
  AppProperty { path: pathPrefix + "track.cue.hotcues.4.length";    onValueChanged: deck.setCue(hotcueLength, 3, value) }
  AppProperty { path: pathPrefix + "track.cue.hotcues.5.length";    onValueChanged: deck.setCue(hotcueLength, 4, value) }
  AppProperty { path: pathPrefix + "track.cue.hotcues.6.length";    onValueChanged: deck.setCue(hotcueLength, 5, value) }
  AppProperty { path: pathPrefix + "track.cue.hotcues.7.length";    onValueChanged: deck.setCue(hotcueLength, 6, value) }
  AppProperty { path: pathPrefix + "track.cue.hotcues.8.length";    onValueChanged: deck.setCue(hotcueLength, 7, value) }

  AppProperty { path: pathPrefix + "track.cue.hotcues.1.name";      onValueChanged: deck.setCue(hotcueName, 0, value) }
  AppProperty { path: pathPrefix + "track.cue.hotcues.2.name";      onValueChanged: deck.setCue(hotcueName, 1, value) }
  AppProperty { path: pathPrefix + "track.cue.hotcues.3.name";      onValueChanged: deck.setCue(hotcueName, 2, value) }
  AppProperty { path: pathPrefix + "track.cue.hotcues.4.name";      onValueChanged: deck.setCue(hotcueName, 3, value) }
  AppProperty { path: pathPrefix + "track.cue.hotcues.5.name";      onValueChanged: deck.setCue(hotcueName, 4, value) }
  AppProperty { path: pathPrefix + "track.cue.hotcues.6.name";      onValueChanged: deck.setCue(hotcueName, 5, value) }
  AppProperty { path: pathPrefix + "track.cue.hotcues.7.name";      onValueChanged: deck.setCue(hotcueName, 6, value) }
  AppProperty { path: pathPrefix + "track.cue.hotcues.8.name";      onValueChanged: deck.setCue(hotcueName, 7, value) }

  AppProperty { path: pathPrefix + "track.cue.hotcues.1.type";      onValueChanged: deck.setCue(hotcueType, 0, value) }
  AppProperty { path: pathPrefix + "track.cue.hotcues.2.type";      onValueChanged: deck.setCue(hotcueType, 1, value) }
  AppProperty { path: pathPrefix + "track.cue.hotcues.3.type";      onValueChanged: deck.setCue(hotcueType, 2, value) }
  AppProperty { path: pathPrefix + "track.cue.hotcues.4.type";      onValueChanged: deck.setCue(hotcueType, 3, value) }
  AppProperty { path: pathPrefix + "track.cue.hotcues.5.type";      onValueChanged: deck.setCue(hotcueType, 4, value) }
  AppProperty { path: pathPrefix + "track.cue.hotcues.6.type";      onValueChanged: deck.setCue(hotcueType, 5, value) }
  AppProperty { path: pathPrefix + "track.cue.hotcues.7.type";      onValueChanged: deck.setCue(hotcueType, 6, value) }
  AppProperty { path: pathPrefix + "track.cue.hotcues.8.type";      onValueChanged: deck.setCue(hotcueType, 7, value) }

  function setCue(list, index, value) {
    list[index] = value
    touch()
  }

  // Coalesce bursts of changes (a track load fires dozens) into one snapshot.
  function touch() { snapshotTimer.restart() }

  function publish() {
    if (!link) return

    var cues = []
    for (var i = 0; i < 8; i++) {
      cues.push(hotcueExists[i]
        ? { pos: hotcuePos[i], length: hotcueLength[i], name: hotcueName[i], type: hotcueType[i] }
        : null)
    }

    link.push("decks", deckIdx, {
      loaded:     !!propLoaded.value,
      type:       propType.value,
      title:      propTitle.value,
      artist:     propArtist.value,
      key:        propKey.value,
      legacyKey:  propLegacyKey.value,
      keyIndex:   propKeyIndex.value,
      keyNow:     propKeyNow.value,
      file:       propFilePath.value,
      length:     propLength.value,
      gridOffset: propGridOffset.value,      // milliseconds
      baseBpm:    propBaseBpm.value,
      tempo:      propTempo.value,
      playing:    !!propPlay.value,
      sync:       !!propSync.value,
      keyLock:    !!propKeyLock.value,
      loopActive: !!propLoopActive.value,
      loopSize:   propLoopSize.value,
      cueStart:   propCueStart.value,
      cueLength:  propCueLength.value,
      cues:       cues
    })
    publishPosition()
  }

  // Playhead and phase, sent on their own so the 10 Hz stream stays tiny.
  function publishPosition() {
    if (!link) return
    link.push("pos", deckIdx, { e: propElapsed.value, p: propPhase.value })
  }

  Timer { id: snapshotTimer; interval: 40; onTriggered: deck.publish() }

  Timer {
    interval: 100
    repeat: true
    running: !!propPlay.value
    onTriggered: deck.publishPosition()
  }

  // A stopped deck still moves when it is cued, scratched or seeked.
  Timer {
    interval: 500
    repeat: true
    running: !propPlay.value && !!propLoaded.value
    onTriggered: deck.publishPosition()
  }

  // --- commands -------------------------------------------------------------

  function run(action, value) {
    switch (action) {
      case "seek":
        // Normalised position 0..1, like Traktor's "Seek Position" control.
        propSeek.value = Math.max(0, Math.min(1, Number(value)))
        seekEcho.restart()
        break

      case "load_preview":
        propFromPreview.value = true
        break
    }
  }

  Timer { id: seekEcho; interval: 60; onTriggered: deck.publishPosition() }
}
