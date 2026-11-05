"""Task views: frame tasks (pose gizmo), joint targets (sliders) and the null-space preference nested in its task."""

import math
import time

import numpy as np

from .. import html
from ..geometry import matrix_to_rpy, rpy_to_matrix, set_pose, wxyz_to_matrix
from ..ports import Throttle
from ..tune import EDIT_HOLD_S, finite
from .base import ModuleView


def fmt_xyz(p) -> str:
    return " ".join(html.signed(v) for v in p)


def degrees(r: np.ndarray) -> tuple:
    return tuple(round(math.degrees(v), 2) for v in matrix_to_rpy(r))


class FrameTaskView(ModuleView):
    tab = "tasks"

    def __init__(self, app, module):
        super().__init__(app, module)
        self.pose_name = next((n for n, p in self.port_specs.items() if p["type"] == "pose"), None)
        spec = self.port_specs.get(self.pose_name) or {}
        self.frame = spec.get("frame", "")

    def title(self):
        name = f"{self.id} · {self.frame}" if self.frame else self.id
        return name, name

    def build(self) -> None:
        self.card = self.ui.html(html.card(html.badge_row(self.badges(None))))
        self.add_enabled("启用任务", "Enabled")
        self.lock = None
        if "locked" in (self.module.get("tune_schema") or {}).get("properties", {}):
            self.lock = self.ui.checkbox(
                "锁定关节链", "Lock chain", bool(self.params.get("locked")), disabled=not self.module.get("driven"),
                hint=("冻结该链上的关节，目标拖拽失效；需在配置里显式给出 joints",
                      "Freeze the chain joints and disable the target gizmo; needs explicit joints"))
            self.lock.on_update(self.toggle_lock)
        self.add_home()
        if self.pose_name is not None:
            here = self.ui.button("目标 = 当前位姿", "Target = current pose", icon="CURRENT_LOCATION",
                                  hint=("把目标移到末端当前位姿", "Move the target onto the frame's current pose"))
            here.on_click(lambda _: self.target_here())
        self.add_ports()
        self.entry_edited = 0.0
        self.entry_xyz = self.entry_rpy = self.pose_html = None
        if self.pose_name is not None:
            reference = self.port_specs[self.pose_name].get("reference") or ""
            where = (f"（{reference} 系）", f" (in {reference})") if reference else ("", "")
            with self.ui.folder("目标输入", "Target entry", expand=False):
                self.entry_xyz = self.ui.vector3(f"位置 (m){where[0]}", f"Position (m){where[1]}", (0, 0, 0),
                                                 step=0.001)
                self.entry_rpy = self.ui.vector3("姿态 RPY (°)", "Orientation RPY (°)", (0, 0, 0), step=0.1,
                                                 hint=("固定轴 roll-pitch-yaw", "Fixed-axis roll-pitch-yaw"))
                self.entry_xyz.on_update(self.entered)
                self.entry_rpy.on_update(self.entered)
                self.pose_html = self.ui.html()
        self.add_tune(exclude=(("locked",),))
        self.add_plot()
        self.show_lock()

    @property
    def locked(self) -> bool:
        return bool(self.params.get("locked"))

    def toggle_lock(self, event) -> None:
        if event.client is None or not self.ui.user():
            return
        self.lock_edited = time.monotonic()
        self.app.request("tune", self.id, params={"locked": bool(self.lock.value)}, on_fail=self.revert_lock)

    def revert_lock(self) -> None:
        self.lock_edited = 0.0
        self.show_lock()

    def show_lock(self) -> None:
        port = self.ports.get(self.pose_name)
        if port is not None:
            port.gizmo.visible = not self.locked
        if self.lock is not None and time.monotonic() - getattr(self, "lock_edited", 0.0) >= EDIT_HOLD_S:
            self.ui.set_value(self.lock, self.locked)

    def on_params(self, values: dict) -> None:
        super().on_params(values)
        self.lock_edited = 0.0
        self.show_lock()

    def target_world(self) -> np.ndarray | None:
        port = self.ports.get(self.pose_name)
        if port is None:
            return None
        pose = np.eye(4)
        pose[:3, :3] = wxyz_to_matrix(np.asarray(port.gizmo.wxyz, dtype=float))
        pose[:3, 3] = port.gizmo.position
        return pose

    def entered(self, event) -> None:
        port = self.ports.get(self.pose_name)
        if event.client is None or not self.ui.user() or port is None or self.locked:
            return
        self.entry_edited = time.monotonic()
        local = np.eye(4)
        local[:3, :3] = rpy_to_matrix([math.radians(float(v)) for v in self.entry_rpy.value])
        local[:3, 3] = [float(v) for v in self.entry_xyz.value]
        set_pose(port.gizmo, self.app.reference_pose(port.reference) @ local)
        port._moved()
        port.send.flush()

    def show_entry(self) -> None:
        value = ((self.state or {}).get("ports") or {}).get(self.pose_name)
        if self.entry_xyz is None or not value or time.monotonic() - self.entry_edited < EDIT_HOLD_S:
            return
        r = wxyz_to_matrix(np.asarray(value["rotation"], dtype=float))
        self.ui.set_value(self.entry_xyz, tuple(round(float(v), 4) for v in value["translation"]))
        self.ui.set_value(self.entry_rpy, degrees(r))

    def target_here(self) -> None:
        port = self.ports.get(self.pose_name)
        if port is None or not self.frame:
            return
        current = self.app.reference_pose(self.frame)
        set_pose(port.gizmo, current)
        port._moved()
        port.send.flush()

    def update(self) -> None:
        t = self.ui.t
        cells, tone = [], None
        target = self.target_world()
        out = self.outputs()
        planned = out.get("planned")
        planned = np.asarray(planned, dtype=float) if planned is not None else None
        if target is not None and self.frame and self.state is not None:
            current = self.app.reference_pose(self.frame)
            tracked = planned if planned is not None else target[:3, 3]
            pos = 1e3 * float(np.linalg.norm(tracked - current[:3, 3]))
            cos = (np.trace(current[:3, :3].T @ target[:3, :3]) - 1.0) / 2.0
            rot = math.degrees(math.acos(min(max(cos, -1.0), 1.0)))
            lag = 1e3 * float(out.get("lag") or 0.0)
            cells = [html.stat(t("位置误差", "pos err"), f"{pos:.1f} mm",
                               "ok" if pos < 5 else ("warn" if pos < 30 else "bad")),
                     html.stat(t("姿态误差", "rot err"), f"{rot:.2f}°",
                               "ok" if rot < 1 else ("warn" if rot < 5 else "bad")),
                     html.stat(t("规划滞后", "ramp lag"), f"{lag:.1f} mm", "info" if lag > 0.1 else None)]
            if self.pose_html is not None:
                inverse = np.linalg.inv(self.app.reference_pose(self.ports[self.pose_name].reference))
                local = inverse @ current
                rows = [(t("当前位置", "current xyz"), html.esc(fmt_xyz(local[:3, 3]))),
                        (t("当前姿态°", "current rpy°"), html.esc(" ".join(html.signed(v, 1) for v in degrees(local[:3, :3]))))]
                if planned is not None:
                    rows.append((t("规划位置", "planned xyz"), html.esc(fmt_xyz(inverse[:3, :3] @ planned + inverse[:3, 3]))))
                self.set_html(self.pose_html, html.rows(rows))
        self.show_entry()
        if self.state is not None and self.state.get("held_by"):
            tone = "warn"
        badges = self.badges(self.state)
        if self.locked:
            badges.append(html.badge(t("已锁定", "locked"), "warn"))
        port = self.ports.get(self.pose_name)
        if port is not None and port.dragging:
            badges.append(html.badge(t("拖拽中", "dragging"), "info"))
        levels = self.module.get("levels") or []
        meta = (f"{t('层', 'level')} {'/'.join(str(v) for v in levels) or '–'} · "
                f"{len(self.module.get('driven') or [])} {t('关节', 'joints')}")
        badges.append(f"<span style='font-size:11px;opacity:.6;margin-left:4px'>{html.esc(meta)}</span>")
        body = (html.grid(cells, 3) if cells else "") + html.badge_row(badges)
        self.set_html(self.card, html.card(body, tone=tone))


