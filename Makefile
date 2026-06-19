PYTHON := .venv/bin/python
FRONTEND_DIR := aidm_frontend

.PHONY: install backend frontend unified test lint typecheck build bundle-budget smoke scenario-regression socket-concurrency-smoke hosted-cookie-auth-smoke security-forbidden-smoke session-export-import-smoke hosted-rc-evidence hosted-rc-plan export-support-bundle beta-slo-baseline local-beta-slo-baseline backup-restore-drill migration-chain-drill browser-smoke visual-smoke visual-smoke-review frontend-npm-ci-evidence packaging-cleanup-evidence github-actions-rc-plan github-actions-evidence clean clean-deps source-archive rc-issue-evidence rc-issue-closure-evidence release-evidence-packet release-artifact-consistency release-checklist-status rc-recommendation-matrix external-proof-inputs external-proof-execution-plan operator-signoff-values-template external-proof-values-merge external-proof-values-check operator-signoff-from-inputs operator-signoff-draft operator-signoff-action-plan operator-signoff-status rc-finalize-signoff rc-handoff-artifacts post-rc-issue-evidence db-upgrade health secrets api-types request-json-parsing state-writers socketio-worker-model-decision dev-check closed-beta-rc closed-beta-rc-fast deployment-readiness observability-check reproject-session reproject-all

install:
	python3 -m venv .venv
	$(PYTHON) -m pip install -r requirements.txt
	cd $(FRONTEND_DIR) && npm ci

backend:
	./scripts/run_local_backend.sh

frontend:
	cd $(FRONTEND_DIR) && npm run dev -- --host 127.0.0.1

unified:
	./scripts/run_unified_local.sh

test:
	$(PYTHON) -m pytest

lint:
	cd $(FRONTEND_DIR) && npm run lint

typecheck:
	cd $(FRONTEND_DIR) && npm run typecheck

build:
	cd $(FRONTEND_DIR) && npm run build

bundle-budget:
	cd $(FRONTEND_DIR) && npm run bundle:budget

smoke:
	$(PYTHON) scripts/smoke_beta_flow.py

scenario-regression:
	$(PYTHON) scripts/scenario_regression.py

socket-concurrency-smoke:
	$(PYTHON) scripts/socket_concurrency_smoke.py

hosted-cookie-auth-smoke:
	$(PYTHON) scripts/hosted_cookie_auth_smoke.py $(HOSTED_COOKIE_AUTH_SMOKE_ARGS)

security-forbidden-smoke:
	$(PYTHON) scripts/security_forbidden_smoke.py $(SECURITY_FORBIDDEN_SMOKE_ARGS)

session-export-import-smoke:
	$(PYTHON) scripts/session_export_import_smoke.py $(SESSION_EXPORT_IMPORT_SMOKE_ARGS)

hosted-rc-evidence:
	$(PYTHON) scripts/hosted_rc_evidence_check.py $(HOSTED_RC_EVIDENCE_ARGS)

hosted-rc-plan:
	$(PYTHON) scripts/hosted_rc_evidence_check.py --dry-run --preserve-existing-real-evidence --target-url https://closed-beta.example.test --env-file .env.production.example --auth-token hosted-rc-plan-operator-token --workspace-id workspace-1 --non-admin-token hosted-rc-plan-player-token --campaign-id 1 --session-id 1 --player-id 1 --socketio-worker-model single --database hosted-database-required --llm-provider-model hosted-provider-model-required --observability-provider hosted-observability-provider-required --alert-owner hosted-alert-owner-required

export-support-bundle:
	$(PYTHON) scripts/export_support_bundle.py $(EXPORT_SUPPORT_BUNDLE_ARGS)

beta-slo-baseline:
	$(PYTHON) scripts/render_beta_slo_baseline.py $(BETA_SLO_BASELINE_ARGS)

local-beta-slo-baseline:
	$(PYTHON) scripts/render_local_beta_slo_baseline.py $(LOCAL_BETA_SLO_BASELINE_ARGS)

