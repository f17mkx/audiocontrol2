# Volume control

`audiocontrol2` supports two volume backends, selected via the `control` option
of the `[volume]` section in `/etc/audiocontrol2.conf`.

## `control=alsa` (default)

Uses `alsaaudio.Mixer(mixer_control)` to get and set a single ALSA mixer.
This works well for sources that flow through ALSA (AirPlay via shairport-sync,
Spotify via spotifyd, MPD, etc).

```ini
[volume]
control=alsa
mixer_control=Master
```

## `control=dsp`

Writes the HiFiBerry DSP `volumeControlRegister` through `sigmatcpserver`.
This controls every source that passes through the DSP, including sources
that bypass ALSA entirely - most notably the **optical (S/PDIF) input** on
DAC+ DSP and Beocreate cards.

Before the DSP backend existed, setting the volume via `/api/volume` while the
optical input was active only moved the GUI slider; the audible volume did not
change because the ALSA `Master` / `Softvol` mixer is not in the optical
signal path. See [issue #43](https://github.com/hifiberry/audiocontrol2/issues/43).

### Transports

The DSP backend talks to `sigmatcpserver` through one of two transports. It
picks whichever works at startup; you can force a specific one with the
`transport` option.

| `transport` value | What it does                                                |
|-------------------|-------------------------------------------------------------|
| `auto` (default)  | Try SigmaTCP first, fall back to REST.                      |
| `sigmatcp`        | Use SigmaTCP only. Works on every stock HiFiBerryOS image.  |
| `rest`            | Use REST only. Requires `sigmatcpserver --enable-rest`.     |

SigmaTCP (TCP port 8086) is preferred because it is the default wire protocol
on HiFiBerryOS and needs no `sigmatcpserver` configuration change. The
backend uses the `SigmaTCPClient` class from the `hifiberrydsp` package that
ships with HiFiBerryOS.

### Requirements

- A HiFiBerry board with a DSP (e.g. DAC+ DSP, Beocreate).
- `sigmatcpserver` running (default on HiFiBerryOS).
- A loaded DSP profile that exposes a `volumeControlRegister` metadata entry
  (every stock HiFiBerry profile does).
- For `transport=sigmatcp` or `auto`: the `hifiberrydsp` Python package
  (pre-installed on HiFiBerryOS).
- For `transport=rest`: `sigmatcpserver` started with `--enable-rest`.

### Configuration

```ini
[volume]
control=dsp
# Optional - defaults shown:
#transport=auto
#dsp_host=localhost
#dsp_port=8086         # SigmaTCP
#dsp_rest_port=13141   # REST fallback
#dbrange=60
```

`dbrange` controls the log curve used to map 0 - 100 % to the DSP
amplification factor. 60 dB matches `dsptoolkit`'s default and keeps the
slider feel consistent with other HiFiBerry tools.

### Verifying the DSP backend

After restarting `audiocontrol2`, the log should contain lines like:

```
INFO: dspvolume - DSPVolume: using SigmaTCP reg=0x3d
INFO: audiocontrol2 - using DSP volume control at localhost (transport=auto)
```

You can then confirm the DSP register actually moves when you hit the API:

```bash
curl -X POST -H 'Content-Type: application/json' \
     -d '{"percent":"30"}' http://<device>:81/api/volume
dsptoolkit get-volume
# -> Volume: 0.0079 / 30% / -42db

curl -X POST -H 'Content-Type: application/json' \
     -d '{"percent":"70"}' http://<device>:81/api/volume
dsptoolkit get-volume
# -> Volume: 0.1259 / 70% / -18db
```
