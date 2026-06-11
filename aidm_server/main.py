from __future__ import annotations

import logging
import os
from pathlib import Path
from uuid import uuid4

from flask import Flask, abort, g, request, send_from_directory
from flask_cors import CORS
from flask_socketio import SocketIO
from werkzeug.middleware.proxy_fix import ProxyFix

from aidm_server.auth import (
    DEFAULT_WORKSPACE_ID,
    claim_legacy_players_for_account,
    ensure_account_workspace_membership,
    request_account,
    request_account_token,
    request_workspace_id,
    workspace_role_is_admin,
)
from aidm_server.canon_jobs import start_canon_job_worker
from aidm_server.config import load_config
from aidm_server.database import db, ensure_schema, init_db
from aidm_server.errors import error_response
from aidm_server.logging_context import (
    clear_logging_context,
    configure_logging,
    get_correlation_id,
    set_logging_context,
)
from aidm_server.rate_limiter import FixedWindowRateLimiter, build_rate_limiter
from aidm_server.telemetry import init_telemetry, telemetry_event, telemetry_metric
from aidm_server.blueprints.accounts import accounts_bp
from aidm_server.blueprints.campaigns import campaigns_bp
from aidm_server.blueprints.creatures import creatures_bp
from aidm_server.blueprints.maps import maps_bp
from aidm_server.blueprints.players import players_bp
from aidm_server.blueprints.races import races_bp
from aidm_server.blueprints.runtime_config import runtime_config_bp
from aidm_server.blueprints.segments import segments_bp
from aidm_server.blueprints.sessions import sessions_bp
from aidm_server.blueprints.socketio_events import register_socketio_events
from aidm_server.blueprints.system import system_bp
from aidm_server.blueprints.worlds import worlds_bp


configure_logging()
logger = logging.getLogger(__name__)


FRONTEND_RESERVED_PREFIXES = ('api/', 'socket.io/', 'admin/')


def request_route_key() -> str:
    rule = getattr(request, 'url_rule', None)
    if rule is not None and getattr(rule, 'rule', None):
        return str(rule.rule)
    endpoint = request.endpoint
    return str(endpoint or request.path)


def default_frontend_dist_dir() -> Path:
    return Path(__file__).resolve().parents[1] / 'aidm_frontend' / 'dist'


def frontend_dist_dir(configured_path: str) -> Path:
    if configured_path:
        return Path(configured_path).expanduser().resolve()
    return default_frontend_dist_dir()


def configure_frontend_routes(app: Flask, dist_dir: Path):
    @app.get('/')
    @app.get('/<path:frontend_path>')
    def frontend_app(frontend_path: str = ''):
        normalized_path = frontend_path.lstrip('/')
        if normalized_path.startswith(FRONTEND_RESERVED_PREFIXES):
            abort(404)

        index_path = dist_dir / 'index.html'
        if normalized_path:
            requested_path = (dist_dir / normalized_path).resolve()
            try:
                requested_path.relative_to(dist_dir)
            except ValueError:
                abort(404)
            if requested_path.is_file():
                return send_from_directory(dist_dir, normalized_path)

        if index_path.is_file():
            return send_from_directory(dist_dir, 'index.html')

        return error_response(
            code='frontend_not_built',
            message=(
                'Frontend build not found. Run `npm run build` in aidm_frontend '
                'or start without AIDM_SERVE_FRONTEND=true.'
            ),
            status=503,
            details={'dist_dir': str(dist_dir)},
        )


