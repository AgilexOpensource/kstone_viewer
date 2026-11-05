"""Tests of the viewer helpers that need no browser or kstone: python test_viewer.py.

The VectorPort part needs viser and is skipped without it."""

import math
import tempfile
import time
from pathlib import Path

from kstone_viewer.text import TEXT, Lang, Prefs
from kstone_viewer.tune import format_list, lookup, nest, parse_list

assert nest(("a", "b"), 1) == {"a": {"b": 1}} and lookup({"a": {"b": 1}}, ("a", "b")) == 1
assert lookup({"a": 1}, ("a", "b")) is None
assert parse_list("") == [] and parse_list("1; 2,inf") == [1.0, 2.0, math.inf]
assert format_list([0.5, None]) == "0.5, inf"
try:
    parse_list("1, nan")
    raise AssertionError("NaN accepted")
except ValueError:
    pass

try:
    from kstone_viewer.ports import VectorPort
except ImportError:
    VectorPort = None


class FakeViewer:
    def __init__(self):
        self.throttles, self.sent, self.server = [], [], type("S", (), {"gui": None})()

    def request(self, op, module=None, **fields):
        self.sent.append(fields["value"])


if VectorPort is not None:
    fake = FakeViewer()
    port = VectorPort(fake, "chassis", {"name": "velocity", "labels": ["vx", "vy", "wz"], "timeout_s": 0.25},
                      sliders=False, stop_button=False)
    port.command([0.19, 0.0, 0.0])
    port.show([0.0, 0.0, 0.0])  # Server lags our write: keep resending.
    assert port.live == [0.19, 0.0, 0.0]
    port.show([0.19, 0.0, 0.0])
    port.show([0.0, 0.0, 0.0])  # Another writer stopped it mid-edit: do not override.
    fake.sent.clear()
    port.keepalive(time.monotonic() + 1.0)
    assert port.live is None and fake.sent == []
assert all(len(v) == 2 for v in TEXT.values()) and Lang("xx").code == "zh"
assert Lang("en")("held_by", by="x") == "held by **x**"
with tempfile.TemporaryDirectory(prefix="kstone-") as tmp:
    prefs = Prefs(Path(tmp) / "sub" / "viewer.json")
    prefs.set("render_hz", 20)
    assert Prefs(prefs.path).get("render_hz", 30) == 20 and Prefs(prefs.path).get("render_hz", 1.0) == 1.0
    prefs.path.write_text("{broken")
    assert Prefs(prefs.path).error and Prefs(prefs.path).get("render_hz", 30) == 30
    blocked = Prefs(Path(tmp) / "sub" / "viewer.json" / "x.json")
    blocked.set("a", 1)
    assert blocked.error and blocked.get("a", 0) == 1
