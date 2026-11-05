"""Chassis tab: velocity sliders / embedded joystick / optional pose goal, all writing the module's ports."""

import math
import time
from collections import deque

import numpy as np

from .. import html
from .joystick import JoystickInput, JoystickServer
from ..ports import VectorPort
from ..tune import EDIT_HOLD_S
from .base import ModuleView

MODES = (("sliders", "滑块", "Sliders"), ("joystick", "摇杆", "Joystick"), ("pose", "目标位姿", "Pose goal"))
DEFAULT_LIMITS = (1.0, 1.0, 1.0)
TRAIL_POINTS = 600
TRAIL_STEP = 0.02
TRAIL_FLUSH_S = 0.25
STATE_TONES = {"active": "ok", "paused": "warn", "fault": "bad", "configured": "muted", "stopped": "muted"}
STATES = {"configured": ("已配置", "configured"), "active": ("运行中", "active"), "paused": ("已暂停", "paused"),
          "fault": ("故障", "fault"), "stopped": ("已停止", "stopped")}
REASONS = {"not_started": ("未启动", "not started"), "ready": ("就绪", "ready"), "paused": ("暂停", "paused"),
           "resynchronized": ("已重新同步", "resynchronized"), "stopped": ("已停止", "stopped"),
           "solve_paused": ("求解暂停", "solve paused"), "command_timeout": ("等待命令", "awaiting command"),
           "scheduler_discontinuity": ("调度不连续", "scheduler discontinuity"), "tracking": ("跟踪中", "tracking"),
           "trajectory_error": ("轨迹错误", "trajectory error"), "path_error": ("偏离路径", "path error")}
PHASES = {"align": ("对准", "align"), "drive": ("行驶", "drive"), "final_align": ("末端对准", "final align"),
          "path_error": ("偏离路径", "path error")}
POSE_SOURCES = {"internal": ("内部积分", "internal"), "odometry": ("里程计", "odometry")}
COMMAND_MODES = {"velocity": ("速度", "velocity"), "pose": ("位姿", "pose")}


def yaw_wxyz(yaw: float) -> tuple:
    return math.cos(yaw / 2.0), 0.0, 0.0, math.sin(yaw / 2.0)


def yaw_of(wxyz) -> float:
    w, x, y, z = (float(v) for v in wxyz)
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def wrap(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))


