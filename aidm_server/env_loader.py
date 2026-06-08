from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_runtime_env(root_path: str | Path | None = None):
    root = Path(root_path) if root_path is not None else repo_root()
    load_dotenv(root / '.env', override=False)
    override_env = os.getenv('AIDM_ENV_FILE')
    if override_env:
        load_dotenv(override_env, override=True)
    elif os.getenv('AIDM_SKIP_REPO_ENV_LOCAL') != '1':
        load_dotenv(root / '.env.local', override=True)
