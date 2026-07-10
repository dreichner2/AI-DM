from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_browser_smoke_backends_disable_debugger_and_reloader():
    smoke_scripts = (
        REPO_ROOT / 'aidm_frontend' / 'scripts' / 'browser-smoke.cjs',
        REPO_ROOT / 'aidm_frontend' / 'scripts' / 'visual-smoke.cjs',
    )

    for smoke_script in smoke_scripts:
        source = smoke_script.read_text(encoding='utf-8')
        assert "AIDM_ENV: 'test'" in source
        assert "AIDM_DEBUG: 'false'" in source
