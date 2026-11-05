"""Shared parts of the per-type module views: status badges, enable/home, ports, tuning and the module plot."""

import time
from typing import TYPE_CHECKING, Any

from .. import html
from ..panels.plots import PlotPanel
from ..ports import PosePort, ScalarPort, VectorPort
from ..tune import EDIT_HOLD_S, TunePanel, finite

if TYPE_CHECKING:
    from ..app import App


def output_text(outputs: list, display) -> str:
    """Number and flag outputs as HTML rows, "" when there are none."""
    if not isinstance(display, dict):
        return ""
    values = display.get("outputs", {})
    pairs = []
    for out in outputs:
        v = values.get(out["name"])
        if out["type"] == "flag":
            pairs.append((out["name"], html.badge("✓" if v else "✗", "ok" if v else "muted")))
        elif out["type"] == "number":
            unit = f" {out['unit']}" if out.get("unit") else ""
            pairs.append((out["name"], html.esc(f"{v:.4g}{unit}" if finite(v) else "--")))
    if not pairs:
        return ""
    pairs.append(("@", html.esc(display.get("at", "?"))))
    return html.rows(pairs)


HOME_STATES = {
    "waiting": ("回零排队", "homing queued", "info"),
    "executing": ("回零中", "homing", "info"),
    "timed_out": ("回零超时：连续 timeout_s 无进展", "homing timed out (no progress for timeout_s)", "warn"),
    "aborted": ("回零中止", "homing aborted", "muted"),
}


PREEMPT_SHOW_S = 5.0


def plain(text: str) -> str:
    return text.replace("**", "")


class ModuleView:
    """One module's controls; subclasses lay out ``build`` and ``update`` (``update`` runs at the text rate)."""

    tab = "extensions"

    def __init__(self, app: "App", module: dict):
        self.app = app
        self.ui = app.ui
        self.t = app.t
        self.module = module
        self.id = module["id"]
        self.type = module["type"]
        self.homeable = bool(module.get("homeable", False))
        self.port_specs = {p["name"]: p for p in module.get("ports") or []}
        self.declared = module.get("outputs") or []
        self.params = module.get("params") or {}
        self.ports: dict[str, Any] = {}
        self.tune = None
        self.plot = None
        self.enabled = None
        self.edited = 0.0
        self.state = None
        self.folder = None
        self.preempt_n = None
        self.preempt_at = None

    @property
    def anchor(self) -> str | None:
        """Id of the module whose folder this view nests in, or None for its own tab."""
        return None

    def title(self) -> tuple[str, str]:
        return (f"{self.id} ({self.type})",) * 2

    def attach(self, parent=None) -> None:
        if parent is not None:
            with parent:
                self.folder = self.ui.folder(*self.title())
        else:
            self.folder = self.ui.folder(*self.title())
        self.ui.anchors[self.id] = self.folder
        with self.folder:
            self.build()

    def build(self) -> None:
        raise NotImplementedError

    # -- helpers -------------------------------------------------------------------------------------------------
    def add_enabled(self, zh: str = "", en: str = "") -> None:
        if zh:
            self.enabled = self.ui.checkbox(zh, en, False, disabled=True)
        else:
            self.enabled = self.ui.key(self.ui.gui.add_checkbox(self.t("enabled"), False, disabled=True), "label",
                                       "enabled")
        self.enabled.on_update(self._toggle)

    def add_home(self) -> None:
        if self.homeable:
            button = self.ui.button("回零", "Home", icon="HOME",
                                    hint=("按 home_motion 缓慢回到初始目标", "Ramp back to the initial target (home_motion)"))
            button.on_click(lambda _: self.app.request("home", self.id))

    def add_ports(self, names=None) -> None:
        for name, port in self.port_specs.items():
            if names is not None and name not in names:
                continue
            if port["type"] == "pose":
                self.ports[name] = PosePort(self.app, self.id, port)
            elif port["type"] == "vector":
                self.ports[name] = VectorPort(self.app, self.id, port)
            else:
                self.ports[name] = ScalarPort(self.app, self.id, port)

    def add_tune(self, exclude: tuple = (), inline: bool = False) -> None:
        if self.module.get("tunable") and isinstance(self.module.get("tune_schema"), dict):
            self.tune = TunePanel(self.app, self.id, self.module["tune_schema"], self.params, self.t,
                                  kind=self.type, exclude=exclude, inline=inline)

    def add_plot(self, lead: tuple = ()) -> None:
        """lead: output names filling the plot slots first (default: all own keys in order)."""
        self.plot = PlotPanel(self.app, "module_plot", owner=self.id, expand=False, lead=lead)

    def _toggle(self, event) -> None:
        if event.client is None or not self.ui.user():
            return
        self.edited = time.monotonic()
        self.app.request("set_enabled", self.id, enabled=bool(self.enabled.value))

    def badges(self, state: dict | None) -> list[str]:
        t = self.t
        if state is None:
            return [html.badge(t("waiting"))]
        out = [html.badge(t("running"), "ok") if state.get("running") else html.badge(t("idle"))]
        if state.get("held_by"):
            out = [html.badge(plain(t("held_by", by=state["held_by"])), "warn")]
        elif not state.get("enabled"):
            out = [html.badge(t("disabled"), "muted")]
        if self.recently_preempted(int(state.get("preempted") or 0)):
            out.append(html.badge(t("preempted", n=int(state["preempted"])), "info"))
        home = HOME_STATES.get(state.get("home"))
        if home is not None:
            out.append(html.badge(plain(home[0] if t.code == "zh" else home[1]), home[2]))
        return out

    def recently_preempted(self, n: int) -> bool:
        # The count is cumulative; only a rise seen by this viewer shows, for PREEMPT_SHOW_S.
        now = time.monotonic()
        if self.preempt_n is None:
            self.preempt_n = n
        elif n != self.preempt_n:
            self.preempt_at = now if n > self.preempt_n else None
            self.preempt_n = n
        return self.preempt_at is not None and now - self.preempt_at < PREEMPT_SHOW_S

    def outputs(self) -> dict:
        display = (self.state or {}).get("display")
        return display.get("outputs") or {} if isinstance(display, dict) else {}

    @staticmethod
    def set_html(handle, content: str) -> None:
        if handle.content != content:
            handle.content = content

    # -- server messages -----------------------------------------------------------------------------------------
    def show(self, state: dict) -> None:
        self.state = state
        if self.enabled is not None:
            self.enabled.disabled = False
            if time.monotonic() - self.edited >= EDIT_HOLD_S:
                self.ui.set_value(self.enabled, bool(state.get("enabled")))
        for name, value in (state.get("ports") or {}).items():
            if name in self.ports:
                self.ports[name].show(value)

    def update(self) -> None:
        """Redraw the cards (text rate; also after a language switch)."""

    def on_params(self, values: dict) -> None:
        self.params = values
        if self.tune is not None:
            self.tune.show(values)

    def flush(self, now: float) -> None:
        for port in self.ports.values():
            if isinstance(port, VectorPort):
                port.keepalive(now)

    def stop_keepalive(self) -> None:
        for port in self.ports.values():
            if isinstance(port, VectorPort):
                port.stop_keepalive()

    def close(self) -> None:
        pass
