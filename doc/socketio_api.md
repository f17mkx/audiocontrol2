# Socketio API

`audiocontrol2` exposes a Socket.IO API that lets a client receive real-time
metadata and volume updates, and send player control commands.

## Enabling the Socket.IO API

Enable it in `/etc/audiocontrol2.conf`:

```ini
[webserver]
enable=yes
port=81
socketio_enabled=True
```

## Namespaces

The server exposes three namespaces. Events are **only delivered to clients
that explicitly subscribe to the namespace** - connecting to the default
namespace (`/`) is not enough.

| Namespace   | Server -> client events | Client -> server events                                    |
|-------------|-------------------------|------------------------------------------------------------|
| `/metadata` | `update` (metadata dict) | `get`                                                      |
| `/volume`   | `update` (`{percent}`)   | `get`, `set` (`{percent}`)                                  |
| `/player`   | -                       | `status`, `playing`, `play`, `pause`, `play_pause`, `stop`, `next`, `previous` |

> If your client only sees the connection SID and then nothing else (see
> [issue #42](https://github.com/hifiberry/audiocontrol2/issues/42)), it is
> almost always because it subscribed only to `/` and not to the namespace it
> actually wants updates from.

## Python client example

Install the async client with the same `python-socketio` version as the server
(5.4.0 on current HiFiBerryOS images):

```bash
pip install "python-socketio[asyncio_client]==5.4.0"
```

```python
import asyncio
import json
import socketio

sio = socketio.AsyncClient()


@sio.event
async def connect():
    print("connection established")


@sio.event
async def disconnect():
    print("disconnected from server")


# Register handlers on each namespace you want to listen to.
@sio.on("update", namespace="/metadata")
async def metadata_update(data):
    print("metadata update:", json.dumps(data))


@sio.on("update", namespace="/volume")
async def volume_update(data):
    print("volume update:", data)


async def main():
    # Subscribe to every namespace you want events from. Missing this is the
    # most common reason for "connected but no events".
    await sio.connect(
        "http://hifiberry.local:81",
        namespaces=["/metadata", "/volume", "/player"],
    )

    # Pull the current values once after connecting.
    metadata = await sio.call("get", namespace="/metadata")
    volume = await sio.call("get", namespace="/volume")
    print("initial metadata:", metadata)
    print("initial volume:", volume)

    # Example: set volume to 40%.
    await sio.emit("set", {"percent": 40}, namespace="/volume")

    await sio.wait()


if __name__ == "__main__":
    asyncio.run(main())
```

## JavaScript / browser client example

In the JavaScript `socket.io-client`, each namespace is a separate `Socket`
instance created by passing the namespace as the path component of the URL:

```html
<script src="https://cdn.socket.io/4.7.5/socket.io.min.js"></script>
<script>
  const base = "http://hifiberry.local:81";

  const metadataSocket = io(`${base}/metadata`);
  metadataSocket.on("connect", () => console.log("metadata connected"));
  metadataSocket.on("update", (data) => console.log("metadata update", data));

  const volumeSocket = io(`${base}/volume`);
  volumeSocket.on("update", (data) => console.log("volume update", data));

  // Request the current volume.
  volumeSocket.emit("get", (reply) => console.log("current volume", reply));

  // Set volume to 25 %.
  volumeSocket.emit("set", { percent: 25 });

  const playerSocket = io(`${base}/player`);
  playerSocket.emit("play");
</script>
```

The same rule applies to every other Socket.IO client library (Java, Swift,
Go, ...): open one socket per namespace.

## Troubleshooting

- **Connected but no events.** You are on the default namespace. Add the
  `/metadata`, `/volume` or `/player` namespace to your connection (see the
  table above).
- **`get` returns nothing.** The `get` events are request/response-style - use
  `sio.call(...)` in Python or the acknowledgement callback in JavaScript
  rather than `sio.emit(...)`.
- **Mismatched protocol.** `socketio_enabled=True` requires the gevent-based
  server path; keep the `python-socketio` version on the client aligned with
  the server.
