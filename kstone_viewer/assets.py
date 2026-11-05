"""Local cache of the model bundle a debug server serves (URDF plus the meshes it references)."""

import hashlib
import os
import re
from pathlib import Path

from .client import Client

_ID = re.compile(r"^[0-9a-f]{8,64}$")


def cache_root() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "kstone" / "models"


def _target(root: Path, name: str) -> Path:
    path = (root / name).resolve()
    if not name or Path(name).is_absolute() or root.resolve() not in path.parents:
        raise ValueError(f"asset name '{name}' escapes the cache")
    return path


def _valid(path: Path, meta: dict) -> bool:
    try:
        data = path.read_bytes()
    except OSError:
        return False
    return len(data) == meta.get("size") and hashlib.sha256(data).hexdigest() == meta.get("sha256")


def materialize(client: Client, assets: dict, root: Path | None = None) -> Path:
    """Fetches missing or stale files and returns the cached URDF; raises ValueError/ConnectionError/OSError."""
    bundle_id = str(assets.get("id", ""))
    if not _ID.match(bundle_id):
        raise ValueError(f"bad bundle id '{bundle_id}'")
    directory = (root or cache_root()) / bundle_id
    files = assets.get("files") or {}
    model = _target(directory, str(assets.get("model", "")))
    if model.relative_to(directory.resolve()).as_posix() not in files:
        raise ValueError("bundle does not list its model")
    marker = directory / ".complete"
    if marker.is_file() and all(_valid(_target(directory, n), m) for n, m in files.items()):
        return model
    marker.unlink(missing_ok=True)
    for name, meta in files.items():
        path = _target(directory, name)
        if _valid(path, meta):
            continue
        data = client.fetch(name)
        if len(data) != meta.get("size") or hashlib.sha256(data).hexdigest() != meta.get("sha256"):
            raise ValueError(f"{name}: downloaded content does not match the manifest")
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")
        part.write_bytes(data)
        part.replace(path)
    marker.write_text(bundle_id)
    return model
