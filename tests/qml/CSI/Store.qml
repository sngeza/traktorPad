// Test double for Traktor's property tree: one shared path -> value map.
pragma Singleton
import QtQuick 2.0

QtObject {
  property var values: ({})
  signal changed(string path)

  function set(path, value) {
    if (values[path] === value) return
    values[path] = value
    changed(path)
  }
  function get(path) { return values[path] }
}
