"""Plottable samples of the state message and the plot panels that show them."""

import threading
from collections import deque
from typing import TYPE_CHECKING

import numpy as np
import viser.uplot
from viser import Icon

from ..tune import finite

if TYPE_CHECKING:
    from ..app import App as Viewer

PLOT_PERIOD_S = 0.1
PLOT_SAMPLES = 300
PLOT_COLORS = ("#2563eb", "#dc2626", "#16a34a", "#9333ea")
PLOT_SLOTS = 3


def scalar(v) -> float | None:
    if isinstance(v, bool):
        return float(v)
    return float(v) if isinstance(v, (int, float)) and finite(v) else None


def with_unit(name: str, unit: str | None) -> str:
    return f"{name} [{unit}]" if unit else name


class Samples:
    """Every plottable scalar of a state message keyed by a stable name, and the module owning each key.

    Owner ranks order a module's keys: residuals, then outputs, then ports."""

    def __init__(self, hello: dict):
        # Durations are plotted in ms; the model declares which stats are durations.
        self.stats = {
            s["name"]: (with_unit(f"engine/{s['name']}", "ms" if s.get("kind") == "duration" else None),
                        1e3 if s.get("kind") == "duration" else 1.0)
            for s in hello.get("stats", [])
        }
        self.joints = [(j["idx_q"], f"q/{j['name']}") for j in hello["joints"] if j["nq"] == 1]
        self.owners = {}
        self.outputs = {}
        self.margins = []
        self.labels = {}
        for m in hello["modules"]:
            mid = m["id"]
            outputs = []
            for o in m.get("outputs", []):
                if o["type"] not in ("number", "flag"):
                    continue
                key = with_unit(f"{mid}/{o['name']}", o.get("unit"))
                outputs.append((o["name"], key))
                self.owners[key] = (mid, 1)
                if o.get("role") == "margin" and o["type"] == "number":
                    self.margins.append(key)
            self.outputs[mid] = outputs
            for port in m.get("ports", []):
                self.labels[(mid, port["name"])] = port.get("labels") or []

    def stat_key(self, name: str) -> str | None:
        return self.stats[name][0] if name in self.stats else None

    def __call__(self, state: dict) -> dict:
        out = {}
        stats = state.get("stats") or {}
        stages = state.get("stages") or []
        if stages and all(scalar(s.get("solve_s")) is not None for s in stages):
            out["solver/qp [ms]"] = sum(float(s["solve_s"]) for s in stages) * 1e3
        for name, (key, scale) in self.stats.items():
            if (v := scalar(stats.get(name))) is not None:
                out[key] = v * scale
        for r in state.get("residuals", []):
            if (v := scalar(r.get("residual"))) is not None:
                key = f"residual/{r['name']}"
                out[key] = v
                if r.get("module") is not None:
                    self.owners.setdefault(key, (r["module"], 0))
        for m in state.get("modules", []):
            mid = m.get("id")
            display = m.get("display")
            if isinstance(display, dict):
                values = display.get("outputs") or {}
                for name, key in self.outputs.get(mid, ()):
                    if (x := scalar(values.get(name))) is not None:
                        out[key] = x
            for name, value in (m.get("ports") or {}).items():
                if isinstance(value, list):
                    labels = self.labels.get((mid, name), ())
                    keys = [f"{mid}.{name}[{label}]" for label in labels]
                    values = [scalar(v) for v in value]
                else:
                    keys, values = [f"{mid}.{name}"], [scalar(value)]
                for key, x in zip(keys, values):
                    if x is not None:
                        out[key] = x
                        self.owners.setdefault(key, (mid, 2))
        q = state.get("q", [])
        for i, key in self.joints:
            if i < len(q):
                out[key] = float(q[i])
        return out


