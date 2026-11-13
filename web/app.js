(function () {
  "use strict";

  var LETTERS = ["A", "B", "C", "D"];
  var LOOP_LABELS = ["1/32", "1/16", "1/8", "1/4", "1/2", "1", "2", "4", "8", "16", "32"];
  // Index into Traktor's MoveSize enum -> label (0 is "ultra fine", unused here).
  var JUMP_SIZES = [{ size: 6, label: "1" }, { size: 8, label: "4" }, { size: 9, label: "8" },
                    { size: 10, label: "16" }, { size: 11, label: "32" }];
  // Traktor cue types: cue, fade-in, fade-out, load, grid, loop.
  var CUE_COLORS = ["#39a2ff", "#ff9a2e", "#ff9a2e", "#ffd83a", "#e8edf2", "#57e05a"];
  var ENDING_SECONDS = 30;
  var LOAD_HOLD_MS = 700;

  var key = new URLSearchParams(location.search).get("k") || "";
  var query = key ? "?k=" + encodeURIComponent(key) : "";

  var state = { decks: {}, mixer: {} };
  var bridgeOnline = false;
  var streamOpen = false;
  var locked = false;
  var views = {};

  // ---- preferences (per device; the UI works without them) ----------------

  function loadPref(name, fallback) {
    try { return localStorage.getItem("traktorpad." + name) || fallback; } catch (e) { return fallback; }
  }
  function savePref(name, value) {
    try { localStorage.setItem("traktorpad." + name, value); } catch (e) { /* private mode */ }
  }

  // ---- commands ------------------------------------------------------------

  function send(deck, action, value) {
    if (locked || !bridgeOnline) return;
    var body = { deck: deck, action: action };
    if (value !== undefined) body.value = value;
    fetch("/api/command" + query, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      keepalive: true
    }).catch(function () { /* the status dot already shows a dead link */ });
  }

  // Fire on finger-down, not on click: a click waits for the finger to lift.
  function onPress(el, down, up) {
    var active = null;
    el.addEventListener("pointerdown", function (e) {
      if (active !== null) return;
      e.preventDefault();
      active = e.pointerId;
      try { el.setPointerCapture(e.pointerId); } catch (err) { /* synthetic event */ }
      el.classList.add("pressed");
      down(e);
    });
    function release(e) {
      if (e.pointerId !== active) return;
      active = null;
      el.classList.remove("pressed");
      if (up) up(e);
    }
    el.addEventListener("pointerup", release);
    el.addEventListener("pointercancel", release);
    el.addEventListener("contextmenu", function (e) { e.preventDefault(); });
  }

  // ---- building a deck panel -----------------------------------------------

  function buildDeck(index) {
    var node = document.getElementById("deck-template").content.firstElementChild.cloneNode(true);
    node.dataset.deck = index;
    node.querySelector(".letter").textContent = LETTERS[index - 1];

    var view = {
      index: index,
      root: node,
      title: node.querySelector(".title"),
      artist: node.querySelector(".artist"),
      bpm: node.querySelector(".bpm .num"),
      tempo: node.querySelector(".bpm .sub"),
      key: node.querySelector(".key .num"),
      elapsed: node.querySelector(".elapsed .num"),
      remain: node.querySelector(".remain .num"),
      fill: node.querySelector(".overview .fill"),
      playhead: node.querySelector(".overview .playhead"),
      overview: node.querySelector(".overview"),
      markers: node.querySelector(".markers"),
      loopSize: node.querySelector(".loop-size"),
      jumpValue: node.querySelector(".jump-size-value"),
      load: node.querySelector(".btn.load"),
      loadText: node.querySelector(".load-text"),
      pads: [],
      jump: 1,
      syncedAt: 0,
      cueSignature: ""
    };

    node.querySelectorAll("[data-tap]").forEach(function (button) {
      onPress(button, function () { send(index, button.dataset.tap); });
    });

    node.querySelectorAll("[data-hold]").forEach(function (button) {
      onPress(button,
        function () { send(index, button.dataset.hold, true); },
        function () { send(index, button.dataset.hold, false); });
    });

    node.querySelectorAll("[data-loop-step]").forEach(function (button) {
      onPress(button, function () {
        var deck = state.decks[index] || {};
        var current = typeof deck.loopSize === "number" ? deck.loopSize : 7;
        var next = Math.max(0, Math.min(LOOP_LABELS.length - 1, current + Number(button.dataset.loopStep)));
        if (next !== current) send(index, "loop_size", next);
      });
    });

    node.querySelectorAll("[data-jump]").forEach(function (button) {
      onPress(button, function () {
        send(index, "jump", { size: JUMP_SIZES[view.jump].size, dir: Number(button.dataset.jump) });
      });
    });

    onPress(node.querySelector("[data-jump-cycle]"), function () {
      view.jump = (view.jump + 1) % JUMP_SIZES.length;
      view.jumpValue.textContent = JUMP_SIZES[view.jump].label;
    });
    view.jumpValue.textContent = JUMP_SIZES[view.jump].label;

    // Loading replaces the track, so it takes a deliberate hold and is refused
    // outright while the deck is audible.
    var loadTimer = null;
    onPress(view.load, function () {
      if (view.load.classList.contains("blocked") || locked || !bridgeOnline) return;
      view.load.classList.add("arming");
      loadTimer = setTimeout(function () {
        loadTimer = null;
        view.load.classList.remove("arming");
        send(index, "load");
      }, LOAD_HOLD_MS);
    }, function () {
      clearTimeout(loadTimer);
      loadTimer = null;
      view.load.classList.remove("arming");
    });

    var padGrid = node.querySelector(".pads");
    for (var i = 1; i <= 8; i++) {
      (function (n) {
        var pad = document.createElement("button");
        pad.type = "button";
        pad.className = "btn pad";
        pad.innerHTML = '<span class="n"></span><span class="name"></span><span class="pos"></span>';
        pad.querySelector(".n").textContent = n;
        onPress(pad,
          function () { send(index, "hotcue", { index: n, down: true }); },
          function () { send(index, "hotcue", { index: n, down: false }); });
        padGrid.appendChild(pad);
        view.pads.push({
          root: pad,
          name: pad.querySelector(".name"),
          pos: pad.querySelector(".pos")
        });
      })(i);
    }

    return view;
  }

  // ---- rendering -------------------------------------------------------------

  function formatTime(seconds) {
    if (!(seconds >= 0)) seconds = 0;
    var whole = Math.floor(seconds);
    var m = Math.floor(whole / 60);
    var s = whole % 60;
    return m + ":" + (s < 10 ? "0" : "") + s;
  }

  function effectiveBpm(deck) {
    var base = Number(deck.baseBpm) || 0;
    var tempo = Number(deck.tempo);
    // tempo is a playback-rate factor around 1.0; ignore anything implausible.
    return (tempo > 0.2 && tempo < 5) ? base * tempo : base;
  }

  function levelOf(index) {
    var levels = state.mixer.levels || {};
    return Number(levels[index]) || 0;
  }

  function renderDeck(view) {
    var deck = state.decks[view.index] || {};
    var loaded = !!deck.loaded;
    var root = view.root;
    var live = loaded && !!deck.playing && levelOf(view.index) > 0.02;
    var master = (state.mixer.master || {}).masterDeck === view.index;

    root.classList.toggle("empty", !loaded);
    root.classList.toggle("playing", loaded && !!deck.playing);
    root.classList.toggle("synced", !!deck.sync);
    root.classList.toggle("looping", !!deck.loopActive);
    root.classList.toggle("flux-on", !!deck.flux);
    root.classList.toggle("keylock-on", !!deck.keyLock);
    root.classList.toggle("live", live);
    root.classList.toggle("is-master", master);

    view.title.textContent = loaded ? (deck.title || "Untitled") : "No track loaded";
    view.artist.textContent = loaded ? (deck.artist || "") : "";

    var bpm = effectiveBpm(deck);
    view.bpm.textContent = loaded && bpm ? bpm.toFixed(1) : "--.-";
    var tempo = Number(deck.tempo);
    var percent = (tempo > 0.2 && tempo < 5) ? (tempo - 1) * 100 : 0;
    view.tempo.textContent = loaded ? "BPM  " + (percent >= 0 ? "+" : "") + percent.toFixed(1) + "%" : "BPM";
    view.key.textContent = loaded ? (deck.key || deck.legacyKey || "-") : "-";

    var loop = typeof deck.loopSize === "number" ? deck.loopSize : 7;
    view.loopSize.textContent = LOOP_LABELS[loop] || "4";

    view.load.classList.toggle("blocked", live);
    view.loadText.textContent = live ? "Deck is live" : "Hold to load";

    renderCues(view, deck, loaded);

    view.syncedAt = performance.now();
    renderPosition(view, view.syncedAt);
  }

  function renderCues(view, deck, loaded) {
    var cues = loaded && Array.isArray(deck.cues) ? deck.cues : [];
    var length = Number(deck.length) || 0;
    var signature = JSON.stringify(cues) + "|" + length;
    if (signature === view.cueSignature) return;
    view.cueSignature = signature;

    view.markers.textContent = "";
    view.pads.forEach(function (pad, i) {
      var cue = cues[i];
      var color = cue ? (CUE_COLORS[cue.type] || CUE_COLORS[0]) : "";
      pad.root.classList.toggle("set", !!cue);
      pad.root.style.setProperty("--c", color || "var(--faint)");
      pad.name.textContent = cue ? (cue.name && cue.name !== "n.n." ? cue.name : "Cue " + (i + 1)) : "Empty";
      pad.pos.textContent = cue ? formatTime(cue.pos) : "";

      if (cue && length > 0 && cue.pos >= 0 && cue.pos <= length) {
        var marker = document.createElement("div");
        marker.className = "marker";
        marker.dataset.n = i + 1;
        marker.style.setProperty("--c", color);
        marker.style.left = (cue.pos / length * 100) + "%";
        view.markers.appendChild(marker);
      }
    });
  }

  // Between snapshots (4 per second) the playhead is extrapolated locally.
  function renderPosition(view, now) {
    var deck = state.decks[view.index] || {};
    var length = Number(deck.length) || 0;
    var elapsed = Number(deck.elapsed) || 0;
    var tempo = Number(deck.tempo);
    if (!(tempo > 0.2 && tempo < 5)) tempo = 1;

    if (deck.loaded && deck.playing) {
      elapsed += Math.min((now - view.syncedAt) / 1000, 1) * tempo;
    }
    if (length > 0) elapsed = Math.min(elapsed, length);

    var remaining = Math.max(length - elapsed, 0);
    var fraction = length > 0 ? elapsed / length : 0;

    view.elapsed.textContent = deck.loaded ? formatTime(elapsed) : "-:--";
    view.remain.textContent = deck.loaded ? "-" + formatTime(remaining) : "-:--";
    view.fill.style.transform = "scaleX(" + fraction + ")";
    view.playhead.style.transform = "translateX(" + (fraction * view.overview.clientWidth) + "px)";
    view.root.classList.toggle("ending",
      !!deck.loaded && !!deck.playing && length > 0 && remaining < ENDING_SECONDS);
  }

  function renderMaster() {
    var master = state.mixer.master || {};
    var bpm = Number(master.bpm);
    document.getElementById("master-bpm").textContent = bpm > 0 ? bpm.toFixed(1) : "--.-";
  }

  function renderAll() {
    Object.keys(views).forEach(function (i) { renderDeck(views[i]); });
    renderMaster();
  }

  function renderStatus() {
    var el = document.getElementById("status");
    var text = document.getElementById("status-text");
    if (!streamOpen) {
      el.dataset.state = "offline";
      text.textContent = "No connection to the Mac";
    } else if (!bridgeOnline) {
      el.dataset.state = "waiting";
      text.textContent = "Waiting for Traktor";
    } else {
      el.dataset.state = "online";
      text.textContent = "Traktor connected";
    }
    document.body.classList.toggle("offline", !streamOpen || !bridgeOnline);
  }

  function tick(now) {
    Object.keys(views).forEach(function (i) {
      var deck = state.decks[i];
      if (deck && deck.loaded && deck.playing) renderPosition(views[i], now);
    });
    requestAnimationFrame(tick);
  }

  // ---- live updates ----------------------------------------------------------

  function applyPatch(patch) {
    Object.keys(patch).forEach(function (section) {
      if (!state[section]) state[section] = {};
      Object.keys(patch[section]).forEach(function (name) {
        state[section][name] = patch[section][name];
      });
    });
    if (patch.mixer) {
      renderAll();
    } else if (patch.decks) {
      Object.keys(patch.decks).forEach(function (i) { if (views[i]) renderDeck(views[i]); });
    }
  }

  function connect() {
    var stream = new EventSource("/events" + query);

    stream.addEventListener("open", function () {
      streamOpen = true;
      renderStatus();
    });

    stream.addEventListener("hello", function (event) {
      var hello = JSON.parse(event.data);
      state = { decks: {}, mixer: {} };
      bridgeOnline = !!(hello.bridge && hello.bridge.online);
      applyPatch(hello.state || {});
      renderAll();
      renderStatus();
    });

    stream.addEventListener("patch", function (event) {
      applyPatch(JSON.parse(event.data));
    });

    stream.addEventListener("bridge", function (event) {
      bridgeOnline = !!JSON.parse(event.data).online;
      renderStatus();
    });

    stream.addEventListener("error", function () {
      streamOpen = false;
      renderStatus();
      // EventSource retries by itself unless the server refused it outright.
      if (stream.readyState === EventSource.CLOSED) {
        setTimeout(connect, 2000);
      }
    });
  }

  // ---- top bar -----------------------------------------------------------------

  function setLayout(layout) {
    document.getElementById("decks").dataset.layout = layout;
    document.querySelectorAll("[data-layout]").forEach(function (button) {
      if (button.tagName === "BUTTON") {
        button.setAttribute("aria-pressed", String(button.dataset.layout === layout));
      }
    });
    savePref("layout", layout);
    renderAll();
  }

  function setLocked(value) {
    locked = value;
    document.body.classList.toggle("locked", locked);
    document.getElementById("lock").setAttribute("aria-pressed", String(locked));
    document.getElementById("lock-text").textContent = locked ? "Touch locked" : "Touch on";
    savePref("locked", locked ? "1" : "0");
  }

  function init() {
    var container = document.getElementById("decks");
    for (var i = 1; i <= 4; i++) {
      views[i] = buildDeck(i);
      container.appendChild(views[i].root);
    }

    document.querySelectorAll("button[data-layout]").forEach(function (button) {
      button.addEventListener("click", function () { setLayout(button.dataset.layout); });
    });
    document.getElementById("lock").addEventListener("click", function () { setLocked(!locked); });

    setLayout(loadPref("layout", "2"));
    setLocked(loadPref("locked", "0") === "1");

    // Keep iPadOS from scrolling, zooming or rubber-banding the page.
    document.addEventListener("touchmove", function (e) { e.preventDefault(); }, { passive: false });
    document.addEventListener("gesturestart", function (e) { e.preventDefault(); });
    window.addEventListener("resize", renderAll);

    renderAll();
    renderStatus();
    connect();
    requestAnimationFrame(tick);
  }

  init();
})();
