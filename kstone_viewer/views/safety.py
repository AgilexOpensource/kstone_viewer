"""Safety-tab views: centre-of-mass support polygon and self / world collision barriers."""

import math
import time

import numpy as np

from .. import html
from ..geometry import matrix_to_rpy, rpy_to_matrix, wxyz_to_matrix
from ..ports import PosePort
from ..tune import EDIT_HOLD_S
from .base import ModuleView

CLOSED_LOOP = ("闭环（按反馈关节）", "Closed loop (joint feedback)")
CLOSED_LOOP_HINT = ("开环：只按指令关节计算；闭环：同时按指令关节和实测关节（指令 + 最近一次反馈的跟踪误差）计算，"
                    "没有新鲜反馈时只按指令。指令始终按指令关节下发",
                    "Open loop: evaluated at the commanded joints only; closed loop: at both the commanded and the "
                    "measured joints (command + the latest feedback's tracking error), only the commanded ones "
                    "without fresh feedback. The command always follows the commanded joints")


def number(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) \
        else None


class SafetyView(ModuleView):
    tab = "safety"
    plot_lead: tuple = ()

    def build(self) -> None:
        self.card = self.ui.html(html.card(html.badge_row(self.badges(None))))
        self.add_enabled("启用", "Enabled")
        self.loop = None
        if "closed_loop" in (self.module.get("tune_schema") or {}).get("properties", {}):
            self.loop = self.ui.checkbox(*CLOSED_LOOP, bool(self.params.get("closed_loop")), hint=CLOSED_LOOP_HINT)
            self.loop.on_update(self.toggle_loop)
        self.build_extra()
        self.add_ports()
        self.add_tune(exclude=(("closed_loop",),) + self.tune_exclude)
        self.add_plot(self.plot_lead)

    tune_exclude: tuple = ()

    def build_extra(self) -> None:
        pass

    def toggle_loop(self, event) -> None:
        if event.client is None or not self.ui.user():
            return
        self.loop_edited = time.monotonic()
        self.app.request("tune", self.id, params={"closed_loop": bool(self.loop.value)}, on_fail=self.revert_loop)

    def revert_loop(self) -> None:
        self.loop_edited = 0.0
        self.show_loop()

    def show_loop(self) -> None:
        if self.loop is not None and time.monotonic() - getattr(self, "loop_edited", 0.0) >= EDIT_HOLD_S:
            self.ui.set_value(self.loop, bool(self.params.get("closed_loop")))

    def on_params(self, values: dict) -> None:
        super().on_params(values)
        self.loop_edited = 0.0
        self.show_loop()
        self.update()

    def loop_badge(self) -> str:
        if not self.params.get("closed_loop"):
            return html.badge(self.ui.t("开环", "open loop"), "muted")
        display = (self.state or {}).get("display")
        if isinstance(display, dict) and display.get("at") == "measured":
            return html.badge(self.ui.t("闭环 · 指令 + 反馈", "closed loop · command + feedback"), "ok")
        return html.badge(self.ui.t("闭环 · 无反馈，按指令", "closed loop · no feedback, command"), "warn")

    def at_badge(self) -> str:
        display = (self.state or {}).get("display")
        at = display.get("at") if isinstance(display, dict) else None
        names = {"measured": ("按实测关节", "at measured"), "command": ("按指令关节", "at command")}
        return html.badge(self.ui.t(*names.get(at, (at, at))), "info") if at else ""


