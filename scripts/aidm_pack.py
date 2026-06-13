#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aidm_server.main import create_app  # noqa: E402
from aidm_server.services.campaign_pack_linter import (  # noqa: E402
    lint_campaign_pack_file,
    load_campaign_pack_file,
)
from aidm_server.services.campaign_pack import import_campaign_pack  # noqa: E402


def _with_app_context(callback):
    app = create_app()
    with app.app_context():
        return callback()


def _dump(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _print_issues(result: dict[str, Any]) -> None:
    issues = result.get('issues') if isinstance(result.get('issues'), list) else []
    if not issues:
        print('No campaign pack lint issues found.')
        return
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        severity = str(issue.get('severity') or 'warning').upper()
        code = str(issue.get('code') or 'lint_issue')
        path = str(issue.get('path') or 'campaign pack')
        message = str(issue.get('message') or '')
        print(f'{severity} {code} at {path}: {message}')


def _load_result(args) -> dict[str, Any]:
    return _with_app_context(lambda: lint_campaign_pack_file(args.path, workspace_id=args.workspace_id))


def cmd_lint(args) -> int:
    result = _load_result(args)
    if args.json:
        _dump(result)
    else:
        _print_issues(result)
    return 0 if result.get('ok') else 1


def cmd_preview(args) -> int:
    pack = load_campaign_pack_file(args.path)
    result = _with_app_context(lambda: import_campaign_pack(pack, workspace_id=args.workspace_id, dry_run=True).payload)
    _dump(result)
    return 0


def cmd_graph(args) -> int:
    result = _load_result(args)
    _dump(result.get('graph') or {})
    return 0 if result.get('ok') else 1


def cmd_test_checkpoints(args) -> int:
    result = _load_result(args)
    graph = result.get('graph') if isinstance(result.get('graph'), dict) else {}
    nodes = graph.get('nodes') if isinstance(graph.get('nodes'), list) else []
    reachable = graph.get('reachable') if isinstance(graph.get('reachable'), list) else []
    print(f'Checkpoints: {len(nodes)}')
    print(f'Reachable: {len(reachable)}')
    missing = sorted(set(nodes) - set(reachable))
    if missing:
        print('Unreachable: ' + ', '.join(missing))
    for issue in result.get('issues') or []:
        if isinstance(issue, dict) and str(issue.get('code') or '').startswith('checkpoint'):
            print(f"{issue.get('severity', 'warning').upper()} {issue.get('code')}: {issue.get('message')}")
    return 0 if result.get('ok') and (not nodes or reachable) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='aidm pack', description='Campaign pack authoring tools.')
    parser.add_argument('--workspace-id', default='owner', help='Workspace to use for dry-run world references.')
    subparsers = parser.add_subparsers(dest='command', required=True)

    lint_parser = subparsers.add_parser('lint', help='Validate and lint a campaign pack JSON file.')
    lint_parser.add_argument('path', type=Path)
    lint_parser.add_argument('--json', action='store_true', help='Print the full lint payload as JSON.')
    lint_parser.set_defaults(func=cmd_lint)

    preview_parser = subparsers.add_parser('preview', help='Print the import dry-run preview JSON.')
    preview_parser.add_argument('path', type=Path)
    preview_parser.set_defaults(func=cmd_preview)

    graph_parser = subparsers.add_parser('graph', help='Print checkpoint graph nodes, edges, and reachability.')
    graph_parser.add_argument('path', type=Path)
    graph_parser.set_defaults(func=cmd_graph)

    checkpoint_parser = subparsers.add_parser('test-checkpoints', help='Check checkpoint reachability.')
    checkpoint_parser.add_argument('path', type=Path)
    checkpoint_parser.set_defaults(func=cmd_test_checkpoints)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:
        print(f'ERROR campaign_pack_tool_failed: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
