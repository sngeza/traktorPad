// Waveforms. Traktor cannot export its own, so the iPad analyses the audio file
// of a loaded track itself: three frequency bands, 150 points per second.
(function () {
  "use strict";

  var BINS_PER_SECOND = 150;
  var MAGIC = 0x31575054;            // "TPW1", little endian
  var HEADER = 8;

  var BAND_COLORS = ["#1c56c9", "#38a4ff", "#dff1ff"];   // low, mid, high

  function decode(buffer) {
    var Offline = window.OfflineAudioContext || window.webkitOfflineAudioContext;
    var context;
    // A low sample rate keeps a decoded track small; fall back if it is refused.
    try { context = new Offline(1, 1, 22050); } catch (e) { context = new Offline(1, 1, 44100); }
    return new Promise(function (resolve, reject) {
      var pending = context.decodeAudioData(buffer, resolve, reject);
      if (pending && pending.then) pending.then(resolve, reject);
    });
  }

  // Split into low / mid / high with two one-pole low-pass filters, then keep
  // the peak of each band per bin.
  function analyze(arrayBuffer) {
    return decode(arrayBuffer).then(function (audio) {
      var rate = audio.sampleRate;
      var length = audio.length;
      var left = audio.getChannelData(0);
      var right = audio.numberOfChannels > 1 ? audio.getChannelData(1) : null;

      var bins = Math.ceil(length / rate * BINS_PER_SECOND);
      var raw = new Float32Array(bins * 3);
      var aLow = 1 - Math.exp(-2 * Math.PI * 180 / rate);
      var aMid = 1 - Math.exp(-2 * Math.PI * 2200 / rate);
      var low1 = 0, low2 = 0, mid1 = 0, mid2 = 0;
      var peak = 0;

      for (var i = 0; i < length; i++) {
        var x = right ? (left[i] + right[i]) * 0.5 : left[i];
        low1 += aLow * (x - low1);
        low2 += aLow * (low1 - low2);
        mid1 += aMid * (x - mid1);
        mid2 += aMid * (mid1 - mid2);

        var lo = Math.abs(low2);
        var mi = Math.abs(mid2 - low2);
        var hi = Math.abs(x - mid2);
        var bin = Math.floor(i / rate * BINS_PER_SECOND) * 3;
        if (lo > raw[bin]) raw[bin] = lo;
        if (mi > raw[bin + 1]) raw[bin + 1] = mi;
        if (hi > raw[bin + 2]) raw[bin + 2] = hi;
        var ax = Math.abs(x);
        if (ax > peak) peak = ax;
      }

      var scale = peak > 0 ? 255 / peak : 0;
      var data = new Uint8Array(bins * 3);
      for (var n = 0; n < raw.length; n++) data[n] = Math.min(255, Math.round(raw[n] * scale));
      return { binsPerSecond: BINS_PER_SECOND, data: data };
    });
  }

  function pack(wave) {
    var out = new Uint8Array(HEADER + wave.data.length);
    var view = new DataView(out.buffer);
    view.setUint32(0, MAGIC, true);
    view.setUint16(4, wave.binsPerSecond, true);
    out.set(wave.data, HEADER);
    return out;
  }

  function unpack(buffer) {
    if (buffer.byteLength < HEADER) return null;
    var view = new DataView(buffer);
    if (view.getUint32(0, true) !== MAGIC) return null;
    return { binsPerSecond: view.getUint16(4, true), data: new Uint8Array(buffer, HEADER) };
  }

  // Peak of each band over the bins [from, to).
  function peaks(wave, from, to, out) {
    var data = wave.data;
    var count = data.length / 3;
    var a = Math.max(0, Math.floor(from));
    var b = Math.min(count, Math.max(a + 1, Math.ceil(to)));
    var lo = 0, mi = 0, hi = 0;
    for (var i = a; i < b; i++) {
      var k = i * 3;
      if (data[k] > lo) lo = data[k];
      if (data[k + 1] > mi) mi = data[k + 1];
      if (data[k + 2] > hi) hi = data[k + 2];
    }
    out[0] = lo; out[1] = mi; out[2] = hi;
  }

  // Draw the bands for the time range [t0, t1) across the canvas width.
  // One path per band keeps this to three fills however wide the canvas is.
  function drawBands(ctx, wave, t0, t1, width, height, step) {
    var mid = height / 2;
    var secondsPerPixel = (t1 - t0) / width;
    var columns = Math.ceil(width / step);
    var values = new Float32Array(columns * 3);
    var p = [0, 0, 0];
    for (var c = 0; c < columns; c++) {
      var t = t0 + c * step * secondsPerPixel;
      if (t + step * secondsPerPixel <= 0) continue;
      peaks(wave, t * wave.binsPerSecond, (t + step * secondsPerPixel) * wave.binsPerSecond, p);
      values[c * 3] = p[0]; values[c * 3 + 1] = p[1]; values[c * 3 + 2] = p[2];
    }
    // Gains tuned so that a full-scale kick fills the height and hats stay visible.
    var gains = [1.0, 1.25, 1.6];
    for (var band = 0; band < 3; band++) {
      ctx.fillStyle = BAND_COLORS[band];
      ctx.beginPath();
      for (var col = 0; col < columns; col++) {
        var v = values[col * 3 + band];
        if (!v) continue;
        var h = Math.min(1, Math.sqrt(v / 255) * gains[band] * (band === 0 ? 1 : 0.8)) * mid;
        if (h < 0.5) continue;
        ctx.rect(col * step, mid - h, step, h * 2);
      }
      ctx.fill();
    }
  }

  window.TraktorPadWave = {
    analyze: analyze,
    pack: pack,
    unpack: unpack,
    drawBands: drawBands,
    duration: function (wave) { return wave.data.length / 3 / wave.binsPerSecond; }
  };
})();
