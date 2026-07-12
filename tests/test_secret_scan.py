from __future__ import annotations

from scripts.scan_secrets import scan_paths


def test_secret_scan_detects_likely_committed_api_key(tmp_path):
    source = tmp_path / 'bad.py'
    source.write_text(
        'DEEPGRAM_API_KEY = "' + '0123456789abcdef0123456789abcdef' + '"\n',
        encoding='utf-8',
    )

    findings = scan_paths([source])

    assert len(findings) == 1
    assert findings[0].path == source
    assert findings[0].kind == 'Deepgram-looking API key'


def test_secret_scan_detects_google_api_key(tmp_path):
    source = tmp_path / 'bad.py'
    source.write_text(
        'GOOGLE_GENAI_API_KEY = "' + 'AIza' + ('A' * 35) + '"\n',
        encoding='utf-8',
    )

    findings = scan_paths([source])

    assert len(findings) == 1
    assert findings[0].path == source
    assert findings[0].kind == 'Google API key'


def test_secret_scan_detects_github_fine_grained_token(tmp_path):
    token = 'github_pat_' + ('A' * 48)
    source = tmp_path / 'bad.py'
    source.write_text(f'GITHUB_TOKEN = "{token}"\n', encoding='utf-8')

    findings = scan_paths([source])

    assert len(findings) == 1
    assert findings[0].path == source
    assert findings[0].kind == 'GitHub fine-grained token'
    assert token not in findings[0].snippet
    assert '<redacted>' in findings[0].snippet


def test_secret_scan_detects_private_key_files(tmp_path):
    source = tmp_path / 'server.pem'
    source.write_text(
        '-----BEGIN ' + 'PRIVATE KEY-----\n'
        'placeholder-key-body\n'
        '-----END ' + 'PRIVATE KEY-----\n',
        encoding='utf-8',
    )

    findings = scan_paths([source])

    assert len(findings) == 1
    assert findings[0].path == source
    assert findings[0].kind == 'Private key block'
    assert 'PRIVATE KEY' not in findings[0].snippet
    assert '<redacted>' in findings[0].snippet


def test_secret_scan_redacts_secret_values_from_findings(tmp_path):
    token = 'sk-' + ('A' * 32)
    source = tmp_path / 'bad.py'
    source.write_text(f'OPENAI_API_KEY = "{token}"\n', encoding='utf-8')

    findings = scan_paths([source])

    assert len(findings) == 1
    assert findings[0].kind == 'OpenAI-style API key'
    assert token not in findings[0].snippet
    assert '<redacted>' in findings[0].snippet
    assert 'OPENAI_API_KEY' in findings[0].snippet


def test_secret_scan_does_not_allowlist_real_secret_from_line_context(tmp_path):
    token = 'sk-' + ('A' * 32)
    source = tmp_path / 'bad.py'
    source.write_text(f'OPENAI_EXAMPLE_API_KEY = "{token}"  # example value\n', encoding='utf-8')

    findings = scan_paths([source])

    assert len(findings) == 1
    assert findings[0].kind == 'OpenAI-style API key'
    assert token not in findings[0].snippet


def test_secret_scan_scans_real_env_files_with_environment_suffixes(tmp_path):
    token = 'sk-' + ('A' * 32)
    source = tmp_path / '.env.production'
    source.write_text(f'OPENAI_API_KEY="{token}"\n', encoding='utf-8')

    findings = scan_paths([source])

    assert len(findings) == 1
    assert findings[0].path == source
    assert findings[0].kind == 'OpenAI-style API key'
    assert token not in findings[0].snippet


def test_secret_scan_allows_placeholders_and_ignored_local_env(tmp_path):
    example = tmp_path / '.env.local.example'
    example.write_text('AIDM_API_AUTH_TOKENS=your-token-placeholder\n', encoding='utf-8')
    local_env = tmp_path / '.env.local'
    local_env.write_text(
        'DEEPGRAM_API_KEY = "' + '0123456789abcdef0123456789abcdef' + '"\n',
        encoding='utf-8',
    )

    findings = scan_paths([tmp_path])

    assert findings == []


def test_secret_scan_skips_generated_directories(tmp_path):
    generated = tmp_path / 'aidm_frontend' / 'node_modules' / 'pkg'
    generated.mkdir(parents=True)
    source = generated / 'index.js'
    source.write_text(
        'const token = "' + '0123456789abcdef0123456789abcdef' + '";\n',
        encoding='utf-8',
    )

    findings = scan_paths([tmp_path])

    assert findings == []