def create_app() -> Flask:
    config = load_config()

    app = Flask(__name__)
    app.secret_key = config.secret_key
    if config.trusted_proxy_count > 0:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=config.trusted_proxy_count)

    app.config.update(
        DEBUG=config.debug,
        AIDM_ENV=config.env,
        AIDM_AUTH_REQUIRED=config.auth_required,
        AIDM_API_AUTH_TOKENS=config.api_auth_tokens,
        AIDM_API_AUTH_TOKEN_WORKSPACES=config.api_auth_token_workspaces,
        AIDM_LLM_PROVIDER=config.llm_provider,
        AIDM_LLM_MODEL=config.llm_model,
        AIDM_LLM_FALLBACK_MODELS=config.llm_fallback_models,
        GOOGLE_GENAI_API_KEY=config.google_genai_api_key,
        AIDM_DEEPGRAM_API_KEY=config.deepgram_api_key,
        AIDM_DEEPGRAM_TTS_MODEL=config.deepgram_tts_model,
        AIDM_RULES_ENGINE_ENABLED=config.rules_engine_enabled,
        AIDM_SEGMENT_EVALUATOR_ENABLED=config.segment_evaluator_enabled,
        AIDM_RATE_LIMIT_WINDOW_SECONDS=config.rate_limit_window_seconds,
        AIDM_RATE_LIMIT_MAX_API_REQUESTS=config.rate_limit_max_api_requests,
        AIDM_RATE_LIMIT_MAX_SOCKET_MESSAGES=config.rate_limit_max_socket_messages,
        AIDM_RATE_LIMIT_STORE=config.rate_limit_store,
        AIDM_TURN_COORDINATOR_STORE=config.turn_coordinator_store,
        AIDM_TURN_COORDINATOR_LOCK_TTL_SECONDS=config.turn_coordinator_lock_ttl_seconds,
        AIDM_TURN_COORDINATOR_POLL_INTERVAL_MS=config.turn_coordinator_poll_interval_ms,
        AIDM_MAX_REQUEST_BYTES=config.max_request_bytes,
        AIDM_CORS_ALLOWLIST=config.cors_allowlist,
        AIDM_CORS_ALLOW_PRIVATE_NETWORK=config.cors_allow_private_network,
        AIDM_SOCKET_CORS_ALLOWLIST=config.socketio_cors_allowlist,
        AIDM_SOCKETIO_ASYNC_MODE=config.socketio_async_mode,
        AIDM_AUTO_CREATE_SCHEMA=config.auto_create_schema,
        AIDM_SERVE_FRONTEND=config.serve_frontend,
        AIDM_FRONTEND_DIST_DIR=str(frontend_dist_dir(config.frontend_dist_dir)),
        AIDM_ADMIN_ENABLED=config.admin_enabled,
        AIDM_ADMIN_PASSCODE=config.admin_passcode,
        AIDM_TELEMETRY_ENABLED=config.telemetry_enabled,
        AIDM_TELEMETRY_ENDPOINT=config.telemetry_endpoint,
        AIDM_TELEMETRY_API_KEY=config.telemetry_api_key,
        AIDM_TELEMETRY_TIMEOUT_SECONDS=config.telemetry_timeout_seconds,
        AIDM_TELEMETRY_MAX_QUEUE_SIZE=config.telemetry_max_queue_size,
        AIDM_TRUSTED_PROXY_COUNT=config.trusted_proxy_count,
        SQLALCHEMY_DATABASE_URI=config.database_uri,
    )

    app.config['MAX_CONTENT_LENGTH'] = config.max_request_bytes

    cors_origins = config.cors_allowlist or []
    CORS(
        app,
        resources={r'/api/*': {'origins': cors_origins if cors_origins != ['*'] else '*'}},
        allow_private_network=config.cors_allow_private_network,
    )

    init_db(app)
    init_telemetry(app)

    if config.serve_frontend:
        configure_frontend_routes(app, frontend_dist_dir(config.frontend_dist_dir))
    else:
        @app.get('/')
        def root():
            return {
                'service': 'aidm_backend',
                'status': 'ok',
                'frontend': 'React app served separately from aidm_frontend',
            }

    api_limiter = build_rate_limiter(
        limit=config.rate_limit_max_api_requests,
        window_seconds=config.rate_limit_window_seconds,
        store_name=config.rate_limit_store,
    )
    app.extensions['aidm_api_limiter'] = api_limiter

    @app.before_request
    def _apply_api_guards():
        correlation_id = request.headers.get('X-Request-ID') or f"http-{uuid4().hex[:12]}"
        session_id = None
        if isinstance(request.view_args, dict):
            session_id = request.view_args.get('session_id')
        set_logging_context(correlation_id=correlation_id, session_id=session_id)
        g.aidm_correlation_id = correlation_id

        if not request.path.startswith('/api'):
            return None

        route_key = request_route_key()
        telemetry_metric('api.requests_total', 1, tags={'path': route_key, 'method': request.method})

        if request.path == '/api/health':
            return None

        if request.method == 'OPTIONS':
            return None

        if request.path.startswith('/api/accounts'):
            return None

        account_token = request_account_token()
        account = request_account()
        workspace_id = request_workspace_id()
        if app.config.get('AIDM_AUTH_REQUIRED') and not workspace_id:
            telemetry_event(
                'api.unauthorized',
                payload={'path': route_key, 'method': request.method, 'remote_addr': request.remote_addr},
                severity='warning',
            )
            return error_response(
                code='unauthorized',
                message='Missing or invalid workspace token.',
                status=401,
            )
        membership = ensure_account_workspace_membership(account, workspace_id) if account and workspace_id else None
        if membership:
            claim_legacy_players_for_account(account, workspace_id)
            db.session.commit()
        g.aidm_account = account
        g.aidm_account_id = account.account_id if account else None
        g.aidm_auth_token_present = bool(account_token)
        g.aidm_workspace_id = workspace_id or DEFAULT_WORKSPACE_ID
        g.aidm_workspace_role = membership.role if membership else None
        g.aidm_workspace_admin = workspace_role_is_admin(membership.role if membership else None)

        limiter: FixedWindowRateLimiter = app.extensions['aidm_api_limiter']
        client_ip = request.remote_addr or 'unknown'
        key = f'{client_ip}:{route_key}'
        result = limiter.allow(key)
        if not result.allowed:
            telemetry_event(
                'api.rate_limited',
                payload={
                    'path': route_key,
                    'method': request.method,
                    'client_ip': client_ip,
                    'reset_in_seconds': result.reset_in_seconds,
                },
                severity='warning',
            )
            return error_response(
                code='rate_limited',
                message='API request limit exceeded. Retry later.',
                status=429,
                details={'reset_in_seconds': result.reset_in_seconds},
            )

        return None

    @app.after_request
    def _add_response_correlation_id(response):
        response.headers['X-Request-ID'] = getattr(g, 'aidm_correlation_id', get_correlation_id())
        clear_logging_context()
        return response

    @app.teardown_request
    def _clear_context_on_teardown(_exc):
        clear_logging_context()

    app.register_blueprint(campaigns_bp, url_prefix='/api/campaigns')
    app.register_blueprint(accounts_bp, url_prefix='/api/accounts')
    app.register_blueprint(worlds_bp, url_prefix='/api/worlds')
    app.register_blueprint(players_bp, url_prefix='/api/players')
    app.register_blueprint(races_bp, url_prefix='/api')
    app.register_blueprint(creatures_bp, url_prefix='/api')
    app.register_blueprint(sessions_bp, url_prefix='/api/sessions')
    app.register_blueprint(maps_bp, url_prefix='/api/maps')
    app.register_blueprint(segments_bp, url_prefix='/api/segments')
    app.register_blueprint(runtime_config_bp, url_prefix='/api')
    app.register_blueprint(system_bp, url_prefix='/api')

    if config.admin_enabled:
        from aidm_server.blueprints.admin import configure_admin

        configure_admin(app, db)

    return app


