'''
Copyright (c) 2024 Modul 9/HiFiBerry

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
'''

import threading
import time
import math
import logging

import requests

# Why this module exists:
# On HiFiBerry DAC+ DSP / Beocreate and similar DSP cards, audio coming from the
# optical (S/PDIF) input is processed by the DSP hardware and never flows through
# the ALSA software mixer. That means alsaaudio.Mixer("Master").setvolume() has
# no effect on optical playback — the GUI slider moves but nothing audible
# changes (see https://github.com/hifiberry/audiocontrol2/issues/43).
#
# DSPVolume writes directly to the DSP's volumeControlRegister via the
# sigmatcpserver REST API (port 13141 by default). This affects every source
# that passes through the DSP, including optical.


# dB-range that maps 0–100% to the log volume curve. 60 dB matches dsptoolkit's
# default and keeps the perceived response comparable to other HiFiBerry tools.
DEFAULT_DBRANGE = 60

# Log coefficients for a 60 dB range, copied from hifiberrydsp.filtering.volume
# so this module stays free of the hifiberrydsp package dependency.
_LOG_COEFFS = {
    50: (0.0031623, 5.757),
    60: (0.001, 6.908),
    70: (0.00031623, 8.059),
    80: (0.0001, 9.210),
    90: (0.000031623, 10.36),
    100: (0.00001, 11.51),
}


def _log_coefficients(dbrange):
    for upper, coeffs in sorted(_LOG_COEFFS.items()):
        if dbrange <= upper:
            return coeffs
    return _LOG_COEFFS[100]


def percent_to_amplification(percent, dbrange=DEFAULT_DBRANGE):
    # Map 0–100 % to a linear amplification factor on a log curve so that
    # perceived loudness changes roughly evenly across the slider.
    if percent <= 0:
        return 0.0
    if percent >= 100:
        return 1.0
    (a, b) = _log_coefficients(dbrange)
    return a * math.exp(b * float(percent) / 100.0)


def amplification_to_percent(amplification, dbrange=DEFAULT_DBRANGE):
    if amplification <= 0:
        return 0
    if amplification >= 1:
        return 100
    (a, b) = _log_coefficients(dbrange)
    return round((math.log(amplification / a) / b) * 100)


class DSPVolume(threading.Thread):
    """Volume controller that writes to the HiFiBerry DSP volume register.

    Exposes the same interface as ALSAVolume so it can be used as a drop-in
    replacement in audiocontrol2.py. Requires sigmatcpserver to be running
    with --enable-rest (default on current HiFiBerryOS images)."""

    def __init__(self, host="localhost", port=13141, dbrange=DEFAULT_DBRANGE,
                 poll_interval=0.5, timeout=2.0):
        super().__init__()

        self.listeners = []
        self.volume = -1
        self.unmuted_volume = 0
        self.pollinterval = max(0.1, poll_interval)
        self.dbrange = dbrange
        self.timeout = timeout
        self.base_url = "http://{}:{}".format(host, port)
        # Cached register address resolved from /metadata on first use.
        self.register_addr = None

        # Resolve the register address once at startup so later set_volume()
        # calls don't have to hit /metadata every time.
        try:
            self.register_addr = self._resolve_register()
            if self.register_addr is None:
                logging.error(
                    "DSPVolume: volumeControlRegister not found in DSP "
                    "metadata (is a DSP profile loaded?)")
        except Exception as e:
            logging.error("DSPVolume: failed to resolve volume register: %s", e)

    # -- HTTP helpers ---------------------------------------------------------

    def _resolve_register(self):
        url = "{}/metadata".format(self.base_url)
        r = requests.get(url, timeout=self.timeout)
        r.raise_for_status()
        meta = r.json()
        reg = meta.get("volumeControlRegister")
        if reg is None:
            return None
        return self._parse_int(reg)

    @staticmethod
    def _parse_int(value):
        # Metadata values can be plain decimals, hex ("0x1234") or the
        # dsptoolkit-style "1234/5" (address/length). We only need the address.
        if isinstance(value, int):
            return value
        text = str(value).strip().split("/")[0]
        if text.lower().startswith("0x"):
            return int(text, 16)
        return int(text)

    def _read_register(self):
        if self.register_addr is None:
            return None
        url = "{}/memory/{}?format=float".format(self.base_url,
                                                 self.register_addr)
        r = requests.get(url, timeout=self.timeout)
        r.raise_for_status()
        values = r.json().get("values", [])
        if not values:
            return None
        return float(values[0])

    def _write_register(self, amplification):
        if self.register_addr is None:
            return False
        url = "{}/memory".format(self.base_url)
        payload = {"address": self.register_addr, "value": [amplification]}
        r = requests.post(url, json=payload, timeout=self.timeout)
        r.raise_for_status()
        return True

    # -- Public interface (matches ALSAVolume) --------------------------------

    def set_volume(self, vol):
        # Track the last non-zero volume so unmute can restore it later.
        if vol == 0 and self.volume != 0:
            self.unmuted_volume = self.volume

        if vol == self.volume:
            return

        amp = percent_to_amplification(vol, self.dbrange)
        try:
            self._write_register(amp)
        except Exception as e:
            logging.error("DSPVolume: failed to set volume to %s%%: %s",
                          vol, e)

    def change_volume_percent(self, change):
        vol = self.current_volume()
        newvol = vol + change
        if newvol < 0:
            newvol = 0
        elif newvol > 100:
            newvol = 100

        self.set_volume(newvol)

    def set_mute(self, mute):
        if mute:
            logging.debug("DSPVolume: muting")
            if self.volume != 0:
                self.unmuted_volume = self.volume
                self.set_volume(0)
        else:
            logging.debug("DSPVolume: unmuting")
            if self.volume == 0 and self.unmuted_volume > 0:
                self.set_volume(self.unmuted_volume)

    def toggle_mute(self):
        if self.volume != 0:
            self.unmuted_volume = self.volume
            self.set_volume(0)
        elif self.unmuted_volume > 0:
            self.set_volume(self.unmuted_volume)

    def run(self):
        while True:
            self.notify_listeners()
            time.sleep(self.pollinterval)

    def notify_listeners(self, always_notify=False):
        current_vol = self.current_volume()

        if current_vol == 0 and self.volume != 0:
            self.unmuted_volume = self.volume

        if always_notify or (current_vol != self.volume):
            logging.debug("DSP volume changed to %s%%", current_vol)
            self.volume = current_vol
            for listener in self.listeners:
                try:
                    listener.notify_volume(current_vol)
                except Exception as e:
                    logging.debug("exception %s during %s.notify_volume",
                                  e, listener)

    def current_volume(self):
        try:
            amp = self._read_register()
        except Exception as e:
            logging.debug("DSPVolume: read failed: %s", e)
            return self.volume if self.volume >= 0 else 0

        if amp is None:
            return self.volume if self.volume >= 0 else 0
        return amplification_to_percent(amp, self.dbrange)

    def add_listener(self, listener):
        self.listeners.append(listener)
