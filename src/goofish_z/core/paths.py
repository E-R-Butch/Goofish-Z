"""Runtime-data paths kept outside the public source checkout."""
from __future__ import annotations

import os
from pathlib import Path

from goofish_z.core.errors import GoofishError


def _source_repository_root() -> Path | None:
    for parent in Path(__file__).resolve().parents:
        if (parent / ".git").exists() and (parent / "pyproject.toml").is_file():
            return parent
    return None


def runtime_data_dir() -> Path:
    """Return the one runtime-data root and reject a public-checkout target."""
    configured = os.environ.get("GOOFISH_Z_DATA")
    path = Path(configured).expanduser() if configured else Path.home() / ".goofish-z"
    resolved = path.resolve()
    repository = _source_repository_root()
    if repository is not None and resolved.is_relative_to(repository.resolve()):
        raise GoofishError(
            "GOOFISH_Z_DATA 不得指向 Goofish-Z 源码仓库内部；请使用仓库外的本地目录"
        )
    return resolved


def runtime_data_path(name: str) -> Path:
    if not name or Path(name).name != name:
        raise ValueError("runtime data filename must be one plain name")
    return runtime_data_dir() / name
