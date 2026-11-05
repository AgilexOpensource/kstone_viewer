"""Port controls: pose gizmos, vector sliders and scalar inputs, with throttled sends."""

import math
import threading
import time
from typing import TYPE_CHECKING

import numpy as np
from viser import Icon

from .geometry import matrix_to_wxyz, pose_to_wire, set_pose, wxyz_to_matrix
from .tune import EDIT_HOLD_S, finite

if TYPE_CHECKING:
    from .app import App as Viewer

SEND_PERIOD_S = 1.0 / 30.0


class Throttle:
    """Sends at most once per period; the latest value is always sent eventually by flush()."""

    def __init__(self, viewer: "Viewer", send):
        viewer.throttles.append(self)
        self._send = send
        self._last = 0.0
        self._pending = None
        self._lock = threading.Lock()

    def __call__(self, value) -> None:
        with self._lock:
            self._pending = value
            if time.monotonic() - self._last < SEND_PERIOD_S:
                return
        self.flush()

    def flush(self) -> None:
        with self._lock:
            value, self._pending = self._pending, None
            if value is None:
                return
            self._last = time.monotonic()
        self._send(value)


class PosePort:
    def __init__(self, viewer: "Viewer", module: str, port: dict):
        self.viewer = viewer
        self.module = module
        self.name = port["name"]
        self.reference = port.get("reference", "")
        self.edited = 0.0
        self.dragging = False
        label = f"{module}.{self.name}" + (f" in {self.reference}" if self.reference else "")
        self.gizmo = viewer.server.scene.add_transform_controls(f"/ports/{module}/{self.name}", scale=0.16, line_width=2.0, depth_test=False, opacity=0.85)
        self.label = viewer.server.scene.add_label(f"/ports/{module}/{self.name}/label", label)
        viewer.labels.append(self.label)
        viewer.gizmos.append(self.gizmo)
        self.on_local = None
        self.send = Throttle(viewer, lambda local: viewer.request("set_port", module, port=self.name, value=local))
        self.gizmo.on_update(self._event)

    def set_hidden(self, hidden: bool) -> None:
        ids = {id(self.gizmo), id(self.label)}
        if hidden == ids.issubset(self.viewer.hidden):
            return
        if hidden:
            self.viewer.hidden |= ids
        else:
            self.viewer.hidden -= ids
        self.viewer.apply_display()

    def command(self, local: np.ndarray) -> None:
        """Moves the gizmo to a pose typed in the reference frame and sends it."""
        self.edited = time.monotonic()
        set_pose(self.gizmo, self.viewer.reference_pose(self.reference) @ local)
        self.send(pose_to_wire(local[:3, 3], matrix_to_wxyz(local[:3, :3])))
        self.send.flush()

    def _event(self, event) -> None:
        self.dragging = event.phase != "end"
        self._moved()
        if event.phase == "end":
            self.send.flush()

    def _moved(self) -> None:
        self.edited = time.monotonic()
        world = np.eye(4)
        world[:3, :3] = wxyz_to_matrix(np.asarray(self.gizmo.wxyz))
        world[:3, 3] = self.gizmo.position
        local = np.linalg.inv(self.viewer.reference_pose(self.reference)) @ world
        self.send(pose_to_wire(local[:3, 3], matrix_to_wxyz(local[:3, :3])))
        if self.on_local is not None:
            self.on_local(local)

    def show(self, value: dict) -> None:
        if self.dragging or time.monotonic() - self.edited < EDIT_HOLD_S:
            return
        local = np.eye(4)
        local[:3, :3] = wxyz_to_matrix(np.asarray(value["rotation"], dtype=float))
        local[:3, 3] = value["translation"]
        set_pose(self.gizmo, self.viewer.reference_pose(self.reference) @ local)


