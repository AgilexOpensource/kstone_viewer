# kstone-viewer

[中文](README.zh-CN.md)

Browser viewer for the [kstone](https://github.com/AgilexOpensource/kstone) debug server, for smoke-testing a running engine from a desktop browser.

It talks to the server over TCP and does not need the `kstone` package, so it can run on a different machine from the robot. The page shows run controls, module ports and tuning, plots, safety stats, joints and display settings.

## Requirements

Python 3.10 or later. Tested on Ubuntu 22.04.

## Install

```bash
pip3 install "git+https://github.com/AgilexOpensource/kstone_viewer.git"
```

Or, recommended to keep the system Python clean, a virtual environment:

```bash
python3 -m venv ~/.venvs/kstone-viewer
source ~/.venvs/kstone-viewer/bin/activate
pip install "git+https://github.com/AgilexOpensource/kstone_viewer.git"
```

Activate the venv in every new terminal before running `kstone-viewer`. Dependencies (viser, yourdfpy, trimesh, numpy) are installed automatically.

## Usage

Start a debug server first, in one of these ways:

- Command line: `kstone run <config> --serve 7311`
- Python: `engine.serve()`
- C++: `kstone::DebugServer::start(engine, options)`

The server listens on `127.0.0.1:7311` by default. Then connect the viewer:

```bash
kstone-viewer --connect 127.0.0.1:7311
```

The viewer prints `connected to 127.0.0.1:7311; open http://127.0.0.1:8080`. Open that URL in a browser. The server sends the URDF and its mesh files, cached under `$XDG_CACHE_HOME/kstone/models`; `--urdf` renders a local file instead. When the server goes away, the viewer keeps reconnecting. Stop it with `Ctrl+C`.

## Options

| Option | Default | Description |
|---|---|---|
| `--connect` | `127.0.0.1:7311` | Debug server address, `host:port` |
| `--host` | `127.0.0.1` | Viewer web host |
| `--port` | `8080` | Viewer web port |
| `--urdf` | server's model | Local URDF to render |
| `--token-file` | `$KSTONE_TOKEN` | File holding the server token |
| `--rate` | render rate, at most the publish rate | State rate in Hz |
| `--prefs` | `$XDG_CONFIG_HOME/kstone/viewer.json` | Preference file |
| `--no-prefs` | off | Do not read or write preferences |

- `$XDG_CACHE_HOME` and `$XDG_CONFIG_HOME` fall back to `~/.cache` and `~/.config`.
- The preference file stores display settings and the page language.

## Remote use and security

A server bound to a non-loopback address requires a token. Pass the same token to the viewer:

```bash
kstone-viewer --connect <robot-ip>:7311 --token-file ~/.kstone-token
```

Setting `KSTONE_TOKEN` in the environment works instead of `--token-file`.

The protocol is not encrypted and the token is sent in clear text. Across untrusted networks, keep the server on loopback and forward the port over SSH:

```bash
ssh -N -L 7311:127.0.0.1:7311 user@<robot-ip>
kstone-viewer --connect 127.0.0.1:7311
```

The viewer page has no login. Keep `--host` at `127.0.0.1` and reach the page from another machine over SSH instead of binding it to a network address:

```bash
ssh -N -L 8080:127.0.0.1:8080 user@<viewer-host>
```

- All clients of a server are equal and the last write wins. Every browser that opens the page acts as the same viewer.
- Motion targets your own code writes (frame and joint targets, chassis motion) keep priority. The viewer's writes to them are refused while held, shown as API holds on the module, and are not replayed.

## Troubleshooting

- `[Errno 111] Connection refused`: no server is listening at `--connect`. Start the server and check the host and port.
- `--connect expects host:port`: pass the address as `host:port`, e.g. `127.0.0.1:7311`.
- `server requires a token`: the server was started with a token. Pass it with `--token-file` or `$KSTONE_TOKEN`.
- `authentication failed: bad token`: the token differs from the server's. Use the same file or value on both sides.
- `server refused the connection: server full (8 clients)`: close other clients of that server.
- `unsupported greeting`: the server speaks a different protocol version or is not a kstone debug server. Use matching kstone and kstone-viewer releases.
- `no URDF: the server serves no model assets and reports no local file; pass --urdf`: pass a local URDF with `--urdf`.
- `warning: the server lacks model files, they will not render`: the listed files referenced by the URDF are missing on the server machine. Make them available there.
- `kstone_viewer needs <module>: reinstall kstone-viewer`: a dependency is missing. Reinstall the package in the active environment.
- `preferences ignored`: the preference file could not be read. Fix or delete it, or run with `--no-prefs`.

## Development

```bash
git clone https://github.com/AgilexOpensource/kstone_viewer.git && cd kstone_viewer
pip install -e .
python tests/test_viewer.py
# needs kstone installed and its repository checked out next to this one
python tests/test_protocol.py ../kstone/tests/fixtures
```

`test_viewer.py` needs neither a browser nor kstone. `test_protocol.py` starts a kstone debug server and drives it with the viewer client.

## License

Apache-2.0, see [LICENSE](LICENSE).
