"""The viewer session: scene, ancestor-style tabs built from per-type views, and the handlers for server messages."""

import math
import threading
import time
from pathlib import Path

import numpy as np
import viser
import yourdfpy
from viser.extras import ViserUrdf

from . import html
from .client import Client
from .geometry import LinkKinematics, UrdfPoser, matrices_to_wxyz, root_transform, set_pose
from .overlay import ModuleOverlay
from .panels.limits import JointLimitsPanel
from .panels.plots import PLOT_PERIOD_S, PlotPanel, Samples
from .panels.stats import StatsPanel
from .text import LANGUAGES, TEXT, Lang, Prefs
from .tune import EDIT_HOLD_S, TunePanel, finite
from .ui import TABS, ViewerUI, icon_of
from .views import ModuleView, view_for

DEFAULT_RENDER_HZ = 30
GHOST_COLOR = (0.35, 0.6, 1.0, 0.35)
GHOST_TOLERANCE = 1e-4
MIN_RENDER_HZ = 5
MAX_RENDER_HZ = 60
MIN_RATE_HZ = 1.0
MAX_RATE_HZ = 1000.0
DAMPING_LOG = (-8.0, 0.0)
BRAND = (124, 92, 255)
# The camera looks at the model from the front right, slightly above, at a distance that fits its extent.
CAMERA_DIRECTION = np.array([1.35, -1.2, 0.6]) / np.linalg.norm([1.35, -1.2, 0.6])
CAMERA_FOV = math.radians(75.0)
CAMERA_MARGIN = 1.6
CAMERA_MIN_DISTANCE = 0.6
# Keeps the tab bar visible while the panel scrolls.
STICKY_TABS = (
    "<style>.mantine-ScrollArea-content div:has([role=tablist]){overflow:visible!important;min-width:0!important;"
    "max-width:100%!important}.mantine-ScrollArea-content [role=tablist]{position:sticky;top:0;z-index:5;"
    "background:var(--mantine-color-body)}"
    # Narrow panels: wrap the tab bar and drop stat grids to two columns.
    "@media (max-width:600px){.mantine-ScrollArea-content [role=tablist]{flex-wrap:wrap}"
    ".mantine-ScrollArea-content div[style*='grid-template-columns:repeat(']"
    "{grid-template-columns:repeat(2,minmax(0,1fr))!important}}</style>"
)
RUN_OPTIONS = (("run", "运行", "Run"), ("pause", "暂停", "Pause"), ("step", "单步", "Step"))
JOINT_HEADER = (("关节", "joint"), ("理想", "ideal"), ("反馈", "feedback"), ("差", "diff"), ("限位", "limits"))
JOINT_ALIGN, JOINT_WIDTHS = ("left", "right", "right", "right", "left"), ("39%", "14%", "14%", "15%", "18%")


def apply_theme(server: viser.ViserServer, dark: bool) -> None:
    server.gui.configure_theme(
        control_layout="collapsible", control_width="large", dark_mode=dark,
        show_logo=False, show_share_button=False, brand_color=BRAND,
    )


def ms(seconds) -> str:
    return f"{seconds * 1e3:.2f}" if finite(seconds) else "--"


