from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _target_commands(makefile: str, target: str) -> list[str]:
    lines = makefile.splitlines()
    start = next(index for index, line in enumerate(lines) if line == f'{target}:')
    commands: list[str] = []
    for line in lines[start + 1 :]:
        if line and not line.startswith('\t') and not line.startswith(' '):
            break
        if line.startswith('\t'):
            commands.append(line.strip())
    return commands


def test_rc_handoff_generates_packaging_evidence_before_signoff_draft():
    makefile = (REPO_ROOT / 'Makefile').read_text(encoding='utf-8')
    commands = _target_commands(makefile, 'rc-handoff-artifacts')

    github_plan_index = next(
        index for index, command in enumerate(commands) if 'scripts/prepare_github_actions_rc_evidence.py' in command
    )
    github_evidence_index = next(
        index for index, command in enumerate(commands) if 'scripts/render_github_actions_evidence.py' in command
    )
    packaging_index = next(
        index for index, command in enumerate(commands) if 'scripts/render_packaging_cleanup_evidence.py' in command
    )
    first_packet_index = next(
        index for index, command in enumerate(commands) if 'scripts/render_release_evidence_packet.py' in command
    )
    status_index = next(
        index
        for index, command in enumerate(commands)
        if command == '$(PYTHON) scripts/render_operator_signoff_status.py $(OPERATOR_SIGNOFF_STATUS_ARGS)'
    )
    draft_index = next(
        index for index, command in enumerate(commands) if 'scripts/render_operator_signoff_status.py --write-draft-from-packet' in command
    )
    action_plan_index = next(
        index for index, command in enumerate(commands) if 'scripts/render_operator_signoff_status.py --write-action-plan' in command
    )

    assert (
        github_plan_index
        < github_evidence_index
        < packaging_index
        < first_packet_index
        < status_index
        < draft_index
        < action_plan_index
    )


def test_rc_handoff_refreshes_github_actions_evidence_with_read_only_gh_details():
    makefile = (REPO_ROOT / 'Makefile').read_text(encoding='utf-8')
    commands = _target_commands(makefile, 'rc-handoff-artifacts')

    github_evidence_command = next(
        command for command in commands if 'scripts/render_github_actions_evidence.py' in command
    )

    assert '--auto-gh' in github_evidence_command
    assert '--include-gh-details' in github_evidence_command
    assert '--verify-closed-beta-rc-artifact-contents' in github_evidence_command
    assert '--json-output tmp/release/github-actions-evidence.json' in github_evidence_command
    assert '$(GITHUB_ACTIONS_EVIDENCE_ARGS)' in github_evidence_command


def test_rc_handoff_generates_hosted_plan_before_issue_and_packet_evidence():
    makefile = (REPO_ROOT / 'Makefile').read_text(encoding='utf-8')
    commands = _target_commands(makefile, 'rc-handoff-artifacts')
    hosted_plan_commands = _target_commands(makefile, 'hosted-rc-plan')

    hosted_plan_index = next(index for index, command in enumerate(commands) if 'hosted-rc-plan' in command)
    issue_index = next(index for index, command in enumerate(commands) if 'scripts/render_rc_issue_evidence.py' in command)
    first_packet_index = next(
        index for index, command in enumerate(commands) if 'scripts/render_release_evidence_packet.py' in command
    )

    assert hosted_plan_index < issue_index < first_packet_index
    assert len(hosted_plan_commands) == 1
    hosted_plan_command = hosted_plan_commands[0]
    assert 'scripts/hosted_rc_evidence_check.py' in hosted_plan_command
    assert '--dry-run' in hosted_plan_command
    assert '--preserve-existing-real-evidence' in hosted_plan_command
    assert '--target-url https://closed-beta.example.test' in hosted_plan_command


def test_rc_handoff_checks_artifact_consistency_then_refreshes_packet():
    makefile = (REPO_ROOT / 'Makefile').read_text(encoding='utf-8')
    commands = _target_commands(makefile, 'rc-handoff-artifacts')

    packet_indexes = [
        index for index, command in enumerate(commands) if 'scripts/render_release_evidence_packet.py' in command
    ]
    consistency_index = next(
        index for index, command in enumerate(commands) if 'scripts/check_release_artifact_consistency.py' in command
    )
    final_checklist_index = max(
        index for index, command in enumerate(commands) if 'scripts/render_release_checklist_status.py' in command
    )

    assert packet_indexes[-2] < consistency_index < packet_indexes[-1] < final_checklist_index


def test_external_proof_values_merge_target_uses_strict_default_helper():
    makefile = (REPO_ROOT / 'Makefile').read_text(encoding='utf-8')
    commands = _target_commands(makefile, 'external-proof-values-merge')

    assert commands == ['$(PYTHON) scripts/merge_external_proof_values.py $(EXTERNAL_PROOF_VALUES_MERGE_ARGS)']


def test_rc_finalize_signoff_generates_final_manifest_and_rechecks_values():
    makefile = (REPO_ROOT / 'Makefile').read_text(encoding='utf-8')
    commands = _target_commands(makefile, 'rc-finalize-signoff')

    assert commands[0] == (
        '$(PYTHON) scripts/render_operator_signoff_from_external_inputs.py '
        '--output tmp/release/operator-signoff.json '
        '--status-output tmp/release/operator-signoff-status.md '
        '--status-json-output tmp/release/operator-signoff-status.json '
        '--write-self-evidence-to-values --require-complete $(OPERATOR_SIGNOFF_FROM_INPUTS_ARGS)'
    )
    assert commands[1] == (
        '$(PYTHON) scripts/check_external_proof_values.py --require-complete '
        '$(EXTERNAL_PROOF_VALUES_CHECK_ARGS)'
    )
    assert any('scripts/check_release_artifact_consistency.py' in command for command in commands)
    assert commands[-1] == '$(PYTHON) scripts/render_release_checklist_status.py $(RELEASE_CHECKLIST_STATUS_ARGS)'


def test_github_actions_rc_plan_target_uses_guarded_helper():
    makefile = (REPO_ROOT / 'Makefile').read_text(encoding='utf-8')
    commands = _target_commands(makefile, 'github-actions-rc-plan')

    assert commands == [
        '$(PYTHON) scripts/prepare_github_actions_rc_evidence.py $(GITHUB_ACTIONS_RC_PLAN_ARGS)'
    ]
