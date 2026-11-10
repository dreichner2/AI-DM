import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SMOKE_SCRIPTS = (
    REPO_ROOT / 'aidm_frontend' / 'scripts' / 'browser-smoke.cjs',
    REPO_ROOT / 'aidm_frontend' / 'scripts' / 'visual-smoke.cjs',
)
# Loads a smoke script with a fake Playwright whose launch() behaves per scenario, then reports the
# launch calls. "missing" fails like an absent browser download unless the chrome channel is used.
LAUNCH_HARNESS = r"""
const Module = require('node:module')
const scenario = process.env.AIDM_TEST_LAUNCH_SCENARIO
const launches = []
const chromium = {
  async launch(options) {
    launches.push(options ?? null)
    if (scenario === 'missing' && options?.channel !== 'chrome') {
      throw new Error("browserType.launch: Executable doesn't exist at /fake/ms-playwright/chromium")
    }
    if (scenario === 'crash') throw new Error('browserType.launch: Browser closed unexpectedly')
    return { channel: options?.channel ?? 'bundled' }
  },
}
const originalLoad = Module._load
Module._load = function (request, parent, isMain) {
  if (request === '@playwright/test') return { chromium, expect() {} }
  return originalLoad.call(this, request, parent, isMain)
}
const { launchChromium } = require(process.env.AIDM_TEST_SMOKE_SCRIPT)
launchChromium().then(
  (browser) => console.log(JSON.stringify({ browser, launches })),
  (error) => console.log(JSON.stringify({ error: error.message, launches })),
)
"""


def test_browser_smoke_backends_disable_debugger_and_reloader():
    for smoke_script in SMOKE_SCRIPTS:
        source = smoke_script.read_text(encoding='utf-8')
        assert "AIDM_ENV: 'test'" in source
        assert "AIDM_DEBUG: 'false'" in source


def _launch(smoke_script: Path, scenario: str, channel: str = '') -> tuple[dict, list[str]]:
    env = {
        **os.environ,
        'AIDM_TEST_SMOKE_SCRIPT': str(smoke_script),
        'AIDM_TEST_LAUNCH_SCENARIO': scenario,
        'PLAYWRIGHT_CHROMIUM_CHANNEL': channel,
    }
    result = subprocess.run(
        ['node', '-e', LAUNCH_HARNESS],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    *log_lines, report = result.stdout.strip().splitlines()
    return json.loads(report), log_lines


@pytest.mark.skipif(shutil.which('node') is None, reason='requires Node.js')
@pytest.mark.parametrize('smoke_script', SMOKE_SCRIPTS, ids=lambda path: path.name)
def test_smoke_scripts_retry_with_chrome_only_when_bundled_chromium_is_missing(smoke_script):
    report, log_lines = _launch(smoke_script, 'missing')
    assert report == {'browser': {'channel': 'chrome'}, 'launches': [None, {'channel': 'chrome'}]}
    assert len(log_lines) == 1
    assert 'bundled Chromium is not installed; retrying with the installed Google Chrome' in log_lines[0]

    report, log_lines = _launch(smoke_script, 'missing', channel='msedge')
    assert report['launches'] == [{'channel': 'msedge'}]
    assert "Executable doesn't exist" in report['error']
    assert log_lines == []

    report, log_lines = _launch(smoke_script, 'crash')
    assert report == {'error': 'browserType.launch: Browser closed unexpectedly', 'launches': [None]}
    assert log_lines == []

    report, log_lines = _launch(smoke_script, 'ok')
    assert report == {'browser': {'channel': 'bundled'}, 'launches': [None]}
    assert log_lines == []
