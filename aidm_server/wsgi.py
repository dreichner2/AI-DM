from __future__ import annotations

import os

from aidm_server.env_loader import load_runtime_env


load_runtime_env()

from aidm_server.config import load_config, validate_production_startup_config  # noqa: E402
from aidm_server.main import build_runtime  # noqa: E402


if os.getenv('AIDM_ENV', 'development').strip().lower() == 'production':
    validate_production_startup_config(load_config())
app, socketio = build_runtime()
