from __future__ import annotations

import os

from aidm_server.env_loader import load_runtime_env


load_runtime_env()

if os.getenv('AIDM_SOCKETIO_ASYNC_MODE', '').strip().lower() == 'eventlet':
    import eventlet

    eventlet.monkey_patch()

from aidm_server.config import load_config, validate_production_startup_config
from aidm_server.main import build_runtime


if os.getenv('AIDM_ENV', 'development').strip().lower() == 'production':
    validate_production_startup_config(load_config())
app, socketio = build_runtime()