backup-restore-drill:
	$(PYTHON) scripts/backup_restore_drill.py $(BACKUP_RESTORE_DRILL_ARGS)

migration-chain-drill:
	$(PYTHON) scripts/migration_chain_drill.py $(MIGRATION_CHAIN_DRILL_ARGS)

observability-check:
	$(PYTHON) scripts/check_observability_bundle.py $(OBSERVABILITY_CHECK_ARGS)

browser-smoke:
	cd $(FRONTEND_DIR) && npm run smoke:browser

visual-smoke:
	cd $(FRONTEND_DIR) && npm run smoke:visual

visual-smoke-review:
	$(PYTHON) scripts/review_visual_smoke_artifacts.py --json-output tmp/release/visual-smoke-review.json $(VISUAL_SMOKE_REVIEW_ARGS)

frontend-npm-ci-evidence:
	$(PYTHON) scripts/render_frontend_npm_ci_evidence.py $(FRONTEND_NPM_CI_EVIDENCE_ARGS)

packaging-cleanup-evidence:
	$(PYTHON) scripts/render_packaging_cleanup_evidence.py $(PACKAGING_CLEANUP_EVIDENCE_ARGS)

github-actions-rc-plan:
	$(PYTHON) scripts/prepare_github_actions_rc_evidence.py $(GITHUB_ACTIONS_RC_PLAN_ARGS)

github-actions-evidence:
	$(PYTHON) scripts/render_github_actions_evidence.py --json-output tmp/release/github-actions-evidence.json $(GITHUB_ACTIONS_EVIDENCE_ARGS)

clean:
	./scripts/cleanup_artifacts.sh

clean-deps: clean
	rm -rf .venv $(FRONTEND_DIR)/node_modules
	@echo "Removed local dependency folders."

source-archive:
	./scripts/create_source_archive.sh

rc-issue-evidence:
	$(PYTHON) scripts/render_rc_issue_evidence.py $(RC_ISSUE_EVIDENCE_ARGS)

rc-issue-closure-evidence:
	$(PYTHON) scripts/render_rc_issue_closure_evidence.py $(RC_ISSUE_CLOSURE_EVIDENCE_ARGS)

release-evidence-packet:
	$(PYTHON) scripts/render_release_evidence_packet.py --json-output tmp/release/release-evidence-packet.json $(RELEASE_EVIDENCE_PACKET_ARGS)

release-artifact-consistency:
	$(PYTHON) scripts/check_release_artifact_consistency.py $(RELEASE_ARTIFACT_CONSISTENCY_ARGS)

release-checklist-status:
	$(PYTHON) scripts/render_release_checklist_status.py $(RELEASE_CHECKLIST_STATUS_ARGS)

rc-recommendation-matrix:
	$(PYTHON) scripts/render_rc_recommendation_matrix.py $(RC_RECOMMENDATION_MATRIX_ARGS)

external-proof-inputs:
	$(PYTHON) scripts/render_external_proof_input_template.py $(EXTERNAL_PROOF_INPUTS_ARGS)

external-proof-execution-plan:
	$(PYTHON) scripts/render_external_proof_execution_plan.py $(EXTERNAL_PROOF_EXECUTION_PLAN_ARGS)

operator-signoff-values-template:
	$(PYTHON) scripts/render_operator_signoff_from_external_inputs.py --write-values-template $(OPERATOR_SIGNOFF_VALUES_TEMPLATE_ARGS)

external-proof-values-merge:
	$(PYTHON) scripts/merge_external_proof_values.py $(EXTERNAL_PROOF_VALUES_MERGE_ARGS)

external-proof-values-check:
	$(PYTHON) scripts/check_external_proof_values.py $(EXTERNAL_PROOF_VALUES_CHECK_ARGS)

operator-signoff-from-inputs:
	$(PYTHON) scripts/render_operator_signoff_from_external_inputs.py $(OPERATOR_SIGNOFF_FROM_INPUTS_ARGS)

