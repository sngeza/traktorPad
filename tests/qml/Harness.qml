// Loads the bridge exactly as the installer lays it out inside Traktor's qml
// folder, against the CSI and Traktor.Gui test doubles in tests/qml.
import QtQuick 2.0
import CSI 1.0
import "CSI/Common/TraktorPad"
import "Screens/TraktorPad" as Screens

Item {
  id: harness
  width: 200
  height: 200
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

  // Two instances, like the two screens of one controller: only one may act.
  Screens.PadBrowser { baseUrl: harness.serverUrl }
  Screens.PadBrowser { baseUrl: harness.serverUrl }
}