def create_socketio(app: Flask) -> SocketIO:
    allowed_origins = app.config.get('AIDM_SOCKET_CORS_ALLOWLIST', ['*'])
    async_mode = app.config.get('AIDM_SOCKETIO_ASYNC_MODE', 'threading')
    cors_allowed_origins = (
        '*'
        if allowed_origins == ['*']
        else None
        if not allowed_origins
        else allowed_origins
    )
    return SocketIO(
        app,
        cors_allowed_origins=cors_allowed_origins,
        async_mode=async_mode,
    )


def build_runtime(*, ensure_schema_created: bool | None = None) -> tuple[Flask, SocketIO]:
    app = create_app()
    if ensure_schema_created is None:
        ensure_schema_created = bool(app.config.get('AIDM_AUTO_CREATE_SCHEMA', True))
    if app.config.get('AIDM_ENV') == 'production' and ensure_schema_created:
        raise RuntimeError(
            'AIDM_AUTO_CREATE_SCHEMA must be false in production. Apply migrations before starting.'
        )
    if ensure_schema_created:
        ensure_schema(app)
    socketio = create_socketio(app)
    register_socketio_events(socketio)
    start_canon_job_worker(app, socketio)
    return app, socketio


if __name__ == '__main__':
    from aidm_server.env_loader import load_runtime_env

    load_runtime_env()
    port = int(os.getenv('PORT', '5000'))
    app, socketio = build_runtime()
    socketio.run(app, debug=app.config.get('DEBUG', False), port=port)