class ChassisView(ModuleView):
    tab = "chassis"

    def __init__(self, app, module):
        super().__init__(app, module)
        velocity = self.port_specs.get("velocity") or {}
        upper = velocity.get("upper") or DEFAULT_LIMITS
        self.limits = tuple(abs(float(v)) or 1.0 for v in (list(upper) + list(DEFAULT_LIMITS))[:3])
        chassis = self.params.get("chassis") if isinstance(self.params.get("chassis"), dict) else {}
        self.differential = chassis.get("kinematics") == "differential"
        self.has_pose = (self.port_specs.get("pose_goal") or {}).get("type") == "vector"
        self.stick = JoystickInput(differential=self.differential, deadband=0.05)
        self.joystick = None
        self.stick_sent = (0.0, 0.0, 0.0)
        self.mode_key = "sliders"
        self.goal_edited = 0.0
        self.trail_points: deque = deque(maxlen=TRAIL_POINTS)
        self.trail_flush = 0.0

    def title(self):
        return f"底盘 · {self.id}", f"Chassis · {self.id}"

    def build(self) -> None:
        ui = self.ui
        self.card = ui.html(html.card(html.badge_row(self.badges(None))))
        self.add_enabled("启用", "Enabled")
        modes = MODES if self.has_pose else MODES[:2]
        self.mode = ui.choice("控制方式", "Control", modes, self.mode_key, buttons=True,
                              hint=("写入模块的 velocity / pose_goal 端口", "Writes the module's velocity / pose_goal ports"))
        self.mode.on_change(self.select_mode)
        stop = ui.button("急停", "Stop", icon="HAND_STOP", color="red",
                         hint=("发送零速度并停止重发", "Send zero velocity and stop resending"))
        stop.on_click(lambda _: self.stop())
        self.velocity = None
        if "velocity" in self.port_specs:
            self.velocity = VectorPort(self.app, self.id, self.port_specs["velocity"], sliders=False, stop_button=False)
            self.ports["velocity"] = self.velocity
        linear, angular = self.limits[0], self.limits[2]
        self.slider_folder = ui.folder("速度滑块", "Velocity sliders")
        with self.slider_folder:
            self.vx = ui.slider("前进 vx", "Forward vx", -linear, linear, linear / 100.0, 0.0,
                                hint=("m/s，松开不回零；按“归零”或急停", "m/s; stays until Zero or Stop"))
            self.vy = ui.slider("横移 vy", "Lateral vy", -self.limits[1], self.limits[1], self.limits[1] / 100.0, 0.0)
            self.vy.visible = not self.differential
            self.wz = ui.slider("旋转 wz", "Yaw rate wz", -angular, angular, angular / 100.0, 0.0,
                                hint=("rad/s，正值逆时针", "rad/s, positive = CCW"))
            ui.button("归零", "Zero", icon="ARROW_BACK_UP").on_click(lambda _: self.stop())
            for handle in (self.vx, self.vy, self.wz):
                handle.on_update(self.slid)
        self.joystick_folder = ui.folder("虚拟摇杆", "Joystick")
        with self.joystick_folder:
            self.joystick_html = ui.html("")
        self.joystick_folder.visible = False
        self.pose = None
        if self.has_pose:
            self.build_pose()
        self.add_tune()
        with ui.folder("场景", "Scene", expand=False):
            ui.button("以底盘为视角中心", "Center view on chassis", icon="FOCUS_CENTERED",
                      hint=("仅移动相机，不影响底盘状态", "Moves the camera only; chassis state is unchanged")).on_click(
                lambda _: self.center_view())
            ui.button("恢复默认视角", "Reset view", icon="HOME").on_click(lambda _: self.app.reset_camera())
            ui.button("清除轨迹", "Clear trail", icon="TRASH").on_click(lambda _: self.trail_points.clear())
        root = f"/chassis/{self.id}"
        self.arrow = ui.scene.add_arrows(f"{root}/velocity", np.zeros((1, 2, 3)), (59, 130, 246), shaft_radius=0.012,
                                         head_radius=0.03, head_length=0.06, visible=False)
        self.arc = ui.scene.add_line_segments(f"{root}/turn", np.zeros((1, 2, 3)), (245, 158, 11), thickness=4.0,
                                              thickness_units="screen", visible=False)
        self.trail = ui.scene.add_line_segments(f"{root}/trail", np.zeros((1, 2, 3)), (124, 92, 255), thickness=2.0,
                                                thickness_units="screen", visible=False)
        self.add_plot()

    def center_view(self) -> None:
        out = self.outputs()
        self.app.center_camera((float(out.get("x") or 0.0), float(out.get("y") or 0.0)))

    def build_pose(self) -> None:
        ui = self.ui
        self.pose = VectorPort(self.app, self.id, self.port_specs["pose_goal"], sliders=False, stop_button=False,
                               zero_idle=False)
        self.ports["pose_goal"] = self.pose
        self.pose_folder = ui.folder("目标位姿", "Pose goal")
        with self.pose_folder:
            ui.static("<div style='font-size:12px;opacity:.7'>拖动场景中的底盘目标（平移 xy / 绕 z 旋转），或直接输入。</div>",
                      "<div style='font-size:12px;opacity:.7'>Drag the chassis goal gizmo in the scene "
                      "(xy / yaw) or type it in.</div>")
            self.goal_entry = ui.vector3("目标 x, y (m), yaw (°)", "Goal x, y (m), yaw (°)", (0.0, 0.0, 0.0), step=0.01)
            self.goal_entry.on_update(self.entered)
            ui.button("目标 = 当前位姿", "Goal = current pose", icon="CURRENT_LOCATION").on_click(
                lambda _: self.goal_here())
        self.pose_folder.visible = False
        # Z is locked and only the yaw ring rotates; no depth test so the goal stays grabbable inside the base mesh.
        self.goal_control = ui.scene.add_transform_controls(
            f"/chassis/{self.id}/goal", scale=0.45, line_width=2.5, active_axes=(True, True, False), depth_test=False,
            translation_limits=((-1e4, 1e4), (-1e4, 1e4), (0.0, 0.0)),
            rotation_limits=((0.0, 0.0), (0.0, 0.0), (-1e4, 1e4)), visible=False)
        self.goal_control.on_update(self.dragged)

    # -- inputs (viser threads) ----------------------------------------------------------------------------------
    def slid(self, event) -> None:
        if event.client is None or not self.ui.user() or self.velocity is None:
            return
        self.velocity.command([float(self.vx.value), 0.0 if self.differential else float(self.vy.value),
                               float(self.wz.value)])

    def zero_inputs(self) -> None:
        for handle in (self.vx, self.vy, self.wz):
            self.ui.set_value(handle, 0.0)
        self.stick.release()
        self.stick_sent = (0.0, 0.0, 0.0)

    def stop(self) -> None:
        self.zero_inputs()
        if self.pose is not None:
            self.pose.stop_keepalive()
        if self.velocity is not None:
            self.velocity.zero()

    def select_mode(self, key: str) -> None:
        if key == self.mode_key:
            return
        if key == "joystick" and self.joystick is None:
            try:
                self.joystick = JoystickServer(self.ui.host, self.stick, self.stick_state)
                self.joystick_html.content = self.joystick.iframe(250 if self.differential else 270)
            except OSError as error:
                self.app.warn(f"joystick server: {error}")
                key = "sliders"
        self.stop()
        self.stick.enabled = key == "joystick"
        self.mode_key = key
        self.mode.key = key
        if key == "pose":
            self.goal_here()
        self.layout()

    def send_goal(self, x: float, y: float, yaw: float) -> None:
        self.goal_edited = time.monotonic()
        self.pose.command([x, y, wrap(yaw)])

    def goal_here(self) -> None:
        if self.pose is None:
            return
        out = self.outputs()
        x, y, yaw = (float(out.get(k) or 0.0) for k in ("x", "y", "yaw"))
        self.place_goal(x, y, yaw)
        if self.mode_key == "pose":
            self.send_goal(x, y, yaw)
            self.pose.send.flush()

    def entered(self, event) -> None:
        if event.client is None or not self.ui.user() or self.mode_key != "pose":
            return
        x, y, yaw_deg = (float(v) for v in self.goal_entry.value)
        with self.ui.syncing():
            self.goal_control.position = (x, y, 0.0)
            self.goal_control.wxyz = yaw_wxyz(math.radians(yaw_deg))
        self.send_goal(x, y, math.radians(yaw_deg))

    def dragged(self, _event) -> None:
        if not self.ui.user() or self.mode_key != "pose":
            return
        x, y, _ = (float(v) for v in self.goal_control.position)
        yaw = yaw_of(self.goal_control.wxyz)
        self.ui.set_value(self.goal_entry, (round(x, 4), round(y, 4), round(math.degrees(yaw), 2)))
        self.send_goal(x, y, yaw)

    def place_goal(self, x: float, y: float, yaw: float) -> None:
        with self.ui.syncing():
            self.goal_control.position = (x, y, 0.0)
            self.goal_control.wxyz = yaw_wxyz(yaw)
        self.ui.set_value(self.goal_entry, (round(x, 4), round(y, 4), round(math.degrees(yaw), 2)))

    def stick_state(self) -> dict:
        out = self.outputs()
        vx, vy, wz = (float(out.get(k) or 0.0) for k in ("vx", "vy", "wz"))
        linear, angular = max(self.limits[0], 1e-9), max(self.limits[2], 1e-9)
        return {"language": self.ui.language, "dark": self.ui.show("dark_mode"),
                "enabled": self.mode_key == "joystick" and bool((self.state or {}).get("enabled")),
                "external": False, "source": "", "vx": max(-1.0, min(1.0, vx / linear)),
                "vy": max(-1.0, min(1.0, vy / linear)), "wz": max(-1.0, min(1.0, wz / angular)), "out": [vx, vy, wz]}

    # -- host thread ---------------------------------------------------------------------------------------------
    def flush(self, now: float) -> None:
        if self.mode_key == "joystick" and self.velocity is not None:
            sx, sy, sw = self.stick.sample()
            command = (sx * self.limits[0], sy * self.limits[1], sw * self.limits[2])
            if command != self.stick_sent:
                self.stick_sent = command
                self.velocity.command(list(command))
        super().flush(now)

    def show(self, state: dict) -> None:
        super().show(state)
        out = self.outputs()
        self.draw_motion(*(v if isinstance(v := out.get(k), (int, float)) and math.isfinite(v) else None
                           for k in ("x", "y", "yaw", "vx", "vy", "wz")))
        if self.mode_key == "sliders" and self.velocity is not None and \
                time.monotonic() - self.velocity.edited >= EDIT_HOLD_S:
            values = self.velocity.values
            for handle, v in zip((self.vx, self.vy, self.wz), values):
                self.ui.set_value(handle, min(max(float(v), handle.min), handle.max), 1e-6)

    def layout(self) -> None:
        ui = self.ui
        ui.set(self.slider_folder, "visible", self.mode_key == "sliders")
        ui.set(self.joystick_folder, "visible", self.mode_key == "joystick")
        if self.pose is not None:
            pose_mode = self.mode_key == "pose"
            ui.set(self.pose_folder, "visible", pose_mode)
            ui.set(self.goal_control, "visible", pose_mode and ui.show("show_gizmos"))

    def update(self) -> None:
        ui, t = self.ui, self.ui.t
        self.layout()
        out = self.outputs()
        if self.pose is not None and self.mode_key == "pose" and time.monotonic() - self.goal_edited >= EDIT_HOLD_S \
                and len(self.pose.values) == 3:
            self.place_goal(*self.pose.values)

        def num(key):
            v = out.get(key)
            return float(v) if isinstance(v, (int, float)) and math.isfinite(v) else None

        def named(names, key):
            return html.esc(t(*names[key]) if key in names else (key or "-"))

        def show(v, digits, unit=""):
            return "--" if v is None else html.signed(v, digits) + unit

        x, y, yaw, vx, vy, wz = (num(k) for k in ("x", "y", "yaw", "vx", "vy", "wz"))
        moving = {k: v is not None and abs(v) > 1e-3 for k, v in (("vx", vx), ("vy", vy), ("wz", wz))}
        cells = [html.stat("x", show(x, 3), sub="m"), html.stat("y", show(y, 3), sub="m"),
                 html.stat("yaw", show(None if yaw is None else math.degrees(yaw), 1), sub="°"),
                 html.stat("vx", show(vx, 2), "info" if moving["vx"] else None, "m/s"),
                 html.stat("vy", show(vy, 2), "info" if moving["vy"] else None, "m/s"),
                 html.stat("wz", show(wz, 2), "info" if moving["wz"] else None, "rad/s")]
        badges = self.badges(self.state)
        controller = out.get("state")
        fault = controller == "fault" or bool(out.get("fault"))
        reason = str(out.get("reason") or "")
        reason_text = t(*REASONS.get(reason, (reason or "-", reason or "-")))
        st = self.state or {}
        # "active" would repeat the module's running badge.
        dup = controller == "active" and st.get("running") and st.get("enabled") and not st.get("held_by")
        if isinstance(controller, str) and not dup:
            badges.append(html.badge(t(*STATES.get(controller, (controller, controller))),
                                     STATE_TONES.get(controller, "muted")))
            if reason and reason != controller:
                badges.append(html.badge(reason_text, "muted"))
        badges.append(html.badge(t("差速", "diff drive") if self.differential else t("全向", "omni"), "accent"))
        fresh = bool(out.get("command_fresh"))
        if self.differential and out.get("command_mode") == "pose" and fresh:
            phase = str(out.get("phase") or "-")
            badges.append(html.badge(t("阶段", "phase") + " " + t(*PHASES.get(phase, (phase, phase))), "info"))
        label = dict((k, t(zh, en)) for k, zh, en in MODES)[self.mode_key]
        badges.append(html.badge(label, "info"))
        body = html.grid(cells, 3) + html.badge_row(badges)
        if isinstance(out.get("command"), str):
            source = out.get("command") or ""
            priority = num("command_priority")
            if source:
                command = (f"{html.esc(source)} · p{0 if priority is None else int(priority)} · "
                           f"{named(COMMAND_MODES, out.get('command_mode'))} · "
                           f"{t('有效', 'fresh') if fresh else t('过期', 'stale')}")
            else:
                command = t("无", "none")
            body += html.rows([(t("命令来源", "command"), command),
                               (t("位姿来源", "pose source"), named(POSE_SOURCES, out.get("source")))])
        if controller in ("fault", "paused"):
            state_text = t(*STATES[controller])
            body += html.note(t(f"底盘{state_text}：{html.esc(reason_text)}",
                                f"Chassis {state_text}: {html.esc(reason_text)}"), "bad" if controller == "fault" else "warn")
        tone = "bad" if fault else ("warn" if (self.state or {}).get("held_by") else "ok")
        self.set_html(self.card, html.card(body, tone=tone))

    def draw_motion(self, x, y, yaw, vx, vy, wz) -> None:
        """Velocity arrow, turn arc and trail in the world, like the ancestor's chassis overlays."""
        ui = self.ui
        if x is None or y is None or yaw is None:
            for handle in (self.arrow, self.arc, self.trail):
                ui.set(handle, "visible", False)
            return
        vx, vy, wz = vx or 0.0, vy or 0.0, wz or 0.0
        c, s = math.cos(yaw), math.sin(yaw)
        speed = math.hypot(vx, vy)
        show = ui.show("chassis_vectors")
        ui.set(self.arrow, "visible", show and speed > 1e-3)
        if show and speed > 1e-3:
            scale = 0.6 / max(self.limits[0], 1e-6)
            tip = (x + (c * vx - s * vy) * scale, y + (s * vx + c * vy) * scale)
            self.arrow.points = np.asarray([[[x, y, 0.03], [tip[0], tip[1], 0.03]]], dtype=float)
        ui.set(self.arc, "visible", show and abs(wz) > 1e-3)
        if show and abs(wz) > 1e-3:
            sweep = max(-1.5 * math.pi, min(1.5 * math.pi, wz / max(self.limits[2], 1e-6) * math.pi))
            angles = yaw + np.linspace(0.0, sweep, 25)
            ring = np.stack([x + 0.35 * np.cos(angles), y + 0.35 * np.sin(angles), np.full(25, 0.03)], axis=1)
            self.arc.points = np.stack([ring[:-1], ring[1:]], axis=1)
        if not self.trail_points or math.hypot(x - self.trail_points[-1][0], y - self.trail_points[-1][1]) > TRAIL_STEP:
            self.trail_points.append((x, y))
        show_trail = ui.show("chassis_trail") and len(self.trail_points) > 1
        ui.set(self.trail, "visible", show_trail)
        now = time.monotonic()
        if show_trail and now - self.trail_flush > TRAIL_FLUSH_S:
            self.trail_flush = now
            points = np.asarray([(px, py, 0.005) for px, py in self.trail_points], dtype=float)
            self.trail.points = np.stack([points[:-1], points[1:]], axis=1)

    def close(self) -> None:
        self.stick.enabled = False
        if self.joystick is not None:
            self.joystick.close()
            self.joystick = None

