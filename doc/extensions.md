# Extending audiocontrol2

You can extend `audiocontrol2` in two ways:

1. Drop Python modules into an external **plugin directory** and reference them
   from the regular `[controller:...]` / `[metadata:...]` sections.
2. Use the generic `[plugin:...]` section to auto-register a class against
   every interface it implements.

Both approaches are enabled by the `[plugins]` section. See
[issue #33](https://github.com/hifiberry/audiocontrol2/issues/33) for the
history of why the old plugin loader was removed and this lightweight one took
its place.

## Interfaces

Pick whichever of these interfaces make sense for your plugin. You can
implement more than one on the same class.

### Metadata display

Receives updates whenever the now-playing metadata changes.

```python
class MyDisplay:
    def __init__(self, params=None):
        # params is the `configparser` section, so you can read options
        # from the [plugin:...] / [metadata:...] block.
        pass

    def notify(self, metadata):
        # metadata is an ac2.metadata.Metadata instance
        print(metadata)
```

### Controller

Controls players and/or reacts to state changes. Inherit from
`ac2.plugins.control.controller.Controller` if you want the Thread scaffolding
and `set_player_control` / `set_volume_control` hooks for free.

```python
from ac2.plugins.control.controller import Controller

class MyController(Controller):
    def __init__(self, params=None):
        super().__init__()
        self.name = "my controller"

    def run(self):
        # runs in a background thread once audiocontrol2 starts it
        ...
```

### Volume listener

Receives volume updates whenever the active volume control reports a change.

```python
class MyVolumeLogger:
    def notify_volume(self, percent):
        print("volume is now", percent)
```

## Configuration

```ini
[plugins]
# Absolute path where your .py files or Python packages live. It will be
# prepended to sys.path before any plugin is imported, so modules dropped
# here win over identically-named modules elsewhere.
plugin_dir=/data/ac2plugins
```

### Option A - load via the existing section types

This matches how the built-in plugins (`ac2.plugins.metadata.*`,
`ac2.plugins.control.*`) are wired up. After configuring `plugin_dir` you can
reference your own modules by their full dotted path:

```ini
[metadata:myplugin.MyDisplay]
color=blue

[controller:myplugin.MyController]
pin=17
```

The class is instantiated with the `configparser` section as its sole
argument (`MyDisplay(params)`).

### Option B - generic `[plugin:...]` section

If you don't want to think about which section type fits your plugin, use
`[plugin:module.Class]`. `audiocontrol2` will look at the methods your class
exposes and register it against each interface it finds:

| Method on the plugin                 | Registered as                    |
|--------------------------------------|----------------------------------|
| `notify(metadata)`                   | metadata display                 |
| `set_player_control(controller)`     | given the player controller      |
| `set_volume_control(volume_control)` | given the volume controller      |
| `update_playback_state(state)`       | state display                    |
| `notify_volume(percent)`             | volume listener                  |
| `run(self)` (i.e. a `Thread`)        | started in the background        |

Example - a single class that wants to know about both metadata and volume:

```ini
[plugin:mymonitor.LedMonitor]
device=/dev/ledring0
```

```python
class LedMonitor:
    def __init__(self, params):
        self.device = params.get("device")

    def notify(self, metadata):
        ...

    def notify_volume(self, percent):
        ...
```

If the class does not expose any of the interfaces above, it is instantiated
but not connected to anything, and a warning is logged.

## Tips

- Place plugin modules in a directory you control (e.g. `/data/ac2plugins`)
  rather than under `/opt/...`; the HiFiBerryOS system paths are overwritten
  on updates.
- Log with the stdlib `logging` module; output ends up in the `audiocontrol2`
  systemd journal.
- If a plugin crashes during import, `audiocontrol2` logs the exception and
  continues with the rest of the config so one broken plugin can't prevent
  the daemon from starting.