class PlotPanel:
    """A uPlot of up to PLOT_SLOTS chosen series; changing the choice restarts the history.

    The lead keys (an owner module's own keys, else the preset keys that exist) come first in the options
    and fill the choices until the user picks one."""

    def __init__(self, viewer: "Viewer", title: str, owner: str | None = None, preset: list = (),
                 expand: bool = True, on_remove=None, fields: dict | None = None, lead: tuple = ()):
        t = viewer.t
        self.viewer = viewer
        self.owner = owner
        self.lead = tuple(f"{owner}/{name}" for name in lead)
        self.preset = [k for k in preset if k is not None]
        self.picked = False
        self.none = t("plot_none")
        self.lock = threading.Lock()
        self.plot = None
        self.code = t.code
        self.keys = []
        self.times = deque(maxlen=PLOT_SAMPLES)
        self.values = []
        gui = viewer.server.gui
        self.folder = gui.add_folder(t(title, **(fields or {})), expand_by_default=expand)
        viewer.keyed(self.folder, "label", title, **(fields or {}))
        with self.folder:
            self.choices = []
            for i in range(max(PLOT_SLOTS, len(self.preset))):
                choice = gui.add_dropdown(t("plot_series", n=i + 1), [self.none], initial_value=self.none)
                viewer.keyed(choice, "label", "plot_series", n=i + 1)
                choice.on_update(self._picked)
                self.choices.append(choice)
            if on_remove is not None:
                button = gui.add_button(t("plot_remove"), color="red", icon=Icon.TRASH)
                viewer.keyed(button, "label", "plot_remove")
                button.on_click(lambda _: on_remove(self))

    def remove(self) -> None:
        with self.lock:
            self.keys = []
            self.plot = None
            self.folder.remove()

    def _picked(self, event) -> None:
        if event.client is not None:
            self.picked = True
        self._reset()

    def _reset(self) -> None:
        with self.lock:
            keys = list(dict.fromkeys(c.value for c in self.choices if c.value != self.none))
            relabel = self.code != self.viewer.t.code
            if keys == self.keys and self.plot is not None and not relabel:
                return
            self.code = self.viewer.t.code
            if keys != self.keys:
                self.keys = keys
                self.times.clear()
                self.values = [deque(maxlen=PLOT_SAMPLES) for _ in keys]
            if self.plot is not None:
                self.plot.remove()
                self.plot = None
            if not keys:
                return
            with self.folder:
                self.plot = self.viewer.server.gui.add_uplot(
                    data=(np.zeros(1),) * (len(keys) + 1),
                    series=(
                        viser.uplot.Series(label=self.viewer.t("plot_time")),
                        *(viser.uplot.Series(label=k, stroke=PLOT_COLORS[i % len(PLOT_COLORS)])
                          for i, k in enumerate(keys)),
                    ),
                    scales={"x": viser.uplot.Scale(time=False)},
                    height=220,
                )

    def set_keys(self, keys: list, owners: dict) -> None:
        if self.owner is not None:
            lead = sorted((k for k in keys if owners.get(k, (None,))[0] == self.owner), key=lambda k: owners[k][1])
            if self.lead:
                lead = [k for k in lead if k.split(" ")[0] in self.lead] or lead
        else:
            present = set(keys)
            lead = [k for k in self.preset if k in present]
        first = set(lead)
        options = (self.none, *lead, *(k for k in keys if k not in first))
        for choice in self.choices:
            if tuple(choice.options) != options:
                choice.options = options
        if lead and not self.picked:
            for choice, key in zip(self.choices, lead):
                if choice.value != key:
                    choice.value = key

    def append(self, now: float, values: dict) -> None:
        with self.lock:
            if not self.keys:
                return
            self.times.append(now)
            for series, key in zip(self.values, self.keys):
                # A missing sample repeats the previous one; uPlot gets no NaN.
                series.append(values.get(key, series[-1] if series else 0.0))

    def draw(self) -> None:
        if self.plot is not None and self.code != self.viewer.t.code:
            self._reset()
        with self.lock:
            if self.plot is not None and self.times:
                self.plot.data = (np.array(self.times), *(np.array(s) for s in self.values))
