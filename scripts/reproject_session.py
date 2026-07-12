#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aidm_server.env_loader import load_runtime_env  # noqa: E402


def parse_args():
    parser = argparse.ArgumentParser(description='Rebuild AI-DM legacy projections from turn_events.')
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument('--session-id', type=int, help='Rebuild projections for one session.')
    target.add_argument('--all', action='store_true', help='Rebuild projections for every session.')
    parser.add_argument('--dry-run', action='store_true', help='Roll back after reporting the repair result.')
    parser.add_argument(
        '--create-schema',
        action='store_true',
        help='Create missing local/test schema before repair. Rejected in production.',
    )
    return parser.parse_args()


def main():
    args = parse_args()
    load_runtime_env(REPO_ROOT)

    from aidm_server.database import ensure_schema
    from aidm_server.main import create_app
    from aidm_server.reprojection import ProjectionRepairError, repair_all_session_projections, repair_session_projections

    app = create_app()
    if args.create_schema:
        if str(app.config.get('AIDM_ENV', 'development')).strip().lower() == 'production':
            print(json.dumps({'error': '--create-schema is not allowed in production.'}, indent=2), file=sys.stderr)
            return 1
        ensure_schema(app)

    with app.app_context():
        try:
            if args.all:
                result = repair_all_session_projections(commit=not args.dry_run)
            else:
                result = repair_session_projections(args.session_id, commit=not args.dry_run)
            if args.dry_run:
                from aidm_server.database import db

                db.session.rollback()
            print(json.dumps({'dry_run': args.dry_run, 'result': result}, indent=2))
        except ProjectionRepairError as exc:
            print(json.dumps({'error': str(exc)}, indent=2), file=sys.stderr)
            return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
