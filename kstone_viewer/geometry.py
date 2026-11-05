"""Pose helpers shared by the viewer modules."""

import math

import numpy as np


def parse_endpoint(text: str) -> tuple[str, int]:
    host, _, port = text.rpartition(":")
    return host or "127.0.0.1", int(port)


def pose_to_wire(position, wxyz) -> dict:
    return {"translation": [float(v) for v in position], "rotation": [float(v) for v in wxyz]}


def matrix_to_wxyz(r: np.ndarray) -> np.ndarray:
    w = math.sqrt(max(0.0, 1.0 + r[0, 0] + r[1, 1] + r[2, 2])) / 2.0
    x = math.copysign(math.sqrt(max(0.0, 1.0 + r[0, 0] - r[1, 1] - r[2, 2])) / 2.0, r[2, 1] - r[1, 2])
    y = math.copysign(math.sqrt(max(0.0, 1.0 - r[0, 0] + r[1, 1] - r[2, 2])) / 2.0, r[0, 2] - r[2, 0])
    z = math.copysign(math.sqrt(max(0.0, 1.0 - r[0, 0] - r[1, 1] + r[2, 2])) / 2.0, r[1, 0] - r[0, 1])
    return np.array([w, x, y, z])


def root_transform(joint: dict, q: list) -> np.ndarray:
    """Pose of a free_flyer (x y z qx qy qz qw) or planar (x y cos sin) root joint."""
    t = np.eye(4)
    i = joint["idx_q"]
    if joint["type"] == "free_flyer":
        t[:3, 3] = q[i : i + 3]
        x, y, z, w = q[i + 3 : i + 7]
        t[:3, :3] = wxyz_to_matrix(np.array([w, x, y, z]))
    else:
        t[:2, 3] = q[i : i + 2]
        c, s = q[i + 2], q[i + 3]
        t[:2, :2] = [[c, -s], [s, c]]
    return t


def axis_transform(kind: str, axis: np.ndarray, q: float) -> np.ndarray:
    t = np.eye(4)
    if kind == "prismatic":
        t[:3, 3] = axis * q
    elif kind in ("revolute", "continuous"):
        x, y, z = axis
        c, s = math.cos(q), math.sin(q)
        k = np.array([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]])
        t[:3, :3] = np.eye(3) + s * k + (1.0 - c) * (k @ k)
    return t


class LinkKinematics:
    """Link poses relative to the URDF base link, one pass over the joint tree (much cheaper than per-link lookups)."""

    def __init__(self, urdf) -> None:
        index = {name: i for i, name in enumerate(urdf.actuated_joint_names)}
        children: dict[str, list] = {}
        for joint in urdf.robot.joints:
            children.setdefault(joint.parent, []).append(joint)
        self.base = urdf.base_link
        self.steps = []
        stack = [self.base]
        while stack:
            for joint in children.get(stack.pop(), []):
                origin = np.eye(4) if joint.origin is None else np.asarray(joint.origin, dtype=float)
                axis = np.asarray(joint.axis if joint.axis is not None else (1.0, 0.0, 0.0), dtype=float)
                axis = axis / (np.linalg.norm(axis) or 1.0)
                mimic = joint.mimic
                source = index.get(mimic.joint if mimic is not None else joint.name)
                scale = 1.0 if mimic is None or mimic.multiplier is None else float(mimic.multiplier)
                offset = 0.0 if mimic is None or mimic.offset is None else float(mimic.offset)
                moving = joint.type in ("revolute", "continuous", "prismatic")
                self.steps.append((joint.parent, joint.child, origin, joint.type, axis, source, scale, offset, moving))
                stack.append(joint.child)

    def poses(self, cfg, local: dict | None = None) -> dict[str, np.ndarray]:
        out = {self.base: np.eye(4)}
        for parent, child, origin, kind, axis, source, scale, offset, moving in self.steps:
            step = origin
            if moving:
                q = (cfg[source] if source is not None else 0.0) * scale + offset
                step = origin @ axis_transform(kind, axis, q)
            if local is not None:
                local[child] = step
            out[child] = out[parent] @ step
        return out


