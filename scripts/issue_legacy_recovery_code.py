#!/usr/bin/env python3
"""Issue a one-time recovery credential for a passwordless legacy account."""

from __future__ import annotations

import argparse
import pathlib
import sys
from collections.abc import Sequence


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            'Rotate a passwordless legacy account to a strong recovery code. '
            'The code is printed once and must be delivered to the verified owner out of band.'
        ),
    )
    parser.add_argument('--username', required=True, help='Legacy account username to recover.')
    parser.add_argument(
        '--confirm-production',
        action='store_true',
        help='Required when the selected runtime environment is production.',
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    from aidm_server.env_loader import load_runtime_env

    load_runtime_env(REPO_ROOT)

    from aidm_server.auth import issue_legacy_recovery_token, normalize_username
    from aidm_server.database import db
    from aidm_server.main import create_app
    from aidm_server.models import Account

    app = create_app()
    if str(app.config.get('AIDM_ENV') or '').strip().lower() == 'production' and not args.confirm_production:
        print(
            'Refusing to issue a production recovery code without --confirm-production.',
            file=sys.stderr,
        )
        return 2

    username = normalize_username(args.username)
    if not username:
        print('A valid --username is required.', file=sys.stderr)
        return 2

    with app.app_context():
        account = Account.query.filter_by(username=username).first()
        try:
            recovery_code = issue_legacy_recovery_token(account)
            db.session.commit()
        except ValueError as exc:
            db.session.rollback()
            print(str(exc), file=sys.stderr)
            return 1
        except Exception:
            db.session.rollback()
            raise

    print('Recovery code (displayed once; deliver only to the verified account owner):')
    print(recovery_code)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
