// Traktor's track browser, mirrored to the iPad.
//
// The browser is only reachable from the Screens layer (Traktor.Gui), which
// Traktor runs for controllers that have displays - a Kontrol D2 or S8 added in
// Controller Manager is enough, the hardware is not needed.
//
// This uses the same Traktor.Browser calls as Traktor's own S8 browser screen
// (Screens/S8/Templates/Browser/BrowserView.qml in the stock QML).

import CSI 1.0
import QtQuick 2.0
import Traktor.Gui 1.0 as Traktor

Item {
  id: pad

  // Zero size + clip keeps this invisible on the controller screen. It must
  // stay "visible", or the ListView below would not create its delegates.
  width: 0
  height: 0
  clip: true

  // The iPad asks for the slice of the list it is looking at; only that slice
  // is read from Traktor, so long playlists cost nothing.
  property int windowStart: 0
  readonly property int windowSize: 80

  property var    rowCache: ({})
  property string lastList: ""
  property string lastPreview: ""
  property int    lastSelected: -1
  property int    pendingIndex: -1
  property string pendingAction: ""

  property alias baseUrl: link.baseUrl

  PadLink {
    id: link
    channel: "browser"
    onCommand: pad.run(cmd)
    onResyncRequested: {
      link.log("browser bridge active")
      pad.lastList = ""
      pad.lastPreview = ""
      pad.publish()
    }
  }

  Traktor.Browser {
    id: browser
    isActive: true
  }

  AppProperty { id: previewLoad;     path: "app.traktor.browser.preview_player.load" }
  AppProperty { id: previewToggle;   path: "app.traktor.browser.preview_player.load_or_play" }
  AppProperty { id: previewUnload;   path: "app.traktor.browser.preview_player.unload" }
  AppProperty { id: previewSeek;     path: "app.traktor.browser.preview_player.seek" }
  AppProperty { id: previewIsLoaded; path: "app.traktor.browser.preview_player.is_loaded" }
  AppProperty { id: previewElapsed;  path: "app.traktor.browser.preview_player.elapsed_time" }
  AppProperty { id: previewLength;   path: "app.traktor.browser.preview_content.track_length" }
  AppProperty { id: prepAppend;      path: "app.traktor.browser.preparation.append" }

  // Rows are 1 px tall, so scrolling to y = N shows rows N .. N + windowSize.
  ListView {
    id: list
    width: 1
    height: pad.windowSize
    clip: true
    interactive: false
    model: browser.dataSet

    delegate: Item {
      id: row
      width: 1
      height: 1

      readonly property int  rowIndex:   index
      readonly property bool isTrack:    model.dataType == BrowserDataType.Track
      readonly property bool isFolder:   model.dataType == BrowserDataType.Folder
      readonly property var  nodeName:   model.nodeName
      readonly property var  trackName:  model.trackName
      readonly property var  artistName: model.artistName
      readonly property var  bpm:        model.bpm
      readonly property var  keyText:    model.key
      readonly property var  keyIndex:   model.keyIndex
      readonly property var  rating:     model.rating
      readonly property var  prepared:   model.prepared
      readonly property var  prevPlayed: model.prevPlayed
      readonly property var  prelisten:  model.prelisten
      readonly property var  loadedIn:   model.loadedInDeck

      Component.onCompleted: pad.rowCache[index] = row
      Component.onDestruction: if (pad.rowCache[rowIndex] === row) delete pad.rowCache[rowIndex]
    }
  }

  // --- publishing -----------------------------------------------------------

  function describe(row) {
    if (!row.isTrack) {
      return { i: row.rowIndex, folder: true, name: String(row.nodeName || "") }
    }
    var decks = []
    var loaded = row.loadedIn || []
    for (var d = 0; d < loaded.length; d++) decks.push(String(loaded[d]))
    return {
      i:        row.rowIndex,
      title:    String(row.trackName || ""),
      artist:   String(row.artistName || ""),
      bpm:      Number(row.bpm) || 0,
      key:      String(row.keyText || ""),
      keyIndex: (typeof row.keyIndex === "number") ? row.keyIndex : -1,
      rating:   Number(row.rating) || 0,
      prepared: !!row.prepared,
      played:   !!row.prevPlayed,
      preview:  !!row.prelisten,
      decks:    decks
    }
  }

  function publish() {
    var count = list.count
    var selected = browser.currentIndex

    // Follow the selection when it is moved from Traktor or a controller.
    if (selected !== lastSelected) {
      lastSelected = selected
      if (selected >= 0 && (selected < windowStart || selected >= windowStart + windowSize)) {
        windowStart = Math.max(0, selected - Math.floor(windowSize / 4))
      }
    }
    windowStart = Math.max(0, Math.min(windowStart, Math.max(0, count - 1)))
    if (list.contentY !== windowStart) list.contentY = windowStart

    var rows = []
    var end = Math.min(count, windowStart + windowSize)
    for (var i = windowStart; i < end; i++) {
      var row = rowCache[i]
      if (row) rows.push(describe(row))
    }

    var listState = {
      path:      String(browser.currentPath || ""),
      count:     count,
      selected:  selected,
      content:   !!browser.isContentList,
      sort:      browser.sorting,
      sortDir:   browser.sortingDirection,
      start:     windowStart,
      rows:      rows
    }
    var serialized = JSON.stringify(listState)
    if (serialized !== lastList) {
      lastList = serialized
      link.push("browser", "list", listState)
    }

    var preview = {
      loaded:  !!previewIsLoaded.value,
      elapsed: Number(previewElapsed.value) || 0,
      length:  Number(previewLength.value) || 0
    }
    serialized = JSON.stringify(preview)
    if (serialized !== lastPreview) {
      lastPreview = serialized
      link.push("browser", "preview", preview)
    }
  }

  Timer {
    interval: 150
    repeat: true
    running: link.owner
    onTriggered: pad.publish()
  }

  // --- commands -------------------------------------------------------------

  function valid(index) {
    return typeof index === "number" && index >= 0 && index < list.count
  }

  // Select a row first, act on it a moment later: Traktor needs the selection
  // to have settled before "load selected" style actions see it.
  function selectThen(index, action) {
    if (!valid(index)) return
    browser.changeCurrentIndex(index)
    pendingIndex = index
    pendingAction = action
    afterSelect.restart()
  }

  Timer {
    id: afterSelect
    interval: 60
    onTriggered: {
      var action = pad.pendingAction
      pad.pendingAction = ""
      if (browser.currentIndex !== pad.pendingIndex) return   // selection moved on
      if (action === "preview") previewLoad.value = true
      else if (action === "prep") prepAppend.value = true
      pad.publish()
    }
  }

  function run(cmd) {
    switch (cmd.action) {
      case "window":
        windowStart = Math.max(0, Math.round(cmd.value))
        break

      case "select":
        if (valid(cmd.index)) browser.changeCurrentIndex(cmd.index)
        break

      case "open":
      case "load":
        // enterNode() is what the S8's browse encoder push does: it opens the
        // folder under the cursor, or loads the track into the given deck
        // (0-based). It returns true when it moved into a folder.
        if (!valid(cmd.index)) break
        browser.changeCurrentIndex(cmd.index)
        var deckId = (cmd.action === "load") ? cmd.deck - 1 : 0
        if (browser.enterNode(deckId, cmd.index)) {
          browser.relocateCurrentIndex()
          windowStart = 0
          lastSelected = -1
        }
        break

      case "back":
        browser.exitNode()
        lastSelected = -1
        break

      case "sort":
        if (browser.sorting === cmd.value) {
          browser.sortingDirection = (browser.sortingDirection === 1) ? 0 : 1
        } else {
          browser.sorting = cmd.value
        }
        break

      case "preview":        selectThen(cmd.index, "preview"); break
      case "prep":           selectThen(cmd.index, "prep");    break
      case "preview_toggle": previewToggle.value = true;       break
      case "preview_unload": previewUnload.value = true;       break
      case "preview_seek":
        previewSeek.value = Math.max(0, Math.min(1, Number(cmd.value)))
        break
    }
    publish()
  }
}