class CenterOfMassView(SafetyView):
    tune_exclude = (("payloads",),)

    def title(self):
        return f"质心 · {self.id}", f"Centre of mass · {self.id}"

    def payload_list(self) -> list:
        payloads = self.params.get("payloads")
        return [p for p in payloads if isinstance(p, dict)] if isinstance(payloads, list) else []

    def build_extra(self) -> None:
        """One mass slider per configured payload, e.g. to preset the expected load before a grasp."""
        self.masses, self.mass_edited = [], 0.0
        if "payloads" not in (self.module.get("tune_schema") or {}).get("properties", {}):
            return
        for i, p in enumerate(self.payload_list()):
            frame, mass = str(p.get("frame") or i), float(p.get("mass") or 0.0)
            slider = self.ui.slider(f"负载 · {frame}", f"Payload · {frame}", 0.0, float(max(10, math.ceil(4 * mass))),
                                    0.01, mass, hint=(f"挂在 {frame} 上的额外质量 (kg)，计入质心；抓取前可预设预期负载",
                                                      f"extra mass at {frame} (kg), counted in the CoM; preset the "
                                                      "expected load before a grasp"))
            slider.on_update(lambda event, i=i: self.set_mass(event, i))
            self.masses.append(slider)

    def set_mass(self, event, index: int) -> None:
        if event.client is None or not self.ui.user():
            return
        self.mass_edited = time.monotonic()
        payloads = [dict(p) for p in self.payload_list()]
        if index >= len(payloads):
            return
        payloads[index]["mass"] = float(self.masses[index].value)
        self.app.request("tune", self.id, params={"payloads": payloads}, on_fail=self.revert_masses)

    def revert_masses(self) -> None:
        self.mass_edited = 0.0
        self.show_masses()

    def show_masses(self) -> None:
        if time.monotonic() - self.mass_edited < EDIT_HOLD_S:
            return
        for slider, p in zip(self.masses, self.payload_list()):
            self.ui.set_value(slider, float(p.get("mass") or 0.0), tolerance=1e-9)

    def on_params(self, values: dict) -> None:
        super().on_params(values)
        self.mass_edited = 0.0
        self.show_masses()

    def update(self) -> None:
        t, out = self.ui.t, self.outputs()
        slack, margin = number(out.get("min_slack")), number(self.params.get("margin")) or 0.0
        running = bool((self.state or {}).get("running"))
        tone = "muted" if slack is None or not running else ("bad" if slack < 0 else ("warn" if slack < margin else "ok"))
        ground = out.get("ground")
        payloads = self.params.get("payloads") if isinstance(self.params.get("payloads"), list) else []
        mass = sum(float(p.get("mass") or 0.0) for p in payloads if isinstance(p, dict))
        cells = [html.stat(t("最小裕度", "min slack"), "--" if slack is None else f"{slack * 1000:.1f} mm", tone,
                           t("余量", "margin") + f" {margin * 1000:.0f} mm"),
                 html.stat(t("地面投影", "ground point"), f"{round(ground[0] * 1000)}, {round(ground[1] * 1000)}" if isinstance(ground, list) else "--",
                           None, "x, y [mm]"),
                 html.stat(t("负载", "payload"), f"{mass:.2f} kg", None, t(f"{len(payloads)} 个", f"{len(payloads)} items"))]
        badges = self.badges(self.state)
        if slack is not None:
            if slack < 0:
                badges.append(html.badge(t("在多边形外", "outside polygon"), "bad"))
            elif slack < margin:
                badges.append(html.badge(t("进入余量带", "in margin band"), "warn"))
            else:
                badges.append(html.badge(t("在多边形内", "inside"), "ok"))
        badges += [self.loop_badge(), self.at_badge()]
        self.set_html(self.card, html.card(html.grid(cells, 3) + html.badge_row(badges), tone=tone))


class Obstacle:
    """One world obstacle: enabled flag, pose typed as xyz (m) + rpy (deg), and its gizmo."""

    def __init__(self, view: "CollisionView", name: str, spec: dict):
        ui, self.ui, self.app, self.module, self.name = view.ui, view.ui, view.app, view.id, name
        self.edited = self.flag_edited = 0.0
        self.enabled = ui.checkbox("启用", "Enabled", False)
        self.xyz = ui.vector3("位置 (m)", "Position (m)", (0.0, 0.0, 0.0), step=0.001)
        self.rpy = ui.vector3("姿态 rpy (°)", "Orientation rpy (deg)", (0.0, 0.0, 0.0), step=0.1,
                              hint=("固定轴 roll/pitch/yaw：R = Rz Ry Rx", "Fixed-axis roll/pitch/yaw: R = Rz Ry Rx"))
        self.pose = PosePort(view.app, view.id, spec)
        self.pose.on_local = self.show_local
        self.enabled.on_update(self.toggled)
        self.xyz.on_update(self.typed)
        self.rpy.on_update(self.typed)

    def toggled(self, event) -> None:
        if event.client is None or not self.ui.user():
            return
        self.flag_edited = time.monotonic()
        value = bool(self.enabled.value)
        self.pose.set_hidden(not value)
        self.app.request("set_port", self.module, port=f"{self.name}/enabled", value=value)

    def typed(self, event) -> None:
        if event.client is None or not self.ui.user():
            return
        self.edited = time.monotonic()
        local = np.eye(4)
        local[:3, :3] = rpy_to_matrix([math.radians(float(v)) for v in self.rpy.value])
        local[:3, 3] = [float(v) for v in self.xyz.value]
        self.pose.command(local)

    def show_local(self, local: np.ndarray) -> None:
        self.ui.set_value(self.xyz, tuple(round(float(v), 4) for v in local[:3, 3]))
        self.ui.set_value(self.rpy, tuple(round(math.degrees(v), 2) for v in matrix_to_rpy(local[:3, :3])))

    def show(self, pose, flag) -> None:
        now = time.monotonic()
        if isinstance(flag, bool) and now - self.flag_edited >= EDIT_HOLD_S:
            self.ui.set_value(self.enabled, flag)
            self.pose.set_hidden(not flag)
        if not isinstance(pose, dict):
            return
        self.pose.show(pose)
        if self.pose.dragging or now - max(self.edited, self.pose.edited) < EDIT_HOLD_S:
            return
        local = np.eye(4)
        local[:3, :3] = wxyz_to_matrix(np.asarray(pose["rotation"], dtype=float))
        local[:3, 3] = pose["translation"]
        self.show_local(local)


