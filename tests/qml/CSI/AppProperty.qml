import QtQuick 2.0

QtObject {
  id: prop
  property string path
  property var value
  property bool syncing: false

  function pull() {
    var stored = Store.get(path)
    if (stored !== undefined && stored !== value) {
      syncing = true
      value = stored
      syncing = false
    }
  }

  onValueChanged: if (!syncing && path) Store.set(path, value)
  onPathChanged: pull()
  Component.onCompleted: pull()

  property Connections watcher: Connections {
    target: Store
    function onChanged(path) { if (path === prop.path) prop.pull() }
  }
}