class App:
    def __init__(self, server: viser.ViserServer, client: Client, hello: dict, urdf_path: Path, prefs: Prefs,
                 host: str = "127.0.0.1"):
        self.server = server
        self.client = client
        self.prefs = prefs
        self.hello = hello
        self.chassis = any(m.get("type") == "chassis" for m in hello["modules"])
        self.rebuild = False
        self.ui = ui = ViewerUI(server, language=prefs.get("language", "zh", lambda v: v in LANGUAGES),
                                preferences=prefs, host=host)
        self.t = Lang(ui.language)
        self.joints = {j["name"]: j for j in hello["joints"]}
        self.model_joints = [j for j in hello["joints"] if j["nq"] > 0]
        self.task_joints: set | None = None
        self.pending = {}
        self.throttles = []
        self.gizmos = []
        self.labels = []
        self.hidden = set()
        self.warned = set()
        self.failure = None
        self.render_hz = prefs.get("render_hz", DEFAULT_RENDER_HZ, lambda v: MIN_RENDER_HZ <= v <= MAX_RENDER_HZ)
        apply_theme(server, ui.show("dark_mode"))
        server.gui.add_html(STICKY_TABS)
        self.build_scene(urdf_path, hello)
        self.state = None
        self.last_lease = None
        self.mine = False
        self.last_text = 0.0
        self.samples = Samples(hello)
        self.sample_keys = []
        self.sample_set = set()
        self.plot_lock = threading.Lock()
        self.plots = []
        self.plot_count = 0

        self.views: dict[str, ModuleView] = {m["id"]: view_for(m["type"])(self, m) for m in hello["modules"]}
        used = {v.tab for v in self.views.values() if v.anchor not in self.views}
        tabs = server.gui.add_tab_group()
        for key, zh, en, icon in TABS:
            if key in ("overview", "joints", "display", "plots", "safety") or key in used:
                ui.tabs[key] = ui.bind(tabs.add_tab(ui.t(zh, en), icon_of(icon)), "tab", (zh, en))
        with ui.tab("overview"):
            self.build_overview(hello)
        # Views without an anchor first, so anchored views find their parent folder.
        for view in sorted(self.views.values(), key=lambda v: v.anchor in self.views):
            parent = ui.anchors.get(view.anchor) if view.anchor else None
            if parent is not None:
                view.attach(parent)
            else:
                with ui.tab(view.tab):
                    view.attach()
        with ui.tab("safety"):
            with ui.folder("关节限制", "Joint limits", expand=False):
                self.limits = JointLimitsPanel(self, hello)
            with ui.folder("引擎计数", "Engine counters", expand=False):
                self.safety = StatsPanel(self, hello, "counter")
        self.all_plots = [self.time_plot] + [v.plot for v in self.views.values() if v.plot is not None]
        with ui.tab("plots"):
            ui.button("新增曲线", "Add plot", icon="PLUS").on_click(lambda _: self.add_plot())
        self.add_plot()
        self.overlays = {m["id"]: o for m in hello["modules"] if not (o := ModuleOverlay(server, m, ui.show)).empty}
        with ui.tab("joints"):
            self.build_joints()
        with ui.tab("display"):
            self.build_display()

        for j in hello["joints"]:
            if j["name"] in self.urdf.joint_map and j["type"] in ("free_flyer", "planar", "spherical", "translation"):
                self.warn(self.t("not_drawn", name=j["name"], type=j["type"]))
        if prefs.error:
            self.warn(prefs.error)
        ui.on_display(self.on_display)
        self.apply_display()

    def keyed(self, handle, attribute: str, key: str, **fields):
        return self.ui.key(handle, attribute, key, **fields)

    # -- scene ---------------------------------------------------------------------------------------------------
    def build_scene(self, urdf_path: Path, hello: dict) -> None:
        server = self.server
        # Shadows are off everywhere: the shadow pass re-renders every triangle and halves fps on dense meshes.
        server.scene.configure_default_lights(cast_shadow=False)
        self.urdf = yourdfpy.URDF.load(str(urdf_path), load_meshes=True, build_scene_graph=True)
        server.gui.set_panel_label(f"kstone · {self.urdf.robot.name or urdf_path.stem}")
        # Root nodes carry the floating-base pose; URDF and skeleton poses are relative to the base link.
        self.base_nodes = [server.scene.add_frame(n, show_axes=False) for n in ("/robot", "/skeleton")]
        self.robot = ViserUrdf(server, self.urdf, root_node_name="/robot")
        # The main robot shows the command; a translucent ghost shows the measured state when they differ.
        self.ghost_base = server.scene.add_frame("/measured", show_axes=False)
        self.ghost = ViserUrdf(
            server,
            yourdfpy.URDF.load(str(urdf_path), load_meshes=True, build_scene_graph=True),
            root_node_name="/measured",
            mesh_color_override=GHOST_COLOR,
        )
        for mesh in (*self.robot._meshes, *self.ghost._meshes):
            mesh.cast_shadow = False
            mesh.receive_shadow = False
        self.ghost_shown = False
        self.actuated = list(self.robot.get_actuated_joint_names())
        self.base = np.eye(4)
        self.root = next(
            (
                j
                for j in hello["joints"]
                if j.get("parent") == "universe" and j["type"] in ("free_flyer", "planar")
                and j["name"] not in self.urdf.joint_map
            ),
            None,
        )
        self.grid = server.scene.add_grid(
            "/floor", width=8.0, height=8.0, cell_size=0.25, section_size=1.0,
            cell_color=(215, 218, 226), section_color=(160, 166, 180),
            infinite_grid=True, fade_distance=18.0, shadow_opacity=0.0,
        )
        self.framing = None
        self.initial_look = None
        # Clients already open keep their camera when the viewer is rebuilt.
        self.framed_clients = set(server.get_clients())
        # Link frames and parent-child segments, so robots without visual meshes are still visible.
        self.edges = [(j.parent, j.child) for j in self.urdf.robot.joints]
        self.kinematics = LinkKinematics(self.urdf)
        self.poser = UrdfPoser(self.robot, self.kinematics)
        self.ghost_poser = UrdfPoser(self.ghost, self.kinematics)
        self.poses = self.kinematics.poses(np.zeros(len(self.actuated)))
        self.links = list(self.urdf.link_map)
        self.axes = None
        self.bones = None

    # -- overview ------------------------------------------------------------------------------------------------
    def build_overview(self, hello: dict) -> None:
        ui, t = self.ui, self.t
        self.status = ui.html(html.card(html.badge_row([html.badge(t("waiting"))])))
        self.message = self.server.gui.add_markdown("")
        self.run = ui.choice("引擎", "Engine", RUN_OPTIONS, "run", buttons=True,
                             hint=("暂停时保持当前命令；单步运行指定周期数", "Pause holds the command; Step runs N cycles"))
        self.run.on_change(self.run_clicked)
        self.step_cycles = ui.number("单步周期数", "Step cycles", 1, low=1, high=1000000, step=1,
                                     hint=("暂停后可见；单步按此周期数运行", "Shown while paused; Step runs this many cycles"))
        self.rate_slider = ui.slider("求解频率 (Hz)", "Solve rate (Hz)", MIN_RATE_HZ, MAX_RATE_HZ, 1, hello["rate_hz"],
                                     hint=("主循环的求解频率；机器人插件仍按启动时的频率", "Main loop solve rate; robot plugins keep their start-up rate"))
        self.rate_touched = -1e9
        self.rate_window: tuple[float, float] | None = None
        self.actual_hz: float | None = None
        self.qp_average: float | None = None
        self.rate_slider.on_update(self.rate_changed)
        home = ui.button("全部回零", "Home all", icon="HOME",
                         hint=("每个部位只回零一次：优先由正在驱动它的模块回零", "Home each part once, through the module driving it"))
        home.on_click(lambda event: ui.confirm(event.client, ui.k("home_all"), ui.k("home_all_confirm"),
                                               ui.k("home_all"), self.home_all))
        reset = ui.button("全部恢复默认", "Reset all", icon="RESTORE", color="orange",
                          hint=("把所有可调参数恢复为默认值", "Restore every tunable parameter to its default"))
        reset.on_click(lambda event: ui.confirm(event.client, ui.k("defaults_all"), ui.k("defaults_all_confirm"),
                                                ui.k("defaults_all"), self.defaults_all))
        with ui.folder("控制权", "Control"):
            self.lease = self.keyed(self.server.gui.add_markdown(t("control_shared")), "content", "control_shared")
            self.exclusive = ui.checkbox("独占控制", "Exclusive control", False,
                                         hint=("持有租约时其他客户端的写入会被拒绝", "Other clients' writes are refused while you hold the lease"))
            self.exclusive.on_update(self.toggle_lease)
        self.solver = None
        with ui.folder("求解器", "Solver"):
            self.solver_card = ui.html("")
            solver = hello.get("solver") or {}
            schema = solver.get("tune_schema")
            self.damping = None
            self.damping_edited = 0.0
            if isinstance(schema, dict) and "damping" in (schema.get("properties") or {}):
                value = float((solver.get("params") or {}).get("damping") or 1e-3)
                self.damping = ui.slider("QP 阻尼 (log10)", "QP damping (log10)", DAMPING_LOG[0], DAMPING_LOG[1], 0.1,
                                         math.log10(max(value, 10.0 ** DAMPING_LOG[0])),
                                         hint=("加到各加权层 Hessian 对角线上，越大越稳但越偏离最优",
                                               "Added to every weighted stage's Hessian diagonal; larger is steadier but less exact"))
                self.damping.on_update(self.damping_changed)
            if isinstance(schema, dict):
                self.solver = TunePanel(self, None, schema, solver.get("params"), t, op="tune_solver", title="solver",
                                        exclude=(("damping",),) if self.damping is not None else ())
        self.time_plot = PlotPanel(self, "overview_time", preset=["solver/qp [ms]", self.samples.stat_key("last_cycle")])
        with ui.folder("周期耗时分布", "Cycle latency", expand=False):
            self.latency = StatsPanel(self, hello, "duration")
        with ui.folder("残差", "Residuals", expand=False):
            self.residuals = ui.html("")

    # -- joints and display --------------------------------------------------------------------------------------
    def build_joints(self) -> None:
        ui = self.ui
        self.joint_filter = ui.text("筛选", "Filter", "", hint=("按关节名过滤", "Filter by joint name"))
        self.joint_table = ui.html("")

    def build_display(self) -> None:
        ui = self.ui
        language = ui.choice("语言", "Language", [(code, name, name) for code, name in LANGUAGES.items()],
                             ui.language)
        language.on_change(self.set_language)
        self.toggles = {}
        dark = ui.checkbox("深色模式", "Dark mode", ui.show("dark_mode"))
        dark.on_update(lambda event: self.toggled(event, "dark_mode"))
        types = {m["type"] for m in self.hello["modules"]}
        outputs = {o["name"] for m in self.hello["modules"] for o in m.get("outputs") or ()}
        needs = {"show_planned": bool(outputs & {"planned", "goal"}), "show_gaze": "gaze" in types,
                 "show_com": "center_of_mass" in types, "chassis_vectors": "chassis" in types,
                 "chassis_trail": "chassis" in types, "follow_camera": "chassis" in types}
        with ui.folder("场景元素", "Scene"):
            for key, label in ui.display_labels.items():
                if not key.startswith("overlay:") and needs.get(key, True):
                    self.add_toggle(key, label)
        if self.overlays:
            with ui.folder(*TEXT["overlays"]):
                for key in self.overlays:
                    self.add_toggle(f"overlay:{key}", (key, key))
        with ui.folder("视角", "Camera"):
            ui.button("重置视角", "Reset view", icon="FOCUS_CENTERED").on_click(lambda _: self.reset_camera())
            # The server never streams faster than publish_hz, so higher settings would do nothing.
            render = ui.slider("刷新率 [Hz]", "Render rate [Hz]", MIN_RENDER_HZ, MAX_RENDER_HZ, 1, self.render_hz)
            render.on_update(lambda _: self.set_render_hz(int(render.value)))
        path = self.prefs.path
        note = self.server.gui.add_markdown("")
        if path is not None:
            self.keyed(note, "content", "prefs_note", path=path)
        else:
            self.keyed(note, "content", "prefs_off")

    def add_toggle(self, key: str, label) -> None:
        self.ui.declare_display(key, label)
        handle = self.ui.checkbox(*label, self.ui.show(key))
        handle.on_update(lambda event: self.toggled(event, key))
        self.toggles[key] = handle

    def toggled(self, event, key: str) -> None:
        if self.ui.user():
            self.ui.set_display(key, bool(event.target.value))

    def set_language(self, code: str) -> None:
        # Relabel in place: a rebuild would send the client back to the first tab.
        if code == self.ui.language:
            return
        self.prefs.set("language", code)
        self.ui.set_language(code)
        self.t.code = self.ui.language
        if self.state is not None:
            self.update_text(self.state)
        self.on_lease(self.last_lease)

    def set_render_hz(self, hz: int) -> None:
        self.render_hz = hz
        self.prefs.set("render_hz", hz)

    def on_display(self, key: str, value: bool) -> None:
        if key == "dark_mode":
            apply_theme(self.server, value)
        self.apply_display(key)
        if self.prefs.error:
            self.warn(self.prefs.error)

    def apply_display(self, key: str = "") -> None:
        show = self.ui.show
        self.grid.visible = show("show_grid")
        self.robot.show_visual = show("show_robot")
        self.ghost.show_visual = show("show_measured") and self.ghost_shown
        self.base_nodes[1].visible = show("show_skeleton")
        for handle in self.gizmos:
            handle.visible = show("show_gizmos") and id(handle) not in self.hidden
        for handle in self.labels:
            handle.visible = show("show_gizmos") and show("show_labels") and id(handle) not in self.hidden
        for key, overlay in self.overlays.items():
            overlay.set_enabled(show(f"overlay:{key}"))

    # -- overview actions ----------------------------------------------------------------------------------------
    def run_clicked(self, key: str) -> None:
        if key == "step":
            self.request("step", cycles=int(self.step_cycles.value))
        else:
            self.pending[self.client.send("pause", paused=key == "pause")] = (key, None)

    def rate_changed(self, event) -> None:
        if event.client is None or not self.ui.user():
            return
        self.rate_touched = time.monotonic()
        self.request("set_rate", rate_hz=float(self.rate_slider.value))

    def damping_changed(self, event) -> None:
        if event.client is None or not self.ui.user():
            return
        self.damping_edited = time.monotonic()
        self.request("tune_solver", params={"damping": 10.0 ** float(self.damping.value)}, on_fail=self.revert_damping)

    def revert_damping(self) -> None:
        self.damping_edited = 0.0
        self.show_damping()

    def show_damping(self) -> None:
        values = (self.solver.values if self.solver is not None else None) or {}
        if self.damping is None or values.get("damping") is None:
            return
        if time.monotonic() - self.damping_edited >= EDIT_HOLD_S:
            low, high = DAMPING_LOG
            self.ui.set_value(self.damping, min(max(math.log10(max(float(values["damping"]), 1e-12)), low), high), 1e-6)

    def measure_rate(self, state: dict) -> float:
        """Syncs the slider unless just dragged; returns the set rate and refreshes the measured one over ~1 s."""
        rate = float(state.get("rate_hz") or self.hello["rate_hz"])
        if time.monotonic() - self.rate_touched > 1.0:
            self.ui.set_value(self.rate_slider, min(max(rate, MIN_RATE_HZ), MAX_RATE_HZ), 0.5)
        now, cycles = time.monotonic(), float((state.get("stats") or {}).get("cycles") or 0)
        if self.rate_window is None or cycles < self.rate_window[1]:
            self.rate_window = (now, cycles)
        elif now - self.rate_window[0] >= 1.0:
            self.actual_hz = (cycles - self.rate_window[1]) / (now - self.rate_window[0])
            self.rate_window = (now, cycles)
        return rate

    def home_all(self) -> None:
        """Home each part once: modules currently driving it first, then uncovered ones in config order."""
        candidates = [v for v in self.views.values() if v.homeable]
        state = self.state or {}
        running = {m["id"] for m in state.get("modules", []) if m.get("running")}
        driving = {r.get("module") for r in state.get("residuals", [])} & running
        chosen = [v for v in candidates if v.id in driving]
        covered: set[str] = set()
        everything = False
        for v in chosen:
            scope = set(v.module.get("driven") or [])
            everything |= not scope
            covered |= scope
        for v in candidates:
            if v in chosen or everything:
                continue
            scope = set(v.module.get("driven") or [])
            if scope and not scope & covered:
                chosen.append(v)
                covered |= scope
            elif not scope and not covered:
                chosen.append(v)
                everything = True
        for v in chosen:
            self.request("home", v.id)

    def defaults_all(self) -> None:
        for view in self.views.values():
            if view.tune is not None:
                view.tune.restore_defaults()
        if self.solver is not None:
            self.solver.restore_defaults()

    def toggle_lease(self, event) -> None:
        if event.client is None or not self.ui.user():
            return
        self.request("acquire" if self.exclusive.value else "release")

    # -- helpers used by views and ports -------------------------------------------------------------------------
    def slider_range(self, joint: str, lower, upper) -> tuple[float, float]:
        if finite(lower) and finite(upper):
            return float(lower), float(upper)
        info = self.joints.get(joint, {})
        return (-1.0, 1.0) if info.get("type") == "prismatic" else (-math.pi, math.pi)

    def warn(self, text: str) -> None:
        if text not in self.warned:
            self.warned.add(text)
            self.message.content = text

    def bad_input(self, what: str, error: str) -> None:
        self.message.content = self.t("bad_input", what=what, error=error)

    def link_pose(self, link: str) -> np.ndarray:
        return self.base @ self.poses.get(link, np.eye(4))

    def reference_pose(self, frame: str) -> np.ndarray:
        """World pose of a model frame: a link, a joint (its child link) or empty for the world."""
        if not frame:
            return np.eye(4)
        if frame in self.urdf.link_map:
            return self.link_pose(frame)
        if frame in self.urdf.joint_map:
            return self.link_pose(self.urdf.joint_map[frame].child)
        self.warn(self.t("bad_frame", frame=frame))
        return np.eye(4)

    def request(self, op: str, module: str | None = None, label: str | None = None, on_fail=None, **fields) -> None:
        port = fields.get("port")
        target = label and ".".join(filter(None, (module, label))) or (f"{module}.{port}" if module and port else module or "")
        self.pending[self.client.send(op, module, **fields)] = (f"{op} {target}".strip(), on_fail)

    # -- server messages -----------------------------------------------------------------------------------------
    def on_lease(self, lease: dict | None) -> None:
        if lease is None:
            return
        self.last_lease = lease
        holder = lease.get("holder")
        if holder is None:
            self.lease.content = self.t("control_shared")
        elif lease.get("mine"):
            self.lease.content = self.t("control_mine")
        else:
            self.lease.content = self.t("control_other", holder=holder)
        self.mine = bool(lease.get("mine"))
        self.ui.set_value(self.exclusive, self.mine)

    def on_params(self, message: dict) -> None:
        view = self.views.get(message.get("module"))
        if view is not None and isinstance(message.get("values"), dict):
            view.on_params(message["values"])

    def on_solver_params(self, message: dict) -> None:
        if self.solver is not None and isinstance(message.get("values"), dict):
            self.solver.show(message["values"])
            self.damping_edited = 0.0
            self.show_damping()

    def on_joint_limits(self, message: dict) -> None:
        if isinstance(message.get("values"), dict):
            self.limits.show(message["values"])

    def on_reply(self, reply: dict) -> None:
        what, on_fail = self.pending.pop(reply.get("id"), (None, None))
        if reply["ok"]:
            if what is not None:
                self.message.content = ""
            return
        self.message.content = self.t(
            "failed", what=what or "request", code=reply.get("code", "?"), error=reply.get("error", "")
        )
        if on_fail is not None:
            on_fail()
        # A refused acquire/release sends no lease update, so undo the checkbox here.
        self.ui.set_value(self.exclusive, self.mine)

    # -- camera and skeleton -------------------------------------------------------------------------------------
    def frame_model(self) -> tuple[np.ndarray, np.ndarray]:
        """Camera (look_at, offset) relative to the base, from the bounding box of the link frames."""
        points = np.array([pose[:3, 3] for pose in self.poses.values()])
        points = points[np.isfinite(points).all(axis=1)] if len(points) else points
        if not len(points):
            points = np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
        points = points @ self.base[:3, :3].T + self.base[:3, 3] - np.array([*self.base[:2, 3], 0.0])
        low, high = points.min(axis=0), points.max(axis=0)
        radius = 0.5 * float(np.linalg.norm(high - low))
        distance = max(CAMERA_MIN_DISTANCE, CAMERA_MARGIN * radius / math.sin(0.5 * CAMERA_FOV))
        return 0.5 * (low + high), CAMERA_DIRECTION * distance

    def place_camera(self, client) -> None:
        if self.framing is None:
            return
        look, offset = self.framing
        look = look + np.array([*self.base[:2, 3], 0.0])
        client.camera.position = tuple(look + offset)
        client.camera.look_at = tuple(look)

    def update_initial_camera(self) -> None:
        """Aim new and reloaded browsers at the base's current spot, not the origin."""
        look, offset = self.framing
        look = look + np.array([*self.base[:2, 3], 0.0])
        if self.initial_look is not None and np.linalg.norm(look - self.initial_look) < 0.05:
            return
        self.initial_look = look
        self.server.initial_camera.position = tuple(look + offset)
        self.server.initial_camera.look_at = tuple(look)

    def follow_camera(self) -> None:
        """Keep every camera aimed at the model above the base, preserving the user's orbit and zoom."""
        if not self.ui.show("follow_camera") or self.framing is None or not self.chassis:
            return
        look = self.framing[0] + np.array([*self.base[:2, 3], 0.0])
        for client in self.server.get_clients().values():
            camera = client.camera
            # Absolute, not incremental: stale camera echoes from the browser would otherwise drop motion.
            if np.allclose(camera.look_at, look, atol=1e-4):
                continue
            camera.position = tuple(look + np.asarray(camera.position) - np.asarray(camera.look_at))
            camera.look_at = tuple(look)

    def center_camera(self, point) -> None:
        """Move every camera so it looks at ``point`` from its current offset (display only)."""
        for client in self.server.get_clients().values():
            look = np.asarray(client.camera.look_at)
            delta = np.array([point[0] - look[0], point[1] - look[1], 0.0])
            with client.atomic():
                client.camera.position = tuple(np.asarray(client.camera.position) + delta)
                client.camera.look_at = tuple(look + delta)

    def reset_camera(self) -> None:
        for client in self.server.get_clients().values():
            self.place_camera(client)

    def draw_skeleton(self) -> None:
        if not self.ui.show("show_skeleton"):
            return
        poses = self.poses
        frames = np.array([poses.get(link, np.eye(4)) for link in self.links])
        wxyzs = matrices_to_wxyz(frames[:, :3, :3]).astype(np.float32)
        positions = frames[:, :3, 3].astype(np.float32)
        if self.axes is None:
            self.axes = self.server.scene.add_batched_axes(
                "/skeleton/frames", wxyzs, positions, axes_length=0.05, axes_radius=0.003
            )
        else:
            self.axes.batched_wxyzs = wxyzs
            self.axes.batched_positions = positions
        points = np.array(
            [[poses[a][:3, 3], poses[b][:3, 3]] for a, b in self.edges if a in poses and b in poses], dtype=np.float32
        )
        if len(points) == 0:
            return
        if self.bones is None:
            self.bones = self.server.scene.add_line_segments(
                "/skeleton/bones", points=points, colors=(120, 120, 120), thickness=3.0, thickness_units="screen"
            )
        else:
            self.bones.points = points

    def joint_cfg(self, q: list) -> np.ndarray:
        cfg = []
        for name in self.actuated:
            j = self.joints.get(name)
            if j is None:
                cfg.append(0.0)
                continue
            i = j["idx_q"]
            cfg.append(math.atan2(q[i + 1], q[i]) if j["nq"] == 2 else q[i])
        return np.array(cfg)

    # -- plots ---------------------------------------------------------------------------------------------------
    def add_plot(self) -> None:
        with self.plot_lock:
            self.plot_count += 1
            with self.ui.tab("plots"):
                panel = PlotPanel(self, "plot_title", fields={"n": self.plot_count}, on_remove=self.remove_plot)
            panel.set_keys(self.sample_keys, self.samples.owners)
            self.plots = [*self.plots, panel]
            self.all_plots = [*self.all_plots, panel]

    def remove_plot(self, panel: PlotPanel) -> None:
        with self.plot_lock:
            if panel not in self.plots:
                return
            self.plots = [p for p in self.plots if p is not panel]
            self.all_plots = [p for p in self.all_plots if p is not panel]
            panel.remove()

    # -- state ---------------------------------------------------------------------------------------------------
    def on_state(self, state: dict) -> None:
        self.state = state
        measured = state["q"]
        command = state.get("q_command") or measured
        if len(command) != len(measured):
            command = measured
        if self.root is not None:
            self.base = root_transform(self.root, command)
            for node in self.base_nodes:
                set_pose(node, self.base)
            self.follow_camera()
        self.poses = self.poser.update(self.joint_cfg(command))
        self.draw_skeleton()
        if self.framing is None:
            self.framing = self.frame_model()
        self.update_initial_camera()
        shown = max((abs(a - b) for a, b in zip(command, measured) if finite(a) and finite(b)), default=0.0) > GHOST_TOLERANCE
        if shown and self.ui.show("show_measured"):
            if self.root is not None:
                set_pose(self.ghost_base, root_transform(self.root, measured))
            self.ghost_poser.update(self.joint_cfg(measured))
        if shown != self.ghost_shown:
            self.ghost_shown = shown
            self.ghost.show_visual = shown and self.ui.show("show_measured")
        for module in state["modules"]:
            view = self.views.get(module.get("id"))
            if view is not None:
                view.show(module)
            overlay = self.overlays.get(module.get("id"))
            if overlay is not None:
                overlay.show(module.get("display"))
        values = self.samples(state)
        if not self.sample_set.issuperset(values):
            with self.plot_lock:
                self.sample_keys += [k for k in values if k not in self.sample_set]
                self.sample_set.update(values)
                for panel in self.all_plots:
                    panel.set_keys(self.sample_keys, self.samples.owners)
        for panel in self.all_plots:
            panel.append(state["time"], values)
        self.safety.append(state)
        self.latency.append(state)
        now = time.monotonic()
        if now - self.last_text >= PLOT_PERIOD_S:
            self.last_text = now
            self.update_text(state)

    def update_text(self, state: dict) -> None:
        self.update_status(state)
        self.update_joints(state)
        for view in self.views.values():
            view.update()
        for panel in self.all_plots:
            panel.draw()
        self.safety.draw(state)
        self.latency.draw(state)
        self.limits.refresh()

    def update_status(self, state: dict) -> None:
        ui, t = self.ui, self.t
        stats = state.get("stats") or {}
        held = stats.get("held_cycles", 0) or 0
        badges = [html.badge(ui.t("已暂停", "paused"), "warn") if state.get("paused")
                  else html.badge(t("running"), "ok"),
                  html.badge(ui.t("实测新鲜", "fresh"), "ok") if state.get("fresh", True)
                  else html.badge(ui.t("实测过期", "stale"), "bad")]
        if self.last_lease and self.last_lease.get("holder") is not None:
            badges.append(html.badge(ui.t("独占", "exclusive"), "accent" if self.mine else "warn"))
        failed = stats.get("failed_cycles", 0) or 0
        rate = self.measure_rate(state)
        actual = self.actual_hz
        slow = actual is not None and not state.get("paused") and actual < 0.95 * rate
        stages = state.get("stages") or []
        qp = sum(float(s.get("solve_s") or 0.0) for s in stages) * 1e3
        if qp > 0 and not state.get("paused"):
            self.qp_average = qp if self.qp_average is None else self.qp_average + 0.05 * (qp - self.qp_average)
        cycle = float(stats.get("last_cycle") or 0.0) * 1e3
        budget = 1e3 / rate if rate > 0 else 0.0
        load = cycle / budget if budget > 0 else 0.0
        cells = [
            html.stat(ui.t("QP 求解", "QP solve"), f"{qp:.2f} ms",
                      sub=ui.t("平均", "avg") + ("" if self.qp_average is None else f" {self.qp_average:.2f} ms")),
            html.stat(ui.t("整周期", "cycle"), f"{cycle:.2f} ms", "bad" if load > 0.8 else ("warn" if load > 0.5 else None),
                      sub=ui.t("预算", "budget") + f" {budget:.1f} ms · {min(load, 9.99) * 100:.0f}%"),
            html.stat(ui.t("引擎时间", "engine time"), f"{state['time']:.2f} s",
                      sub=ui.t("暂停时照常计时", "keeps counting while paused") if state.get("paused") else ""),
            html.stat(ui.t("实际频率", "actual rate"), "--" if actual is None else f"{actual:.0f} Hz", "warn" if slow else None,
                      sub=ui.t("设定", "set") + f" {rate:g} Hz"),
            html.stat(ui.t("周期 p99", "cycle p99"), f"{ms(stats.get('cycle_p99'))} ms",
                      sub=f"p50 {ms(stats.get('cycle_p50'))} ms"),
            html.stat(ui.t("超时", "overruns"), f"{stats.get('overruns', 0) or 0:.0f}",
                      "warn" if stats.get("overruns") else None),
            html.stat(ui.t("失败周期", "failed"), f"{failed:.0f}", "bad" if failed else None),
            html.stat(ui.t("保持周期", "held"), f"{held:.0f}", "warn" if held else None),
        ]
        ModuleView.set_html(self.status, html.card(html.badge_row(badges) + html.grid(cells)))
        paused = bool(state.get("paused"))
        if self.step_cycles.visible != paused:
            self.step_cycles.visible = paused
        rows = [[html.esc(r["name"]), "-" if r["level"] < 0 else str(r["level"]),
                 f"{r['residual']:.2e}" if finite(r.get("residual")) else "--"] for r in state["residuals"]]
        ModuleView.set_html(self.residuals, html.table((ui.t("任务", "task"), ui.t("层级", "level"), ui.t("残差", "residual")),
                                                       rows, align=("left", "right", "right")))
        mode = ((self.solver.values if self.solver is not None else None) or {}).get("mode")
        info = ([(ui.t("模式", "mode"), html.esc(mode))] if mode is not None else []) + \
               [(ui.t("模型维度", "model size"), f"nq {self.hello['nq']} · nv {self.hello['nv']}")]
        body = html.rows(info)
        quality = state.get("quality")
        if quality:
            body += html.badge_row([html.badge(ui.t("正常", "nominal") if quality == "nominal" else ui.t("降级", "degraded"),
                                               "ok" if quality == "nominal" else "warn")])
        if stages:
            tones = {"optimal": "ok", "inexact": "warn"}
            single = mode in ("weighted", "lexicographic")
            lines = [[ui.t("全部", "all") if single else str(s.get("level", i)), html.badge(str(s.get("status", "-")), tones.get(s.get("status"), "bad")),
                      str(s.get("iterations", 0)), f"{float(s.get('solve_s') or 0.0) * 1e3:.3f}",
                      "✓" if s.get("held") else "–"] for i, s in enumerate(stages)]
            body += html.table((ui.t("层", "level"), ui.t("状态", "status"), ui.t("迭代", "iter"), "ms", ui.t("保持", "held")),
                               lines, align=("left", "left", "right", "right", "center"))
        if body:
            ModuleView.set_html(self.solver_card, html.card(body))

    def update_joints(self, state: dict) -> None:
        """Ideal (the command the model draws) and feedback per joint; joints no task drives (the chassis root,
        mimic followers, free wheels) are listed last."""
        ui = self.ui
        needle = self.joint_filter.value.strip().lower()
        command = state.get("q_command") or state["q"]
        if len(command) != len(state["q"]):
            command = state["q"]
        if self.task_joints is None:
            self.task_joints = {name for m in self.hello["modules"] if m.get("type") != "chassis"
                                for name in m.get("driven") or ()}
        driven, idle = [], []
        for j in self.model_joints:
            if needle and needle not in j["name"].lower():
                continue
            i, n = j["idx_q"], j["nq"]
            group = driven if j["name"] in self.task_joints else idle
            if n > 2:
                value = ", ".join(html.signed(v) for v in command[i:i + n])
                group.append([html.esc(j["name"]), value, "", "", html.esc(j["type"])])
                continue
            ideal, measured = self.joint_value(j, command), self.joint_value(j, state["q"])
            error = measured - ideal
            limit = getattr(self.urdf.joint_map.get(j["name"]), "limit", None)
            lo, hi = (limit.lower, limit.upper) if limit is not None else (None, None)
            if finite(lo) and finite(hi) and hi > lo:
                fraction = (measured - lo) / (hi - lo)
                edge = min(fraction, 1.0 - fraction)
                gauge = html.bar(fraction, "bad" if edge < 0.02 else ("warn" if edge < 0.08 else "info"), height=5)
            else:
                gauge = ""
            delta = html.signed(error) if abs(error) >= 5e-4 else "·"
            if abs(error) >= 0.02:
                delta = f"<span style='color:#d97706;font-weight:600'>{delta}</span>"
            group.append([html.esc(j["name"]), html.signed(ideal), html.signed(measured), delta, gauge])
        if not driven and not idle:
            content = html.note(ui.t("没有匹配的关节", "No joints match"), "muted")
        else:
            header = tuple(ui.t(*h) for h in JOINT_HEADER)
            content = html.table(header, driven, align=JOINT_ALIGN, widths=JOINT_WIDTHS) if driven else ""
            if idle:
                content += html.note(ui.t("以下关节没有任务驱动（例如底盘根关节、从动关节）",
                                          "No task drives the joints below (e.g. the chassis root, mimic joints)"),
                                     "muted", one_line=True)
                content += html.table(header, idle, align=JOINT_ALIGN, widths=JOINT_WIDTHS)
        ModuleView.set_html(self.joint_table, content)

    @staticmethod
    def joint_value(j: dict, q: list) -> float:
        i = j["idx_q"]
        return math.atan2(q[i + 1], q[i]) if j["nq"] == 2 else q[i]

    def flush(self) -> None:
        if self.framing is not None:
            for cid, client in self.server.get_clients().items():
                if cid not in self.framed_clients:
                    self.framed_clients.add(cid)
                    self.place_camera(client)
        for throttle in self.throttles:
            throttle.flush()
        now = time.monotonic()
        watched = bool(self.server.get_clients())
        for view in self.views.values():
            if not watched:  # With no browser left, let held commands lapse instead of resending them unattended.
                view.stop_keepalive()
            view.flush(now)
            if view.tune is not None:
                view.tune.poll()
        if self.solver is not None:
            self.solver.poll()

    def close(self) -> None:
        for view in self.views.values():
            view.close()
