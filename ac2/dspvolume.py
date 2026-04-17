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
# On HiFiBerry DAC+ DSP / Beocreate and similar DSP cards, audio coming from
# the optical (S/PDIF) input is processed by the DSP hardware and never flows
# through the ALSA software mixer. That means alsaaudio.Mixer("Master"|"Softvol")
# .setvolume() has no effect on optical playback - the GUI slider moves but
# nothing audible changes (see issue #43).
#
# DSPVolume writes directly to the DSP's volumeControlRegister via
# sigmatcpserver. That register sits inside the DSP signal path, so it
# affects every source, including optical.
#
# Two transports are supported:
#   1. SigmaTCP (port 8086, the stock HiFiBerryOS setup) via the
#      hifiberrydsp.client.sigmatcp.SigmaTCPClient that ships with the OS.
#      This is tried first because it works on every HiFiBerryOS install
#      without any sigmatcpserver reconfiguration.
#   2. REST (port 13141) as a fallback when hifiberrydsp isn't importable
#      but sigmatcpserver was started with --enable-rest.
#
# You can force a specific transport via the `transport` config option
# (values: "auto", "sigmatcp", "rest").


DEFAULT_DBRANGE = 60

# Log coefficients, copied from hifiberrydsp.filtering.volume so the REST
# path stays free of the hifiberrydsp package dependency.
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


# -- Transports --------------------------------------------------------------


class _SigmaTCPTransport:
    """Talks to sigmatcpserver on TCP port 8086 via the client shipped with
    HiFiBerryOS. Works out of the box on every stock HiFiBerryOS install."""

    def __init__(self, host="127.0.0.1", port=8086):
        # Import lazily so the REST fallback still works when hifiberrydsp
        # isn't installed.
        from hifiberrydsp.client.sigmatcp import SigmaTCPClient
        from hifiberrydsp.hardware.adau145x import Adau145x

        self._client = SigmaTCPClient(Adau145x, host, port=port)
        self._register = None

    def resolve_register(self):
        # request_metadata can return "1234" or "1234/5" (address/length).
        raw = self._client.request_metadata("volumeControlRegister")
        if raw is None or raw == "":
            return None
        addr = str(raw).strip().split("/")[0]
        self._register = int(addr, 0)  # 0 -> auto-detect hex/dec
        return self._register

    def read(self):
        if self._register is None:
            return None
        return self._client.read_decimal(self._register)

    def write(self, amplification):
        if self._register is None:
            return False
        self._client.write_decimal(self._register, amplification)
        return True

    def describe(self):
        return "SigmaTCP reg=0x{:x}".format(self._register or 0)


class _RestTransport:
    """Talks to sigmatcpserver's REST API (port 13141 by default). Requires
    sigmatcpserver to be running with --enable-rest."""

    def __init__(self, host="localhost", port=13141, timeout=2.0):
        self._base = "http://{}:{}".format(host, port)
        self._timeout = timeout
        self._register = None

    def resolve_register(self):
        r = requests.get("{}/metadata".format(self._base),
                         timeout=self._timeout)
        r.raise_for_status()
        meta = r.json()
        raw = meta.get("volumeControlRegister")
        if raw is None:
            return None
        addr = str(raw).strip().split("/")[0]
        self._register = int(addr, 0)
        return self._register

    def read(self):
        if self._register is None:
            return None
        url = "{}/memory/{}?format=float".format(self._base, self._register)
        r = requests.get(url, timeout=self._timeout)
        r.raise_for_status()
        values = r.json().get("values", [])
        return float(values[0]) if values else None

    def write(self, amplification):
        if self._register is None:
            return False
        payload = {"address": self._register, "value": [amplification]}
        r = requests.post("{}/memory".format(self._base),
                          json=payload, timeout=self._timeout)
        r.raise_for_status()
        return True

    def describe(self):
        return "REST reg=0x{:x}".format(self._register or 0)


def _pick_transport(mode, host, port, rest_port):
    """Return a transport instance with the register already resolved, or
    None if no transport could be established."""
    attempts = []
    if mode in ("auto", "sigmatcp"):
        attempts.append(("sigmatcp",
                         lambda: _SigmaTCPTransport(host=host, port=port)))
    if mode in ("auto", "rest"):
        attempts.append(("rest",
                         lambda: _RestTransport(host=host, port=rest_port)))

    for name, factory in attempts:
        try:
            t = factory()
            reg = t.resolve_register()
            if reg is None:
                logging.warning(
                    "DSPVolume: %s transport reached the DSP but "
                    "volumeControlRegister metadata is missing (profile "
                    "without volume control?)", name)
                continue
            logging.info("DSPVolume: using %s", t.describe())
            return t
        except ImportError as e:
            logging.debug("DSPVolume: %s transport unavailable: %s", name, e)
        except Exception as e:
            logging.warning("DSPVolume: %s transport failed: %s", name, e)
    return None


# -- Public class ------------------------------------------------------------


class DSPVolume(threading.Thread):
    """Volume controller that writes to the HiFiBerry DSP volume register.

    Exposes the same interface as ALSAVolume so it can be used as a drop-in
    replacement in audiocontrol2.py."""

    def __init__(self,
                 host="localhost",
                 port=8086,
                 rest_port=13141,
                 transport="auto",
                 dbrange=DEFAULT_DBRANGE,
                 poll_interval=0.5):
        super().__init__()

        self.listeners = []
        self.volume = -1
        self.unmuted_volume = 0
        self.pollinterval = max(0.1, poll_interval)
        self.dbrange = dbrange

        self._transport = _pick_transport(
            mode=transport,
            host=host,
            port=port,
            rest_port=rest_port,
        )
        if self._transport is None:
            logging.error(
                "DSPVolume: no working transport to sigmatcpserver; "
                "volume control via the DSP will be a no-op. Check that "
                "sigmatcpserver is running and exposes either TCP (:8086) "
                "or the REST API (:13141, --enable-rest).")

    # -- Public interface (matches ALSAVolume) --------------------------------

    def set_volume(self, vol):
        if vol == 0 and self.volume != 0:
            self.unmuted_volume = self.volume

        if vol == self.volume:
            return

        if self._transport is None:
            return

        amp = percent_to_amplification(vol, self.dbrange)
        try:
            self._transport.write(amp)
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
        if self._transport is None:
            return self.volume if self.volume >= 0 else 0

        try:
            amp = self._transport.read()
        except Exception as e:
            logging.debug("DSPVolume: read failed: %s", e)
            return self.volume if self.volume >= 0 else 0

        if amp is None:
            return self.volume if self.volume >= 0 else 0
        return amplification_to_percent(amp, self.dbrange)

    def add_listener(self, listener):
        self.listeners.append(listener)
