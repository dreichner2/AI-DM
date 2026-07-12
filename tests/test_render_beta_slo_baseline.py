from __future__ import annotations

import json

from scripts.render_beta_slo_baseline import main, render_baseline


class _FakeResponse:
    def __init__(self, payload: dict, *, status_code: int = 200, text: str = ''):
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def json(self):
        return self._payload


def _slo_payload() -> dict:
    return {
        'dm_response_latency_ms_p95': 321.4,
        'dm_response_latency_sample_count': 8,
        'ai_provider_failure_rate': 0.125,
        'turn_persistence_failure_rate': 0.0,
        'canon_job_failure_rate': 0.25,
        'socket_unauthorized_event_count': 2,
        'socket_rate_limited_event_count': 1,
        'coherence_feedback_avg': 4.25,
        'provider_model_turn_counts': [
            {'provider': 'gemini', 'model': 'gemini-2.5-pro', 'turn_count': 7},
            {'provider': 'openai', 'model': 'gpt-5', 'turn_count': 1},
        ],
    }


def _incidents_payload() -> dict:
    return {
        'incidents': [
            {
                'type': 'bad_turn_report',
                'session_id': 3,
                'turn_id': 5,
                'category': 'rules',
                'provider': 'gemini',
                'model': 'gemini-2.5-pro',
                'severity': 'medium',
            },
            {
                'type': 'failed_turn',
                'session_id': 4,
                'turn_id': 6,
                'provider': 'openai',
                'model': 'gpt-5',
                'status': 'failed',
            },
        ],
        'summary': {'bad_turn_report_count': 1},
    }


def test_render_baseline_includes_slo_metrics_and_incidents():
    markdown = render_baseline(
        slo=_slo_payload(),
        incidents=_incidents_payload(),
        generated_at='2026-06-19T00:00:00+00:00',
        release='RC1',
        commit_sha='abc123',
        environment='staging',
        target_url='https://aidm.example.test',
        socketio_worker_model='single',
        database='postgres',
        llm_provider_model='gemini/gemini-2.5-pro',
        observability_provider='managed-prometheus',
        alert_owner='beta-oncall',
        evidence_report='tmp/release/deployment-readiness-evidence.md',
    )

    assert '# Beta SLO Baseline' in markdown
    assert '| DM response p95 latency | 321 ms | `/api/beta/slo` |  |' in markdown
    assert '| AI provider failure rate | 12.50% | `/api/beta/slo` |  |' in markdown
    assert '| Bad-turn reports by provider/model | gemini/gemini-2.5-pro: 1 | `/api/beta/incidents` |  |' in markdown
    assert '| gemini | gemini-2.5-pro | 7 |' in markdown
    assert '| 3 | 5 | rules | gemini/gemini-2.5-pro | medium |  |  |' in markdown


def test_main_renders_from_saved_json(tmp_path):
    slo_path = tmp_path / 'slo.json'
    incidents_path = tmp_path / 'incidents.json'
    output_path = tmp_path / 'baseline.md'
    slo_path.write_text(json.dumps(_slo_payload()), encoding='utf-8')
    incidents_path.write_text(json.dumps(_incidents_payload()), encoding='utf-8')

    exit_code = main(
        [
            '--slo-json',
            str(slo_path),
            '--incidents-json',
            str(incidents_path),
            '--target-url',
            'https://aidm.example.test',
            '--release',
            'RC1',
            '--commit-sha',
            'abc1234',
            '--invite-more-testers',
            'yes',
            '--output',
            str(output_path),
        ]
    )

    assert exit_code == 0
    output = output_path.read_text(encoding='utf-8')
    assert '- RC or release: RC1' in output
    assert '- Commit SHA: abc1234' in output
    assert '- Target URL: https://aidm.example.test' in output
    assert '- Invite more testers: yes' in output
    assert '| Socket unauthorized events | 2 | `/api/beta/slo` |  |' in output


def test_main_rejects_non_http_target_before_fetch(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        'scripts.render_beta_slo_baseline.requests.get',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError('request should not run')),
    )

    exit_code = main(['--target-url', 'staging', '--output', str(tmp_path / 'baseline.md')])

    assert exit_code == 2
    assert '--target-url must be an absolute http(s) URL.' in capsys.readouterr().err


def test_main_rejects_invalid_explicit_commit_before_reading_json(tmp_path, capsys):
    exit_code = main(
        [
            '--target-url',
            'https://aidm.example.test',
            '--commit-sha',
            'not-a-sha',
            '--slo-json',
            str(tmp_path / 'missing.json'),
        ]
    )

    assert exit_code == 2
    assert '--commit-sha must resolve to a 7-64 character hexadecimal Git SHA.' in capsys.readouterr().err


def test_main_fetches_target_slo_and_incidents(tmp_path, monkeypatch):
    calls = []

    def fake_get(url, headers, timeout):
        calls.append((url, headers, timeout))
        if url.endswith('/api/beta/slo'):
            return _FakeResponse(_slo_payload())
        if url.endswith('/api/beta/incidents?limit=5'):
            return _FakeResponse(_incidents_payload())
        raise AssertionError(url)

    monkeypatch.setattr('scripts.render_beta_slo_baseline.requests.get', fake_get)
    output_path = tmp_path / 'baseline.md'

    exit_code = main(
        [
            '--target-url',
            'https://aidm.example.test/',
            '--auth-token',
            'operator-token',
            '--workspace-id',
            'workspace-1',
            '--limit',
            '5',
            '--output',
            str(output_path),
        ]
    )

    assert exit_code == 0
    assert calls == [
        (
            'https://aidm.example.test/api/beta/slo',
            {
                'Accept': 'application/json',
                'Authorization': 'Bearer operator-token',
                'X-AIDM-Workspace-Id': 'workspace-1',
            },
            15.0,
        ),
        (
            'https://aidm.example.test/api/beta/incidents?limit=5',
            {
                'Accept': 'application/json',
                'Authorization': 'Bearer operator-token',
                'X-AIDM-Workspace-Id': 'workspace-1',
            },
            15.0,
        ),
    ]
    assert 'Wrote beta SLO baseline' not in output_path.read_text(encoding='utf-8')


def test_main_requires_target_url_for_evidence_binding(capsys):
    exit_code = main([])

    assert exit_code == 2
    assert '--target-url is required so the evidence is bound to its source environment.' in capsys.readouterr().err
