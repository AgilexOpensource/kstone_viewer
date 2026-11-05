"""Safety tab: run-time joint limits (tune_limits). Tighten only; the model's limits come from URDF + YAML."""

import math
import time

from .. import html
from ..tune import EDIT_HOLD_S

NONE = "—"


def finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def bounded_velocity(value) -> float | None:
    return float(value) if finite(value) and 1e-10 < value < 1e20 else None


def set_html(handle, content: str) -> None:
    if handle.content != content:
        handle.content = content


class JointLimitsPanel:
    def __init__(self, viewer, hello: dict):
        self.viewer, ui = viewer, viewer.ui
        self.ui = ui
        self.values = hello.get("joint_limits") or {}
        # Scalar joints the solver can limit, with their model limits.
        self.joints: dict[str, dict] = {}
        for j in hello["joints"]:
            if j["nq"] != 1 or j["nv"] != 1:
                continue
            limits = j.get("limits") or {}
            lower, upper = (limits.get("lower") or [None])[0], (limits.get("upper") or [None])[0]
            ranged = bool(limits.get("position")) and finite(lower) and finite(upper) and lower < upper
            self.joints[j["name"]] = {"lower": lower if ranged else None, "upper": upper if ranged else None,
                                      "velocity": bounded_velocity((limits.get("velocity") or [None])[0])}
        self.added: list[str] = []
        self.folders: dict[str, tuple] = {}
        self.edited: dict[tuple, float] = {}
        ui.static("<div style='font-size:12px;opacity:.7'>只能收紧模型限制（URDF + YAML）；放宽请改 YAML。"
                  "速度 = 模型值即取消覆盖。修改仅本次运行有效。</div>",
                  "<div style='font-size:12px;opacity:.7'>Only tightens the model limits (URDF + YAML); raise them "
                  "in the YAML. Velocity at the model value drops the override. Changes last for this run only.</div>")
        ui.button("恢复配置值", "Restore configured values", icon="RESTORE").on_click(lambda _: self.reset())
        self.card = ui.html("")
        self.folder = ui.folder("单关节调整", "Per-joint tuning", expand=False)
        with self.folder:
            self.pick = ui.gui.add_dropdown(ui.t("添加关节", "Add joint"), (NONE,), initial_value=NONE)
            ui.bind(self.pick, "label", ("添加关节", "Add joint"))
            self.add = ui.button("添加", "Add", icon="PLUS", hint=("仅本次运行有效", "For this run only"))
            self.add.on_click(lambda _: self.add_joint(self.pick.value))
        self.show(self.values)

    def overrides(self) -> dict[str, dict]:
        """joint -> {lower, upper, velocity} overrides in force."""
        v, p = self.values.get("velocity") or {}, self.values.get("position") or {}
        out: dict[str, dict] = {}
        for key, table in (("velocity", v), ("lower", p.get("lower") or {}), ("upper", p.get("upper") or {})):
            for name, value in table.items():
                if finite(value):
                    out.setdefault(name, {})[key] = float(value)
        return out

    def effective(self, name: str, key: str) -> float | None:
        override = self.overrides().get(name, {}).get(key)
        return override if override is not None else self.joints[name][key]

    def show(self, values: dict) -> None:
        self.values = values or {}
        overrides = self.overrides()
        listed = [n for n in self.joints if n in overrides or n in self.added]
        for name in [n for n in self.folders if n not in listed]:
            self.folders.pop(name)[0].remove()
        for name in listed:
            if name not in self.folders:
                with self.folder:
                    self.build_joint(name)
            self.sync_joint(name)
        options = tuple(n for n in self.joints if n not in listed) or (NONE,)
        if tuple(self.pick.options) != options:
            with self.ui.syncing():
                self.pick.options = options
                self.pick.value = options[0]
        self.add.disabled = options == (NONE,)
        self.update()

    def build_joint(self, name: str) -> None:
        ui, j = self.ui, self.joints[name]
        sliders = {}
        with ui.folder(name, name, expand=True) as folder:
            if j["lower"] is not None:
                step = max((j["upper"] - j["lower"]) / 1000.0, 1e-4)
                for side, zh, en in (("lower", "位置下限", "Lower"), ("upper", "位置上限", "Upper")):
                    sliders[side] = ui.slider(zh, en, j["lower"], j["upper"], step, j[side],
                                              hint=("在模型范围内收紧", "Tightens the model range"))
            top = j["velocity"] if j["velocity"] is not None else 10.0
            hint = (f"模型 {html.fmt(j['velocity'], 2)}" if j["velocity"] else "模型无限制，0 = 不限",
                    f"model {html.fmt(j['velocity'], 2)}" if j["velocity"] else "model unbounded, 0 = none")
            sliders["velocity"] = ui.slider("速度上限", "Velocity", 0.0, top, top / 500.0, top, hint=hint)
        for key, handle in sliders.items():
            handle.on_update(lambda event, key=key: self.changed(event, name, key))
        self.folders[name] = (folder, sliders)

    def sync_joint(self, name: str) -> None:
        now = time.monotonic()
        for key, handle in self.folders[name][1].items():
            if now - self.edited.get((name, key), -1e9) < EDIT_HOLD_S:
                continue
            value = self.effective(name, key)
            if key == "velocity" and value is None:
                value = 0.0
            if value is not None:
                self.ui.set_value(handle, min(max(float(value), handle.min), handle.max), tolerance=1e-9)

    def changed(self, event, name: str, key: str) -> None:
        if event.client is None or not self.ui.user():
            return
        self.edited[(name, key)] = time.monotonic()
        value = float(event.target.value)
        j = self.joints[name]
        if key == "velocity":
            # At the model value (or 0 when unbounded) the override is dropped.
            if j["velocity"] is not None and value >= j["velocity"] - 1e-12:
                value = 0.0
            params = {"velocity": {name: value}}
        else:
            params = {"position": {key: {name: value}}}
        self.viewer.request("tune_limits", label=f"{name}.{key}", on_fail=self.revert, params=params)

    def add_joint(self, name: str) -> None:
        if name in self.joints and name not in self.added:
            self.added.append(name)
            self.show(self.values)

    def reset(self) -> None:
        self.added.clear()
        self.edited.clear()
        self.viewer.request("tune_limits", label="reset", on_fail=self.revert, params={"reset": True})

    def revert(self) -> None:
        self.edited.clear()
        self.show(self.values)

    def refresh(self) -> None:
        for name in self.folders:
            self.sync_joint(name)

    def update(self) -> None:
        ui, overrides = self.ui, self.overrides()
        if not self.joints:
            set_html(self.card, html.note(ui.t("没有可限制的标量关节", "No scalar joints to limit"), "muted"))
            return

        def cell(name: str, key: str, digits: int) -> str:
            value = self.effective(name, key)
            text = ("-∞" if key == "lower" else "∞") if value is None else html.fmt(value, digits)
            return f"<b style='color:#d9480f'>{text}</b>" if key in overrides.get(name, {}) else text

        body = [[html.esc(n), cell(n, "lower", 3), cell(n, "upper", 3), cell(n, "velocity", 2)] for n in self.joints]
        set_html(self.card, html.table(
            (ui.t("关节", "joint"), ui.t("下限", "lower"), ui.t("上限", "upper"), ui.t("速度", "vel")), body,
            align=("left", "right", "right", "right"), max_height=320)
            + html.note(ui.t("生效值；橙色为本次运行收紧的值，其余来自模型。",
                             "Values in force; orange marks values tightened in this run, the rest come from the model."),
                        "muted"))
