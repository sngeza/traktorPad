import QtQuick 2.0

QtObject {
  enum Type { Boolean, Integer, Float }
  property string path
  property int type
  property var value
  onValueChanged: if (path) Store.set(path, value)
}
