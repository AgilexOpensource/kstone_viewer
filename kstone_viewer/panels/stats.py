"""Engine stats tables: counters with rates (safety tab) and durations (overview)."""

from collections import deque
from typing import TYPE_CHECKING

from .plots import scalar

if TYPE_CHECKING:
    from ..app import App as Viewer

RATE_WINDOW_S = 1.0


class StatsPanel:
    """Every stat of `kind` the model declares, so new engine stats show up without viewer changes."""

    def __init__(self, viewer: "Viewer", hello: dict, kind: str):
        t = viewer.t
        self.t = t
        self.kind = kind
        gui = viewer.server.gui
        self.stats = [s for s in hello.get("stats", []) if s.get("kind") == kind]
        self.history = deque()
        self.text = gui.add_markdown(t("waiting"))

    def append(self, state: dict) -> None:
        now = state["time"]
        while self.history and (self.history[0][0] > now or now - self.history[0][0] > RATE_WINDOW_S):
            self.history.popleft()
        stats = state.get("stats") or {}
        self.history.append((now, {s["name"]: scalar(stats.get(s["name"])) for s in self.stats}))

    def draw(self, state: dict) -> None:
        if not self.history:
            return
        t = self.t
        first_time, first = self.history[0]
        last_time, last = self.history[-1]
        dt = last_time - first_time
        counters = self.kind == "counter"
        rows = [t("safety_header"), "---|---|---"] if counters else [t("latency_header"), "---|---"]
        for s in self.stats:
            name, value = s["name"], last[s["name"]]
            label = t.get(f"stat_{name}", name)
            if value is None:
                rows.append(f"{label}|--|" if counters else f"{label}|--")
            elif not counters:
                rows.append(f"{label}|{value * 1e3:.3f} ms")
            else:
                start = first[name]
                rate = (value - start) / dt if dt > 0 and start is not None else 0.0
                rows.append(f"{label}|{value:.0f}|{rate:.1f}")
        if counters:
            fresh = t("safety_fresh") if state.get("fresh", True) else t("safety_stale")
            rows = [f"t **{state['time']:.2f} s** · {fresh}", "", *rows]
        text = "\n".join(rows)
        if self.text.content != text:
            self.text.content = text
