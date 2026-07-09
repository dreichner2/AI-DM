"""Application configuration helpers for AI-DM."""

from __future__ import annotations

import os
import pathlib
import secrets
from dataclasses import dataclass
from typing import Dict, List, Mapping

from sqlalchemy.engine import make_url

from aidm_server.provider_registry import SUPPORTED_LLM_PROVIDERS, normalize_provider_model_id, provider_default_model
from aidm_server.rate_limiter import (
    RATE_LIMIT_STORE_DATABASE,
    RATE_LIMIT_STORE_MEMORY,
    SUPPORTED_RATE_LIMIT_STORES,
)

TURN_COORDINATOR_STORE_MEMORY = 'memory'
TURN_COORDINATOR_STORE_DATABASE = 'database'
SUPPORTED_TURN_COORDINATOR_STORES = {TURN_COORDINATOR_STORE_MEMORY, TURN_COORDINATOR_STORE_DATABASE}

SOCKETIO_WORKER_MODEL_SINGLE = 'single'
SOCKETIO_WORKER_MODEL_STICKY = 'sticky'
SOCKETIO_WORKER_MODEL_MESSAGE_QUEUE = 'message_queue'
SUPPORTED_SOCKETIO_WORKER_MODELS = {
    SOCKETIO_WORKER_MODEL_SINGLE,
    SOCKETIO_WORKER_MODEL_STICKY,
    SOCKETIO_WORKER_MODEL_MESSAGE_QUEUE,
}

DEFAULT_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data: https://fonts.gstatic.com; "
    "connect-src 'self' ws: wss:; "
    "media-src 'self' data: blob:; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "frame-ancestors 'none'; "
    "form-action 'self'"
)