class JointTargetView(ModuleView):
    tab = "tasks"

    def __init__(self, app, module):
        super().__init__(app, module)
        self.vector_name = next((n for n, p in self.port_specs.items() if p["type"] == "vector"), None)
        self.names = (self.port_specs.get(self.vector_name) or {}).get("labels") or []

    def title(self):
        return f"关节目标 · {self.id}", f"Joint targets · {self.id}"

    def build(self) -> None:
        self.card = self.ui.html(html.card(html.badge_row(self.badges(None))))
        # With claim_idle the checkbox is the ancestor's joint control: hold and claim these joints while checked.
        self.direct = "claim_idle" in self.params
        if self.direct:
            self.control = self.ui.checkbox(
                "关节控制", "Joint control", False, disabled=True,
                hint=("拖动滑块即接管这些关节，其他任务有新输入时交还；勾选后空闲时也一直占用，取消勾选立即交还",
                      "Moving a slider takes these joints until another task gets new input; checked: keep "
                      "them while idle too, unchecked: hand them back at once"))
            self.control.on_update(self.toggle_control)
        else:
            self.add_enabled("启用", "Enabled")
        self.add_ports()
        quick = []
        if self.vector_name is not None and len(self.params.get("home") or []) == len(self.names):
            quick.append(("initial", "初始值", "Initial"))
        if self.vector_name is not None and self.measured() is not None:
            quick.append(("current", "跟随当前", "Hold current"))
        if self.homeable:
            quick.append(("home", "回初始", "Home"))
        if quick:
            hint = ("初始值 / 跟随当前：设滑块目标；回初始：按 home_motion 规划回初始值，期间占用这些关节，完成后恢复原控制",
                     "Initial / Hold current: set the slider targets; Home: planned move to the initial values "
                     "(home_motion), holding these joints until done")
            self.actions = self.ui.choice("快捷", "Quick", quick, quick[0][0], buttons=True, hint=hint)
            self.actions.on_change(self.quick)
        self.add_tune(exclude=(("claim_idle",),) if self.direct else ())
        self.hold_leaf = next((l for l in getattr(getattr(self, "tune", None), "leaves", []) if l.path == ("hold_idle",)), None)
        self.add_plot()

    def show(self, state: dict) -> None:
        super().show(state)
        if self.hold_leaf is not None:
            # claim_idle already holds idle joints, so hold_idle only matters with joint control off.
            self.hold_leaf.control.disabled = bool(self.params.get("claim_idle"))
        if self.direct:
            self.control.disabled = False
            if time.monotonic() - self.edited >= EDIT_HOLD_S:
                self.ui.set_value(self.control, self.holding())

    def holding(self) -> bool:
        return bool(self.params.get("claim_idle")) and bool((self.state or {}).get("enabled"))

    def controlling(self) -> bool:
        # No claim flag in the state: a held claim_idle, or another module held by this one, means it drives.
        if self.holding():
            return True
        mods = (self.app.state or {}).get("modules") or []
        return any(m.get("held_by") == self.id for m in mods)

    def toggle_control(self, event) -> None:
        if event.client is None or not self.ui.user():
            return
        self.edited = time.monotonic()
        on = bool(self.control.value)
        if on and not (self.state or {}).get("enabled"):
            self.app.request("set_enabled", self.id, enabled=True)
        self.app.request("tune", self.id, params={"claim_idle": on})

    def measured(self) -> list | None:
        q = (self.app.state or {}).get("q")
        joints = [self.app.joints.get(n) for n in self.names]
        if any(j is None or j["nq"] != 1 for j in joints):
            return None
        return [float(q[j["idx_q"]]) for j in joints] if q else []

    def quick(self, key: str) -> None:
        if key == "home":
            self.app.request("home", self.id)
        elif key == "initial":
            self.set_targets([float(v) for v in self.params.get("home") or []])
        else:
            self.set_targets(self.measured())

    def set_targets(self, values: list | None) -> None:
        port = self.ports.get(self.vector_name)
        if port is None or not values:
            return
        for slider, v in zip(port.sliders, values):
            self.ui.set_value(slider, min(max(v, slider.min), slider.max))
        port.command(values)
        port.send.flush()

    def update(self) -> None:
        port = self.ports.get(self.vector_name)
        actual = self.measured() or []
        held = self.state is not None and bool(self.state.get("held_by"))
        pairs = []
        for i, name in enumerate(self.names):
            # While another module holds these joints, this card's target is not what drives them.
            target = port.values[i] if port is not None and i < len(port.values) and not held else None
            now = html.signed(actual[i]) if i < len(actual) else "--"
            goal = html.signed(target) if target is not None else "--"
            far = target is not None and i < len(actual) and abs(actual[i] - target) >= 0.02
            text = f"{now} → {goal}"
            pairs.append((name, f"<span style='color:#d97706'>{text}</span>" if far else text))
        tone = "warn" if held else None
        badges = self.badges(self.state)
        if self.direct and self.state is not None and not held:
            t = self.ui.t
            if self.state.get("enabled") and self.controlling():
                badges = [html.badge(t("关节控制中", "controlling"), "ok")] + badges[1:]
            elif self.state.get("enabled"):
                badges = [html.badge(t("未控制（由其他任务驱动）", "not controlling"), "muted")] + badges[1:]
        self.set_html(self.card, html.card(html.badge_row(badges) + html.rows(pairs), tone=tone))
        if port is not None:
            for slider, fixed in zip(port.sliders, port.fixed):
                slider.disabled = fixed