operator-signoff-draft:
	$(PYTHON) scripts/render_operator_signoff_status.py --write-draft-from-packet $(OPERATOR_SIGNOFF_DRAFT_ARGS)

operator-signoff-action-plan:
	$(PYTHON) scripts/render_operator_signoff_status.py --write-action-plan $(OPERATOR_SIGNOFF_ACTION_PLAN_ARGS)

operator-signoff-status:
	$(PYTHON) scripts/render_operator_signoff_status.py $(OPERATOR_SIGNOFF_STATUS_ARGS)

rc-finalize-signoff:
	$(PYTHON) scripts/render_operator_signoff_from_external_inputs.py --output tmp/release/operator-signoff.json --status-output tmp/release/operator-signoff-status.md --status-json-output tmp/release/operator-signoff-status.json --write-self-evidence-to-values --require-complete $(OPERATOR_SIGNOFF_FROM_INPUTS_ARGS)
	$(PYTHON) scripts/check_external_proof_values.py --require-complete $(EXTERNAL_PROOF_VALUES_CHECK_ARGS)
	$(PYTHON) scripts/render_release_evidence_packet.py --json-output tmp/release/release-evidence-packet.json $(RELEASE_EVIDENCE_PACKET_ARGS)
	$(PYTHON) scripts/check_release_artifact_consistency.py $(RELEASE_ARTIFACT_CONSISTENCY_ARGS)
	$(PYTHON) scripts/render_release_evidence_packet.py --json-output tmp/release/release-evidence-packet.json $(RELEASE_EVIDENCE_PACKET_ARGS)
	$(PYTHON) scripts/render_release_checklist_status.py $(RELEASE_CHECKLIST_STATUS_ARGS)

rc-handoff-artifacts:
	$(PYTHON) scripts/prepare_github_actions_rc_evidence.py $(GITHUB_ACTIONS_RC_PLAN_ARGS)
	$(PYTHON) scripts/render_github_actions_evidence.py --auto-gh --include-gh-details --verify-closed-beta-rc-artifact-contents --json-output tmp/release/github-actions-evidence.json $(GITHUB_ACTIONS_EVIDENCE_ARGS)
	$(PYTHON) scripts/render_frontend_npm_ci_evidence.py $(FRONTEND_NPM_CI_EVIDENCE_ARGS)
	./scripts/create_source_archive.sh
	$(PYTHON) scripts/render_packaging_cleanup_evidence.py $(PACKAGING_CLEANUP_EVIDENCE_ARGS)
	$(MAKE) hosted-rc-plan PYTHON=$(PYTHON)
	$(PYTHON) scripts/render_rc_issue_evidence.py $(RC_ISSUE_EVIDENCE_ARGS)
	$(PYTHON) scripts/render_rc_issue_closure_evidence.py $(RC_ISSUE_CLOSURE_EVIDENCE_ARGS)
	$(PYTHON) scripts/render_release_evidence_packet.py --json-output tmp/release/release-evidence-packet.json $(RELEASE_EVIDENCE_PACKET_ARGS)
	$(PYTHON) scripts/render_operator_signoff_status.py $(OPERATOR_SIGNOFF_STATUS_ARGS)
	$(PYTHON) scripts/render_release_evidence_packet.py --json-output tmp/release/release-evidence-packet.json $(RELEASE_EVIDENCE_PACKET_ARGS)
	$(PYTHON) scripts/render_operator_signoff_status.py --write-draft-from-packet $(OPERATOR_SIGNOFF_DRAFT_ARGS)
	$(PYTHON) scripts/render_operator_signoff_status.py --write-action-plan $(OPERATOR_SIGNOFF_ACTION_PLAN_ARGS)
	$(PYTHON) scripts/render_release_evidence_packet.py --json-output tmp/release/release-evidence-packet.json $(RELEASE_EVIDENCE_PACKET_ARGS)
	$(PYTHON) scripts/render_release_checklist_status.py $(RELEASE_CHECKLIST_STATUS_ARGS)
	$(PYTHON) scripts/render_rc_recommendation_matrix.py $(RC_RECOMMENDATION_MATRIX_ARGS)
	$(PYTHON) scripts/render_external_proof_input_template.py $(EXTERNAL_PROOF_INPUTS_ARGS)
	$(PYTHON) scripts/render_operator_signoff_from_external_inputs.py --write-values-template $(OPERATOR_SIGNOFF_VALUES_TEMPLATE_ARGS)
	$(PYTHON) scripts/check_external_proof_values.py $(EXTERNAL_PROOF_VALUES_CHECK_ARGS)
	$(PYTHON) scripts/render_operator_signoff_from_external_inputs.py $(OPERATOR_SIGNOFF_FROM_INPUTS_ARGS)
	$(PYTHON) scripts/render_external_proof_execution_plan.py $(EXTERNAL_PROOF_EXECUTION_PLAN_ARGS)
	$(PYTHON) scripts/render_release_evidence_packet.py --json-output tmp/release/release-evidence-packet.json $(RELEASE_EVIDENCE_PACKET_ARGS)
	$(PYTHON) scripts/check_release_artifact_consistency.py $(RELEASE_ARTIFACT_CONSISTENCY_ARGS)
	$(PYTHON) scripts/render_release_evidence_packet.py --json-output tmp/release/release-evidence-packet.json $(RELEASE_EVIDENCE_PACKET_ARGS)
	$(PYTHON) scripts/render_release_checklist_status.py $(RELEASE_CHECKLIST_STATUS_ARGS)

