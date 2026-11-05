"""Tuning controls generated from a module's tune_schema (JSON Schema subset)."""

import contextlib
import json
import math
import time

from .text import param_text

EDIT_HOLD_S = 1.0
# Wider ranges (e.g. weights up to 1e6) would make a 1000-step slider snap small values to 0.
SLIDER_SPAN = 10.0
COMMIT_S = 0.6


def finite(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def lookup(values, path):
    for key in path:
        if not isinstance(values, dict) or key not in values:
            return None
        values = values[key]
    return values


def nest(path, value) -> dict:
    out = value
    for key in reversed(path):
        out = {key: out}
    return out


def format_list(values) -> str:
    # The server writes non-finite numbers as null.
    return ", ".join("inf" if v is None else f"{v:g}" for v in values or [])


def parse_list(text: str) -> list:
    values = [float(s) for s in text.replace(";", ",").split(",") if s.strip()]
    if any(math.isnan(v) for v in values):
        raise ValueError("NaN")
    return values


class Leaf:
    def __init__(self, panel: "TunePanel", path: tuple, schema: dict):
        self.panel = panel
        self.path = path
        self.schema = schema
        self.kind = schema.get("type")
        # Arrays of objects or strings are edited as JSON text.
        self.json = self.kind == "array" and (schema.get("items") or {}).get("type") not in ("number", "integer")
        self.edited = 0.0
        self.shown = None
        self.draft = False
        gui = panel.viewer.server.gui
        label, hint, text = panel.label(path, schema.get("description"))
        lo = schema.get("minimum", schema.get("exclusiveMinimum"))
        hi = schema.get("maximum", schema.get("exclusiveMaximum"))
        lo = lo if finite(lo) else None
        hi = hi if finite(hi) else None
        if self.kind == "boolean":
            self.control = gui.add_checkbox(label, initial_value=False, hint=hint)
        elif self.kind == "string" and schema.get("enum"):
            options = [str(o) for o in schema["enum"]]
            self.control = gui.add_dropdown(label, options, initial_value=options[0], hint=hint)
        elif self.kind == "array":
            self.control = gui.add_text(label, "", hint=hint)
        elif self.kind == "integer":
            self.control = gui.add_number(label, 0, min=lo, max=hi, step=1, hint=hint)
        elif self.slider(lo, hi):
            self.control = gui.add_slider(label, min=lo, max=hi, step=(hi - lo) / 1000.0, initial_value=lo, hint=hint)
        else:
            # Text, not a number input: the browser's number input mangles "1e-6" into "-16".
            self.control = gui.add_text(label, "", hint=hint)
        # Typed fields fire per keystroke; they commit once typing pauses.
        self.typed = self.kind != "boolean" and not hasattr(self.control, "options") and not (
            self.kind == "number" and self.slider(lo, hi)
        )
        panel.relabel(self.control, text)
        self.control.on_update(self._changed)

    @staticmethod
    def slider(lo, hi) -> bool:
        return lo is not None and hi is not None and 0 < hi - lo <= SLIDER_SPAN

    def _value(self):
        v = self.control.value
        if self.json:
            try:
                return json.loads(v or "[]")
            except json.JSONDecodeError as e:
                raise ValueError(str(e)) from None
        if self.kind == "array":
            return parse_list(v)
        if self.kind == "integer":
            return int(round(float(v)))
        if self.kind == "number":
            v = float(v)
            if not math.isfinite(v):
                raise ValueError("non-finite")
        return v

    def _changed(self, event) -> None:
        if event.client is None:
            return
        self.edited = time.monotonic()
        if self.typed:
            self.draft = True
            return
        self.commit()

    def poll(self) -> None:
        if self.draft and time.monotonic() - self.edited >= COMMIT_S:
            self.commit()

    def commit(self) -> None:
        self.draft = False
        what = f"{self.panel.op} {self.panel.target}.{'.'.join(self.path)}"
        try:
            value = self._value()
        except ValueError as e:
            self.panel.viewer.bad_input(what, str(e))
            return
        if value == self.shown:
            return
        self.shown = value
        self.panel.send(self.path, value)

    def show(self, value, force: bool = False) -> None:
        if value is None and self.kind != "array":
            return
        if self.draft or (not force and time.monotonic() - self.edited < EDIT_HOLD_S):
            return
        if self.json:
            self.shown = value or []
            text = json.dumps(self.shown)
        elif self.kind == "array":
            self.shown = [math.inf if v is None else v for v in value or []]
            text = format_list(value)
        else:
            self.shown = value
            text = value
            if self.kind == "number" and isinstance(self.control.value, str):
                text = format(value, ".10g") if finite(value) else str(value)
            elif self.kind in ("number", "integer") and hasattr(self.control, "min"):
                lo, hi = self.control.min, self.control.max
                text = min(max(value, lo if lo is not None else value), hi if hi is not None else value)
        if self.control.value != text:
            self.control.value = text


class TunePanel:
    """One folder per module (or the solver); edits send a partial tune, server broadcasts refresh the controls."""

    def __init__(self, viewer, module: str | None, schema: dict, params: dict, t, op: str = "tune", title: str = "params",
                 kind: str | None = None, exclude: tuple = (), inline: bool = False):
        self.viewer = viewer
        self.kind = kind
        self.exclude = set(exclude)
        self.module = module
        self.op = op
        self.target = module or "solver"
        self.schema = schema
        self.leaves = []
        self.values = params or {}
        gui = viewer.server.gui
        self.folder = None if inline else gui.add_folder(t(title), expand_by_default=False)
        if self.folder is not None:
            viewer.keyed(self.folder, "label", title)
        from viser import Icon

        with self.folder or contextlib.nullcontext():
            self._build(schema, ())
            button = gui.add_button(t("defaults"), icon=Icon.RESTORE)
            viewer.keyed(button, "label", "defaults")
            button.on_click(lambda _: self.restore_defaults())
        self.show(self.values, force=True)

    def _build(self, schema: dict, path: tuple) -> None:
        for key, sub in (schema.get("properties") or {}).items():
            if sub.get("type") == "object":
                if not self._editable(sub, path + (key,)):
                    continue
                label, _, text = self.label(path + (key,), None)
                folder = self.viewer.server.gui.add_folder(label, expand_by_default=False)
                self.relabel(folder, text, hint=False)
                with folder:
                    self._build(sub, path + (key,))
            elif sub.get("tunable", True) and (path + (key,)) not in self.exclude:
                self.leaves.append(Leaf(self, path + (key,), sub))

    def _editable(self, schema: dict, path: tuple) -> bool:
        for key, sub in (schema.get("properties") or {}).items():
            if sub.get("type") == "object":
                if self._editable(sub, path + (key,)):
                    return True
            elif sub.get("tunable", True) and (path + (key,)) not in self.exclude:
                return True
        return False

    def label(self, path: tuple, doc: str | None) -> tuple:
        """(label, hint, (zh, en, hint_zh, hint_en) or None) in the current language."""
        text = param_text(self.kind, path)
        if text is None:
            return path[-1], doc, None
        zh, en, *hints = text
        hint_zh, hint_en = hints if hints else (doc, doc)
        text = (zh, en, hint_zh, hint_en or doc)
        ui = self.viewer.ui
        return ui.t(zh, en), ui.t(text[2], text[3]), text

    def relabel(self, handle, text: tuple | None, hint: bool = True) -> None:
        if text is None:
            return
        self.viewer.ui.bind(handle, "label", text[:2])
        if hint and text[2] is not None:
            self.viewer.ui.bind(handle, "hint", text[2:])

    def send(self, path: tuple, value) -> None:
        self.viewer.request(self.op, self.module, label=".".join(path), on_fail=self.revert, params=nest(path, value))

    def defaults(self) -> dict:
        out = {}
        for leaf in self.leaves:
            d = leaf.schema.get("default")
            if d is None:
                continue
            node = out
            for key in leaf.path[:-1]:
                node = node.setdefault(key, {})
            node[leaf.path[-1]] = d
        for path in self.exclude:
            sub = self.schema
            for key in path:
                sub = (sub.get("properties") or {}).get(key) or {}
            if sub.get("default") is not None and sub.get("tunable", True):
                node = out
                for key in path[:-1]:
                    node = node.setdefault(key, {})
                node[path[-1]] = sub["default"]
        return out

    def restore_defaults(self) -> None:
        for leaf in self.leaves:
            leaf.edited = 0.0
        self.viewer.request(self.op, self.module, label="defaults", on_fail=self.revert, params=self.defaults())

    def revert(self) -> None:
        self.show(self.values, force=True)

    def poll(self) -> None:
        for leaf in self.leaves:
            leaf.poll()

    def show(self, values: dict, force: bool = False) -> None:
        self.values = values
        for leaf in self.leaves:
            leaf.show(lookup(values, leaf.path), force)