def _to_bool(value: str | bool | None, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


def _to_int(value: str | int | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_list(value: str | None, default: List[str]) -> List[str]:
    if value is None:
        return list(default)
    items = [item.strip() for item in value.split(',') if item.strip()]
    return items if items else list(default)


def _to_token_workspace_map(value: str | None) -> Dict[str, str]:
    """Parse workspace=token entries into a token -> workspace map."""
    if value is None:
        return {}
    mapping: Dict[str, str] = {}
    for item in value.split(','):
        raw_item = item.strip()
        if not raw_item or '=' not in raw_item:
            continue
        workspace_id, token = raw_item.split('=', 1)
        workspace_id = workspace_id.strip()
        token = token.strip()
        if workspace_id and token:
            mapping[token] = workspace_id
    return mapping


@dataclass(frozen=True)
class AppConfig:
    env: str
    debug: bool
    secret_key: str
    cors_allowlist: List[str]
    cors_allow_private_network: bool
    socketio_cors_allowlist: List[str]
    socketio_async_mode: str
    socketio_worker_model: str
    socketio_worker_model_explicit: bool
    socketio_message_queue: str | None
    database_uri: str
    auto_create_schema: bool
    serve_frontend: bool
    frontend_dist_dir: str
    max_request_bytes: int
    security_headers_enabled: bool
    content_security_policy: str
    admin_enabled: bool
    admin_passcode: str | None

    auth_required: bool
    api_auth_tokens: List[str]
    api_auth_token_workspaces: Dict[str, str]
    account_cookie_auth_enabled: bool
    account_cookie_name: str
    account_cookie_secure: bool
    account_cookie_samesite: str
    account_token_response_enabled: bool

    llm_provider: str
    llm_model: str
    llm_fallback_models: List[str]
    google_genai_api_key: str | None
    deepgram_api_key: str | None
    deepgram_tts_model: str

    rules_engine_enabled: bool
    segment_evaluator_enabled: bool

    telemetry_enabled: bool
    telemetry_endpoint: str
    telemetry_api_key: str | None
    observability_provider: str
    alert_owner: str
    telemetry_timeout_seconds: int
    telemetry_max_queue_size: int

    rate_limit_window_seconds: int
    rate_limit_max_api_requests: int
    rate_limit_max_socket_messages: int
    rate_limit_store: str
    trusted_proxy_count: int
    turn_coordinator_store: str
    turn_coordinator_lock_ttl_seconds: int
    turn_coordinator_poll_interval_ms: int


DEFAULT_LOCAL_DATA_DIR = pathlib.Path.home() / '.aidm'


def default_sqlite_uri() -> str:
    local_data_dir = pathlib.Path(os.getenv('AIDM_LOCAL_DATA_DIR', str(DEFAULT_LOCAL_DATA_DIR))).expanduser()
    return f"sqlite:///{local_data_dir / 'dnd_ai_dm.db'}"


def _resolve_secret_key(env: str, configured_value: str | None) -> str:
    secret_key = (configured_value or '').strip()
    if secret_key:
        return secret_key
    if env == 'production':
        raise ValueError('FLASK_SECRET_KEY is required when AIDM_ENV=production.')
    return secrets.token_hex(32)


def validate_production_startup_config(
    config: AppConfig,
    *,
    environ: Mapping[str, str] | None = None,
) -> None:
    """Reject unsafe production settings before a WSGI worker starts serving."""
    if config.env != 'production':
        return

    runtime_env = environ if environ is not None else os.environ
    errors: list[str] = []

    database_uri = str(runtime_env.get('AIDM_DATABASE_URI') or '').strip()
    if not database_uri:
        errors.append('AIDM_DATABASE_URI must be explicitly configured')
    else:
        try:
            database_driver = make_url(database_uri).drivername
        except Exception:
            errors.append('AIDM_DATABASE_URI must be a valid SQLAlchemy database URL')
        else:
            if database_driver != 'postgresql+psycopg':
                errors.append('AIDM_DATABASE_URI must use postgresql+psycopg in production')
    if config.debug:
        errors.append('AIDM_DEBUG must be false')
    if config.auto_create_schema:
        errors.append('AIDM_AUTO_CREATE_SCHEMA must be false')
    if config.admin_enabled:
        errors.append('AIDM_ADMIN_ENABLED must be false in production')
    if not config.auth_required:
        errors.append('AIDM_AUTH_REQUIRED must be true')
    if not config.api_auth_tokens and not config.api_auth_token_workspaces:
        errors.append('at least one API auth token must be configured')
    if config.rate_limit_store != RATE_LIMIT_STORE_DATABASE:
        errors.append('AIDM_RATE_LIMIT_STORE must be database')
    if config.turn_coordinator_store != TURN_COORDINATOR_STORE_DATABASE:
        errors.append('AIDM_TURN_COORDINATOR_STORE must be database')
    if not config.socketio_worker_model_explicit:
        errors.append('AIDM_SOCKETIO_WORKER_MODEL must be explicitly configured')
    if config.socketio_worker_model != SOCKETIO_WORKER_MODEL_SINGLE:
        errors.append('production currently supports only AIDM_SOCKETIO_WORKER_MODEL=single')
    if config.socketio_async_mode != 'threading':
        errors.append('AIDM_SOCKETIO_ASYNC_MODE must be threading')
    web_concurrency_raw = str(runtime_env.get('WEB_CONCURRENCY') or '1').strip()
    web_concurrency = (
        int(web_concurrency_raw)
        if web_concurrency_raw.isascii()
        and web_concurrency_raw.isdigit()
        and not web_concurrency_raw.startswith('0')
        else 0
    )
    if web_concurrency < 1:
        errors.append('WEB_CONCURRENCY must be a positive integer')
    elif config.socketio_worker_model == SOCKETIO_WORKER_MODEL_SINGLE and web_concurrency != 1:
        errors.append('AIDM_SOCKETIO_WORKER_MODEL=single requires WEB_CONCURRENCY=1')
    gunicorn_threads_raw = str(runtime_env.get('AIDM_GUNICORN_THREADS') or '100').strip()
    gunicorn_threads = (
        int(gunicorn_threads_raw)
        if gunicorn_threads_raw.isascii()
        and gunicorn_threads_raw.isdigit()
        and not gunicorn_threads_raw.startswith('0')
        else 0
    )
    if gunicorn_threads < 16:
        errors.append('AIDM_GUNICORN_THREADS must be an integer >= 16')
    if '*' in config.cors_allowlist or '*' in config.socketio_cors_allowlist:
        errors.append('wildcard REST or Socket.IO CORS origins are not allowed')
    if not config.security_headers_enabled or not config.content_security_policy:
        errors.append('security headers and a content security policy must be enabled')
    if not config.observability_provider or not config.alert_owner:
        errors.append('AIDM_OBSERVABILITY_PROVIDER and AIDM_ALERT_OWNER must be configured')
    if config.account_cookie_auth_enabled and not config.account_cookie_secure:
        errors.append('AIDM_ACCOUNT_COOKIE_SECURE must be true when account cookie auth is enabled')

    if errors:
        raise ValueError(f"Unsafe production startup configuration: {'; '.join(errors)}.")


def load_config() -> AppConfig:
    env = os.getenv('AIDM_ENV', 'development').strip().lower()
    debug = _to_bool(os.getenv('AIDM_DEBUG'), default=(env != 'production'))
    llm_provider = os.getenv('AIDM_LLM_PROVIDER', 'gemini').strip().lower()
    if llm_provider not in SUPPORTED_LLM_PROVIDERS:
        raise ValueError(
            'Unsupported AIDM_LLM_PROVIDER '
            f'"{llm_provider}". Expected one of: {", ".join(sorted(SUPPORTED_LLM_PROVIDERS))}.'
        )
    default_llm_model = provider_default_model(llm_provider)

    default_cors = ['*'] if debug else []
    cors_allowlist = _to_list(os.getenv('AIDM_CORS_ALLOWLIST'), default_cors)
    cors_allow_private_network = _to_bool(
        os.getenv('AIDM_CORS_ALLOW_PRIVATE_NETWORK'),
        default=(env != 'production'),
    )
    socketio_cors_allowlist = _to_list(os.getenv('AIDM_SOCKET_CORS_ALLOWLIST'), cors_allowlist)
    socketio_worker_model_explicit = 'AIDM_SOCKETIO_WORKER_MODEL' in os.environ
    socketio_worker_model = (
        os.getenv('AIDM_SOCKETIO_WORKER_MODEL', SOCKETIO_WORKER_MODEL_SINGLE).strip().lower().replace('-', '_')
    )
    if socketio_worker_model not in SUPPORTED_SOCKETIO_WORKER_MODELS:
        raise ValueError(
            'Unsupported AIDM_SOCKETIO_WORKER_MODEL '
            f'"{socketio_worker_model}". Expected one of: {", ".join(sorted(SUPPORTED_SOCKETIO_WORKER_MODELS))}.'
        )
    llm_fallback_models = _to_list(os.getenv('AIDM_LLM_FALLBACK_MODELS'), [])
    rate_limit_store = os.getenv('AIDM_RATE_LIMIT_STORE', RATE_LIMIT_STORE_MEMORY).strip().lower()
    if rate_limit_store not in SUPPORTED_RATE_LIMIT_STORES:
        raise ValueError(
            'Unsupported AIDM_RATE_LIMIT_STORE '
            f'"{rate_limit_store}". Expected one of: {", ".join(sorted(SUPPORTED_RATE_LIMIT_STORES))}.'
        )
    turn_coordinator_store = os.getenv(
        'AIDM_TURN_COORDINATOR_STORE',
        TURN_COORDINATOR_STORE_MEMORY,
    ).strip().lower()
    if turn_coordinator_store not in SUPPORTED_TURN_COORDINATOR_STORES:
        raise ValueError(
            'Unsupported AIDM_TURN_COORDINATOR_STORE '
            f'"{turn_coordinator_store}". Expected one of: {", ".join(sorted(SUPPORTED_TURN_COORDINATOR_STORES))}.'
        )
    account_cookie_samesite = (os.getenv('AIDM_ACCOUNT_COOKIE_SAMESITE') or 'Lax').strip().capitalize()
    if account_cookie_samesite not in {'Lax', 'Strict', 'None'}:
        raise ValueError('Unsupported AIDM_ACCOUNT_COOKIE_SAMESITE. Expected one of: Lax, Strict, None.')

    return AppConfig(
        env=env,
        debug=debug,
        secret_key=_resolve_secret_key(env, os.getenv('FLASK_SECRET_KEY')),
        cors_allowlist=cors_allowlist,
        cors_allow_private_network=cors_allow_private_network,
        socketio_cors_allowlist=socketio_cors_allowlist,
        socketio_async_mode=os.getenv('AIDM_SOCKETIO_ASYNC_MODE', 'threading').strip().lower(),
        socketio_worker_model=socketio_worker_model,
        socketio_worker_model_explicit=socketio_worker_model_explicit,
        socketio_message_queue=(os.getenv('AIDM_SOCKETIO_MESSAGE_QUEUE') or '').strip() or None,
        database_uri=os.getenv('AIDM_DATABASE_URI') or default_sqlite_uri(),
        auto_create_schema=_to_bool(os.getenv('AIDM_AUTO_CREATE_SCHEMA'), default=(env != 'production')),
        serve_frontend=_to_bool(os.getenv('AIDM_SERVE_FRONTEND'), default=False),
        frontend_dist_dir=(os.getenv('AIDM_FRONTEND_DIST_DIR') or '').strip(),
        max_request_bytes=_to_int(os.getenv('AIDM_MAX_REQUEST_BYTES'), default=1_048_576),
        security_headers_enabled=_to_bool(os.getenv('AIDM_SECURITY_HEADERS_ENABLED'), default=True),
        content_security_policy=(os.getenv('AIDM_CONTENT_SECURITY_POLICY') or DEFAULT_CONTENT_SECURITY_POLICY).strip(),
        admin_enabled=_to_bool(
            os.getenv('AIDM_ADMIN_ENABLED'),
            default=env in {'development', 'local'},
        ),
        admin_passcode=(os.getenv('AIDM_ADMIN_PASSCODE') or '').strip() or None,
        auth_required=_to_bool(os.getenv('AIDM_AUTH_REQUIRED'), default=False),
        api_auth_tokens=_to_list(os.getenv('AIDM_API_AUTH_TOKENS'), []),
        api_auth_token_workspaces=_to_token_workspace_map(os.getenv('AIDM_API_AUTH_TOKEN_WORKSPACES')),
        account_cookie_auth_enabled=_to_bool(os.getenv('AIDM_ACCOUNT_COOKIE_AUTH_ENABLED'), default=False),
        account_cookie_name=(os.getenv('AIDM_ACCOUNT_COOKIE_NAME') or 'aidm_account_session').strip()
        or 'aidm_account_session',
        account_cookie_secure=_to_bool(os.getenv('AIDM_ACCOUNT_COOKIE_SECURE'), default=(env == 'production')),
        account_cookie_samesite=account_cookie_samesite,
        account_token_response_enabled=_to_bool(
            os.getenv('AIDM_ACCOUNT_TOKEN_RESPONSE_ENABLED'),
            default=True,
        ),
        llm_provider=llm_provider,
        llm_model=normalize_provider_model_id(llm_provider, os.getenv('AIDM_LLM_MODEL', default_llm_model)),
        llm_fallback_models=llm_fallback_models,
        google_genai_api_key=os.getenv('GOOGLE_GENAI_API_KEY'),
        deepgram_api_key=os.getenv('AIDM_DEEPGRAM_API_KEY') or os.getenv('DEEPGRAM_API_KEY'),
        deepgram_tts_model=os.getenv('AIDM_DEEPGRAM_TTS_MODEL', 'aura-2-draco-en'),
        rules_engine_enabled=_to_bool(os.getenv('AIDM_RULES_ENGINE_ENABLED'), default=True),
        segment_evaluator_enabled=_to_bool(os.getenv('AIDM_SEGMENT_EVALUATOR_ENABLED'), default=True),
        telemetry_enabled=_to_bool(os.getenv('AIDM_TELEMETRY_ENABLED'), default=False),
        telemetry_endpoint=os.getenv('AIDM_TELEMETRY_ENDPOINT', ''),
        telemetry_api_key=os.getenv('AIDM_TELEMETRY_API_KEY'),
        observability_provider=(os.getenv('AIDM_OBSERVABILITY_PROVIDER') or '').strip(),
        alert_owner=(os.getenv('AIDM_ALERT_OWNER') or '').strip(),
        telemetry_timeout_seconds=_to_int(os.getenv('AIDM_TELEMETRY_TIMEOUT_SECONDS'), default=2),
        telemetry_max_queue_size=_to_int(os.getenv('AIDM_TELEMETRY_MAX_QUEUE_SIZE'), default=1000),
        rate_limit_window_seconds=_to_int(os.getenv('AIDM_RATE_LIMIT_WINDOW_SECONDS'), default=30),
        rate_limit_max_api_requests=_to_int(os.getenv('AIDM_RATE_LIMIT_MAX_API_REQUESTS'), default=120),
        rate_limit_max_socket_messages=_to_int(os.getenv('AIDM_RATE_LIMIT_MAX_SOCKET_MESSAGES'), default=40),
        rate_limit_store=rate_limit_store,
        trusted_proxy_count=max(0, _to_int(os.getenv('AIDM_TRUSTED_PROXY_COUNT'), default=0)),
        turn_coordinator_store=turn_coordinator_store,
        turn_coordinator_lock_ttl_seconds=max(
            30,
            _to_int(os.getenv('AIDM_TURN_COORDINATOR_LOCK_TTL_SECONDS'), default=900),
        ),
        turn_coordinator_poll_interval_ms=max(
            10,
            _to_int(os.getenv('AIDM_TURN_COORDINATOR_POLL_INTERVAL_MS'), default=50),
        ),
    )