post-rc-issue-evidence:
	$(PYTHON) scripts/post_rc_issue_evidence.py $(POST_RC_ISSUE_EVIDENCE_ARGS)

db-upgrade:
	FLASK_APP=aidm_server.main:create_app flask db upgrade

health:
	./scripts/check_local_health.sh

secrets:
	$(PYTHON) scripts/scan_secrets.py

api-types:
	$(PYTHON) scripts/generate_api_types.py

request-json-parsing:
	$(PYTHON) scripts/check_request_json_parsing.py

state-writers:
	$(PYTHON) scripts/check_state_snapshot_writers.py

socketio-worker-model-decision:
	$(PYTHON) scripts/check_socketio_worker_model_decision.py

dev-check:
	$(PYTHON) -m compileall -q aidm_server scripts
	$(PYTHON) -m ruff check --select E9,F63,F7,F82 aidm_server tests scripts
	$(PYTHON) scripts/scan_secrets.py
	$(PYTHON) scripts/generate_api_types.py --check
	$(PYTHON) scripts/check_request_json_parsing.py
	$(PYTHON) scripts/check_observability_bundle.py
	$(PYTHON) scripts/check_state_snapshot_writers.py
	$(PYTHON) scripts/check_socketio_worker_model_decision.py
	$(PYTHON) scripts/migration_chain_drill.py
	cd $(FRONTEND_DIR) && npm run typecheck
	cd $(FRONTEND_DIR) && npm run lint

closed-beta-rc:
	$(PYTHON) scripts/closed_beta_rc_check.py --python $(PYTHON) --evidence-report tmp/release/rc-evidence.md

closed-beta-rc-fast:
	$(PYTHON) scripts/closed_beta_rc_check.py --python $(PYTHON) --skip-browser-smoke --skip-dependency-audits --evidence-report tmp/release/rc-evidence.md

deployment-readiness:
	$(PYTHON) scripts/deployment_readiness_check.py $(DEPLOYMENT_READINESS_ARGS)

reproject-session:
	@if [ -z "$(SESSION_ID)" ]; then echo "SESSION_ID is required"; exit 1; fi
	$(PYTHON) scripts/reproject_session.py --session-id $(SESSION_ID)

reproject-all:
	$(PYTHON) scripts/reproject_session.py --all
