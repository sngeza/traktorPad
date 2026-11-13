// One Traktor deck: publishes its state and executes touch commands.
//
// Property paths here are the ones Traktor's own controller mappings use
// (checked against the stock QML of Traktor Pro 4.5.0).

import CSI 1.0
import QtQuick 2.0

Module {
  id: deck

  property int deckIdx: 1          // 1..4 = A..D
  property var link: null          // PadLink

  readonly property string pathPrefix: "app.traktor.decks." + deckIdx + "."
  readonly property string padPrefix:  "mapping.state.traktorpad.deck" + deckIdx + "."

  property var hotcueExists: [false, false, false, false, false, false, false, false]
  property var hotcuePos:    [0, 0, 0, 0, 0, 0, 0, 0]
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
  AppProperty { id: propLength;      path: pathPrefix + "track.content.track_length"; onValueChanged: deck.touch() }
  AppProperty { id: propBaseBpm;     path: pathPrefix + "tempo.base_bpm";             onValueChanged: deck.touch() }
  AppProperty { id: propTempo;       path: pathPrefix + "tempo.tempo_for_display";    onValueChanged: deck.touch() }

  AppProperty { id: propPlay;        path: pathPrefix + "play";                       onValueChanged: deck.touch() }
  AppProperty { id: propCue;         path: pathPrefix + "cue" }
  AppProperty { id: propSync;        path: pathPrefix + "sync.enabled";               onValueChanged: deck.touch() }
  AppProperty { id: propKeyLock;     path: pathPrefix + "track.key.lock_enabled";     onValueChanged: deck.touch() }
  AppProperty { id: propFlux;        path: pathPrefix + "flux.enabled";               onValueChanged: deck.touch() }
  AppProperty { id: propLoopActive;  path: pathPrefix + "loop.active";                onValueChanged: deck.touch() }
  AppProperty { id: propLoopSize;    path: pathPrefix + "loop.size";                  onValueChanged: deck.touch() }

  // Read on a timer only: these change every frame while a deck plays.
  AppProperty { id: propElapsed;     path: pathPrefix + "track.player.elapsed_time" }

  // Write-only controls.
  AppProperty { id: propAutoLoop;    path: pathPrefix + "loop.set.auto_with_size" }
  AppProperty { id: propMove;        path: pathPrefix + "move" }
  AppProperty { id: propMoveMode;    path: pathPrefix + "move.mode" }
  AppProperty { id: propMoveSize;    path: pathPrefix + "move.size" }
  AppProperty { id: propLoadSel;     path: pathPrefix + "load.selected" }

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
        ? { pos: hotcuePos[i], name: hotcueName[i], type: hotcueType[i] }
        : null)
    }

    link.push("decks", deckIdx, {
      loaded:     !!propLoaded.value,
      type:       propType.value,
      title:      propTitle.value,
      artist:     propArtist.value,
      key:        propKey.value,
      legacyKey:  propLegacyKey.value,
      length:     propLength.value,
      elapsed:    propElapsed.value,
      baseBpm:    propBaseBpm.value,
      tempo:      propTempo.value,
      playing:    !!propPlay.value,
      sync:       !!propSync.value,
      keyLock:    !!propKeyLock.value,
      flux:       !!propFlux.value,
      loopActive: !!propLoopActive.value,
      loopSize:   propLoopSize.value,
      cues:       cues
    })
  }

  Timer { id: snapshotTimer; interval: 40; onTriggered: deck.publish() }

  // Playhead keep-alive. The iPad extrapolates between these, so 4 Hz is plenty.
  Timer {
    interval: 250
    repeat: true
    running: !!propPlay.value
    onTriggered: deck.publish()
  }

  // --- hotcue pads ----------------------------------------------------------
  //
  // Hotcues have no writable property; Traktor triggers them through the
  // Hotcues module's "trigger" pins. Each pad below is a virtual button: a
  // mapping property that is pulsed true/false and wired into that pin.

  Hotcues { name: "hotcues"; channel: deckIdx }

  MappingPropertyDescriptor { id: pad1; path: padPrefix + "pad1"; type: MappingPropertyDescriptor.Boolean; value: false }
  MappingPropertyDescriptor { id: pad2; path: padPrefix + "pad2"; type: MappingPropertyDescriptor.Boolean; value: false }
  MappingPropertyDescriptor { id: pad3; path: padPrefix + "pad3"; type: MappingPropertyDescriptor.Boolean; value: false }
  MappingPropertyDescriptor { id: pad4; path: padPrefix + "pad4"; type: MappingPropertyDescriptor.Boolean; value: false }
  MappingPropertyDescriptor { id: pad5; path: padPrefix + "pad5"; type: MappingPropertyDescriptor.Boolean; value: false }
  MappingPropertyDescriptor { id: pad6; path: padPrefix + "pad6"; type: MappingPropertyDescriptor.Boolean; value: false }
  MappingPropertyDescriptor { id: pad7; path: padPrefix + "pad7"; type: MappingPropertyDescriptor.Boolean; value: false }
  MappingPropertyDescriptor { id: pad8; path: padPrefix + "pad8"; type: MappingPropertyDescriptor.Boolean; value: false }

  Wire { from: DirectPropertyAdapter { path: padPrefix + "pad1"; input: false } to: "hotcues.1.trigger" }
  Wire { from: DirectPropertyAdapter { path: padPrefix + "pad2"; input: false } to: "hotcues.2.trigger" }
  Wire { from: DirectPropertyAdapter { path: padPrefix + "pad3"; input: false } to: "hotcues.3.trigger" }
  Wire { from: DirectPropertyAdapter { path: padPrefix + "pad4"; input: false } to: "hotcues.4.trigger" }
  Wire { from: DirectPropertyAdapter { path: padPrefix + "pad5"; input: false } to: "hotcues.5.trigger" }
  Wire { from: DirectPropertyAdapter { path: padPrefix + "pad6"; input: false } to: "hotcues.6.trigger" }
  Wire { from: DirectPropertyAdapter { path: padPrefix + "pad7"; input: false } to: "hotcues.7.trigger" }
  Wire { from: DirectPropertyAdapter { path: padPrefix + "pad8"; input: false } to: "hotcues.8.trigger" }

  function padFor(index) {
    switch (index) {
      case 1: return pad1
      case 2: return pad2
      case 3: return pad3
      case 4: return pad4
      case 5: return pad5
      case 6: return pad6
      case 7: return pad7
      case 8: return pad8
    }
    return null
  }

  function releasePads() {
    for (var i = 1; i <= 8; i++) padFor(i).value = false
  }

  // Safety net: a pad never stays held if the iPad drops off mid-press.
  Timer { id: padTimeout; interval: 5000; onTriggered: deck.releasePads() }
  Timer { id: moveRelease; interval: 60; onTriggered: propMove.value = 0 }
  Timer { id: cueTimeout; interval: 30000; onTriggered: propCue.value = false }

  // --- commands -------------------------------------------------------------

  function toggle(prop, value) {
    prop.value = (value === true || value === false) ? value : !prop.value
  }

  function run(action, value) {
    switch (action) {
      case "play":     toggle(propPlay, value);       break
      case "sync":     toggle(propSync, value);       break
      case "keylock":  toggle(propKeyLock, value);    break
      case "flux":     toggle(propFlux, value);       break
      case "loop":     toggle(propLoopActive, value); break

      case "cue":
        // Held like the hardware button: down = true, up = false.
        propCue.value = !!value
        if (value) cueTimeout.restart(); else cueTimeout.stop()
        break

      case "loop_size":
        propLoopSize.value = Math.max(0, Math.min(10, Math.round(value)))
        break

      case "autoloop":
        propAutoLoop.value = Math.max(0, Math.min(10, Math.round(value)))
        break

      case "jump":
        // value = { size: MoveSize index, dir: -1 | 1 }
        propMoveMode.value = 0
        propMoveSize.value = Math.max(1, Math.min(11, Math.round(value.size)))
        propMove.value = value.dir < 0 ? -1 : 1
        moveRelease.restart()
        break

      case "hotcue":
        // value = { index: 1..8, down: bool }
        var pad = padFor(Math.round(value.index))
        if (!pad) break
        pad.value = !!value.down
        if (value.down) padTimeout.restart()
        break

      case "load":
        propLoadSel.value = true
        break
    }
  }
}
