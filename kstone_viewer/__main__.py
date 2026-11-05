"""Browser viewer for a running `kstone run --serve` engine.

    kstone-viewer [--connect 127.0.0.1:7311] [--port 8080] [--token-file path] [--rate hz]

The token defaults to $KSTONE_TOKEN when the server requires one. Reconnects when the server goes away.
The model comes from --urdf, else is downloaded from the server (cached in $XDG_CACHE_HOME/kstone/models),
else the server's URDF path is used if it exists on this host.

Tabs: overview (lease, run/pause/step, home all, residuals), modules (ports, schema-generated tuning,
scalar outputs with units and a plot preset to the module's own keys), plots (added and removed panels of any
state scalar), safety (feedback freshness, every engine stat with counter rates, cycle-time and margin plots),
joints and display. Ports with timeout_s are resent while nonzero until stopped. Module visuals and display
outputs are drawn as overlays styled by output role, at the state the server evaluated them at, toggled per
module. Display settings and the language are saved to $XDG_CONFIG_HOME/kstone/viewer.json unless --no-prefs
is given.
"""

import argparse
import math
import os
import queue
import threading
import time
from pathlib import Path

try:
    import viser
    import yourdfpy  # noqa: F401
except ModuleNotFoundError as e:
    raise SystemExit(f"kstone_viewer needs {e.name}: reinstall kstone-viewer") from None

from .assets import materialize
from .client import Client
from .geometry import parse_endpoint
from .text import LANGUAGES, Lang, Prefs, default_prefs_path
from .app import MAX_RENDER_HZ, App

RECONNECT_MAX_S = 5.0


class Connection:
    """Receives on its own thread; the render loop takes the latest state and the other messages in order."""

    def __init__(self, client: Client):
        self.client = client
        self.state = None
        self.messages = queue.Queue()
        self.error = None
        self.arrived = threading.Event()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            while True:
                message = self.client.receive()
                if message.get("type") == "state":
                    with self._lock:
                        self.state = message
                else:
                    self.messages.put(message)
                self.arrived.set()
        except (ConnectionError, OSError, ValueError) as e:
            self.error = str(e) or type(e).__name__
            self.arrived.set()

    def take_state(self):
        with self._lock:
            state, self.state = self.state, None
        return state

    def close(self) -> None:
        self.client.close()
        self._thread.join(1.0)


def build(server: viser.ViserServer, client: Client, hello: dict, urdf: Path, prefs: Prefs, old: App | None = None) -> App:
    if old is not None:
        old.close()
    server.gui.reset()
    server.scene.reset()
    return App(server, client, hello, urdf, prefs)


def serve(server: viser.ViserServer, client: Client, hello: dict, urdf: Path, rate, prefs: Prefs) -> str:
    """Runs one session until the connection drops; returns the reason."""
    viewer = build(server, client, hello, urdf, prefs)
    connection = Connection(client)
    streamed = None
    try:
        while connection.error is None:
            # The server paces the stream at the render rate, so every state it sends is drawn and none are dropped.
            wanted = rate if rate is not None else min(float(viewer.render_hz), float(hello["publish_hz"]))
            if wanted != streamed:
                streamed = wanted
                viewer.request("subscribe", rate_hz=wanted)
            if viewer.rebuild:
                # Tuned values arrive again with the next params broadcast; until then the model's are shown.
                hello = {**hello, "modules": [
                    {**m, "params": viewer.views[m["id"]].tune.values}
                    if m["id"] in viewer.views and viewer.views[m["id"]].tune is not None else m
                    for m in hello["modules"]
                ]}
                if viewer.solver is not None:
                    hello["solver"] = {**hello["solver"], "params": viewer.solver.values}
                hello["joint_limits"] = viewer.limits.values
                old = viewer
                viewer = build(server, client, hello, urdf, prefs, old)
                viewer.pending = old.pending
                if old.last_lease is not None:
                    viewer.on_lease(old.last_lease)
                if old.state is not None:
                    viewer.on_state(old.state)
            while True:
                try:
                    message = connection.messages.get_nowait()
                except queue.Empty:
                    break
                kind = message.get("type")
                if kind == "reply":
                    viewer.on_reply(message)
                elif kind == "lease":
                    viewer.on_lease(message)
                elif kind == "params":
                    viewer.on_params(message)
                elif kind == "solver_params":
                    viewer.on_solver_params(message)
                elif kind == "joint_limits":
                    viewer.on_joint_limits(message)
            connection.arrived.clear()
            state = connection.take_state()
            if state is not None:
                with server.atomic():
                    viewer.on_state(state)
            viewer.flush()
            connection.arrived.wait(1.0 / MAX_RENDER_HZ)
        return connection.error
    finally:
        viewer.close()
        connection.close()


