from __future__ import annotations

import pathlib
import sys


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aidm_server.env_loader import load_runtime_env  # noqa: E402

load_runtime_env(REPO_ROOT)

from aidm_server.deploy_bootstrap import main  # noqa: E402


if __name__ == '__main__':
    raise SystemExit(main())
