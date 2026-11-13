(function () {
  "use strict";

  var Wave = window.TraktorPadWave;

  var LETTERS = ["A", "B"];
  var ZOOMS = [4, 8, 12, 20, 32];              // seconds of audio across the waveform
  var PLAYHEAD_AT = 0.3;                        // fraction of the width, from the left
  // Traktor cue types: cue, fade-in, fade-out, load, grid, loop.
  var CUE_COLORS = ["#39a2ff", "#ff9a2e", "#ff9a2e", "#ffd83a", "#e8edf2", "#57e05a"];
  var ENDING_SECONDS = 30;
  var HOLD_TO_SEEK_MS = 500;
  var ROW_HEIGHT = 46;
  var WINDOW_LEAD = 25;                         // rows requested above the first visible one

  var key = new URLSearchParams(location.search).get("k") || "";

  var state = { decks: {}, pos: {}, mixer: {}, browser: {} };
  var bridge = { main: false, browser: false };
  var streamOpen = false;
  var locked = false;
  var zoom = 1;
  var views = {};

  function url(path, params) {
    var query = new URLSearchParams(params || {});
    if (key) query.set("k", key);
    var text = query.toString();
    return text ? path + "?" + text : path;
  }

  // ---- preferences (per device; the UI works without them) ----------------

  function loadPref(name, fallback) {
    try { return localStorage.getItem("traktorpad." + name) || fallback; } catch (e) { return fallback; }
  }
  function savePref(name, value) {
    try { localStorage.setItem("traktorpad." + name, value); } catch (e) { /* private mode */ }
  }

  // ---- commands ------------------------------------------------------------

  function send(body) {
    if (locked) return;
    fetch(url("/api/command"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      keepalive: true
    }).catch(function () { /* the status dot already shows a dead link */ });
  }

  function formatTime(seconds) {
    if (!(seconds >= 0)) seconds = 0;
    var whole = Math.floor(seconds);
    var s = whole % 60;
    return Math.floor(whole / 60) + ":" + (s < 10 ? "0" : "") + s;
  }

  function setText(el, text) {
    if (el.textContent !== text) el.textContent = text;
  }

  function validTempo(deck) {
    var tempo = Number(deck.tempo);
    // tempo is a playback-rate factor around 1.0; ignore anything implausible.
    return (tempo > 0.2 && tempo < 5) ? tempo : 1;
  }

  function isLive(index) {
    var deck = state.decks[index] || {};
    var levels = state.mixer.levels || {};
    return !!deck.loaded && !!deck.playing && (Number(levels[index]) || 0) > 0.02;
  }

  // ---- decks -----------------------------------------------------------------

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
      bpmSub: node.querySelector(".bpm .sub"),
      key: node.querySelector(".key .num"),
      phase: node.querySelector(".phase"),
      phaseFill: node.querySelector(".phase-fill"),
      phaseLabel: node.querySelector(".phase-label"),
      elapsed: node.querySelector(".elapsed .num"),
      remain: node.querySelector(".remain .num"),
      beatNow: node.querySelector(".beat-now"),
      beatLeft: node.querySelector(".beat-left"),
      scroll: node.querySelector(".scroll-canvas"),
      overview: node.querySelector(".overview-canvas"),
      overviewBox: node.querySelector(".overview"),
      note: node.querySelector(".wave-note"),
      wave: null,
      waveFile: null,
      overviewLayer: null,
      dirty: true,
      lastDrawn: -1,
      // playhead estimate: position `base` at time `at`, plus a correction that
      // fades out so server updates never make the waveform jump.
      base: 0, at: 0, correction: 0
    };

    attachSeek(view);
    return view;
  }

  function position(view, now) {
    var deck = state.decks[view.index] || {};
    var elapsed = view.base;
    if (deck.loaded && deck.playing) {
      var dt = Math.min(now - view.at, 1000);
      elapsed += dt / 1000 * validTempo(deck);
      elapsed += view.correction * Math.max(0, 1 - dt / 250);
    }
    var length = Number(deck.length) || 0;
    return length > 0 ? Math.max(0, Math.min(elapsed, length)) : Math.max(0, elapsed);
  }

  function syncPosition(view, elapsed) {
    var now = performance.now();
    var deck = state.decks[view.index] || {};
    var predicted = position(view, now);
    var error = predicted - elapsed;
    view.correction = (deck.playing && Math.abs(error) < 0.15) ? error : 0;
    view.base = elapsed;
    view.at = now;
    view.dirty = true;
  }

  function renderDeck(view) {
    var deck = state.decks[view.index] || {};
    var loaded = !!deck.loaded;
    var root = view.root;
    var master = (state.mixer.master || {}).masterDeck === view.index;

    root.classList.toggle("empty", !loaded);
    root.classList.toggle("playing", loaded && !!deck.playing);
    root.classList.toggle("synced", loaded && !!deck.sync);
    root.classList.toggle("looping", loaded && !!deck.loopActive);
    root.classList.toggle("live", isLive(view.index));
    root.classList.toggle("is-master", loaded && master);

    setText(view.title, loaded ? (deck.title || "Untitled") : "No track loaded");
    setText(view.artist, loaded ? (deck.artist || "") : "");

    var base = Number(deck.baseBpm) || 0;
    var tempo = validTempo(deck);
    var percent = (tempo - 1) * 100;
    setText(view.bpm, loaded && base ? (base * tempo).toFixed(2) : "--.--");
    setText(view.bpmSub, loaded && base
      ? "Orig " + base.toFixed(2) + "  " + (percent >= 0 ? "+" : "") + percent.toFixed(1) + "%"
      : "BPM");
    setText(view.key, loaded ? (deck.key || deck.legacyKey || "-") : "-");

    if (loaded && deck.file) {
      if (deck.file !== view.waveFile) loadWave(view, deck.file);
    } else if (view.waveFile !== null || view.wave) {
      view.waveFile = null;
      view.wave = null;
      view.overviewLayer = null;
      setText(view.note, "");
    }
    view.dirty = true;
  }

  // ---- waveform loading --------------------------------------------------------

  function loadWave(view, file) {
    view.waveFile = file;
    view.wave = null;
    view.overviewLayer = null;
    setText(view.note, "Loading waveform…");
    var params = { deck: view.index, file: file };
    var current = function () { return view.waveFile === file; };

    fetch(url("/api/waveform", params)).then(function (cached) {
      if (cached.ok) {
        return cached.arrayBuffer().then(function (buffer) { return Wave.unpack(buffer); });
      }
      return null;
    }).then(function (wave) {
      if (wave || !current()) return wave;
      setText(view.note, "Analysing track…");
      return fetch(url("/api/audio", params)).then(function (audio) {
        if (!audio.ok) throw new Error("unreadable");
        return audio.arrayBuffer();
      }).then(function (buffer) {
        return Wave.analyze(buffer);
      }).then(function (fresh) {
        // Cache on the Mac so the next load of this track is instant.
        fetch(url("/api/waveform", params), { method: "PUT", body: Wave.pack(fresh) })
          .catch(function () {});
        return fresh;
      });
    }).then(function (wave) {
      if (!current()) return;
      view.wave = wave;
      view.overviewLayer = null;
      view.dirty = true;
      setText(view.note, "");
    }).catch(function (error) {
      if (!current()) return;
      setText(view.note, error && error.message === "unreadable"
        ? "No waveform: the Mac could not read this file"
        : "No waveform: this file could not be analysed");
    });
  }

  // ---- drawing -----------------------------------------------------------------

  function fit(canvas) {
    var dpr = window.devicePixelRatio || 1;
    var width = Math.round(canvas.clientWidth * dpr);
    var height = Math.round(canvas.clientHeight * dpr);
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
      return true;
    }
    return false;
  }

  function cueList(deck) {
    return Array.isArray(deck.cues) ? deck.cues : [];
  }

  function drawScroll(view, deck, pos) {
    var canvas = view.scroll;
    fit(canvas);
    var ctx = canvas.getContext("2d");
    var W = canvas.width, H = canvas.height;
    var dpr = window.devicePixelRatio || 1;
    ctx.clearRect(0, 0, W, H);
    if (!deck.loaded) return;

    var span = ZOOMS[zoom];
    var pps = W / span;
    var t0 = pos - PLAYHEAD_AT * span;
    var x = function (t) { return (t - t0) * pps; };
    var length = Number(deck.length) || 0;

    // Outside the track.
    ctx.fillStyle = "#06080a";
    if (t0 < 0) ctx.fillRect(0, 0, x(0), H);
    if (length && x(length) < W) ctx.fillRect(x(length), 0, W - x(length), H);

    // Active loop.
    if (deck.loopActive && deck.cueLength > 0) {
      ctx.fillStyle = "rgba(87, 224, 90, 0.2)";
      ctx.fillRect(x(deck.cueStart), 0, deck.cueLength * pps, H);
    }

    if (view.wave) Wave.drawBands(ctx, view.wave, t0, t0 + span, W, H, dpr >= 2 ? 2 : 1);

    // Beatgrid: every beat, with bars emphasised.
    var bpm = Number(deck.baseBpm) || 0;
    if (bpm > 0) {
      var beat = 60 / bpm;
      var offset = (Number(deck.gridOffset) || 0) / 1000;
      if (beat * pps > 5 * dpr) {
        var n = Math.ceil((Math.max(t0, 0) - offset) / beat);
        for (var t = offset + n * beat; t < t0 + span && (!length || t <= length); n++, t = offset + n * beat) {
          var bar = ((n % 4) + 4) % 4 === 0;
          ctx.fillStyle = bar ? "rgba(255,255,255,0.55)" : "rgba(255,255,255,0.18)";
          ctx.fillRect(Math.round(x(t)), 0, bar ? Math.max(1, dpr) : 1, H);
        }
      }
    }

    // Hotcues.
    ctx.font = "700 " + Math.round(11 * dpr) + "px -apple-system, sans-serif";
    ctx.textBaseline = "top";
    cueList(deck).forEach(function (cue, i) {
      if (!cue) return;
      var cx = x(cue.pos);
      if (cx < -40 * dpr || cx > W + 40 * dpr) return;
      var color = CUE_COLORS[cue.type] || CUE_COLORS[0];
      if (cue.length > 0) {
        ctx.fillStyle = "rgba(87, 224, 90, 0.12)";
        ctx.fillRect(cx, 0, cue.length * pps, H);
      }
      ctx.fillStyle = color;
      ctx.fillRect(Math.round(cx) - dpr, 0, 2 * dpr, H);
      var label = String(i + 1) + (cue.name && cue.name !== "n.n." ? " " + cue.name : "");
      var w = ctx.measureText(label).width + 8 * dpr;
      ctx.fillRect(Math.round(cx), 0, w, 15 * dpr);
      ctx.fillStyle = "#0a0c0f";
      ctx.fillText(label, Math.round(cx) + 4 * dpr, 2 * dpr);
    });

    // Playhead.
    var px = Math.round(PLAYHEAD_AT * W);
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(px - dpr, 0, 2 * dpr, H);
  }

  function buildOverviewLayer(view, deck, W, H) {
    var layer = document.createElement("canvas");
    layer.width = W;
    layer.height = H;
    var ctx = layer.getContext("2d");
    var length = Number(deck.length) || (view.wave ? Wave.duration(view.wave) : 0);
    if (view.wave && length > 0) Wave.drawBands(ctx, view.wave, 0, length, W, H, 1);
    return layer;
  }

  function drawOverview(view, deck, pos) {
    var canvas = view.overview;
    if (fit(canvas)) view.overviewLayer = null;
    var ctx = canvas.getContext("2d");
    var W = canvas.width, H = canvas.height;
    var dpr = window.devicePixelRatio || 1;
    ctx.clearRect(0, 0, W, H);
    var length = Number(deck.length) || 0;
    if (!deck.loaded || length <= 0) return;

    if (!view.overviewLayer) view.overviewLayer = buildOverviewLayer(view, deck, W, H);
    ctx.drawImage(view.overviewLayer, 0, 0);

    var px = pos / length * W;
    ctx.fillStyle = "rgba(10, 12, 15, 0.62)";       // dim what has been played
    ctx.fillRect(0, 0, px, H);

    if (deck.loopActive && deck.cueLength > 0) {
      ctx.fillStyle = "rgba(87, 224, 90, 0.45)";
      ctx.fillRect(deck.cueStart / length * W, 0, Math.max(2 * dpr, deck.cueLength / length * W), H);
    }

    ctx.font = "700 " + Math.round(10 * dpr) + "px -apple-system, sans-serif";
    ctx.textBaseline = "top";
    cueList(deck).forEach(function (cue, i) {
      if (!cue || cue.pos < 0 || cue.pos > length) return;
      var cx = Math.round(cue.pos / length * W);
      ctx.fillStyle = CUE_COLORS[cue.type] || CUE_COLORS[0];
      ctx.fillRect(cx - dpr, 0, 2 * dpr, H);
      ctx.fillText(String(i + 1), cx + 3 * dpr, 2 * dpr);
    });

    ctx.fillStyle = "#ffffff";
    ctx.fillRect(Math.round(px) - dpr, 0, 2 * dpr, H);
  }

  function renderTimes(view, deck, pos) {
    var loaded = !!deck.loaded;
    var length = Number(deck.length) || 0;
    var remaining = Math.max(length - pos, 0);

    setText(view.elapsed, loaded ? formatTime(pos) : "-:--");
    setText(view.remain, loaded ? "-" + formatTime(remaining) : "-:--");

    var bpm = Number(deck.baseBpm) || 0;
    if (loaded && bpm > 0) {
      var beats = (pos - (Number(deck.gridOffset) || 0) / 1000) * bpm / 60;
      var whole = Math.floor(beats + 1e-6);
      var barNumber = Math.floor(whole / 4) + 1;
      var beatNumber = ((whole % 4) + 4) % 4 + 1;
      setText(view.beatNow, (beats < 0 ? "-" : "") + (beats < 0 ? 0 : barNumber) + "." + beatNumber);
      setText(view.beatLeft, String(Math.ceil(remaining * bpm / 60 / 4)));
    } else {
      setText(view.beatNow, "-.-");
      setText(view.beatLeft, "-");
    }

    view.root.classList.toggle("ending",
      loaded && !!deck.playing && length > 0 && remaining < ENDING_SECONDS);
  }

  // Traktor reports phase as -0.5 .. +0.5 of a beat against the master.
  function renderPhase(view, deck) {
    var master = (state.mixer.master || {}).masterDeck === view.index;
    var raw = Number((state.pos[view.index] || {}).p);
    var idle = !deck.loaded || master || !isFinite(raw);
    view.phase.classList.toggle("idle", idle);
    setText(view.phaseLabel, master && deck.loaded ? "Master" : "Phase");
    if (idle) return;
    var phase = Math.max(-1, Math.min(1, raw * 2));
    var half = Math.abs(phase) * 50;
    view.phaseFill.style.width = half + "%";
    view.phaseFill.style.left = (phase < 0 ? 50 - half : 50) + "%";
    view.phase.classList.toggle("locked", Math.abs(phase) < 0.04);
  }

  function frame(now) {
    for (var i = 1; i <= 2; i++) {
      var view = views[i];
      var deck = state.decks[i] || {};
      var moving = deck.loaded && deck.playing;
      if (!moving && !view.dirty) continue;
      var pos = position(view, now);
      drawScroll(view, deck, pos);
      drawOverview(view, deck, pos);
      renderTimes(view, deck, pos);
      renderPhase(view, deck);
      view.dirty = false;
    }
    requestAnimationFrame(frame);
  }

  // ---- tap to seek ---------------------------------------------------------------
  //
  // A stopped or inaudible deck seeks at once and can be scrubbed. A deck that
  // is playing to the audience needs a half-second hold, so a stray touch does
  // not throw the mix.

  function attachSeek(view) {
    var box = view.overviewBox;
    var active = null;
    var armTimer = null;
    var lastSent = 0;

    function fraction(event) {
      var rect = box.getBoundingClientRect();
      return Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
    }
    function seek(event) {
      var deck = state.decks[view.index] || {};
      var value = fraction(event);
      send({ action: "seek", deck: view.index, value: value });
      if (!deck.playing) syncPosition(view, value * (Number(deck.length) || 0));
      lastSent = performance.now();
    }
    function cancel() {
      clearTimeout(armTimer);
      armTimer = null;
      box.classList.remove("arming");
    }

    box.addEventListener("pointerdown", function (event) {
      var deck = state.decks[view.index] || {};
      if (active !== null || locked || !deck.loaded || !bridge.main) return;
      event.preventDefault();
      active = event.pointerId;
      try { box.setPointerCapture(event.pointerId); } catch (e) { /* synthetic event */ }
      if (isLive(view.index)) {
        box.classList.add("arming");
        armTimer = setTimeout(function () { cancel(); seek(event); }, HOLD_TO_SEEK_MS);
      } else {
        seek(event);
      }
    });
    box.addEventListener("pointermove", function (event) {
      if (event.pointerId !== active || armTimer || isLive(view.index)) return;
      if (performance.now() - lastSent > 80) seek(event);
    });
    function release(event) {
      if (event.pointerId !== active) return;
      active = null;
      cancel();
    }
    box.addEventListener("pointerup", release);
    box.addEventListener("pointercancel", release);
  }

  // ---- browser ---------------------------------------------------------------------

  var listEl, rowsEl, spaceEl;
  var rowCache = {};
  var listId = "";
  var lastSelected = -1;
  var localSelected = -1;
  var lastTouch = 0;
  var lastWindowRequest = { start: -1, at: 0 };
  var renderQueued = false;

  function browserList() { return state.browser.list || {}; }

  // Keys are compatible when they sit next to each other on the circle of
  // fifths, or are relative major/minor. Traktor's key index is chromatic:
  // 0-11 major from C, 12-23 minor from C.
  function wheel(index) {
    if (!(index >= 0 && index < 24)) return null;
    var minor = index >= 12;
    var pitch = index % 12;
    return { minor: minor, slot: ((minor ? pitch + 3 : pitch) * 7) % 12 };
  }
  function compatible(a, b) {
    var x = wheel(a), y = wheel(b);
    if (!x || !y) return false;
    var distance = Math.min((x.slot - y.slot + 12) % 12, (y.slot - x.slot + 12) % 12);
    return x.minor === y.minor ? distance <= 1 : distance === 0;
  }
  function referenceKey() {
    var master = (state.mixer.master || {}).masterDeck;
    var order = [master, 1, 2];
    for (var i = 0; i < order.length; i++) {
      var deck = state.decks[order[i]];
      if (deck && deck.loaded && (order[i] === master || deck.playing)) {
        return deck.keyNow >= 0 ? deck.keyNow : deck.keyIndex;
      }
    }
    return -1;
  }

  function stars(rating) {
    var n = Number(rating) || 0;
    if (n > 5) n = Math.round(n / 51);          // some versions report 0-255
    return n > 0 ? "★★★★★".slice(0, n) : "";
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function buildRow(index, row, selected, refKey) {
    var node = el("div", "row");
    node.dataset.i = index;
    node.style.top = index * ROW_HEIGHT + "px";
    if (selected) node.classList.add("selected");

    var decks = el("div", "d");
    if (!row) {
      node.classList.add("pending");
      node.appendChild(decks);
      node.appendChild(el("div", "t", "…"));
      return node;
    }
    if (row.folder) {
      node.classList.add("folder");
      node.appendChild(decks);
      node.appendChild(el("div", "t", row.name));
      return node;
    }
    if (row.played) node.classList.add("played");
    (row.decks || []).forEach(function (letter) {
      if (letter === "A" || letter === "B") decks.appendChild(el("i", letter === "B" ? "b" : "", letter));
    });
    if (row.preview) decks.appendChild(el("i", "p", "♪"));
    if (row.prepared && !decks.firstChild) decks.appendChild(el("i", "p", "+"));
    node.appendChild(decks);
    node.appendChild(el("div", "t", row.title || "Untitled"));
    node.appendChild(el("div", "a", row.artist || ""));
    node.appendChild(el("div", "n", row.bpm ? row.bpm.toFixed(1) : ""));
    var keyText = (row.key && row.key.toLowerCase() !== "none") ? row.key : "";
    node.appendChild(el("div", "n k" + (keyText && compatible(row.keyIndex, refKey) ? " match" : ""), keyText));
    node.appendChild(el("div", "n r", stars(row.rating)));
    return node;
  }

  function selectedIndex() {
    return localSelected >= 0 ? localSelected : (browserList().selected >= 0 ? browserList().selected : -1);
  }

  function renderRows() {
    renderQueued = false;
    var list = browserList();
    var count = list.count || 0;
    var first = Math.max(0, Math.floor(listEl.scrollTop / ROW_HEIGHT) - 3);
    var last = Math.min(count, first + Math.ceil(listEl.clientHeight / ROW_HEIGHT) + 6);
    var selected = selectedIndex();
    var refKey = referenceKey();
    var missing = false;

    var fragment = document.createDocumentFragment();
    for (var i = first; i < last; i++) {
      if (!rowCache[i]) missing = true;
      fragment.appendChild(buildRow(i, rowCache[i], i === selected, refKey));
    }
    rowsEl.textContent = "";
    rowsEl.appendChild(fragment);

    if (missing) requestWindow(Math.max(0, first - WINDOW_LEAD));
  }

  function queueRows() {
    if (!renderQueued) {
      renderQueued = true;
      requestAnimationFrame(renderRows);
    }
  }

  // Ask Traktor for the slice of the list that is on screen.
  function requestWindow(start) {
    var now = performance.now();
    if (!bridge.browser) return;
    if (start === lastWindowRequest.start && now - lastWindowRequest.at < 600) return;
    if (now - lastWindowRequest.at < 100) { setTimeout(queueRows, 110); return; }
    lastWindowRequest = { start: start, at: now };
    send({ action: "window", value: start });
    setTimeout(queueRows, 700);                 // retry if rows still have not come
  }

  function selectedRow() {
    var index = selectedIndex();
    return index >= 0 ? rowCache[index] : null;
  }

  function renderBrowser() {
    var list = browserList();
    var preview = state.browser.preview || {};
    var count = list.count || 0;
    var id = [list.path, count, list.sort, list.sortDir].join("|");
    var pathChanged = false;

    if (id !== listId) {
      pathChanged = id.split("|")[0] !== listId.split("|")[0];
      listId = id;
      rowCache = {};
      localSelected = -1;
    }
    (list.rows || []).forEach(function (row) { rowCache[row.i] = row; });
    if (localSelected >= 0 && list.selected === localSelected) localSelected = -1;

    spaceEl.style.height = count * ROW_HEIGHT + "px";

    // Follow the selection when it is moved from Traktor or a controller, but
    // do not fight a finger that is scrolling.
    if (list.selected !== lastSelected || pathChanged) {
      lastSelected = list.selected;
      if (pathChanged || performance.now() - lastTouch > 1500) {
        var top = list.selected * ROW_HEIGHT;
        var visibleTop = listEl.scrollTop;
        var visibleBottom = visibleTop + listEl.clientHeight - ROW_HEIGHT;
        if (pathChanged || top < visibleTop || top > visibleBottom) {
          listEl.scrollTop = Math.max(0, top - listEl.clientHeight / 2 + ROW_HEIGHT / 2);
        }
      }
    }

    var parts = (list.path || "").split(" | ").filter(Boolean);
    var crumbs = document.getElementById("crumbs");
    crumbs.textContent = "";
    if (!parts.length) crumbs.appendChild(el("span", "", "Library"));
    parts.forEach(function (part, i) {
      if (i) crumbs.appendChild(el("span", "sep", "›"));
      crumbs.appendChild(el("span", i < parts.length - 1 ? "parent" : "", part));
    });
    setText(document.getElementById("count"), count ? count + (list.content ? " tracks" : " items") : "");
    document.getElementById("back").disabled = !parts.length;
    document.getElementById("browser").classList.toggle("folders", !list.content);

    document.querySelectorAll(".col").forEach(function (col) {
      var sorted = list.content && Number(col.dataset.sort) === list.sort && list.sort > 0;
      col.classList.toggle("sorted", !!sorted);
      col.classList.toggle("desc", !!sorted && list.sortDir !== 1);
    });

    var row = selectedRow();
    var isTrack = !!row && !row.folder;
    document.querySelectorAll("[data-load]").forEach(function (button) {
      var live = isLive(Number(button.dataset.load));
      button.disabled = !isTrack || live;
      button.classList.toggle("live", live);
      button.querySelector("span").textContent = live ? "Live" : "Load";
    });
    document.getElementById("preview").disabled = !isTrack;
    document.getElementById("prep").disabled = !isTrack;

    var strip = document.getElementById("preview-strip");
    strip.hidden = !preview.loaded;
    if (preview.loaded) {
      var length = Number(preview.length) || 0;
      var elapsed = Number(preview.elapsed) || 0;
      document.getElementById("preview-fill").style.transform =
        "scaleX(" + (length > 0 ? Math.min(1, elapsed / length) : 0) + ")";
      setText(document.getElementById("preview-time"), formatTime(elapsed) + " / " + formatTime(length));
      document.querySelectorAll("[data-from-preview]").forEach(function (button) {
        var live = isLive(Number(button.dataset.fromPreview));
        button.disabled = live;
        button.querySelector("span").textContent = live ? "Live" : "Load";
      });
    }

    var note = document.getElementById("browser-note");
    note.hidden = bridge.browser;
    if (!bridge.browser) {
      note.textContent = bridge.main
        ? "Decks are connected, but Traktor's browser is not. It needs a controller with a display: in Traktor, add Kontrol D2 under Preferences › Controller Manager › Add… › Traktor, then restart Traktor."
        : "Waiting for Traktor…";
    }

    queueRows();
  }

  function initBrowser() {
    listEl = document.getElementById("list");
    rowsEl = document.getElementById("list-rows");
    spaceEl = document.getElementById("list-space");

    listEl.addEventListener("scroll", queueRows, { passive: true });
    listEl.addEventListener("touchstart", function () { lastTouch = performance.now(); }, { passive: true });
    listEl.addEventListener("touchmove", function () { lastTouch = performance.now(); }, { passive: true });
    listEl.addEventListener("wheel", function () { lastTouch = performance.now(); }, { passive: true });

    rowsEl.addEventListener("click", function (event) {
      var node = event.target.closest(".row");
      if (!node || locked) return;
      var index = Number(node.dataset.i);
      var row = rowCache[index];
      lastTouch = performance.now();
      if (row && row.folder) {
        send({ action: "open", index: index });
      } else {
        localSelected = index;            // highlight now, Traktor confirms a moment later
        send({ action: "select", index: index });
        renderBrowser();
      }
    });

    document.getElementById("back").addEventListener("click", function () { send({ action: "back" }); });
    document.querySelectorAll("[data-load]").forEach(function (button) {
      button.addEventListener("click", function () {
        var index = selectedIndex();
        if (index >= 0) send({ action: "load", index: index, deck: Number(button.dataset.load) });
      });
    });
    document.getElementById("preview").addEventListener("click", function () {
      var index = selectedIndex();
      if (index >= 0) send({ action: "preview", index: index });
    });
    document.getElementById("prep").addEventListener("click", function () {
      var index = selectedIndex();
      if (index >= 0) send({ action: "prep", index: index });
    });
    document.querySelectorAll(".col").forEach(function (col) {
      col.addEventListener("click", function () {
        if (browserList().content) send({ action: "sort", value: Number(col.dataset.sort) });
      });
    });

    document.getElementById("preview-toggle").addEventListener("click", function () { send({ action: "preview_toggle" }); });
    document.getElementById("preview-close").addEventListener("click", function () { send({ action: "preview_unload" }); });
    document.querySelectorAll("[data-from-preview]").forEach(function (button) {
      button.addEventListener("click", function () {
        send({ action: "load_preview", deck: Number(button.dataset.fromPreview) });
      });
    });
    var bar = document.getElementById("preview-bar");
    bar.addEventListener("pointerdown", function (event) {
      var rect = bar.getBoundingClientRect();
      send({ action: "preview_seek", value: Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)) });
    });
  }

  // ---- status, top bar ----------------------------------------------------------------

  function renderMaster() {
    var bpm = Number((state.mixer.master || {}).bpm);
    setText(document.getElementById("master-bpm"), bpm > 0 ? bpm.toFixed(2) : "--.--");
  }

  function renderStatus() {
    var status = document.getElementById("status");
    var text;
    if (!streamOpen) { status.dataset.state = "offline"; text = "No connection to the Mac"; }
    else if (!bridge.main && !bridge.browser) { status.dataset.state = "waiting"; text = "Waiting for Traktor"; }
    else if (!bridge.browser) { status.dataset.state = "partial"; text = "Decks connected · browser not available"; }
    else if (!bridge.main) { status.dataset.state = "partial"; text = "Browser connected · decks not available"; }
    else { status.dataset.state = "online"; text = "Traktor connected"; }
    setText(document.getElementById("status-text"), text);
    document.body.classList.toggle("no-decks", !streamOpen || !bridge.main);
  }

  function renderAll() {
    views[1] && renderDeck(views[1]);
    views[2] && renderDeck(views[2]);
    renderMaster();
    renderBrowser();
    renderStatus();
  }

  function setZoom(value) {
    zoom = Math.max(0, Math.min(ZOOMS.length - 1, value));
    setText(document.getElementById("zoom-value"), ZOOMS[zoom] + " s");
    savePref("zoom", String(zoom));
    if (views[1]) { views[1].dirty = true; views[2].dirty = true; }
  }

  function setLocked(value) {
    locked = value;
    document.body.classList.toggle("locked", locked);
    document.getElementById("lock").setAttribute("aria-pressed", String(locked));
    setText(document.getElementById("lock-text"), locked ? "Touch locked" : "Touch on");
    savePref("locked", locked ? "1" : "0");
  }

  // ---- live updates ---------------------------------------------------------------------

  function applyPatch(patch) {
    Object.keys(patch).forEach(function (section) {
      if (!state[section]) state[section] = {};
      Object.keys(patch[section]).forEach(function (name) {
        state[section][name] = patch[section][name];
      });
    });

    if (patch.decks) {
      Object.keys(patch.decks).forEach(function (i) { if (views[i]) renderDeck(views[i]); });
    }
    if (patch.pos) {
      Object.keys(patch.pos).forEach(function (i) {
        if (views[i]) syncPosition(views[i], Number(patch.pos[i].e) || 0);
      });
    }
    if (patch.mixer) {
      renderMaster();
      renderDeck(views[1]);
      renderDeck(views[2]);
    }
    if (patch.browser || patch.decks || patch.mixer) renderBrowser();
  }

  function connect() {
    var stream = new EventSource(url("/events"));

    stream.addEventListener("open", function () { streamOpen = true; renderStatus(); });

    stream.addEventListener("hello", function (event) {
      var hello = JSON.parse(event.data);
      state = { decks: {}, pos: {}, mixer: {}, browser: {} };
      bridge = hello.bridge || { main: false, browser: false };
      applyPatch(hello.state || {});
      renderAll();
    });

    stream.addEventListener("patch", function (event) { applyPatch(JSON.parse(event.data)); });

    stream.addEventListener("bridge", function (event) {
      bridge = JSON.parse(event.data);
      renderStatus();
      renderBrowser();
    });

    stream.addEventListener("error", function () {
      streamOpen = false;
      renderStatus();
      // EventSource retries by itself unless the server refused it outright.
      if (stream.readyState === EventSource.CLOSED) setTimeout(connect, 2000);
    });
  }

  function init() {
    var container = document.getElementById("decks");
    for (var i = 1; i <= 2; i++) {
      views[i] = buildDeck(i);
      container.appendChild(views[i].root);
    }
    initBrowser();

    document.getElementById("zoom-in").addEventListener("click", function () { setZoom(zoom - 1); });
    document.getElementById("zoom-out").addEventListener("click", function () { setZoom(zoom + 1); });
    document.getElementById("lock").addEventListener("click", function () { setLocked(!locked); });
    setZoom(Number(loadPref("zoom", "1")));
    setLocked(loadPref("locked", "0") === "1");

    // Keep iPadOS from scrolling, zooming or rubber-banding the page; only the
    // track list scrolls.
    document.addEventListener("touchmove", function (event) {
      if (!event.target.closest || !event.target.closest(".list")) event.preventDefault();
    }, { passive: false });
    document.addEventListener("gesturestart", function (event) { event.preventDefault(); });
    window.addEventListener("resize", function () {
      views[1].dirty = true; views[2].dirty = true;
      queueRows();
    });

    renderAll();
    connect();
    requestAnimationFrame(frame);
  }

  init();
})();
