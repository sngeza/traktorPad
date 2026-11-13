// Loads the real bridge against the CSI test double in ./CSI.
import QtQuick 2.0
import CSI 1.0
import "../../bridge/qml/TraktorPad"

Item {
  id: harness
  property string serverUrl
  property string log: ""

  function seed(text) {
    var values = JSON.parse(text)
    for (var path in values) Store.set(path, values[path])
  }

  Connections {
    target: Store
    function onChanged(path) { harness.log += path + "=" + JSON.stringify(Store.get(path)) + "\n" }
  }

  TraktorPadModule { name: "traktorpad"; baseUrl: harness.serverUrl }
}
