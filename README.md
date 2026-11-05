# kstone-viewer

**English** | [简体中文](README.zh-CN.md)

A browser viewer for the [kstone](https://github.com/AgilexOpensource/kstone) debug server. It is a smoke-test tool. It talks to the server over TCP and does not need the `kstone` package, so it can run on a different machine from the robot.

## Install

```bash
pip install "git+https://github.com/AgilexOpensource/kstone_viewer.git"
```

It depends on viser, yourdfpy, trimesh and numpy.

## Use

Start a debug server with `kstone run <config> --realtime --serve 7311`, `engine.serve()` in Python or `DebugServer` in C++. Then:

```bash
kstone-viewer --connect 127.0.0.1:7311
```

The viewer prints the URL to open, default `http://127.0.0.1:8080`. The server sends the URDF and its mesh files; `--urdf` renders a local file instead.

For a remote server, pass its token with `--token-file` or `$KSTONE_TOKEN`. The protocol is not encrypted, so across untrusted networks forward the port over SSH and connect to the local end:

```bash
ssh -N -L 7311:127.0.0.1:7311 user@robot
kstone-viewer --connect 127.0.0.1:7311
```

`kstone-viewer --help` lists the other options.

## Test

```bash
python tests/test_viewer.py
# with kstone installed and its repository checked out next to this one
python tests/test_protocol.py ../kstone/tests/fixtures
```

`test_viewer.py` needs neither a browser nor kstone. `test_protocol.py` starts a kstone debug server and drives it with the viewer client.
