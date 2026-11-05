"""Behavior-tab views (gaze)."""

from .. import html
from ..tune import finite
from .base import ModuleView

MODE_TONES = {"gaze": "ok", "home": "info", "paused": "warn", "disabled": "muted"}
MODE_TEXT = {"gaze": ("注视", "gaze"), "home": ("回初始", "home"), "paused": ("被占用", "paused"),
             "disabled": ("已禁用", "disabled")}


class GazeView(ModuleView):
    tab = "behaviors"

    def title(self):
        return f"注视 · {self.id}", f"Gaze · {self.id}"

    def build(self) -> None:
        self.card = self.ui.html(html.card(html.badge_row(self.badges(None))))
        self.add_enabled("启用", "Enabled")
        self.add_home()
        self.add_ports()
        self.add_tune(inline=True)
        self.add_plot()

    def mode(self) -> str:
        state = self.state or {}
        if not state.get("enabled"):
            return "disabled"
        if state.get("held_by"):
            return "paused"
        return "gaze" if (self.outputs().get("active_frames") or 0) > 0 else "home"

    def update(self) -> None:
        t = self.ui.t
        if self.state is None:
            return
        mode = self.mode()
        focus = self.outputs().get("focus")
        badges = [html.badge(t(*MODE_TEXT[mode]), MODE_TONES[mode])] + self.badges(self.state)[1:]
        if self.state.get("held_by"):
            badges.insert(1, self.badges(self.state)[0])
        n = self.outputs().get("active_frames")
        body = html.badge_row(badges) + html.rows([
            (t("注视点", "focus"), html.esc(" ".join(html.signed(v) for v in focus))
             if isinstance(focus, list) and all(finite(v) for v in focus) else "–"),
            (t("活动 frame", "active frames"), html.esc(str(int(n))) if finite(n) else "–")])
        self.set_html(self.card, html.card(body, tone="warn" if mode == "paused" else None))
