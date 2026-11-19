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

try:
    from kstone_viewer.app import App
except ImportError:
    App = None
if App is not None:
    class HomeFake:
        def __init__(self):
            self.sent = []

        def request(self, op, module=None, **fields):
            self.sent.append((op, module))

    fake = HomeFake()
    App.home_all(fake)
    assert fake.sent == [("home_all", None)], fake.sent

    class HomingFake(HomeFake):
        def __init__(self, capabilities):
            super().__init__()
            self.client = type("C", (), {"capabilities": frozenset(capabilities)})()
            self.home_all_button = None
            self.homing_all = False

        can = App.can

    # While a module homes, Home all turns into Cancel all homing (only when the server has the op).
    fake = HomingFake({"cancel_home_all"})
    App.show_homing_all(fake, [{"home": "idle"}, {"home": "executing"}])
    assert fake.homing_all
    App.home_all_clicked(fake, None)
    assert fake.sent == [("cancel_home_all", None)], fake.sent
    App.show_homing_all(fake, [{"home": "cancelled"}])
    assert not fake.homing_all
    fake = HomingFake(())
    App.show_homing_all(fake, [{"home": "waiting"}])
    assert not fake.homing_all

    class ResyncFake:
        def __init__(self):
            self.sent = []

        def request(self, op, module=None, label=None, **fields):
            self.sent.append((op, fields))

    fake = ResyncFake()
    App.resync(fake)
    assert fake.sent == [("resync", {})], fake.sent

    from kstone_viewer.app import feedback_badges
    en = lambda zh, en: en
    two = {"groups": [{"name": "joints", "fresh": True, "attached": True},
                      {"name": "base", "fresh": False, "attached": False}], "fresh": False}
    out = feedback_badges(two, en)
    assert len(out) == 2 and "joints fresh" in out[0] and "base stale" in out[1], out
    assert "syncing" in feedback_badges({"fresh": True, "attached": False}, en)[0]
    assert "feedback fresh" in feedback_badges({}, en)[0]
    off = {"groups": [{"name": "head", "fresh": True, "attached": True, "deviation": 0.5},
                      {"name": "base", "fresh": True, "attached": True, "deviation": 0.001}]}
    out = feedback_badges(off, en)
    assert "head off by 0.500" in out[0] and "base fresh" in out[1], out
    assert "off by" in feedback_badges({"groups": [{"name": "default", "deviation": 0.1}]}, en)[0]

    from kstone_viewer.panels.plots import Samples
    samples = Samples({"modules": [], "joints": [{"name": "lift", "idx_q": 0, "nq": 1}]})
    out = samples({"q": [0.1], "q_command": [0.2]})
    assert out["q/lift"] == 0.1 and out["q_command/lift"] == 0.2, out
    assert "q_command/lift" not in samples({"q": [0.1]})
    out = Samples({"modules": [], "joints": []})(
        {"q": [], "modules": [{"id": "arm", "ports": {"target": {"position": [1.0, 2.0, 3.0], "orientation": [1, 0, 0, 0]}}}]})
    assert out["arm.target[x]"] == 1.0 and out["arm.target[z]"] == 3.0, out
    assert abs(out["arm.target[norm]"] - 14 ** 0.5) < 1e-9, out