class NullSpacePreferenceView(ModuleView):
    tab = "tasks"

    @property
    def anchor(self):
        deps = self.module.get("dependencies") or []
        return deps[0] if deps else None

    def title(self):
        return f"零空间偏好 · {self.id}", f"Null-space preference · {self.id}"

    def attach(self, parent=None) -> None:
        if parent is not None:
            with parent:
                self.folder = self.ui.folder(*self.title(), expand=False)
            self.ui.anchors[self.id] = self.folder
            with self.folder:
                self.build()
        else:
            super().attach()

    def build(self) -> None:
        self.card = self.ui.html(html.card(html.badge_row(self.badges(None))))
        self.add_enabled()
        self.add_tune(exclude=(("posture",),))
        self.add_posture_sliders()

    def posture_joints(self) -> list[dict]:
        """The module's posture order: configured joints, else the driven scalar joints in model order."""
        names = self.params.get("joints") or [n for n in self.module.get("driven") or ()
                                               if self.app.joints.get(n, {}).get("nv") == 1]
        return [self.app.joints[n] for n in names if n in self.app.joints]

    def add_posture_sliders(self) -> None:
        self.posture = {}
        self.posture_edited = 0.0
        joints = self.posture_joints()
        if not joints:
            return
        self.posture_folder = self.ui.folder("自定义姿态", "Custom posture", expand=False)
        with self.posture_folder:
            self.posture_note = self.ui.html("")
            for j in joints:
                limits = j.get("limits") or {}
                lo, hi = (limits.get("lower") or [None])[0], (limits.get("upper") or [None])[0]
                if j["nq"] != 1 or not (limits.get("position") and finite(lo) and finite(hi) and lo < hi):
                    lo, hi = -math.pi, math.pi
                handle = self.ui.slider(j["name"], j["name"], lo, hi, max((hi - lo) / 1000.0, 1e-4), 0.0)
                handle.on_update(self.posture_changed)
                self.posture[j["name"]] = handle
        self.posture_send = Throttle(self.app, lambda values: self.app.request(
            "tune", self.id, label="posture", on_fail=lambda: self.show_posture(force=True), params={"posture": values}))
        self.show_posture(force=True)

    def posture_changed(self, event) -> None:
        if event.client is None or not self.ui.user():
            return
        self.posture_edited = time.monotonic()
        self.posture_send([float(h.value) for h in self.posture.values()])

    def show_posture(self, force: bool = False) -> None:
        if not self.posture:
            return
        custom = self.params.get("reference") == "custom"
        values = self.params.get("posture") or []
        self.set_html(self.posture_note, html.note(
            self.ui.t("拖动即生效；posture_values 会被并入", "Live; posture_values are folded in") if custom else
            self.ui.t("仅 reference = custom 时可调", "Editable with reference = custom only"), "muted", one_line=True))
        for handle in self.posture.values():
            handle.disabled = not custom
        if len(values) != len(self.posture) or (not force and time.monotonic() - self.posture_edited < EDIT_HOLD_S):
            return
        with self.ui.syncing():
            for handle, value in zip(self.posture.values(), values):
                if finite(value):
                    self.ui.set_value(handle, min(max(float(value), handle.min), handle.max), tolerance=1e-9)

    def on_params(self, values: dict) -> None:
        super().on_params(values)
        self.show_posture()

    def update(self) -> None:
        t, p = self.ui.t, self.params
        mode = str(p.get("preference", "--"))
        names = {"posture": ("姿态", "Posture"), "manipulability": ("可操作度", "Manipulability"),
                 "joint_limits": ("关节限位", "Joint limits")}
        cells = [html.stat(t("偏好", "Preference"), t(*names.get(mode, (mode, mode)))),
                 html.stat(t("权重", "Weight"), html.fmt(p.get(f"{mode}_weight", "--"))),
                 html.stat(t("阻尼", "Damping"), html.fmt((p.get("projector") or {}).get("damping", "--")))]
        body = html.badge_row(self.badges(self.state)) + html.grid(cells, 3)
        self.set_html(self.card, html.card(body))