class UrdfPoser:
    """Drives a ViserUrdf, sending only joint frames that moved; falls back to update_cfg if viser internals differ."""

    def __init__(self, handle, kinematics: LinkKinematics) -> None:
        self.handle, self.kinematics = handle, kinematics
        frames, joints = getattr(handle, "_joint_frames", None), getattr(handle, "_joint_map_values", None)
        ok = isinstance(frames, list) and isinstance(joints, list) and len(frames) == len(joints)
        self.frames = [(j.child, f) for j, f in zip(joints, frames)] if ok else None
        self.scale = float(getattr(handle, "_scale", 1.0))
        self.sent: dict[int, np.ndarray] = {}

    def update(self, cfg) -> dict[str, np.ndarray]:
        if self.frames is None:
            self.handle.update_cfg(cfg)
            return self.kinematics.poses(cfg)
        local: dict[str, np.ndarray] = {}
        poses = self.kinematics.poses(cfg, local)
        for i, (child, frame) in enumerate(self.frames):
            step = local.get(child)
            if step is None:
                continue
            last = self.sent.setdefault(i, np.full((4, 4), np.nan))
            # Revolute steps keep their translation, so each half is sent only when it moved.
            if not np.abs(last[:3, :3] - step[:3, :3]).max() <= 1e-7:
                put(frame, "wxyz", matrix_to_wxyz(step[:3, :3]))
                last[:3, :3] = step[:3, :3]
            if not np.abs(last[:3, 3] - step[:3, 3]).max() <= 1e-7:
                put(frame, "position", step[:3, 3] * self.scale)
                last[:3, 3] = step[:3, 3]
        return poses


def matrices_to_wxyz(r: np.ndarray) -> np.ndarray:
    return np.array([matrix_to_wxyz(m) for m in r]) if len(r) else np.zeros((0, 4))


try:
    from viser._messages import SetOrientationMessage, SetPositionMessage
    _MESSAGES = {"wxyz": SetOrientationMessage, "position": SetPositionMessage}
except ImportError:
    _MESSAGES = {}


def put(handle, attr: str, value) -> None:
    """Pose setter without viser's per-call allclose; uses the public setter if viser internals differ."""
    impl = getattr(handle, "_impl", None)
    current = getattr(impl, attr, None)
    queue = getattr(getattr(impl, "api", None), "_queue_scene_message", None)
    if attr not in _MESSAGES or not isinstance(current, np.ndarray) or queue is None or getattr(impl, "removed", True):
        setattr(handle, attr, value)
        return
    v = tuple(float(x) for x in value)
    if len(v) == current.size and max(abs(a - b) for a, b in zip(v, current.tolist())) <= 1e-9:
        return
    current[:] = v
    queue(_MESSAGES[attr](impl.name, v))


def set_pose(handle, pose: np.ndarray) -> None:
    put(handle, "position", pose[:3, 3])
    put(handle, "wxyz", matrix_to_wxyz(pose[:3, :3]))


def wxyz_to_matrix(q) -> np.ndarray:
    w, x, y, z = q / np.linalg.norm(q)
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
        [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
        [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
    ])


def rpy_to_matrix(rpy) -> np.ndarray:
    """Fixed-axis roll, pitch, yaw in radians: R = Rz(yaw) Ry(pitch) Rx(roll)."""
    cr, sr = math.cos(rpy[0]), math.sin(rpy[0])
    cp, sp = math.cos(rpy[1]), math.sin(rpy[1])
    cy, sy = math.cos(rpy[2]), math.sin(rpy[2])
    return np.array([
        [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
        [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
        [-sp, cp * sr, cp * cr],
    ])


def matrix_to_rpy(r: np.ndarray) -> tuple[float, float, float]:
    pitch = math.asin(min(max(-float(r[2, 0]), -1.0), 1.0))
    return math.atan2(r[2, 1], r[2, 2]), pitch, math.atan2(r[1, 0], r[0, 0])
