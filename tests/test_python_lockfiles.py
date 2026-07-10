from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_hash_locks_include_sqlalchemy_linux_greenlet_dependency():
    runtime_input = (REPO_ROOT / 'requirements.runtime.txt').read_text(encoding='utf-8')
    constraints = (REPO_ROOT / 'requirements.constraints.txt').read_text(encoding='utf-8')

    assert '\ngreenlet\n' in runtime_input
    assert '\ngreenlet==3.5.3\n' in constraints
    for lock_name in ('requirements.runtime.lock.txt', 'requirements-dev.lock.txt'):
        lock = (REPO_ROOT / lock_name).read_text(encoding='utf-8')
        assert '\ngreenlet==3.5.3 \\\n' in lock
