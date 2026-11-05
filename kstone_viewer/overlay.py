"""Module overlays: declared visuals and display outputs, drawn at the pose the server evaluated them at."""

import numpy as np
import trimesh

from .geometry import put
from .tune import finite

VISUAL_COLOR = (255, 140, 40)
VISUAL_OPACITY = 0.45
POINT_RADIUS = 0.02
POINT_OPACITY = 0.9
# Output role -> (color, opacity of tinted visuals); value outputs cycle through the palette.
ROLE_STYLE = {
    "alert": ((230, 50, 40), 0.7),
    "warning": ((232, 162, 0), 0.6),
    "boundary": ((20, 184, 166), 0.6),
}
# Visuals tinted by several outputs take the most severe role.
ROLE_RANK = {"value": 0, "margin": 0, "boundary": 1, "warning": 2, "alert": 3}
PALETTE = [(37, 99, 235), (22, 163, 74), (219, 39, 119), (202, 138, 4), (8, 145, 178)]
GAZE_COLOR = (232, 162, 0)


def display_key(module_type: str, output: str) -> str | None:
    """The scene toggle (besides the module's own) that also gates an output, like the ancestor's display groups."""
    if output in ("planned", "goal"):
        return "show_planned"
    if module_type == "gaze":
        return "show_gaze"
    if module_type == "center_of_mass":
        return "show_com"
    return None


def shape_mesh(shape: dict) -> trimesh.Trimesh | None:
    kind, size = shape.get("type"), shape.get("size") or [0.0, 0.0, 0.0]
    if kind == "sphere":
        return trimesh.creation.icosphere(subdivisions=2, radius=size[0])
    if kind == "box":
        return trimesh.creation.box(extents=size)
    if kind == "cylinder":
        return trimesh.creation.cylinder(radius=size[0], height=size[1], sections=24)
    if kind == "capsule":
        return trimesh.creation.capsule(height=size[1], radius=size[0], count=[16, 16])
    if kind == "convex" and shape.get("vertices"):
        return trimesh.convex.convex_hull(np.array(shape["vertices"], dtype=float))
    return None


def valid_point(p) -> bool:
    return isinstance(p, list) and len(p) == 3 and all(finite(v) for v in p)


class ModuleOverlay:
    def __init__(self, server, module: dict, show=lambda key: True):
        self.server, self.show_key = server, show
        self.keys = {out["name"]: display_key(module.get("type", ""), out["name"]) for out in module.get("outputs") or []}
        self.root = server.scene.add_frame(f"/overlay/{module['id']}", show_axes=False)
        self.outputs = module.get("outputs") or []
        self.visuals = []
        for i, visual in enumerate(module.get("visuals") or []):
            mesh = shape_mesh(visual.get("shape", {}))
            if mesh is None:
                self.visuals.append(None)
                continue
            self.visuals.append(
                server.scene.add_mesh_simple(
                    f"/overlay/{module['id']}/visual{i}", mesh.vertices.astype(np.float32),
                    mesh.faces.astype(np.uint32), color=VISUAL_COLOR, opacity=VISUAL_OPACITY,
                    cast_shadow=False, receive_shadow=False,
                )
            )
        self.styles = {}
        self.handles = {}
        for i, out in enumerate(self.outputs):
            name = out["name"]
            self.styles[name] = ROLE_STYLE.get(out.get("role"), (PALETTE[i % len(PALETTE)], VISUAL_OPACITY))
            if module.get("type") == "gaze" and out["type"] == "point":
                self.styles[name] = (GAZE_COLOR, VISUAL_OPACITY)
            path = f"/overlay/{module['id']}/{name}"
            if out["type"] == "point":
                self.handles[name] = server.scene.add_icosphere(
                    path, POINT_RADIUS, self.styles[name][0], opacity=POINT_OPACITY, visible=False,
                    cast_shadow=False, receive_shadow=False,
                )
            elif out["type"] in ("segment", "polygon"):
                self.handles[name] = None  # created on first value, since line segments need points
        # Segment outputs are labelled with their length, like the ancestor's witness distance.
        self.labels = {
            out["name"]: server.scene.add_label(f"/overlay/{module['id']}/{out['name']}_label", "", visible=False)
            for out in self.outputs
            if out["type"] == "segment"
        }
        self.enabled = True
        self.root.visible = False

    @property
    def empty(self) -> bool:
        return not self.outputs and not self.visuals

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        if not enabled:
            self.root.visible = False

    def _lines(self, name: str, points: np.ndarray) -> None:
        handle = self.handles.get(name)
        if len(points) == 0:
            if handle is not None:
                handle.visible = False
            return
        if handle is None:
            self.handles[name] = self.server.scene.add_line_segments(
                f"{self.root.name}/{name}", points=points, colors=self.styles[name][0], thickness=4.0,
                thickness_units="screen",
            )
        else:
            handle.points = points
            handle.visible = True

    def show(self, display) -> None:
        if not self.enabled or not isinstance(display, dict):
            self.root.visible = False
            return
        self.root.visible = True
        poses = display.get("visual_poses") or []
        for visual, pose in zip(self.visuals, poses):
            if visual is None:
                continue
            visual.visible = pose is not None
            if pose is not None:
                put(visual, "position", pose["translation"])
                put(visual, "wxyz", pose["rotation"])
        tint = {}
        values = display.get("outputs", {})
        tinting = sorted((o for o in self.outputs if o["type"] == "visuals"), key=lambda o: ROLE_RANK.get(o.get("role"), 0))
        for out in tinting:
            for v in values.get(out["name"]) or []:
                tint[int(v)] = self.styles[out["name"]]
        for i, visual in enumerate(self.visuals):
            if visual is not None:
                color, opacity = tint.get(i, (VISUAL_COLOR, VISUAL_OPACITY))
                if visual.color != color:
                    visual.color = color
                if visual.opacity != opacity:
                    visual.opacity = opacity
        for out in self.outputs:
            name, kind, value = out["name"], out["type"], values.get(out["name"])
            key = self.keys.get(name)
            if key is not None and not self.show_key(key):
                value = None
            if kind == "point":
                handle = self.handles[name]
                handle.visible = valid_point(value)
                if handle.visible:
                    handle.position = value
            elif kind in ("segment", "polygon"):
                pts = [p for p in value or [] if valid_point(p)] if isinstance(value, list) else []
                if kind == "segment":
                    segs = np.array([pts[:2]], dtype=np.float32) if len(pts) == 2 else np.zeros((0, 2, 3), np.float32)
                    label = self.labels[name]
                    label.visible = len(pts) == 2 and self.show_key("show_labels")
                    if label.visible:
                        a, b = np.asarray(pts[0], float), np.asarray(pts[1], float)
                        label.position = tuple((a + b) / 2)
                        label.text = f"{name} {np.linalg.norm(b - a) * 1000:.1f} mm"
                else:
                    segs = np.array([[pts[i], pts[(i + 1) % len(pts)]] for i in range(len(pts))], dtype=np.float32)
                    if len(pts) < 2:
                        segs = np.zeros((0, 2, 3), np.float32)
                self._lines(name, segs)
