from __future__ import annotations

import argparse
import pathlib
import sys
from collections.abc import Sequence

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aidm_server.env_loader import load_runtime_env  # noqa: E402
from aidm_server.contracts import ProviderRequest  # noqa: E402
from aidm_server.llm import get_provider  # noqa: E402


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='Send one live connectivity-check prompt to the configured LLM provider.',
    )
    parser.parse_args(argv)

    load_runtime_env(REPO_ROOT)
    try:
        provider = get_provider()
        response = provider.generate(
            ProviderRequest(
                prompt='Reply with a short confirmation that the AI provider is online.',
                system_message='You are running a connectivity check.',
            )
        )
        print(f'provider={response.provider}')
        print(f'model={response.model}')
        print(f'text={response.text.strip()}')
        return 0
    except Exception as exc:
        print(f'check_failed={str(exc)}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
