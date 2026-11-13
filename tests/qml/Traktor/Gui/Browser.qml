// Test double for Traktor.Gui's Browser: a two-level library with the same
// properties and calls the real one offers to the S8 browser screen.
import QtQuick 2.0
import CSI 1.0

Item {
  id: browser

  property bool   isActive: false
  property alias  dataSet: rows
  property int    currentIndex: 0
  property string currentPath: ""
  property bool   isContentList: false
  property int    sorting: 0
  property int    sortingDirection: 1
  property int    iconId: 0

  ListModel { id: rows }

  function showRoot() {
    rows.clear()
    var names = ["Track Collection", "Playlists"]
    for (var i = 0; i < names.length; i++) {
      rows.append({ dataType: BrowserDataType.Folder, nodeName: names[i], trackName: "",
                    artistName: "", bpm: 0, key: "", keyIndex: -1, rating: 0,
                    prepared: false, prevPlayed: false, prelisten: false, loadedInDeck: [] })
    }
    currentPath = ""
    isContentList = false
    currentIndex = 0
  }

  function showTracks(name, count) {
    rows.clear()
    for (var i = 0; i < count; i++) {
      rows.append({ dataType: BrowserDataType.Track, nodeName: "", trackName: "Track " + i,
                    artistName: "Artist " + (i % 7), bpm: 120 + (i % 10), key: "8m",
                    keyIndex: i % 24, rating: i % 6, prepared: false, prevPlayed: false,
                    prelisten: false, loadedInDeck: [] })
    }
    currentPath = name
    isContentList = true
    currentIndex = 0
  }

  function changeCurrentIndex(index) { currentIndex = index }
  function relocateCurrentIndex() {}

  function enterNode(deckId, index) {
    var row = rows.get(index)
    if (row.dataType === BrowserDataType.Folder) {
      showTracks(row.nodeName, row.nodeName === "Track Collection" ? 400 : 12)
      return true
    }
    Store.set("test.loaded.deck" + (deckId + 1), row.trackName)
    return false
  }

  function exitNode() { showRoot() }

  Component.onCompleted: showRoot()
}
