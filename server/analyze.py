"""Waveform analysis on the Mac, for when no iPad has analysed a track yet.

A port of analyze() / pack() in web/wave.js: three frequency bands (low, mid,
high), peak per bin, 150 bins per second, stored in the same "TPW1" cache
format so the iPad and the LED panel share one cache.

Audio is decoded with macOS's own `afconvert`, so this stays standard library.
"""

import math
import os
import shutil
import struct
import subprocess
import tempfile
import threading
import wave
from array import array
from pathlib import Path

BINS_PER_SECOND = 150
SAMPLE_RATE = 22050
MAGIC = 0x31575054          # "TPW1", little endian
HEADER = 8
DECODE_TIMEOUT = 120


def pack(bins_per_second, data):
    return struct.pack("<IHH", MAGIC, bins_per_second, 0) + bytes(data)


def unpack(blob):
    """(bins_per_second, bytes of low/mid/high triplets), or None."""
    if len(blob) < HEADER:
        return None
    magic, bins_per_second, _ = struct.unpack_from("<IHH", blob)
    if magic != MAGIC or bins_per_second <= 0:
        return None
    data = blob[HEADER:]
    return bins_per_second, data[:len(data) - len(data) % 3]


def analyze_samples(samples, rate):
    """Samples are signed 16-bit mono. Returns the packed cache bytes."""
    bins = math.ceil(len(samples) / rate * BINS_PER_SECOND)
    lows = [0.0] * bins
    mids = [0.0] * bins
    highs = [0.0] * bins
    a_low = 1 - math.exp(-2 * math.pi * 180 / rate)
    a_mid = 1 - math.exp(-2 * math.pi * 2200 / rate)
    low1 = low2 = mid1 = mid2 = 0.0
    peak = 0
    per_bin = rate / BINS_PER_SECOND
    scale_in = 1 / 32768

    # The hot loop: about ten seconds for a five-minute track.
    bin_index = 0
    next_edge = per_bin
    lo_max = mi_max = hi_max = 0.0
    for i, s in enumerate(samples):
        if i >= next_edge:
            lows[bin_index] = lo_max
            mids[bin_index] = mi_max
            highs[bin_index] = hi_max
            lo_max = mi_max = hi_max = 0.0
            bin_index = int(i / per_bin)
            next_edge = (bin_index + 1) * per_bin
        x = s * scale_in
        low1 += a_low * (x - low1)
        low2 += a_low * (low1 - low2)
        mid1 += a_mid * (x - mid1)
        mid2 += a_mid * (mid1 - mid2)
        v = low2 if low2 > 0 else -low2
        if v > lo_max:
            lo_max = v
        v = mid2 - low2
        if v < 0:
            v = -v
        if v > mi_max:
            mi_max = v
        v = x - mid2
        if v < 0:
            v = -v
        if v > hi_max:
            hi_max = v
        if s > peak:
            peak = s
        elif -s > peak:
            peak = -s
    if bins:
        lows[min(bin_index, bins - 1)] = lo_max
        mids[min(bin_index, bins - 1)] = mi_max
        highs[min(bin_index, bins - 1)] = hi_max

    scale = 255 / (peak * scale_in) if peak else 0
    out = bytearray(bins * 3)
    for n in range(bins):
        out[n * 3] = min(255, round(lows[n] * scale))
        out[n * 3 + 1] = min(255, round(mids[n] * scale))
        out[n * 3 + 2] = min(255, round(highs[n] * scale))
    return pack(BINS_PER_SECOND, out)


def decode(path):
    """Decode any file Core Audio can read to 16-bit mono samples at SAMPLE_RATE."""
    tool = shutil.which("afconvert")
    if tool is None:
        raise RuntimeError("afconvert not found (it ships with macOS)")
    handle, target = tempfile.mkstemp(suffix=".wav", prefix="traktorpad-")
    os.close(handle)
    try:
        subprocess.run(
            [tool, "-f", "WAVE", "-d", "LEI16@%d" % SAMPLE_RATE, "-c", "1",
             str(path), target],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            timeout=DECODE_TIMEOUT)
        with wave.open(target, "rb") as source:
            rate = source.getframerate()
            channels = source.getnchannels()
            if source.getsampwidth() != 2:
                raise RuntimeError("unexpected sample width from afconvert")
            samples = array("h")
            samples.frombytes(source.readframes(source.getnframes()))
        if channels > 1:
            samples = array("h", samples[::channels])
        return samples, rate
    except subprocess.CalledProcessError as error:
        message = (error.stderr or b"").decode(errors="replace").strip()
        raise RuntimeError("afconvert could not read the file: %s" % message) from None
    finally:
        try:
            os.unlink(target)
        except OSError:
            pass


def analyze_file(path):
    samples, rate = decode(path)
    return analyze_samples(samples, rate)


def write_atomic(target, blob):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp%d" % threading.get_ident())
    temporary.write_bytes(blob)
    temporary.replace(target)


class Analyzer:
    """Analyses tracks one at a time on a background thread, into the cache."""

    def __init__(self, log=print):
        self.log = log
        self.lock = threading.Lock()
        self.wanted = {}            # cache file -> audio path, in arrival order
        self.failed = set()
        self.busy = None
        self.wake = threading.Event()
        self.stopping = False
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def request(self, audio_path, cache_file):
        cache_file = Path(cache_file)
        with self.lock:
            if cache_file in self.failed or cache_file == self.busy:
                return
            self.wanted[cache_file] = Path(audio_path)
        self.wake.set()

    def stop(self):
        self.stopping = True
        self.wake.set()

    def _run(self):
        while not self.stopping:
            self.wake.wait()
            self.wake.clear()
            while not self.stopping:
                with self.lock:
                    if not self.wanted:
                        break
                    # Newest first: that is the track just loaded.
                    cache_file = next(reversed(self.wanted))
                    audio_path = self.wanted.pop(cache_file)
                    self.busy = cache_file
                try:
                    if not cache_file.is_file():
                        write_atomic(cache_file, analyze_file(audio_path))
                except Exception as error:      # noqa: BLE001 - keep the worker alive
                    with self.lock:
                        self.failed.add(cache_file)
                    self.log("[led] no waveform for %s: %s" % (audio_path.name, error))
                finally:
                    with self.lock:
                        self.busy = None
