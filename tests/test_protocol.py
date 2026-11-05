"""Viewer client against a live kstone debug server: python test_protocol.py <kstone fixtures dir>."""

import math
import sys
import tempfile
import threading
from pathlib import Path

import kstone

from kstone_viewer.assets import materialize
from kstone_viewer.client import Client
from kstone_viewer.tune import lookup, nest, parse_list

fixtures = Path(sys.argv[1])
e = kstone.load_engine(fixtures / "arm_loopback.yaml")
try:
    e.serve("0.0.0.0", 0)
    raise AssertionError("non-loopback served without a token")
except kstone.Error as err:
    assert err.code == "invalid_argument", err
token = "0123456789abcdef-viewer"
server = e.serve("127.0.0.1", 0, token=token)
client = Client("127.0.0.1", server.port)
model = client.handshake(token)
assert model["type"] == "model" and {m["id"] for m in model["modules"]} == set(e.modules)
assert Path(model["urdf"]) == (fixtures / "arm.urdf").resolve(), model["urdf"]
assert model["assets"]["model"] == "model.urdf" and model["assets"]["missing"] == [], model["assets"]
with tempfile.TemporaryDirectory(prefix="kstone-") as cache:
    cached = materialize(client, model["assets"], Path(cache))
    assert cached.read_text() == (fixtures / "arm.urdf").read_text()
    assert materialize(client, model["assets"], Path(cache)) == cached
    for bad in ({**model["assets"], "id": "../x"}, {**model["assets"], "model": "../escape.urdf"}):
        try:
            materialize(client, bad, Path(cache))
            raise AssertionError("bad bundle accepted")
        except ValueError:
            pass
try:
    client.fetch("../arm.urdf")
    raise AssertionError("unlisted asset served")
except ConnectionError as err:
    assert "not_found" in str(err)

runner = threading.Thread(target=e.run)
runner.start()
assert client.request("set_enabled", "fingers", enabled=False)["ok"]
# Python's json writes inf as Infinity; the server accepts it and echoes null.
request = client.send("tune", "arm", params=nest(("trajectory", "velocity_cap"), parse_list("0.5, inf, inf")))
replied = broadcast = False
while not (replied and broadcast):
    message = client.receive()
    if message["type"] == "reply" and message["id"] == request:
        assert message["ok"], message
        replied = True
    elif message["type"] == "params" and message["module"] == "arm":
        assert lookup(message["values"], ("trajectory", "velocity_cap")) == [0.5, None, None], message
        broadcast = True
e.stop()
runner.join(5.0)
assert not e.module("fingers").enabled
assert list(e.module("arm").params()["trajectory"]["velocity_cap"]) == [0.5, math.inf, math.inf]
client.close()
server.stop()

server = e.serve("127.0.0.1", 0, token=token, assets=False)
client = Client("127.0.0.1", server.port)
assert client.handshake(token)["assets"] is None
client.close()
server.stop()
print("ok")