class VectorPort:
    """Sliders per element; ``sliders=False`` leaves the controls to the caller, which drives :meth:`command`."""

    def __init__(self, viewer: "Viewer", module: str, port: dict, sliders: bool = True, stop_button: bool = True,
                 zero_idle: bool = True):
        self.viewer = viewer
        self.edited = 0.0
        self.name = port["name"]
        self.labels = port.get("labels") or []
        self.lower = port.get("lower") or []
        self.upper = port.get("upper") or []
        # A port with timeout_s lapses unless rewritten: the last nonzero value is resent until stopped.
        self.timeout = port.get("timeout_s") or 0.0
        self.zero_idle = zero_idle
        self.live = None
        self.confirmed = False
        self.sent = 0.0
        self.send = Throttle(viewer, self._send)
        self._request = lambda v: viewer.request("set_port", module, port=self.name, value=v, on_fail=self.stop_keepalive)
        self.sliders = []
        self.fixed = []
        # The server always sends one label per element.
        self.values = [0.0] * len(self.labels)
        gui = viewer.server.gui
        ranges = []
        for i, label in enumerate(self.labels if sliders else ()):
            lo, hi = self.viewer.slider_range(
                label, self.lower[i] if i < len(self.lower) else None, self.upper[i] if i < len(self.upper) else None
            )
            ranges.append((lo, hi) if hi > lo else (lo - 1.0, lo + 1.0, True))
        # One step for the whole port so every element shows the same decimals.
        step = min((10.0 ** math.floor(math.log10((r[1] - r[0]) / 200.0)) for r in ranges), default=0.01)
        for label, (lo, hi, *flag) in zip(self.labels, ranges):
            fixed = bool(flag)
            slider = gui.add_slider(
                label, min=lo, max=hi, step=step, initial_value=min(max(0.0, lo), hi), disabled=fixed,
            )
            if fixed:
                slider.value = (lo + hi) / 2.0
            slider.on_update(lambda event, i=len(self.sliders): self._moved(event, i))
            self.sliders.append(slider)
            self.fixed.append(fixed)
        if self.timeout > 0.0 and stop_button:
            button = gui.add_button(viewer.t("stop_port", name=self.name), icon=Icon.HAND_STOP)
            viewer.keyed(button, "label", "stop_port", name=self.name)
            button.on_click(lambda _: self.zero())

    def _send(self, value: list) -> None:
        self.sent = time.monotonic()
        if self.timeout > 0.0:
            live = value if any(v != 0.0 for v in value) or not self.zero_idle else None
            if live != self.live:
                self.confirmed = False
            self.live = live
        self._request(value)

    def stop_keepalive(self) -> None:
        self.live = None

    def keepalive(self, now: float) -> None:
        if self.live is not None and now - self.sent >= self.timeout / 2.0:
            self._send(self.live)

    def command(self, values: list) -> None:
        self.edited = time.monotonic()
        self.values = [float(v) for v in values]
        self.send(list(self.values))

    def zero(self) -> None:
        self.edited = time.monotonic()
        self.values = [0.0] * len(self.values)
        for slider in self.sliders:
            slider.value = min(max(0.0, slider.min), slider.max)
        self.send(list(self.values))
        self.send.flush()

    def _moved(self, event, i: int) -> None:
        if event.client is None:  # Programmatic update from show().
            return
        if self.sliders[i].disabled:  # The number box still edits a disabled slider: put it back.
            self.sliders[i].value = min(max(self.values[i], self.sliders[i].min), self.sliders[i].max)
            return
        self.edited = time.monotonic()
        self.values[i] = float(self.sliders[i].value)
        self.send(list(self.values))

    def show(self, value: list) -> None:
        if len(value) != len(self.labels):
            return
        if self.live is not None:
            held = all(math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-9) for a, b in zip(self.live, value))
            # Once the server has shown our value, a different one means another writer won, even mid-edit.
            if held:
                self.confirmed = True
            elif self.confirmed or time.monotonic() - self.edited >= EDIT_HOLD_S:
                self.live = None
        if time.monotonic() - self.edited < EDIT_HOLD_S:
            return
        self.values = [float(v) for v in value]
        for slider, v in zip(self.sliders, self.values):
            slider.value = min(max(v, slider.min), slider.max)


class ScalarPort:
    def __init__(self, viewer: "Viewer", module: str, port: dict):
        self.edited = 0.0
        self.name = port["name"]
        self.flag = port["type"] == "flag"
        gui = viewer.server.gui
        lo = (port.get("lower") or [None])[0]
        hi = (port.get("upper") or [None])[0]
        if self.flag:
            self.control = gui.add_checkbox(self.name, initial_value=False)
        elif finite(lo) and finite(hi) and lo < hi:
            self.control = gui.add_slider(
                self.name, min=lo, max=hi, step=(hi - lo) / 1000.0, initial_value=min(max(0.0, lo), hi)
            )
        else:
            # Text, not a number input: the browser's number input mangles "1e-6" into "-16".
            self.control = gui.add_text(self.name, "0")
        self.control.on_update(lambda event: self._changed(event, viewer, module))

    def _changed(self, event, viewer: "Viewer", module: str) -> None:
        if event.client is None:
            return
        self.edited = time.monotonic()
        if self.flag:
            value = bool(self.control.value)
        else:
            try:
                value = float(self.control.value)
            except ValueError:
                return  # Partial input such as "1e-" while typing.
            if not math.isfinite(value):
                return
        viewer.request("set_port", module, port=self.name, value=value)

    def show(self, value) -> None:
        if time.monotonic() - self.edited < EDIT_HOLD_S:
            return
        if not self.flag and hasattr(self.control, "min"):
            lo, hi = self.control.min, self.control.max
            value = min(max(value, lo if lo is not None else value), hi if hi is not None else value)
        elif not self.flag:
            value = format(value, ".10g")
        self.control.value = value