class CollisionView(SafetyView):
    plot_lead = ("min_distance",)
    obstacles: dict = {}

    def add_ports(self, names=None) -> None:
        specs = self.port_specs
        found = [n for n, p in specs.items() if self.type == "world_collision" and p["type"] == "pose"
                 and specs.get(f"{n}/enabled", {}).get("type") == "flag"]
        rest = [n for n in specs if n not in found and n.removesuffix("/enabled") not in found]
        super().add_ports(rest if names is None else [n for n in names if n in rest])
        self.obstacles = {}
        for name in found:
            with self.ui.folder(f"障碍物 · {name}", f"Obstacle · {name}", expand=False):
                self.obstacles[name] = Obstacle(self, name, specs[name])

    def show(self, state: dict) -> None:
        super().show(state)
        ports = state.get("ports") or {}
        for name, obstacle in self.obstacles.items():
            obstacle.show(ports.get(name), ports.get(f"{name}/enabled"))

    def title(self):
        if self.type == "self_collision":
            return f"自碰撞 · {self.id}", f"Self collision · {self.id}"
        return f"环境碰撞 · {self.id}", f"World collision · {self.id}"

    def update(self) -> None:
        t, out = self.ui.t, self.outputs()
        constraint = self.params.get("constraint") if isinstance(self.params.get("constraint"), dict) else {}
        d_min = number(constraint.get("d_min")) or 0.0
        activation = d_min + (number(constraint.get("activation_margin")) or 0.0)
        distance = number(out.get("min_distance"))
        tone = "muted" if distance is None else ("bad" if distance < d_min else ("warn" if distance < activation else "ok"))
        cells = [html.stat(t("最近距离", "closest"), "--" if distance is None else f"{distance * 1000:.1f} mm", tone,
                           f"d_min {d_min * 1000:.1f} mm"),
                 html.stat(t("屏障起作用于", "barrier from"), f"{activation * 1000:.1f} mm", None,
                           t("d_min + 激活余量", "d_min + activation margin")),
                 html.stat(t("增益", "gain"), html.fmt(constraint.get("gain", "--")))]
        active, pairs = number(out.get("active_pairs")), number(out.get("pairs"))
        if pairs is not None:
            cells[2] = html.stat(t("激活屏障", "barriers"), "--" if active is None else str(int(active)),
                                 "warn" if active else None, f"{int(pairs)} {t('对', 'pairs')}")
        scale = max(activation * 2.0, 1e-6)
        bar = html.bar(1.0 if distance is None else distance / scale, tone or "info", marker=d_min / scale)
        badges = self.badges(self.state) + [self.loop_badge(), self.at_badge()]
        if distance is None:
            badges.append(html.badge(t("无障碍物", "no obstacles"), "muted"))
        body = html.grid(cells, 3) + bar + "<div style='height:6px'></div>" + html.badge_row(badges)
        if "pair" in out:
            body += html.rows([(t("最近对", "pair"), html.esc(out.get("pair") or "–"))])
        self.set_html(self.card, html.card(body, tone=tone if tone != "muted" else None))
