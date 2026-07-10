"""Codex CLI runtime discovery helpers."""

from __future__ import annotations

import os
from pathlib import Path
import shutil


DEFAULT_CODEX_APP_EXECUTABLES: tuple[Path, ...] = (
    Path('/Applications/Codex.app/Contents/Resources/codex'),
    Path.home() / 'Applications/Codex.app/Contents/Resources/codex',
)
DEFAULT_CODEX_NODE_ROOTS: tuple[Path, ...] = (
    Path('/opt/render/project/nodes'),
)


def _executable_file(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def _node_version_key(path: Path) -> tuple[int, ...]:
    version = path.parents[1].name.removeprefix('node-')
    return tuple(int(part) if part.isdigit() else -1 for part in version.split('.'))


def resolve_codex_executable(executable: str | None = None) -> str | None:
    candidate = str(executable or '').strip() or 'codex'
    if os.path.sep in candidate:
        path = Path(candidate).expanduser()
        return str(path) if _executable_file(path) else None

    resolved = shutil.which(candidate)
    if resolved:
        return resolved

    if candidate == 'codex':
        for app_executable in DEFAULT_CODEX_APP_EXECUTABLES:
            if _executable_file(app_executable):
                return str(app_executable)
        configured_node_root = str(os.getenv('AIDM_CODEX_NODE_ROOT') or '').strip()
        node_roots = (
            *((Path(configured_node_root).expanduser(),) if configured_node_root else ()),
            *DEFAULT_CODEX_NODE_ROOTS,
        )
        for node_root in node_roots:
            node_executables = sorted(
                node_root.glob('node-*/bin/codex'),
                key=_node_version_key,
                reverse=True,
            )
            for node_executable in node_executables:
                if _executable_file(node_executable) and _executable_file(node_executable.parent / 'node'):
                    return str(node_executable)

    return None


def codex_executable_configured(executable: str | None = None) -> bool:
    return resolve_codex_executable(executable) is not None
