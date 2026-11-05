"""Per-type module views, keyed by the kstone module type; other types get the generic card."""

from .base import ModuleView
from .behaviors import GazeView
from .chassis import ChassisView
from .generic import GenericView
from .safety import CenterOfMassView, CollisionView
from .tasks import FrameTaskView, JointTargetView, NullSpacePreferenceView

VIEWS: dict[str, type[ModuleView]] = {
    "frame_task": FrameTaskView,
    "joint_target": JointTargetView,
    "null_space_preference": NullSpacePreferenceView,
    "gaze": GazeView,
    "chassis": ChassisView,
    "center_of_mass": CenterOfMassView,
    "self_collision": CollisionView,
    "world_collision": CollisionView,
}


def view_for(module_type: str) -> type[ModuleView]:
    return VIEWS.get(module_type, GenericView)


__all__ = ["VIEWS", "ModuleView", "GenericView", "view_for"]
