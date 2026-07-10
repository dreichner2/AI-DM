from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values, load_dotenv


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_runtime_env(root_path: str | Path | None = None):
    root = Path(root_path) if root_path is not None else repo_root()
    load_dotenv(root / '.env', override=False)
    process_env = os.getenv('AIDM_ENV', 'development').strip().lower()
    override_env = os.getenv('AIDM_ENV_FILE')
    if override_env:
        override_values = dotenv_values(override_env)
        override_environment = (
            process_env
            if 'AIDM_ENV' not in override_values
            else str(override_values.get('AIDM_ENV') or '').strip().lower()
        )
        if process_env == 'production' and override_environment != 'production':
            raise RuntimeError('AIDM_ENV_FILE cannot override a production process with a non-production environment.')
        load_dotenv(override_env, override=True)
    elif (
        os.getenv('AIDM_SKIP_REPO_ENV_LOCAL') != '1'
        and process_env != 'production'
    ):
        load_dotenv(root / '.env.local', override=True)
