from __future__ import annotations

import argparse
import os
import pathlib
import sys
from collections.abc import Sequence


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aidm_server.env_loader import load_runtime_env  # noqa: E402


def _supports_generate_content(model) -> bool:
    actions = getattr(model, 'supported_actions', None) or []
    normalized = {str(action).replace('_', '').replace('-', '').lower() for action in actions}
    return 'generatecontent' in normalized


def _list_generate_content_models(api_key: str) -> int:
    try:
        from google import genai
    except Exception as exc:
        print(f'import_failed={str(exc)}')
        return 1

    try:
        client = genai.Client(api_key=api_key)
        for model in client.models.list():
            if _supports_generate_content(model):
                print(getattr(model, 'name', ''))
        return 0
    except Exception as exc:
        print(f'list_failed={str(exc)}')
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='List Gemini models that support generateContent.',
    )
    parser.parse_args(argv)

    load_runtime_env(REPO_ROOT)
    api_key = (os.getenv('GOOGLE_GENAI_API_KEY') or '').strip()
    if not api_key:
        print('missing GOOGLE_GENAI_API_KEY')
        return 1
    return _list_generate_content_models(api_key)


if __name__ == '__main__':
    raise SystemExit(main())
