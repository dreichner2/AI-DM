from __future__ import annotations

import os
import shlex
import subprocess
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER_SCRIPT = REPO_ROOT / 'scripts' / 'launch_desktop_app.sh'


def _launcher_env(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env['AIDM_LAUNCHER_LOG_DIR'] = str(tmp_path)
    env['AIDM_LAUNCHER_SOURCE_ONLY'] = '1'
    env['AIDM_LAUNCHER_LOCK_WAIT_ATTEMPTS'] = '2'
    env['AIDM_LAUNCHER_LOCK_WAIT_SECONDS'] = '0.05'
    env['AIDM_LAUNCHER_SUPPRESS_UI'] = '1'
    return env


def _wait_for(path: Path, *, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.05)
    raise AssertionError(f'Timed out waiting for {path}')


def _dead_pid() -> int:
    pid = 999999
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return pid
        except PermissionError:
            pid += 1
        else:
            pid += 1


def test_launcher_lock_does_not_remove_active_owner(tmp_path):
    env = _launcher_env(tmp_path)
    lock_dir = tmp_path / 'launcher.lock'
    owner_file = lock_dir / 'owner'
    release_file = tmp_path / 'release-first'
    first_pid_file = tmp_path / 'first-pid'
    second_marker = tmp_path / 'second-acquired'
    launcher = shlex.quote(str(LAUNCHER_SCRIPT))

    first = subprocess.Popen(
        [
            'bash',
            '-c',
            (
                f'source {launcher}\n'
                'acquire_launcher_lock\n'
                f'printf "%s" "$$" > {shlex.quote(str(first_pid_file))}\n'
                f'while [ ! -f {shlex.quote(str(release_file))} ]; do sleep 0.05; done\n'
            ),
        ],
        env=env,
    )
    try:
        _wait_for(owner_file)
        _wait_for(first_pid_file)

        second = subprocess.run(
            [
                'bash',
                '-c',
                (
                    f'source {launcher}\n'
                    'acquire_launcher_lock\n'
                    f'printf acquired > {shlex.quote(str(second_marker))}\n'
                ),
            ],
            env=env,
            text=True,
            capture_output=True,
            timeout=8,
            check=False,
        )

        assert second.returncode == 0
        assert not second_marker.exists()
        assert lock_dir.is_dir()
        assert f'pid={first_pid_file.read_text()}' in owner_file.read_text()
    finally:
        release_file.touch()
        first.wait(timeout=3)

    assert not lock_dir.exists()


def test_launcher_lock_takes_over_dead_owner(tmp_path):
    env = _launcher_env(tmp_path)
    lock_dir = tmp_path / 'launcher.lock'
    owner_file = lock_dir / 'owner'
    lock_dir.mkdir()
    owner_file.write_text(f'pid={_dead_pid()}\ntoken=old\ncreated_at=1\n', encoding='utf-8')
    launcher = shlex.quote(str(LAUNCHER_SCRIPT))

    result = subprocess.run(
        [
            'bash',
            '-c',
            (
                f'source {launcher}\n'
                'acquire_launcher_lock\n'
                'grep -q "pid=$$" "$LOCK_OWNER_FILE"\n'
                'grep -q "token=$LOCK_TOKEN" "$LOCK_OWNER_FILE"\n'
            ),
        ],
        env=env,
        text=True,
        capture_output=True,
        timeout=8,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert not lock_dir.exists()


def test_launcher_lock_does_not_remove_ownerless_lock(tmp_path):
    env = _launcher_env(tmp_path)
    lock_dir = tmp_path / 'launcher.lock'
    lock_dir.mkdir()
    launcher = shlex.quote(str(LAUNCHER_SCRIPT))

    result = subprocess.run(
        [
            'bash',
            '-c',
            (
                f'source {launcher}\n'
                'acquire_launcher_lock\n'
            ),
        ],
        env=env,
        text=True,
        capture_output=True,
        timeout=8,
        check=False,
    )

    assert result.returncode == 1
    assert lock_dir.exists()


def test_launcher_lock_release_is_token_checked(tmp_path):
    env = _launcher_env(tmp_path)
    launcher = shlex.quote(str(LAUNCHER_SCRIPT))

    result = subprocess.run(
        [
            'bash',
            '-c',
            (
                f'source {launcher}\n'
                'mkdir "$LOCK_DIR"\n'
                'printf "pid=%s\\ntoken=new\\ncreated_at=1\\n" "$$" > "$LOCK_OWNER_FILE"\n'
                'LOCK_TOKEN=old\n'
                'release_launcher_lock\n'
                'test -d "$LOCK_DIR"\n'
                'grep -q "token=new" "$LOCK_OWNER_FILE"\n'
            ),
        ],
        env=env,
        text=True,
        capture_output=True,
        timeout=3,
        check=False,
    )

    assert result.returncode == 0, result.stderr
