#!/usr/bin/env python3
"""The one toolchain compatibility rule for local development and release tooling.

Usage:
    python scripts/check_toolchain.py python|node|npm VERSION
    python scripts/check_toolchain.py TOOL [TOOL ...]

The first form checks VERSION. The second checks what is installed: the interpreter
running this script for ``python``, and ``node --version`` / ``npm --version`` for the
others. Each check prints one line (stdout when compatible, stderr otherwise) and the
exit code is 0 only when every check passes.

Keep this file stdlib-only and free of recent syntax: the Makefile and launch scripts run
it with the bootstrap interpreter before the virtualenv exists. CI keeps the exact
versions in .python-version, .nvmrc and its workflows.
"""
from __future__ import annotations

import re
import subprocess
import sys


REQUIREMENTS = {
    'python': 'Python 3.14 (3.14.6 or newer)',
    'node': 'Node.js 24 (24.18.0 or newer)',
    'npm': 'npm 12',
}
DISPLAY_NAMES = {'python': 'Python', 'node': 'Node.js', 'npm': 'npm'}
USAGE = 'usage: check_toolchain.py python|node|npm VERSION | check_toolchain.py TOOL [TOOL ...]'
_VERSION_PATTERN = re.compile(r'(\d+)\.(\d+)\.(\d+)(?:[-+].*)?')


def parse_version(version: str) -> tuple[int, int, int] | None:
    match = _VERSION_PATTERN.fullmatch(version.strip())
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def is_compatible(tool: str, version: str) -> bool:
    if tool not in REQUIREMENTS:
        raise ValueError(f'Unknown tool {tool!r}; expected one of: {", ".join(REQUIREMENTS)}.')
    parsed = parse_version(version)
    if parsed is None:
        return False
    major, minor, patch = parsed
    if tool == 'python':
        return (major, minor) == (3, 14) and patch >= 6
    if tool == 'node':
        return major == 24 and (minor, patch) >= (18, 0)
    return major == 12


def check(tool: str, version: str) -> tuple[bool, str]:
    """Return whether ``version`` satisfies the rule for ``tool`` and the one-line message."""
    if is_compatible(tool, version):
        return True, f'{DISPLAY_NAMES[tool]} {version} satisfies {REQUIREMENTS[tool]}.'
    return False, f'{REQUIREMENTS[tool]} is required; found {version}.'


def installed_version(tool: str) -> str | None:
    """Version this process would use for ``tool``, or None when it cannot be run."""
    if tool == 'python':
        return '.'.join(str(part) for part in sys.version_info[:3])
    try:
        completed = subprocess.run([tool, '--version'], capture_output=True, text=True, check=False)
    except OSError:
        return None
    output = completed.stdout.strip()
    if completed.returncode != 0 or not output:
        return None
    return output[1:] if output.startswith('v') else output


def check_installed(tool: str) -> tuple[bool, str]:
    version = installed_version(tool)
    if version is None:
        return False, f'{REQUIREMENTS[tool]} is required; could not run {tool} --version.'
    return check(tool, version)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) == 2 and args[0] in REQUIREMENTS and args[1] not in REQUIREMENTS:
        results = [check(args[0], args[1])]
    elif args and all(arg in REQUIREMENTS for arg in args):
        results = [check_installed(tool) for tool in args]
    else:
        print(USAGE, file=sys.stderr)
        return 2
    for ok, message in results:
        print(message, file=sys.stdout if ok else sys.stderr)
    return 0 if all(ok for ok, _ in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
