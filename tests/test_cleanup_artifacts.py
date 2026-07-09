from __future__ import annotations

from pathlib import Path
import subprocess


def test_cleanup_preserves_release_evidence_and_removes_runtime_artifacts(tmp_path: Path):
    repo = tmp_path / 'repo'
    script = repo / 'scripts' / 'cleanup_artifacts.sh'
    script.parent.mkdir(parents=True)
    source_script = Path(__file__).resolve().parents[1] / 'scripts' / 'cleanup_artifacts.sh'
    script.write_text(source_script.read_text(encoding='utf-8'), encoding='utf-8')
    script.chmod(0o755)

    release_evidence = repo / 'tmp' / 'release' / 'rc-evidence.md'
    runtime_artifact = repo / 'tmp' / 'browser-smoke' / 'server.log'
    pytest_cache = repo / '.pytest_cache' / 'state'
    frontend_dist = repo / 'aidm_frontend' / 'dist' / 'index.html'
    for path in (release_evidence, runtime_artifact, pytest_cache, frontend_dist):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('test', encoding='utf-8')

    result = subprocess.run(
        ['bash', str(script)],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert release_evidence.read_text(encoding='utf-8') == 'test'
    assert not runtime_artifact.exists()
    assert not pytest_cache.exists()
    assert not frontend_dist.exists()
