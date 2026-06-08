#!/usr/bin/env python3
"""Create or update a friend/test auth token workspace in .env.local."""

from __future__ import annotations

import argparse
import os
import re
import secrets
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = REPO_ROOT / ".env.local"
WORKSPACE_ID_RE = re.compile(r"[^A-Za-z0-9_-]+")


def normalize_workspace_id(value: str) -> str:
    workspace_id = WORKSPACE_ID_RE.sub("_", value.strip()).strip("_")
    if not workspace_id:
        raise ValueError("Workspace name cannot be empty.")
    return workspace_id[:80]


def validate_token(value: str) -> str:
    token = value.strip()
    if not token:
        raise ValueError("Token cannot be empty.")
    if any(character in token for character in ",\n\r\t "):
        raise ValueError("Token cannot contain spaces or commas.")
    return token


def parse_env(lines: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].strip()
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def parse_workspace_mapping(value: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in value.split(","):
        raw_item = item.strip()
        if not raw_item or "=" not in raw_item:
            continue
        workspace_id, token = raw_item.split("=", 1)
        workspace_id = workspace_id.strip()
        token = token.strip()
        if workspace_id and token:
            mapping[normalize_workspace_id(workspace_id)] = token
    return mapping


def serialize_workspace_mapping(mapping: dict[str, str]) -> str:
    return ",".join(f"{workspace_id}={token}" for workspace_id, token in sorted(mapping.items()))


def upsert_env_value(lines: list[str], key: str, value: str) -> list[str]:
    assignment = f"{key}={value}\n"
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if not (stripped.startswith(f"{key}=") or stripped.startswith(f"export {key}=")):
            continue
        next_lines = list(lines)
        next_lines[index] = assignment
        return next_lines

    next_lines = list(lines)
    if next_lines and not next_lines[-1].endswith("\n"):
        next_lines[-1] = f"{next_lines[-1]}\n"
    next_lines.append(assignment)
    return next_lines


def prompt_for_workspace() -> str:
    while True:
        value = input("Friend/workspace name: ").strip()
        try:
            return normalize_workspace_id(value)
        except ValueError as exc:
            print(exc)


def prompt_for_token() -> tuple[str, bool]:
    while True:
        value = input("Custom token, or press Enter to generate one: ").strip()
        if not value:
            return secrets.token_urlsafe(32), True
        try:
            return validate_token(value), False
        except ValueError as exc:
            print(exc)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add a friend/test bearer token mapped to its own AIDM workspace.",
    )
    parser.add_argument(
        "workspace",
        nargs="?",
        help="Workspace name, for example aidan_test or bob_test. If omitted, you will be prompted.",
    )
    parser.add_argument(
        "token",
        nargs="?",
        help="Optional token. If omitted, a strong random token is generated.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=DEFAULT_ENV_FILE,
        help="Env file to update. Defaults to repo .env.local.",
    )
    args = parser.parse_args()

    workspace_id = normalize_workspace_id(args.workspace) if args.workspace else prompt_for_workspace()
    if args.token:
        token = validate_token(args.token)
        generated = False
    elif args.workspace:
        token = secrets.token_urlsafe(32)
        generated = True
    else:
        token, generated = prompt_for_token()
    env_file = args.env_file.expanduser().resolve()

    lines = env_file.read_text(encoding="utf-8").splitlines(keepends=True) if env_file.exists() else []
    env_values = parse_env(lines)
    mapping = parse_workspace_mapping(env_values.get("AIDM_API_AUTH_TOKEN_WORKSPACES", ""))
    replacing = workspace_id in mapping
    mapping[workspace_id] = token

    lines = upsert_env_value(lines, "AIDM_AUTH_REQUIRED", "true")
    lines = upsert_env_value(
        lines,
        "AIDM_API_AUTH_TOKEN_WORKSPACES",
        serialize_workspace_mapping(mapping),
    )

    env_file.write_text("".join(lines), encoding="utf-8")
    os.chmod(env_file, 0o600)

    action = "Updated" if replacing else "Added"
    print(f"{action} workspace: {workspace_id}")
    print(f"{'Generated token' if generated else 'Token'}: {token}")
    print(f"Env file: {env_file}")
    print("Restart the backend for the new token to take effect.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