def pick_urdf(client: Client, hello: dict, override: Path | None) -> Path | None:
    """--urdf, else the server's bundle (cached), else the server's path if it exists here."""
    if override is not None:
        return override
    assets = hello.get("assets")
    if assets:
        if assets.get("missing"):
            print(f"warning: the server lacks model files, they will not render: {', '.join(assets['missing'])}")
        try:
            return materialize(client, assets)
        except (ValueError, OSError) as e:
            print(f"warning: model download failed ({e}); trying the server's local path")
    local = Path(hello["urdf"]) if hello.get("urdf") else None
    return local if local is not None and local.is_file() else None


def main() -> None:
    parser = argparse.ArgumentParser(prog="kstone-viewer", description=__doc__.splitlines()[0])
    parser.add_argument("--connect", default="127.0.0.1:7311", help="debug server address")
    parser.add_argument("--host", default="127.0.0.1", help="viewer web host")
    parser.add_argument("--port", type=int, default=8080, help="viewer web port")
    parser.add_argument("--urdf", type=Path, help="URDF to render instead of the one the server sends")
    parser.add_argument("--token-file", type=Path, help="file holding the server token")
    parser.add_argument("--rate", type=float, help="state rate in Hz (default: the render rate, at most the publish rate)")
    parser.add_argument("--prefs", type=Path, help=f"preference file (default: {default_prefs_path()})")
    parser.add_argument("--no-prefs", action="store_true", help="don't read or write preferences")
    args = parser.parse_args()
    prefs = Prefs(None if args.no_prefs else args.prefs or default_prefs_path())
    if prefs.error:
        print(f"preferences ignored: {prefs.error}")

    if args.rate is not None and not (math.isfinite(args.rate) and args.rate > 0):
        parser.error(f"--rate must be a positive number, got {args.rate}")
    try:
        endpoint = parse_endpoint(args.connect)
    except ValueError:
        parser.error(f"--connect expects host:port, got {args.connect!r}")
    try:
        token = args.token_file.read_text().strip() if args.token_file else os.environ.get("KSTONE_TOKEN")
    except OSError as e:
        parser.error(f"--token-file: {e}")
    server = None
    delay = 0.5

    def lang() -> Lang:
        return Lang(prefs.get("language", "zh", lambda v: v in LANGUAGES))

    # Fatal before the first connection; afterwards report and keep retrying.
    def fail(message: str) -> None:
        nonlocal delay
        if server is None:
            raise SystemExit(message)
        print(f"reconnect failed: {message}; retrying in {delay:.1f} s")
        server.gui.reset()
        server.gui.add_markdown(lang()("reconnect_failed", message=message))
        time.sleep(delay)
        delay = min(delay * 2.0, RECONNECT_MAX_S)

    try:
        while True:
            try:
                client = Client(*endpoint)
            except OSError as e:
                fail(f"{args.connect}: {e}")
                continue
            try:
                hello = client.handshake(token)
            except (ConnectionError, OSError, ValueError) as e:
                client.close()
                fail(f"{args.connect}: {e}")
                continue
            try:
                urdf = pick_urdf(client, hello, args.urdf)
            except (ConnectionError, OSError) as e:
                client.close()
                fail(f"{args.connect}: {e}")
                continue
            if urdf is None:
                client.close()
                fail("no URDF: the server serves no model assets and reports no local file; pass --urdf")
                continue
            if server is None:
                server = viser.ViserServer(host=args.host, port=args.port, label="kstone")
                print(f"connected to {args.connect}; open http://{args.host}:{args.port}")
            else:
                print(f"reconnected to {args.connect}")
            delay = 0.5
            reason = serve(server, client, hello, urdf, args.rate, prefs)
            print(f"disconnected: {reason}; reconnecting")
            server.gui.reset()
            server.gui.add_markdown(lang()("disconnected", endpoint=args.connect, reason=reason))
    except KeyboardInterrupt:
        pass
    finally:
        if server is not None:
            server.stop()


if __name__ == "__main__":
    main()
