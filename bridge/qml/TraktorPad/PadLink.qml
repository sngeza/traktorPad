// TraktorPad bridge transport.
//
// Traktor's QML runtime has no sockets, only XMLHttpRequest, so:
//   - state goes out as batched POSTs to /bridge/state
//   - commands come in through a long-poll GET on /bridge/poll
//
// Everything talks to the TraktorPad server on this Mac (127.0.0.1); the server
// is what the iPad connects to. Keep this file ES5 - Traktor ships an old Qt.

import QtQuick 2.0

Item {
  id: link

  property string baseUrl: "http://127.0.0.1:8765"

  // -1 asks the server for its current head, so a backlog is never replayed
  // into a running set after Traktor (re)starts.
  property int    lastSeq: -1
  property var    pending: ({})
  property bool   hasPending: false
  property var    pollRequest: null
  property int    retryMs: 1000

  signal command(var cmd)
  signal resyncRequested()

  // --- outbound -----------------------------------------------------------

  // Queue `data` under section/key (e.g. "decks"/"1"). Later pushes for the same
  // key replace earlier ones, so a burst of property changes costs one request.
  function push(section, key, data) {
    if (!pending[section]) pending[section] = {}
    pending[section][String(key)] = data
    hasPending = true
    if (!flushTimer.running) flushTimer.start()
  }

  function flush() {
    if (!hasPending) return
    var body = JSON.stringify({ patch: pending })
    pending = {}
    hasPending = false

    var request = new XMLHttpRequest()
    request.open("POST", baseUrl + "/bridge/state", true)
    request.setRequestHeader("Content-Type", "application/json")
    request.send(body)
  }

  Timer { id: flushTimer; interval: 30; onTriggered: link.flush() }

  // --- inbound ------------------------------------------------------------

  function poll() {
    if (pollRequest) return

    var request = new XMLHttpRequest()
    pollRequest = request

    request.onreadystatechange = function() {
      if (request.readyState !== 4) return
      if (pollRequest !== request) return   // superseded by the watchdog

      pollRequest = null
      watchdog.stop()

      var ok = false
      if (request.status === 200) {
        try {
          handleResponse(JSON.parse(request.responseText))
          ok = true
        } catch (e) {
          console.log("TraktorPad: bad poll response: " + e)
        }
      }

      // Never re-poll from inside the callback; go through the timer.
      repollTimer.interval = ok ? 1 : retryMs
      repollTimer.restart()
    }

    request.open("GET", baseUrl + "/bridge/poll?after=" + lastSeq, true)
    request.send()
    watchdog.restart()
  }

  function handleResponse(response) {
    if (typeof response.seq === "number") lastSeq = response.seq
    if (response.needState) resyncRequested()

    var commands = response.commands || []
    for (var i = 0; i < commands.length; i++) {
      try {
        command(commands[i])
      } catch (e) {
        console.log("TraktorPad: command failed: " + e)
      }
    }
  }

  Timer { id: repollTimer; interval: 1; onTriggered: link.poll() }

  // The server answers every poll within ~15 s. If nothing came back after 30 s
  // the request is stuck: drop it and start over.
  Timer {
    id: watchdog
    interval: 30000
    onTriggered: {
      var stuck = link.pollRequest
      link.pollRequest = null
      if (stuck) { try { stuck.abort() } catch (e) {} }
      link.poll()
    }
  }

  Component.onCompleted: repollTimer.start()
}
