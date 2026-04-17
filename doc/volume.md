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

Writes the HiFiBerry DSP `volumeControlRegister` through the REST API exposed
by `sigmatcpserver`. This controls every source that passes through the DSP,
including sources that bypass ALSA entirely - most notably the **optical
(S/PDIF) input** on DAC+ DSP and Beocreate cards.

Before the DSP backend existed, setting the volume via `/api/volume` while the
optical input was active only moved the GUI slider; the audible volume did not
change because the ALSA `Master` mixer is not in the optical signal path. See
[issue #43](https://github.com/hifiberry/audiocontrol2/issues/43).

### Requirements

- A HiFiBerry board with a DSP (e.g. DAC+ DSP, Beocreate).
- `sigmatcpserver` running with `--enable-rest` (default on current
  HiFiBerryOS images).
- A loaded DSP profile that exposes a `volumeControlRegister` metadata entry.

### Configuration

```ini
[volume]
control=dsp
# Optional - defaults shown:
#dsp_host=localhost
#dsp_port=13141
#dbrange=60
```

`dbrange` controls the log curve used to map 0 - 100 % to the DSP
amplification factor. 60 dB matches `dsptoolkit`'s default.
