PRAGMA foreign_keys=OFF;
BEGIN TRANSACTION;
CREATE TABLE analysis_agent_executions (
	id VARCHAR(36) NOT NULL,
	project_id VARCHAR(36) NOT NULL,
	run_id VARCHAR(36) NOT NULL,
	execution_id VARCHAR(36) NOT NULL,
	ordinal INTEGER NOT NULL,
	role VARCHAR(40) NOT NULL,
	agent_id VARCHAR(80) NOT NULL,
	agent_version VARCHAR(40) NOT NULL,
	outcome VARCHAR(24) NOT NULL,
	input_fingerprint VARCHAR(64) NOT NULL,
	output_fingerprint VARCHAR(64) NOT NULL,
	confidence_json TEXT NOT NULL,
	warnings_json TEXT NOT NULL,
	provenance_json TEXT NOT NULL,
	envelope_json TEXT NOT NULL,
	started_at VARCHAR(32) NOT NULL,
	finished_at VARCHAR(32) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_analysis_agent_execution_ordinal UNIQUE (execution_id, ordinal),
	CONSTRAINT uq_analysis_agent_execution_role UNIQUE (execution_id, role),
	CONSTRAINT ck_analysis_agent_ordinal CHECK (ordinal >= 0),
	CONSTRAINT ck_analysis_agent_outcome CHECK (outcome IN ('succeeded', 'failed', 'skipped', 'cancelled', 'interrupted')),
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE,
	FOREIGN KEY(run_id) REFERENCES analysis_runs (id) ON DELETE CASCADE,
	FOREIGN KEY(execution_id) REFERENCES analysis_executions (id) ON DELETE CASCADE
);
CREATE TABLE analysis_corrections (
	id VARCHAR(36) NOT NULL,
	project_id VARCHAR(36) NOT NULL,
	run_id VARCHAR(36),
	category VARCHAR(40) NOT NULL,
	target_entity_id VARCHAR(36),
	target_key VARCHAR(120) NOT NULL,
	revision INTEGER NOT NULL,
	expected_target_revision INTEGER NOT NULL,
	expected_run_fingerprint VARCHAR(64) NOT NULL,
	previous_value_fingerprint VARCHAR(64) NOT NULL,
	patch_json TEXT NOT NULL,
	correction_fingerprint VARCHAR(64) NOT NULL,
	reason VARCHAR(1000) NOT NULL,
	actor_id VARCHAR(80) NOT NULL,
	supersedes_correction_id VARCHAR(36),
	legacy_correction_id VARCHAR(36),
	idempotency_key VARCHAR(160),
	recorded_at VARCHAR(32) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_analysis_correction_target_revision UNIQUE (run_id, category, target_key, revision),
	CONSTRAINT uq_analysis_correction_idempotency UNIQUE (run_id, idempotency_key),
	CONSTRAINT ck_analysis_correction_revision CHECK (revision >= 1),
	CONSTRAINT ck_analysis_correction_expected_revision CHECK (expected_target_revision >= 1),
	CONSTRAINT ck_analysis_correction_reason CHECK (length(trim(reason)) >= 1 AND length(reason) <= 1000),
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE,
	FOREIGN KEY(run_id) REFERENCES analysis_runs (id) ON DELETE RESTRICT,
	FOREIGN KEY(supersedes_correction_id) REFERENCES analysis_corrections (id) ON DELETE RESTRICT,
	UNIQUE (legacy_correction_id)
);
CREATE TABLE analysis_entities (
	id VARCHAR(36) NOT NULL,
	project_id VARCHAR(36) NOT NULL,
	run_id VARCHAR(36) NOT NULL,
	snapshot_id VARCHAR(36) NOT NULL,
	collection VARCHAR(40) NOT NULL,
	ordinal INTEGER NOT NULL,
	parent_entity_id VARCHAR(36),
	identity_key VARCHAR(160) NOT NULL,
	start_offset INTEGER,
	end_offset INTEGER,
	revision INTEGER NOT NULL,
	payload_json TEXT NOT NULL,
	fingerprint VARCHAR(64) NOT NULL,
	confidence_score INTEGER NOT NULL,
	confidence_class VARCHAR(12) NOT NULL,
	confidence_basis VARCHAR(160) NOT NULL,
	warnings_json TEXT NOT NULL,
	provenance_json TEXT NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_analysis_entity_run_collection_ordinal UNIQUE (run_id, collection, ordinal),
	CONSTRAINT ck_analysis_entity_ordinal CHECK (ordinal >= 0),
	CONSTRAINT ck_analysis_entity_revision CHECK (revision >= 1),
	CONSTRAINT ck_analysis_entity_span CHECK ((start_offset IS NULL AND end_offset IS NULL) OR (start_offset >= 0 AND end_offset >= start_offset)),
	CONSTRAINT ck_analysis_entity_confidence CHECK (confidence_score >= 0 AND confidence_score <= 1000000),
	CONSTRAINT ck_analysis_entity_confidence_class CHECK (confidence_class IN ('unknown', 'low', 'medium', 'high')),
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE,
	FOREIGN KEY(run_id) REFERENCES analysis_runs (id) ON DELETE CASCADE,
	FOREIGN KEY(snapshot_id) REFERENCES analysis_snapshots (id) ON DELETE CASCADE
);
CREATE TABLE analysis_evidence_spans (
	id VARCHAR(36) NOT NULL,
	project_id VARCHAR(36) NOT NULL,
	run_id VARCHAR(36) NOT NULL,
	entity_id VARCHAR(36) NOT NULL,
	ordinal INTEGER NOT NULL,
	start_offset INTEGER NOT NULL,
	end_offset INTEGER NOT NULL,
	text_sha256 VARCHAR(64) NOT NULL,
	basis VARCHAR(160) NOT NULL,
	confidence_score INTEGER NOT NULL,
	provenance_json TEXT NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_analysis_evidence_entity_ordinal UNIQUE (entity_id, ordinal),
	CONSTRAINT ck_analysis_evidence_ordinal CHECK (ordinal >= 0),
	CONSTRAINT ck_analysis_evidence_span CHECK (start_offset >= 0 AND end_offset >= start_offset),
	CONSTRAINT ck_analysis_evidence_confidence CHECK (confidence_score >= 0 AND confidence_score <= 1000000),
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE,
	FOREIGN KEY(run_id) REFERENCES analysis_runs (id) ON DELETE CASCADE,
	FOREIGN KEY(entity_id) REFERENCES analysis_entities (id) ON DELETE CASCADE
);
CREATE TABLE analysis_executions (
	id VARCHAR(36) NOT NULL,
	project_id VARCHAR(36) NOT NULL,
	run_id VARCHAR(36) NOT NULL,
	job_id VARCHAR(36) NOT NULL,
	attempt INTEGER NOT NULL,
	outcome VARCHAR(24) NOT NULL,
	input_fingerprint VARCHAR(64) NOT NULL,
	profile_fingerprint VARCHAR(64) NOT NULL,
	agent_registry_fingerprint VARCHAR(64) NOT NULL,
	output_fingerprint VARCHAR(64),
	warnings_json TEXT NOT NULL,
	error_code VARCHAR(80),
	error_message VARCHAR(300),
	error_retryable BOOLEAN,
	started_at VARCHAR(32) NOT NULL,
	finished_at VARCHAR(32) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_analysis_execution_run_attempt UNIQUE (run_id, attempt),
	CONSTRAINT uq_analysis_execution_job_attempt UNIQUE (job_id, attempt),
	CONSTRAINT ck_analysis_execution_attempt CHECK (attempt >= 1),
	CONSTRAINT ck_analysis_execution_outcome CHECK (outcome IN ('succeeded', 'failed', 'cancelled', 'interrupted')),
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE,
	FOREIGN KEY(run_id) REFERENCES analysis_runs (id) ON DELETE CASCADE,
	FOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE RESTRICT
);
CREATE TABLE analysis_review_decisions (
	id VARCHAR(36) NOT NULL,
	project_id VARCHAR(36) NOT NULL,
	run_id VARCHAR(36) NOT NULL,
	snapshot_id VARCHAR(36) NOT NULL,
	gate_id VARCHAR(48) NOT NULL,
	revision INTEGER NOT NULL,
	state VARCHAR(24) NOT NULL,
	artifact_fingerprint VARCHAR(64) NOT NULL,
	evidence_fingerprint VARCHAR(64) NOT NULL,
	eligible BOOLEAN NOT NULL,
	rationale VARCHAR(4000) NOT NULL,
	warning_acknowledgements_json TEXT NOT NULL,
	provenance_json TEXT NOT NULL,
	actor_id VARCHAR(80),
	idempotency_key VARCHAR(160),
	supersedes_decision_id VARCHAR(36),
	decided_at VARCHAR(32),
	created_at VARCHAR(32) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_analysis_review_gate_revision UNIQUE (run_id, gate_id, revision),
	CONSTRAINT uq_analysis_review_gate_idempotency UNIQUE (run_id, gate_id, idempotency_key),
	CONSTRAINT ck_analysis_review_revision CHECK (revision >= 1),
	CONSTRAINT ck_analysis_review_gate CHECK (gate_id IN ('story_structure_review', 'character_registry_review', 'dialogue_attribution_review', 'whole_book_analysis_review')),
	CONSTRAINT ck_analysis_review_state CHECK (state IN ('pending', 'approved', 'rejected', 'changes_requested', 'invalidated')),
	CONSTRAINT ck_analysis_review_rationale CHECK (length(trim(rationale)) >= 1 AND length(rationale) <= 4000),
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE,
	FOREIGN KEY(run_id) REFERENCES analysis_runs (id) ON DELETE CASCADE,
	FOREIGN KEY(snapshot_id) REFERENCES analysis_snapshots (id) ON DELETE RESTRICT,
	FOREIGN KEY(supersedes_decision_id) REFERENCES analysis_review_decisions (id) ON DELETE RESTRICT
);
CREATE TABLE analysis_runs (
	id VARCHAR(36) NOT NULL,
	project_id VARCHAR(36) NOT NULL,
	story_id VARCHAR(36) NOT NULL,
	source_document_id VARCHAR(36) NOT NULL,
	source_revision INTEGER NOT NULL,
	extraction_id VARCHAR(36) NOT NULL,
	import_review_record_id VARCHAR(36) NOT NULL,
	review_id VARCHAR(36) NOT NULL,
	review_revision INTEGER NOT NULL,
	review_decision_id VARCHAR(36) NOT NULL,
	approval_evidence_fingerprint VARCHAR(64) NOT NULL,
	story_revision INTEGER NOT NULL,
	extraction_revision INTEGER NOT NULL,
	extracted_text_sha256 VARCHAR(64) NOT NULL,
	input_fingerprint VARCHAR(64) NOT NULL,
	correction_set_fingerprint VARCHAR(64) NOT NULL,
	profile_json TEXT NOT NULL,
	profile_fingerprint VARCHAR(64) NOT NULL,
	producer_id VARCHAR(80) NOT NULL,
	producer_version VARCHAR(40) NOT NULL,
	run_fingerprint VARCHAR(64) NOT NULL,
	job_id VARCHAR(36) NOT NULL,
	created_at VARCHAR(32) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_analysis_run_review_revision CHECK (review_revision >= 1),
	CONSTRAINT ck_analysis_run_source_revision CHECK (source_revision >= 1),
	CONSTRAINT ck_analysis_run_story_revision CHECK (story_revision >= 1),
	CONSTRAINT ck_analysis_run_extraction_revision CHECK (extraction_revision >= 1),
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE,
	FOREIGN KEY(story_id) REFERENCES imported_stories (id) ON DELETE RESTRICT,
	FOREIGN KEY(source_document_id) REFERENCES source_documents (id) ON DELETE RESTRICT,
	FOREIGN KEY(extraction_id) REFERENCES document_extractions (id) ON DELETE RESTRICT,
	FOREIGN KEY(import_review_record_id) REFERENCES import_reviews (id) ON DELETE RESTRICT,
	FOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE RESTRICT
);
CREATE TABLE analysis_snapshots (
	id VARCHAR(36) NOT NULL,
	project_id VARCHAR(36) NOT NULL,
	run_id VARCHAR(36) NOT NULL,
	execution_id VARCHAR(36) NOT NULL,
	ordinal INTEGER NOT NULL,
	stage VARCHAR(40) NOT NULL,
	fingerprint VARCHAR(64) NOT NULL,
	entity_count INTEGER NOT NULL,
	manifest_json TEXT NOT NULL,
	created_at VARCHAR(32) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_analysis_snapshot_execution_ordinal UNIQUE (execution_id, ordinal),
	CONSTRAINT uq_analysis_snapshot_execution_stage UNIQUE (execution_id, stage),
	CONSTRAINT ck_analysis_snapshot_ordinal CHECK (ordinal >= 0),
	CONSTRAINT ck_analysis_snapshot_entity_count CHECK (entity_count >= 0),
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE,
	FOREIGN KEY(run_id) REFERENCES analysis_runs (id) ON DELETE CASCADE,
	FOREIGN KEY(execution_id) REFERENCES analysis_executions (id) ON DELETE CASCADE
);
CREATE TABLE analysis_stage_checkpoints (
	id VARCHAR(36) NOT NULL,
	project_id VARCHAR(36) NOT NULL,
	run_id VARCHAR(36) NOT NULL,
	job_id VARCHAR(36) NOT NULL,
	attempt INTEGER NOT NULL,
	ordinal INTEGER NOT NULL,
	stage VARCHAR(48) NOT NULL,
	input_fingerprint VARCHAR(64) NOT NULL,
	profile_fingerprint VARCHAR(64) NOT NULL,
	payload_fingerprint VARCHAR(64) NOT NULL,
	payload_json TEXT NOT NULL,
	created_at VARCHAR(32) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_analysis_stage_checkpoint_ordinal UNIQUE (job_id, attempt, ordinal),
	CONSTRAINT uq_analysis_stage_checkpoint_stage UNIQUE (job_id, attempt, stage),
	CONSTRAINT ck_analysis_stage_checkpoint_attempt CHECK (attempt >= 1),
	CONSTRAINT ck_analysis_stage_checkpoint_ordinal CHECK (ordinal >= 0),
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE,
	FOREIGN KEY(run_id) REFERENCES analysis_runs (id) ON DELETE CASCADE,
	FOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE CASCADE
);
CREATE TABLE approved_cast_snapshots (
	id VARCHAR(36) NOT NULL,
	project_id VARCHAR(36) NOT NULL,
	casting_run_id VARCHAR(36) NOT NULL,
	revision INTEGER NOT NULL,
	phase2_snapshot_fingerprint VARCHAR(64) NOT NULL,
	catalog_revision_id VARCHAR(36) NOT NULL,
	catalog_fingerprint VARCHAR(64) NOT NULL,
	casting_profile_fingerprint VARCHAR(64) NOT NULL,
	effective_correction_set_fingerprint VARCHAR(64) NOT NULL,
	role_count INTEGER NOT NULL,
	assignment_count INTEGER NOT NULL,
	unresolved_role_count INTEGER NOT NULL,
	restricted_rights_count INTEGER NOT NULL,
	ineligible_rights_count INTEGER NOT NULL,
	snapshot_fingerprint VARCHAR(64) NOT NULL,
	manifest_json TEXT NOT NULL,
	created_at VARCHAR(32) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_approved_cast_snapshot_run_revision UNIQUE (casting_run_id, revision),
	CONSTRAINT ck_approved_cast_snapshot_revision CHECK (revision >= 1),
	CONSTRAINT ck_approved_cast_snapshot_counts CHECK (role_count >= 0 AND assignment_count >= 0 AND unresolved_role_count >= 0 AND restricted_rights_count >= 0 AND ineligible_rights_count >= 0),
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE,
	FOREIGN KEY(casting_run_id) REFERENCES casting_runs (id) ON DELETE CASCADE,
	FOREIGN KEY(catalog_revision_id) REFERENCES voice_catalog_revisions (id) ON DELETE RESTRICT
);
CREATE TABLE cast_assignment_invalidations (
	id VARCHAR(36) NOT NULL,
	project_id VARCHAR(36) NOT NULL,
	casting_run_id VARCHAR(36) NOT NULL,
	role_id VARCHAR(36) NOT NULL,
	assignment_id VARCHAR(36) NOT NULL,
	reason_codes_json TEXT NOT NULL,
	evidence_fingerprint VARCHAR(64) NOT NULL,
	provenance_json TEXT NOT NULL,
	created_at VARCHAR(32) NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE,
	FOREIGN KEY(casting_run_id) REFERENCES casting_runs (id) ON DELETE CASCADE,
	FOREIGN KEY(role_id) REFERENCES production_roles (id) ON DELETE CASCADE,
	FOREIGN KEY(assignment_id) REFERENCES cast_assignments (id) ON DELETE RESTRICT
);
CREATE TABLE cast_assignments (
	id VARCHAR(36) NOT NULL,
	project_id VARCHAR(36) NOT NULL,
	casting_run_id VARCHAR(36) NOT NULL,
	role_id VARCHAR(36) NOT NULL,
	correction_id VARCHAR(36),
	voice_profile_record_id VARCHAR(36),
	catalog_revision_id VARCHAR(36) NOT NULL,
	casting_profile_fingerprint VARCHAR(64) NOT NULL,
	phase2_snapshot_fingerprint VARCHAR(64) NOT NULL,
	effective_correction_set_fingerprint VARCHAR(64) NOT NULL,
	authority VARCHAR(24) NOT NULL,
	assignment_state VARCHAR(24) NOT NULL,
	rationale VARCHAR(4000) NOT NULL,
	warnings_json TEXT NOT NULL,
	rights_state VARCHAR(16) NOT NULL,
	revision INTEGER NOT NULL,
	provenance_json TEXT NOT NULL,
	supersedes_assignment_id VARCHAR(36),
	created_at VARCHAR(32) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_cast_assignment_role_revision UNIQUE (role_id, revision),
	CONSTRAINT ck_cast_assignment_revision CHECK (revision >= 1),
	CONSTRAINT ck_cast_assignment_authority CHECK (authority IN ('machine_proposal', 'human_selection', 'human_locked')),
	CONSTRAINT ck_cast_assignment_state CHECK (assignment_state IN ('proposed', 'selected', 'locked', 'cleared', 'intentionally_uncast')),
	CONSTRAINT ck_cast_assignment_rights_state CHECK (rights_state IN ('verified', 'restricted', 'unknown', 'prohibited')),
	CONSTRAINT ck_cast_assignment_correction_authority CHECK ((authority = 'machine_proposal' AND correction_id IS NULL) OR (authority IN ('human_selection', 'human_locked') AND correction_id IS NOT NULL)),
	CONSTRAINT ck_cast_assignment_rationale CHECK (length(trim(rationale)) >= 1 AND length(rationale) <= 4000),
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE,
	FOREIGN KEY(casting_run_id) REFERENCES casting_runs (id) ON DELETE CASCADE,
	FOREIGN KEY(role_id) REFERENCES production_roles (id) ON DELETE CASCADE,
	FOREIGN KEY(correction_id) REFERENCES casting_corrections (id) ON DELETE RESTRICT,
	FOREIGN KEY(voice_profile_record_id) REFERENCES voice_profiles (id) ON DELETE RESTRICT,
	FOREIGN KEY(catalog_revision_id) REFERENCES voice_catalog_revisions (id) ON DELETE RESTRICT,
	FOREIGN KEY(supersedes_assignment_id) REFERENCES cast_assignments (id) ON DELETE RESTRICT
);
CREATE TABLE casting_candidates (
	id VARCHAR(36) NOT NULL,
	project_id VARCHAR(36) NOT NULL,
	casting_run_id VARCHAR(36) NOT NULL,
	role_id VARCHAR(36) NOT NULL,
	voice_profile_record_id VARCHAR(36) NOT NULL,
	role_revision INTEGER NOT NULL,
	ordinal INTEGER NOT NULL,
	compatibility_status VARCHAR(16) NOT NULL,
	compatibility_score INTEGER,
	confidence_class VARCHAR(12) NOT NULL,
	hard_constraint_results_json TEXT NOT NULL,
	soft_preference_results_json TEXT NOT NULL,
	rights_eligibility VARCHAR(16) NOT NULL,
	language_eligibility VARCHAR(16) NOT NULL,
	provider_availability VARCHAR(16) NOT NULL,
	model_availability VARCHAR(16) NOT NULL,
	long_form_suitability VARCHAR(24) NOT NULL,
	conflict_warnings_json TEXT NOT NULL,
	explanation_json TEXT NOT NULL,
	provenance_json TEXT NOT NULL,
	input_fingerprint VARCHAR(64) NOT NULL,
	output_fingerprint VARCHAR(64) NOT NULL,
	created_at VARCHAR(32) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_casting_candidate_role_revision_voice UNIQUE (role_id, role_revision, voice_profile_record_id),
	CONSTRAINT uq_casting_candidate_role_revision_ordinal UNIQUE (role_id, role_revision, ordinal),
	CONSTRAINT ck_casting_candidate_role_revision CHECK (role_revision >= 1),
	CONSTRAINT ck_casting_candidate_ordinal CHECK (ordinal >= 0 AND ordinal < 50),
	CONSTRAINT ck_casting_candidate_status CHECK (compatibility_status IN ('eligible', 'conditional', 'ineligible', 'unknown')),
	CONSTRAINT ck_casting_candidate_score CHECK (compatibility_score IS NULL OR (compatibility_score >= 0 AND compatibility_score <= 1000000)),
	CONSTRAINT ck_casting_candidate_confidence CHECK (confidence_class IN ('unknown', 'low', 'medium', 'high')),
	CONSTRAINT ck_casting_candidate_rights CHECK (rights_eligibility IN ('eligible', 'restricted', 'ineligible', 'unknown')),
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE,
	FOREIGN KEY(casting_run_id) REFERENCES casting_runs (id) ON DELETE CASCADE,
	FOREIGN KEY(role_id) REFERENCES production_roles (id) ON DELETE CASCADE,
	FOREIGN KEY(voice_profile_record_id) REFERENCES voice_profiles (id) ON DELETE RESTRICT
);
CREATE TABLE casting_conflicts (
	id VARCHAR(36) NOT NULL,
	project_id VARCHAR(36) NOT NULL,
	casting_run_id VARCHAR(36) NOT NULL,
	primary_role_id VARCHAR(36) NOT NULL,
	secondary_role_id VARCHAR(36),
	voice_profile_record_id VARCHAR(36),
	category VARCHAR(48) NOT NULL,
	severity VARCHAR(16) NOT NULL,
	status VARCHAR(16) NOT NULL,
	details_json TEXT NOT NULL,
	evidence_fingerprint VARCHAR(64) NOT NULL,
	provenance_json TEXT NOT NULL,
	created_at VARCHAR(32) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_casting_conflict_category CHECK (category IN ('incompatible_voice_reuse', 'narrator_major_character_reuse', 'metadata_similarity_risk', 'accent_or_locale_mismatch', 'insufficient_expressive_range', 'rights_conflict', 'provider_or_model_unavailable', 'deprecated_voice', 'role_length_suitability', 'unresolved_role_assignment', 'voice_reuse_threshold_exceeded')),
	CONSTRAINT ck_casting_conflict_severity CHECK (severity IN ('info', 'warning', 'blocking')),
	CONSTRAINT ck_casting_conflict_status CHECK (status IN ('open', 'acknowledged', 'resolved', 'superseded')),
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE,
	FOREIGN KEY(casting_run_id) REFERENCES casting_runs (id) ON DELETE CASCADE,
	FOREIGN KEY(primary_role_id) REFERENCES production_roles (id) ON DELETE CASCADE,
	FOREIGN KEY(secondary_role_id) REFERENCES production_roles (id) ON DELETE CASCADE,
	FOREIGN KEY(voice_profile_record_id) REFERENCES voice_profiles (id) ON DELETE RESTRICT
);
CREATE TABLE casting_corrections (
	id VARCHAR(36) NOT NULL,
	project_id VARCHAR(36) NOT NULL,
	casting_run_id VARCHAR(36) NOT NULL,
	role_id VARCHAR(36) NOT NULL,
	kind VARCHAR(48) NOT NULL,
	revision INTEGER NOT NULL,
	prior_effective_fingerprint VARCHAR(64) NOT NULL,
	corrected_value_json TEXT NOT NULL,
	correction_fingerprint VARCHAR(64) NOT NULL,
	actor_id VARCHAR(80) NOT NULL,
	reason VARCHAR(2000) NOT NULL,
	provenance_json TEXT NOT NULL,
	supersedes_correction_id VARCHAR(36),
	idempotency_key VARCHAR(160),
	recorded_at VARCHAR(32) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_casting_correction_role_revision UNIQUE (role_id, revision),
	CONSTRAINT uq_casting_correction_idempotency UNIQUE (casting_run_id, idempotency_key),
	CONSTRAINT uq_casting_correction_single_successor UNIQUE (supersedes_correction_id),
	CONSTRAINT ck_casting_correction_revision CHECK (revision >= 1),
	CONSTRAINT ck_casting_correction_kind CHECK (kind IN ('select_voice', 'clear_assignment', 'lock_assignment', 'unlock_assignment', 'mark_intentionally_uncast', 'change_role_label', 'change_casting_requirement', 'acknowledge_restricted_rights', 'approve_voice_reuse', 'reject_candidate', 'record_custom_rationale')),
	CONSTRAINT ck_casting_correction_reason CHECK (length(trim(reason)) >= 1 AND length(reason) <= 2000),
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE,
	FOREIGN KEY(casting_run_id) REFERENCES casting_runs (id) ON DELETE CASCADE,
	FOREIGN KEY(role_id) REFERENCES production_roles (id) ON DELETE RESTRICT,
	FOREIGN KEY(supersedes_correction_id) REFERENCES casting_corrections (id) ON DELETE RESTRICT
);
CREATE TABLE casting_gate_decisions (
	id VARCHAR(36) NOT NULL,
	project_id VARCHAR(36) NOT NULL,
	casting_run_id VARCHAR(36) NOT NULL,
	cast_snapshot_id VARCHAR(36) NOT NULL,
	gate_review_id VARCHAR(36) NOT NULL,
	gate_id VARCHAR(40) NOT NULL,
	revision INTEGER NOT NULL,
	decision VARCHAR(24) NOT NULL,
	evidence_fingerprint VARCHAR(64) NOT NULL,
	actor_id VARCHAR(80),
	warning_acknowledgements_json TEXT NOT NULL,
	rationale VARCHAR(4000) NOT NULL,
	provenance_json TEXT NOT NULL,
	supersedes_decision_id VARCHAR(36),
	idempotency_key VARCHAR(160),
	decided_at VARCHAR(32),
	created_at VARCHAR(32) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_casting_gate_decision_revision UNIQUE (casting_run_id, gate_id, revision),
	CONSTRAINT uq_casting_gate_decision_idempotency UNIQUE (casting_run_id, gate_id, idempotency_key),
	CONSTRAINT ck_casting_gate_decision_revision CHECK (revision >= 1),
	CONSTRAINT ck_casting_gate_decision_gate CHECK (gate_id IN ('narrator_casting_review', 'character_casting_review', 'complete_cast_review')),
	CONSTRAINT ck_casting_gate_decision_state CHECK (decision IN ('pending', 'approved', 'rejected', 'changes_requested', 'invalidated')),
	CONSTRAINT ck_casting_gate_decision_rationale CHECK (length(trim(rationale)) >= 1 AND length(rationale) <= 4000),
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE,
	FOREIGN KEY(casting_run_id) REFERENCES casting_runs (id) ON DELETE CASCADE,
	FOREIGN KEY(cast_snapshot_id) REFERENCES approved_cast_snapshots (id) ON DELETE RESTRICT,
	FOREIGN KEY(gate_review_id) REFERENCES casting_gate_reviews (id) ON DELETE RESTRICT,
	FOREIGN KEY(supersedes_decision_id) REFERENCES casting_gate_decisions (id) ON DELETE RESTRICT
);
CREATE TABLE casting_gate_reviews (
	id VARCHAR(36) NOT NULL,
	project_id VARCHAR(36) NOT NULL,
	casting_run_id VARCHAR(36) NOT NULL,
	cast_snapshot_id VARCHAR(36) NOT NULL,
	gate_id VARCHAR(40) NOT NULL,
	revision INTEGER NOT NULL,
	eligible BOOLEAN NOT NULL,
	evidence_fingerprint VARCHAR(64) NOT NULL,
	required_gate_decision_ids_json TEXT NOT NULL,
	warnings_json TEXT NOT NULL,
	provenance_json TEXT NOT NULL,
	created_at VARCHAR(32) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_casting_gate_review_revision UNIQUE (casting_run_id, gate_id, revision),
	CONSTRAINT ck_casting_gate_review_revision CHECK (revision >= 1),
	CONSTRAINT ck_casting_gate_review_gate CHECK (gate_id IN ('narrator_casting_review', 'character_casting_review', 'complete_cast_review')),
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE,
	FOREIGN KEY(casting_run_id) REFERENCES casting_runs (id) ON DELETE CASCADE,
	FOREIGN KEY(cast_snapshot_id) REFERENCES approved_cast_snapshots (id) ON DELETE RESTRICT
);
CREATE TABLE casting_profiles (
	id VARCHAR(36) NOT NULL,
	profile_id VARCHAR(80) NOT NULL,
	semantic_version VARCHAR(40) NOT NULL,
	producer_id VARCHAR(80) NOT NULL,
	producer_version VARCHAR(40) NOT NULL,
	compatibility_rules_json TEXT NOT NULL,
	hard_constraints_json TEXT NOT NULL,
	soft_preferences_json TEXT NOT NULL,
	conflict_rules_json TEXT NOT NULL,
	rights_eligibility_rules_json TEXT NOT NULL,
	pre_reduction_candidate_limit INTEGER NOT NULL,
	candidate_limit INTEGER NOT NULL,
	explanation_requirements_json TEXT NOT NULL,
	profile_fingerprint VARCHAR(64) NOT NULL,
	provenance_json TEXT NOT NULL,
	created_at VARCHAR(32) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_casting_profile_version UNIQUE (profile_id, semantic_version),
	CONSTRAINT ck_casting_profile_pre_reduction_limit CHECK (pre_reduction_candidate_limit >= 1 AND pre_reduction_candidate_limit <= 50),
	CONSTRAINT ck_casting_profile_candidate_limit CHECK (candidate_limit >= 1 AND candidate_limit <= pre_reduction_candidate_limit),
	UNIQUE (profile_fingerprint)
);
CREATE TABLE casting_runs (
	id VARCHAR(36) NOT NULL,
	project_id VARCHAR(36) NOT NULL,
	source_document_id VARCHAR(36) NOT NULL,
	source_revision INTEGER NOT NULL,
	extraction_id VARCHAR(36) NOT NULL,
	extraction_revision INTEGER NOT NULL,
	extracted_text_sha256 VARCHAR(64) NOT NULL,
	import_review_decision_id VARCHAR(36) NOT NULL,
	analysis_run_id VARCHAR(36) NOT NULL,
	analysis_snapshot_id VARCHAR(36) NOT NULL,
	analysis_snapshot_revision INTEGER NOT NULL,
	analysis_snapshot_fingerprint VARCHAR(64) NOT NULL,
	analysis_correction_set_fingerprint VARCHAR(64) NOT NULL,
	character_registry_fingerprint VARCHAR(64) NOT NULL,
	phase2_gate_decision_ids_json TEXT NOT NULL,
	phase2_gate_evidence_fingerprint VARCHAR(64) NOT NULL,
	casting_profile_id VARCHAR(36) NOT NULL,
	casting_profile_fingerprint VARCHAR(64) NOT NULL,
	catalog_revision_id VARCHAR(36) NOT NULL,
	catalog_fingerprint VARCHAR(64) NOT NULL,
	effective_correction_set_fingerprint VARCHAR(64) NOT NULL,
	producer_id VARCHAR(80) NOT NULL,
	producer_version VARCHAR(40) NOT NULL,
	input_fingerprint VARCHAR(64) NOT NULL,
	run_fingerprint VARCHAR(64) NOT NULL,
	job_id VARCHAR(36) NOT NULL,
	state VARCHAR(24) NOT NULL,
	warnings_json TEXT NOT NULL,
	created_at VARCHAR(32) NOT NULL,
	published_at VARCHAR(32),
	PRIMARY KEY (id),
	CONSTRAINT ck_casting_run_source_revision CHECK (source_revision >= 1),
	CONSTRAINT ck_casting_run_extraction_revision CHECK (extraction_revision >= 1),
	CONSTRAINT ck_casting_run_snapshot_revision CHECK (analysis_snapshot_revision >= 1),
	CONSTRAINT ck_casting_run_state CHECK (state IN ('pending', 'running', 'succeeded', 'failed', 'cancelled', 'interrupted')),
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE,
	FOREIGN KEY(source_document_id) REFERENCES source_documents (id) ON DELETE RESTRICT,
	FOREIGN KEY(extraction_id) REFERENCES document_extractions (id) ON DELETE RESTRICT,
	FOREIGN KEY(analysis_run_id) REFERENCES analysis_runs (id) ON DELETE RESTRICT,
	FOREIGN KEY(analysis_snapshot_id) REFERENCES analysis_snapshots (id) ON DELETE RESTRICT,
	FOREIGN KEY(casting_profile_id) REFERENCES casting_profiles (id) ON DELETE RESTRICT,
	FOREIGN KEY(catalog_revision_id) REFERENCES voice_catalog_revisions (id) ON DELETE RESTRICT,
	FOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE RESTRICT
);
CREATE TABLE chapters (
	id VARCHAR(36) NOT NULL,
	project_id VARCHAR(36) NOT NULL,
	story_id VARCHAR(36) NOT NULL,
	ordinal INTEGER NOT NULL,
	title VARCHAR(255),
	start_offset INTEGER NOT NULL,
	end_offset INTEGER NOT NULL,
	revision INTEGER NOT NULL,
	provenance_json TEXT NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_chapter_story_ordinal UNIQUE (story_id, ordinal),
	CONSTRAINT ck_chapter_ordinal CHECK (ordinal >= 0),
	CONSTRAINT ck_chapter_span CHECK (start_offset >= 0 AND end_offset >= start_offset),
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE,
	FOREIGN KEY(story_id) REFERENCES imported_stories (id) ON DELETE CASCADE
);
CREATE TABLE characters (
	id VARCHAR(36) NOT NULL,
	project_id VARCHAR(36) NOT NULL,
	story_id VARCHAR(36) NOT NULL,
	display_name VARCHAR(120) NOT NULL,
	normalized_name VARCHAR(120) NOT NULL,
	aliases_json TEXT NOT NULL,
	evidence_json TEXT NOT NULL,
	revision INTEGER NOT NULL,
	confidence_json TEXT NOT NULL,
	warnings_json TEXT NOT NULL,
	provenance_json TEXT NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_character_story_name UNIQUE (story_id, normalized_name),
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE,
	FOREIGN KEY(story_id) REFERENCES imported_stories (id) ON DELETE CASCADE
);
CREATE TABLE dialogue_attributions (
	id VARCHAR(36) NOT NULL,
	project_id VARCHAR(36) NOT NULL,
	line_id VARCHAR(36) NOT NULL,
	proposed_speaker_id VARCHAR(36),
	effective_speaker_id VARCHAR(36),
	effective_authority VARCHAR(24) NOT NULL,
	evidence_json TEXT NOT NULL,
	revision INTEGER NOT NULL,
	confidence_json TEXT NOT NULL,
	warnings_json TEXT NOT NULL,
	provenance_json TEXT NOT NULL,
	updated_at VARCHAR(32) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_attribution_revision CHECK (revision >= 1),
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE,
	UNIQUE (line_id),
	FOREIGN KEY(line_id) REFERENCES dialogue_lines (id) ON DELETE CASCADE,
	FOREIGN KEY(proposed_speaker_id) REFERENCES characters (id) ON DELETE RESTRICT,
	FOREIGN KEY(effective_speaker_id) REFERENCES characters (id) ON DELETE RESTRICT
);
CREATE TABLE dialogue_lines (
	id VARCHAR(36) NOT NULL,
	project_id VARCHAR(36) NOT NULL,
	scene_id VARCHAR(36) NOT NULL,
	beat_id VARCHAR(36) NOT NULL,
	ordinal INTEGER NOT NULL,
	start_offset INTEGER NOT NULL,
	end_offset INTEGER NOT NULL,
	verbatim_text TEXT NOT NULL,
	text_sha256 VARCHAR(64) NOT NULL,
	revision INTEGER NOT NULL,
	provenance_json TEXT NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_line_scene_ordinal UNIQUE (scene_id, ordinal),
	CONSTRAINT ck_line_ordinal CHECK (ordinal >= 0),
	CONSTRAINT ck_line_span CHECK (start_offset >= 0 AND end_offset >= start_offset),
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE,
	FOREIGN KEY(scene_id) REFERENCES scenes (id) ON DELETE CASCADE,
	FOREIGN KEY(beat_id) REFERENCES story_beats (id) ON DELETE CASCADE
);
CREATE TABLE document_extractions (
	id VARCHAR(36) NOT NULL,
	project_id VARCHAR(36) NOT NULL,
	source_document_id VARCHAR(36) NOT NULL,
	revision INTEGER NOT NULL,
	supersedes_extraction_id VARCHAR(36),
	status VARCHAR(24) NOT NULL,
	format VARCHAR(16) NOT NULL,
	extractor_name VARCHAR(80) NOT NULL,
	extractor_version VARCHAR(40) NOT NULL,
	input_sha256 VARCHAR(64) NOT NULL,
	text_sha256 VARCHAR(64),
	character_count INTEGER,
	page_count INTEGER,
	encoding VARCHAR(24),
	newline_style VARCHAR(24),
	exact_text TEXT,
	text_storage_key VARCHAR(512),
	manifest_json TEXT NOT NULL,
	sections_json TEXT NOT NULL,
	source_mappings_json TEXT NOT NULL,
	evidence_fingerprint VARCHAR(64) NOT NULL,
	warnings_json TEXT NOT NULL,
	created_at VARCHAR(32) NOT NULL,
	updated_at VARCHAR(32) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_extraction_source_revision UNIQUE (source_document_id, revision),
	CONSTRAINT ck_extraction_revision CHECK (revision >= 1),
	CONSTRAINT ck_extraction_status CHECK (status IN ('pending', 'running', 'complete', 'partial', 'failed')),
	CONSTRAINT ck_extraction_character_count CHECK (character_count IS NULL OR character_count >= 0),
	CONSTRAINT ck_extraction_page_count CHECK (page_count IS NULL OR page_count >= 0),
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE,
	FOREIGN KEY(source_document_id) REFERENCES source_documents (id) ON DELETE RESTRICT,
	FOREIGN KEY(supersedes_extraction_id) REFERENCES document_extractions (id) ON DELETE RESTRICT
);
CREATE TABLE human_corrections (
	id VARCHAR(36) NOT NULL,
	project_id VARCHAR(36) NOT NULL,
	line_id VARCHAR(36) NOT NULL,
	attribution_id VARCHAR(36) NOT NULL,
	previous_value_fingerprint VARCHAR(64) NOT NULL,
	previous_character_id VARCHAR(36),
	corrected_character_id VARCHAR(36),
	reason VARCHAR(500) NOT NULL,
	actor_id VARCHAR(80) NOT NULL,
	line_revision INTEGER NOT NULL,
	recorded_at VARCHAR(32) NOT NULL,
	supersedes_correction_id VARCHAR(36),
	PRIMARY KEY (id),
	CONSTRAINT uq_correction_line_revision UNIQUE (line_id, line_revision),
	CONSTRAINT ck_correction_line_revision CHECK (line_revision >= 2),
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE,
	FOREIGN KEY(line_id) REFERENCES dialogue_lines (id) ON DELETE RESTRICT,
	FOREIGN KEY(attribution_id) REFERENCES dialogue_attributions (id) ON DELETE RESTRICT,
	FOREIGN KEY(supersedes_correction_id) REFERENCES human_corrections (id) ON DELETE RESTRICT
);
CREATE TABLE idempotency_records (
	scope VARCHAR(80) NOT NULL,
	"key" VARCHAR(160) NOT NULL,
	request_hash VARCHAR(64) NOT NULL,
	resource_id VARCHAR(36) NOT NULL,
	created_at VARCHAR(32) NOT NULL,
	PRIMARY KEY (scope, "key")
);
CREATE TABLE import_reviews (
	id VARCHAR(36) NOT NULL,
	review_id VARCHAR(36) NOT NULL,
	project_id VARCHAR(36) NOT NULL,
	source_document_id VARCHAR(36) NOT NULL,
	extraction_id VARCHAR(36) NOT NULL,
	candidate_story_id VARCHAR(36) NOT NULL,
	revision INTEGER NOT NULL,
	state VARCHAR(24) NOT NULL,
	evidence_fingerprint VARCHAR(64) NOT NULL,
	preview_text TEXT NOT NULL,
	preview_truncated BOOLEAN NOT NULL,
	warnings_json TEXT NOT NULL,
	warning_acknowledgements_json TEXT NOT NULL,
	provenance_json TEXT NOT NULL,
	decision_id VARCHAR(36),
	decision_rationale VARCHAR(2000),
	reason VARCHAR(2000),
	actor_id VARCHAR(80),
	idempotency_key VARCHAR(160),
	decided_at VARCHAR(32),
	supersedes_record_id VARCHAR(36),
	created_at VARCHAR(32) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_import_review_revision UNIQUE (review_id, revision),
	CONSTRAINT uq_import_review_idempotency UNIQUE (review_id, idempotency_key),
	CONSTRAINT ck_import_review_revision CHECK (revision >= 1),
	CONSTRAINT ck_import_review_state CHECK (state IN ('pending', 'approved', 'changes_requested', 'rejected', 'invalidated')),
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE,
	FOREIGN KEY(source_document_id) REFERENCES source_documents (id) ON DELETE RESTRICT,
	FOREIGN KEY(extraction_id) REFERENCES document_extractions (id) ON DELETE RESTRICT,
	FOREIGN KEY(supersedes_record_id) REFERENCES import_reviews (id) ON DELETE RESTRICT
);
CREATE TABLE imported_stories (
	id VARCHAR(36) NOT NULL,
	project_id VARCHAR(36) NOT NULL,
	source_document_id VARCHAR(36) NOT NULL,
	extraction_id VARCHAR(36) NOT NULL,
	extraction_revision INTEGER NOT NULL,
	title VARCHAR(255) NOT NULL,
	exact_text TEXT NOT NULL,
	content_fingerprint VARCHAR(64) NOT NULL,
	imported_at VARCHAR(32) NOT NULL,
	revision INTEGER NOT NULL,
	provenance_json TEXT NOT NULL,
	warnings_json TEXT NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_story_revision CHECK (revision >= 1),
	CONSTRAINT ck_story_extraction_revision CHECK (extraction_revision >= 1),
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE,
	FOREIGN KEY(source_document_id) REFERENCES source_documents (id) ON DELETE RESTRICT,
	UNIQUE (extraction_id),
	FOREIGN KEY(extraction_id) REFERENCES document_extractions (id) ON DELETE RESTRICT
);
CREATE TABLE job_attempts (
	job_id VARCHAR(36) NOT NULL,
	number INTEGER NOT NULL,
	worker_instance_id VARCHAR(36),
	started_at VARCHAR(32),
	ended_at VARCHAR(32),
	outcome VARCHAR(32),
	error_code VARCHAR(80),
	error_message VARCHAR(300),
	producer_version VARCHAR(40) NOT NULL,
	PRIMARY KEY (job_id, number),
	CONSTRAINT ck_attempt_number CHECK (number >= 1),
	FOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE CASCADE
);
CREATE TABLE job_checkpoints (
	job_id VARCHAR(36) NOT NULL,
	attempt INTEGER NOT NULL,
	sequence INTEGER NOT NULL,
	checkpoint_type VARCHAR(40) NOT NULL,
	schema_version INTEGER NOT NULL,
	input_revision INTEGER NOT NULL,
	input_fingerprint VARCHAR(64) NOT NULL,
	producer_version VARCHAR(40) NOT NULL,
	payload_json TEXT NOT NULL,
	payload_sha256 VARCHAR(64) NOT NULL,
	created_at VARCHAR(32) NOT NULL,
	PRIMARY KEY (job_id, attempt),
	CONSTRAINT ck_checkpoint_attempt CHECK (attempt >= 1),
	CONSTRAINT ck_checkpoint_sequence CHECK (sequence >= 1),
	CONSTRAINT ck_checkpoint_schema CHECK (schema_version >= 1),
	FOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE CASCADE
);
CREATE TABLE job_events (
	job_id VARCHAR(36) NOT NULL,
	sequence INTEGER NOT NULL,
	attempt INTEGER NOT NULL,
	type VARCHAR(32) NOT NULL,
	state VARCHAR(24),
	stage VARCHAR(80),
	progress INTEGER,
	completed_units INTEGER,
	total_units INTEGER,
	warning_json TEXT,
	error_code VARCHAR(80),
	error_message VARCHAR(300),
	error_retryable BOOLEAN,
	created_at VARCHAR(32) NOT NULL,
	PRIMARY KEY (job_id, sequence),
	CONSTRAINT ck_event_sequence CHECK (sequence >= 1),
	CONSTRAINT ck_event_attempt CHECK (attempt >= 1),
	CONSTRAINT ck_event_progress CHECK (progress IS NULL OR (progress >= 0 AND progress <= 1000000)),
	FOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE CASCADE
);
CREATE TABLE jobs (
	id VARCHAR(36) NOT NULL,
	project_id VARCHAR(36) NOT NULL,
	type VARCHAR(40) NOT NULL,
	state VARCHAR(24) NOT NULL,
	input_revision INTEGER NOT NULL,
	input_fingerprint VARCHAR(64) NOT NULL,
	target_type VARCHAR(40) NOT NULL,
	target_id VARCHAR(36),
	payload_json TEXT NOT NULL,
	current_attempt INTEGER NOT NULL,
	stage VARCHAR(80) NOT NULL,
	progress INTEGER NOT NULL,
	checkpoint_available BOOLEAN NOT NULL,
	cancellation_requested BOOLEAN NOT NULL,
	resume_requested BOOLEAN NOT NULL,
	warnings_json TEXT NOT NULL,
	error_code VARCHAR(80),
	error_message VARCHAR(300),
	error_retryable BOOLEAN,
	created_at VARCHAR(32) NOT NULL,
	updated_at VARCHAR(32) NOT NULL,
	terminal_at VARCHAR(32),
	PRIMARY KEY (id),
	CONSTRAINT ck_job_input_revision CHECK (input_revision >= 1),
	CONSTRAINT ck_job_attempt CHECK (current_attempt >= 1),
	CONSTRAINT ck_job_progress CHECK (progress >= 0 AND progress <= 1000000),
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE
);
CREATE TABLE parser_executions (
	id VARCHAR(36) NOT NULL,
	project_id VARCHAR(36) NOT NULL,
	source_document_id VARCHAR(36) NOT NULL,
	extraction_id VARCHAR(36) NOT NULL,
	job_id VARCHAR(36),
	attempt INTEGER NOT NULL,
	parser_name VARCHAR(80) NOT NULL,
	parser_version VARCHAR(40) NOT NULL,
	outcome VARCHAR(24) NOT NULL,
	input_sha256 VARCHAR(64) NOT NULL,
	limits_fingerprint VARCHAR(64) NOT NULL,
	output_text_sha256 VARCHAR(64),
	manifest_json TEXT NOT NULL,
	sections_json TEXT NOT NULL,
	source_mappings_json TEXT NOT NULL,
	warnings_json TEXT NOT NULL,
	error_code VARCHAR(80),
	error_message VARCHAR(300),
	error_retryable BOOLEAN,
	started_at VARCHAR(32) NOT NULL,
	finished_at VARCHAR(32) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_parser_job_attempt UNIQUE (job_id, attempt),
	CONSTRAINT ck_parser_attempt CHECK (attempt >= 1),
	CONSTRAINT ck_parser_outcome CHECK (outcome IN ('succeeded', 'partial', 'failed', 'cancelled', 'interrupted')),
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE,
	FOREIGN KEY(source_document_id) REFERENCES source_documents (id) ON DELETE RESTRICT,
	FOREIGN KEY(extraction_id) REFERENCES document_extractions (id) ON DELETE RESTRICT,
	FOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE RESTRICT
);
CREATE TABLE production_roles (
	id VARCHAR(36) NOT NULL,
	project_id VARCHAR(36) NOT NULL,
	casting_run_id VARCHAR(36) NOT NULL,
	ordinal INTEGER NOT NULL,
	role_type VARCHAR(40) NOT NULL,
	phase2_entity_id VARCHAR(36),
	character_id VARCHAR(128),
	role_importance VARCHAR(16),
	effective_display_label VARCHAR(200) NOT NULL,
	analysis_run_id VARCHAR(36) NOT NULL,
	analysis_snapshot_id VARCHAR(36) NOT NULL,
	dialogue_line_count INTEGER NOT NULL,
	narration_span_count INTEGER NOT NULL,
	approximate_word_count INTEGER NOT NULL,
	chapter_range_json TEXT NOT NULL,
	scene_range_json TEXT NOT NULL,
	language_requirements_json TEXT NOT NULL,
	performance_requirements_json TEXT NOT NULL,
	warnings_json TEXT NOT NULL,
	provenance_json TEXT NOT NULL,
	status VARCHAR(24) NOT NULL,
	role_fingerprint VARCHAR(64) NOT NULL,
	created_at VARCHAR(32) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_production_role_run_ordinal UNIQUE (casting_run_id, ordinal),
	CONSTRAINT ck_production_role_ordinal CHECK (ordinal >= 0),
	CONSTRAINT ck_production_role_type CHECK (role_type IN ('primary_narrator', 'secondary_narrator', 'named_character', 'unresolved_speaker', 'group_or_crowd', 'quoted_document_or_announcement', 'internal_thought', 'custom')),
	CONSTRAINT ck_production_role_importance CHECK (role_importance IS NULL OR role_importance IN ('major', 'supporting', 'minor', 'unresolved')),
	CONSTRAINT ck_production_role_workload CHECK (dialogue_line_count >= 0 AND narration_span_count >= 0 AND approximate_word_count >= 0),
	CONSTRAINT ck_production_role_status CHECK (status IN ('active', 'unresolved', 'intentionally_uncast', 'invalidated')),
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE,
	FOREIGN KEY(casting_run_id) REFERENCES casting_runs (id) ON DELETE CASCADE,
	FOREIGN KEY(analysis_run_id) REFERENCES analysis_runs (id) ON DELETE RESTRICT,
	FOREIGN KEY(analysis_snapshot_id) REFERENCES analysis_snapshots (id) ON DELETE RESTRICT
);
CREATE TABLE projects (
	id VARCHAR(36) NOT NULL,
	name VARCHAR(200) NOT NULL,
	status VARCHAR(24) NOT NULL,
	revision INTEGER NOT NULL,
	story_id VARCHAR(36),
	created_at VARCHAR(32) NOT NULL,
	updated_at VARCHAR(32) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_projects_revision CHECK (revision >= 1)
);
CREATE TABLE scenes (
	id VARCHAR(36) NOT NULL,
	project_id VARCHAR(36) NOT NULL,
	chapter_id VARCHAR(36) NOT NULL,
	ordinal INTEGER NOT NULL,
	heading VARCHAR(255),
	location VARCHAR(255),
	mood VARCHAR(120),
	start_offset INTEGER NOT NULL,
	end_offset INTEGER NOT NULL,
	revision INTEGER NOT NULL,
	confidence_json TEXT NOT NULL,
	warnings_json TEXT NOT NULL,
	provenance_json TEXT NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_scene_chapter_ordinal UNIQUE (chapter_id, ordinal),
	CONSTRAINT ck_scene_ordinal CHECK (ordinal >= 0),
	CONSTRAINT ck_scene_span CHECK (start_offset >= 0 AND end_offset >= start_offset),
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE,
	FOREIGN KEY(chapter_id) REFERENCES chapters (id) ON DELETE CASCADE
);
CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL, service_version TEXT NOT NULL);
INSERT INTO "schema_migrations" VALUES(1,'2026-03-09T00:00:00.000Z','0.1.0');
INSERT INTO "schema_migrations" VALUES(2,'2026-03-09T00:00:00.000Z','0.1.0');
INSERT INTO "schema_migrations" VALUES(3,'2026-03-09T00:00:00.000Z','0.1.0');
INSERT INTO "schema_migrations" VALUES(4,'2026-03-09T00:00:00.000Z','0.1.0');
CREATE TABLE source_documents (
	id VARCHAR(36) NOT NULL,
	project_id VARCHAR(36) NOT NULL,
	display_name VARCHAR(255) NOT NULL,
	media_type VARCHAR(80) NOT NULL,
	declared_format VARCHAR(16) NOT NULL,
	content_sha256 VARCHAR(64) NOT NULL,
	text_sha256 VARCHAR(64),
	byte_length INTEGER NOT NULL,
	encoding VARCHAR(24),
	newline_style VARCHAR(24),
	storage_key VARCHAR(512) NOT NULL,
	imported_at VARCHAR(32) NOT NULL,
	revision INTEGER NOT NULL,
	source_revision INTEGER NOT NULL,
	supersedes_document_id VARCHAR(36),
	extraction_status VARCHAR(24) NOT NULL,
	provenance_json TEXT NOT NULL,
	warnings_json TEXT NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_source_project_source_revision UNIQUE (project_id, source_revision),
	CONSTRAINT ck_source_byte_length CHECK (byte_length >= 0),
	CONSTRAINT ck_source_revision CHECK (revision >= 1),
	CONSTRAINT ck_source_logical_revision CHECK (source_revision >= 1),
	CONSTRAINT ck_source_extraction_status CHECK (extraction_status IN ('pending', 'running', 'complete', 'partial', 'failed')),
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE,
	FOREIGN KEY(supersedes_document_id) REFERENCES source_documents (id) ON DELETE RESTRICT
);
CREATE TABLE story_beats (
	id VARCHAR(36) NOT NULL,
	project_id VARCHAR(36) NOT NULL,
	scene_id VARCHAR(36) NOT NULL,
	ordinal INTEGER NOT NULL,
	kind VARCHAR(24) NOT NULL,
	start_offset INTEGER NOT NULL,
	end_offset INTEGER NOT NULL,
	summary VARCHAR(280),
	dialogue_line_id VARCHAR(36),
	revision INTEGER NOT NULL,
	provenance_json TEXT NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_beat_scene_ordinal UNIQUE (scene_id, ordinal),
	CONSTRAINT ck_beat_ordinal CHECK (ordinal >= 0),
	CONSTRAINT ck_beat_span CHECK (start_offset >= 0 AND end_offset >= start_offset),
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE,
	FOREIGN KEY(scene_id) REFERENCES scenes (id) ON DELETE CASCADE
);
CREATE TABLE voice_catalog_revisions (
	id VARCHAR(36) NOT NULL,
	catalog_id VARCHAR(80) NOT NULL,
	revision INTEGER NOT NULL,
	semantic_version VARCHAR(40) NOT NULL,
	catalog_fingerprint VARCHAR(64) NOT NULL,
	provider_set_fingerprint VARCHAR(64) NOT NULL,
	rights_policy_version VARCHAR(40) NOT NULL,
	source_kind VARCHAR(32) NOT NULL,
	active BOOLEAN NOT NULL,
	provenance_json TEXT NOT NULL,
	created_at VARCHAR(32) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_voice_catalog_revision UNIQUE (catalog_id, revision),
	CONSTRAINT ck_voice_catalog_revision CHECK (revision >= 1),
	CONSTRAINT ck_voice_catalog_source_kind CHECK (source_kind IN ('development_fixture', 'local_static')),
	UNIQUE (catalog_fingerprint)
);
CREATE TABLE voice_model_descriptors (
	id VARCHAR(36) NOT NULL,
	catalog_revision_id VARCHAR(36) NOT NULL,
	provider_descriptor_id VARCHAR(36) NOT NULL,
	model_id VARCHAR(80) NOT NULL,
	model_name VARCHAR(120) NOT NULL,
	model_version VARCHAR(40) NOT NULL,
	supported_languages_json TEXT NOT NULL,
	supported_locales_json TEXT NOT NULL,
	expressive_controls_json TEXT NOT NULL,
	speaking_rate_controls_json TEXT NOT NULL,
	pitch_style_controls_json TEXT NOT NULL,
	output_capabilities_json TEXT NOT NULL,
	execution_classification VARCHAR(24) NOT NULL,
	rights_classification VARCHAR(32) NOT NULL,
	availability VARCHAR(24) NOT NULL,
	deprecated BOOLEAN NOT NULL,
	descriptor_fingerprint VARCHAR(64) NOT NULL,
	provenance_json TEXT NOT NULL,
	created_at VARCHAR(32) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_voice_model_catalog_model UNIQUE (catalog_revision_id, model_id),
	CONSTRAINT ck_voice_model_execution CHECK (execution_classification IN ('local', 'remote_disabled', 'fixture')),
	CONSTRAINT ck_voice_model_availability CHECK (availability IN ('available', 'unavailable', 'disabled', 'unknown')),
	FOREIGN KEY(catalog_revision_id) REFERENCES voice_catalog_revisions (id) ON DELETE CASCADE,
	FOREIGN KEY(provider_descriptor_id) REFERENCES voice_provider_descriptors (id) ON DELETE CASCADE
);
CREATE TABLE voice_profiles (
	id VARCHAR(36) NOT NULL,
	profile_id VARCHAR(80) NOT NULL,
	revision INTEGER NOT NULL,
	profile_version VARCHAR(80) NOT NULL,
	catalog_revision_id VARCHAR(36) NOT NULL,
	provider_descriptor_id VARCHAR(36) NOT NULL,
	model_descriptor_id VARCHAR(36) NOT NULL,
	provider_voice_id VARCHAR(120) NOT NULL,
	display_label VARCHAR(160) NOT NULL,
	language VARCHAR(16) NOT NULL,
	locale VARCHAR(32) NOT NULL,
	declared_accent_dialect VARCHAR(120),
	declared_age_presentation_json TEXT NOT NULL,
	declared_vocal_presentation VARCHAR(80) NOT NULL,
	vocal_weight_texture_json TEXT NOT NULL,
	pitch_range_classification VARCHAR(40) NOT NULL,
	speaking_rate_range_json TEXT NOT NULL,
	energy_range_json TEXT NOT NULL,
	expressive_range_json TEXT NOT NULL,
	narration_suitability VARCHAR(24) NOT NULL,
	dialogue_suitability VARCHAR(24) NOT NULL,
	long_form_suitability VARCHAR(24) NOT NULL,
	character_role_suitability_json TEXT NOT NULL,
	known_limitations_json TEXT NOT NULL,
	rights_state VARCHAR(16) NOT NULL,
	consent_status VARCHAR(24) NOT NULL,
	license_scope VARCHAR(120) NOT NULL,
	commercial_use_status VARCHAR(24) NOT NULL,
	attribution_required BOOLEAN NOT NULL,
	voice_cloning_classification VARCHAR(32) NOT NULL,
	state VARCHAR(16) NOT NULL,
	profile_fingerprint VARCHAR(64) NOT NULL,
	provenance_json TEXT NOT NULL,
	created_at VARCHAR(32) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_voice_profile_catalog_profile UNIQUE (catalog_revision_id, profile_id),
	CONSTRAINT uq_voice_profile_catalog_provider_voice UNIQUE (catalog_revision_id, provider_descriptor_id, model_descriptor_id, provider_voice_id),
	CONSTRAINT ck_voice_profile_revision CHECK (revision >= 1),
	CONSTRAINT ck_voice_profile_narration_suitability CHECK (narration_suitability IN ('preferred', 'suitable', 'limited', 'unsuitable', 'unknown')),
	CONSTRAINT ck_voice_profile_dialogue_suitability CHECK (dialogue_suitability IN ('preferred', 'suitable', 'limited', 'unsuitable', 'unknown')),
	CONSTRAINT ck_voice_profile_long_form_suitability CHECK (long_form_suitability IN ('preferred', 'suitable', 'limited', 'unsuitable', 'unknown')),
	CONSTRAINT ck_voice_profile_rights_state CHECK (rights_state IN ('verified', 'restricted', 'unknown', 'prohibited')),
	CONSTRAINT ck_voice_profile_consent_status CHECK (consent_status IN ('not_applicable_synthetic_fixture', 'verified', 'restricted', 'missing', 'unknown', 'prohibited')),
	CONSTRAINT ck_voice_profile_commercial_use CHECK (commercial_use_status IN ('permitted', 'restricted', 'unknown', 'prohibited')),
	CONSTRAINT ck_voice_profile_state CHECK (state IN ('active', 'unavailable', 'deprecated', 'blocked')),
	FOREIGN KEY(catalog_revision_id) REFERENCES voice_catalog_revisions (id) ON DELETE CASCADE,
	FOREIGN KEY(provider_descriptor_id) REFERENCES voice_provider_descriptors (id) ON DELETE CASCADE,
	FOREIGN KEY(model_descriptor_id) REFERENCES voice_model_descriptors (id) ON DELETE CASCADE
);
CREATE TABLE voice_provider_descriptors (
	id VARCHAR(36) NOT NULL,
	catalog_revision_id VARCHAR(36) NOT NULL,
	provider_id VARCHAR(80) NOT NULL,
	provider_version VARCHAR(40) NOT NULL,
	provider_type VARCHAR(32) NOT NULL,
	runtime_availability VARCHAR(24) NOT NULL,
	catalog_availability VARCHAR(24) NOT NULL,
	synthesis_implemented BOOLEAN NOT NULL,
	network_required BOOLEAN NOT NULL,
	credentials_required BOOLEAN NOT NULL,
	supported_operating_systems_json TEXT NOT NULL,
	supported_languages_json TEXT NOT NULL,
	output_capabilities_json TEXT NOT NULL,
	rights_metadata_capabilities_json TEXT NOT NULL,
	health_status VARCHAR(24) NOT NULL,
	descriptor_fingerprint VARCHAR(64) NOT NULL,
	provenance_json TEXT NOT NULL,
	created_at VARCHAR(32) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_voice_provider_catalog_provider UNIQUE (catalog_revision_id, provider_id),
	CONSTRAINT ck_voice_provider_type CHECK (provider_type IN ('local', 'cloud_capable_disabled', 'development_fixture')),
	CONSTRAINT ck_voice_provider_runtime_availability CHECK (runtime_availability IN ('available', 'unavailable', 'disabled', 'unknown')),
	CONSTRAINT ck_voice_provider_catalog_availability CHECK (catalog_availability IN ('available', 'unavailable', 'unknown')),
	CONSTRAINT ck_voice_provider_health_status CHECK (health_status IN ('healthy', 'degraded', 'unavailable', 'disabled', 'unknown')),
	FOREIGN KEY(catalog_revision_id) REFERENCES voice_catalog_revisions (id) ON DELETE CASCADE
);
CREATE TABLE voice_rights_records (
	id VARCHAR(36) NOT NULL,
	rights_record_id VARCHAR(128) NOT NULL,
	voice_profile_record_id VARCHAR(36) NOT NULL,
	provider_descriptor_id VARCHAR(36) NOT NULL,
	revision INTEGER NOT NULL,
	rights_state VARCHAR(16) NOT NULL,
	license_identifier VARCHAR(160),
	rights_basis VARCHAR(500) NOT NULL,
	license_scope VARCHAR(240) NOT NULL,
	commercial_use_status VARCHAR(24) NOT NULL,
	attribution_required BOOLEAN NOT NULL,
	distribution_limitations_json TEXT NOT NULL,
	voice_cloning_status VARCHAR(32) NOT NULL,
	consent_status VARCHAR(24) NOT NULL,
	effective_date VARCHAR(32),
	expiration_date VARCHAR(32),
	evidence_reference VARCHAR(500) NOT NULL,
	human_verification_status VARCHAR(24) NOT NULL,
	rights_fingerprint VARCHAR(64) NOT NULL,
	provenance_json TEXT NOT NULL,
	created_at VARCHAR(32) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_voice_rights_profile_revision UNIQUE (voice_profile_record_id, revision),
	CONSTRAINT uq_voice_rights_profile_external_id UNIQUE (voice_profile_record_id, rights_record_id),
	CONSTRAINT ck_voice_rights_revision CHECK (revision >= 1),
	CONSTRAINT ck_voice_rights_state CHECK (rights_state IN ('verified', 'restricted', 'unknown', 'prohibited')),
	CONSTRAINT ck_voice_rights_commercial_use CHECK (commercial_use_status IN ('permitted', 'restricted', 'unknown', 'prohibited')),
	CONSTRAINT ck_voice_rights_consent CHECK (consent_status IN ('not_applicable_synthetic_fixture', 'verified', 'restricted', 'missing', 'unknown', 'prohibited')),
	CONSTRAINT ck_voice_rights_human_verification CHECK (human_verification_status IN ('verified', 'not_required_fixture', 'pending', 'rejected')),
	CONSTRAINT ck_voice_rights_cloning_status CHECK (voice_cloning_status IN ('not_applicable_synthetic_fixture', 'not_permitted', 'permitted_with_consent', 'unknown', 'prohibited')),
	FOREIGN KEY(voice_profile_record_id) REFERENCES voice_profiles (id) ON DELETE CASCADE,
	FOREIGN KEY(provider_descriptor_id) REFERENCES voice_provider_descriptors (id) ON DELETE RESTRICT
);
CREATE INDEX ix_voice_catalog_active_created ON voice_catalog_revisions (active, created_at, catalog_id, revision, id);
CREATE INDEX ix_casting_profile_identity ON casting_profiles (profile_id, semantic_version, id);
CREATE INDEX ix_source_documents_project_id ON source_documents (project_id);
CREATE INDEX ix_source_project_imported ON source_documents (project_id, imported_at, id);
CREATE INDEX ix_source_project_hash ON source_documents (project_id, content_sha256);
CREATE INDEX ix_job_target ON jobs (target_type, target_id, created_at, id);
CREATE INDEX ix_job_queue ON jobs (state, created_at, id);
CREATE INDEX ix_job_project_created ON jobs (project_id, created_at, id);
CREATE INDEX ix_jobs_project_id ON jobs (project_id);
CREATE INDEX ix_voice_provider_catalog_order ON voice_provider_descriptors (catalog_revision_id, provider_id, id);
CREATE INDEX ix_voice_provider_descriptors_catalog_revision_id ON voice_provider_descriptors (catalog_revision_id);
CREATE INDEX ix_document_extractions_source_document_id ON document_extractions (source_document_id);
CREATE INDEX ix_document_extractions_project_id ON document_extractions (project_id);
CREATE INDEX ix_extraction_project_source_created ON document_extractions (project_id, source_document_id, created_at, id);
CREATE INDEX ix_event_job_attempt_sequence ON job_events (job_id, attempt, sequence);
CREATE INDEX ix_voice_model_descriptors_catalog_revision_id ON voice_model_descriptors (catalog_revision_id);
CREATE INDEX ix_voice_model_catalog_provider_order ON voice_model_descriptors (catalog_revision_id, provider_descriptor_id, model_id, id);
CREATE INDEX ix_voice_model_descriptors_provider_descriptor_id ON voice_model_descriptors (provider_descriptor_id);
CREATE INDEX ix_story_project_imported ON imported_stories (project_id, imported_at, id);
CREATE INDEX ix_imported_stories_project_id ON imported_stories (project_id);
CREATE INDEX ix_imported_stories_source_document_id ON imported_stories (source_document_id);
CREATE INDEX ix_import_reviews_extraction_id ON import_reviews (extraction_id);
CREATE INDEX ix_import_reviews_project_id ON import_reviews (project_id);
CREATE INDEX ix_import_review_project_created ON import_reviews (project_id, created_at, review_id, revision);
CREATE INDEX ix_import_reviews_review_id ON import_reviews (review_id);
CREATE INDEX ix_import_reviews_source_document_id ON import_reviews (source_document_id);
CREATE INDEX ix_parser_executions_project_id ON parser_executions (project_id);
CREATE INDEX ix_parser_executions_extraction_id ON parser_executions (extraction_id);
CREATE INDEX ix_parser_extraction_attempt ON parser_executions (extraction_id, attempt, id);
CREATE INDEX ix_parser_executions_job_id ON parser_executions (job_id);
CREATE INDEX ix_parser_executions_source_document_id ON parser_executions (source_document_id);
CREATE INDEX ix_voice_profile_catalog_state_label ON voice_profiles (catalog_revision_id, state, display_label, profile_id, id);
CREATE INDEX ix_voice_profiles_catalog_revision_id ON voice_profiles (catalog_revision_id);
CREATE INDEX ix_voice_profile_catalog_language ON voice_profiles (catalog_revision_id, language, locale, state, id);
CREATE INDEX ix_voice_profiles_provider_descriptor_id ON voice_profiles (provider_descriptor_id);
CREATE INDEX ix_voice_profiles_model_descriptor_id ON voice_profiles (model_descriptor_id);
CREATE INDEX ix_chapters_story_id ON chapters (story_id);
CREATE INDEX ix_chapters_project_id ON chapters (project_id);
CREATE INDEX ix_chapter_project_story_order ON chapters (project_id, story_id, ordinal, id);
CREATE INDEX ix_character_project_story_name ON characters (project_id, story_id, normalized_name);
CREATE INDEX ix_characters_story_id ON characters (story_id);
CREATE INDEX ix_characters_project_id ON characters (project_id);
CREATE INDEX ix_analysis_run_project_created ON analysis_runs (project_id, created_at, id);
CREATE INDEX ix_analysis_runs_project_id ON analysis_runs (project_id);
CREATE INDEX ix_analysis_runs_extraction_id ON analysis_runs (extraction_id);
CREATE INDEX ix_analysis_runs_import_review_record_id ON analysis_runs (import_review_record_id);
CREATE INDEX ix_analysis_run_project_extraction ON analysis_runs (project_id, extraction_id, extraction_revision, id);
CREATE INDEX ix_analysis_runs_story_id ON analysis_runs (story_id);
CREATE INDEX ix_analysis_runs_source_document_id ON analysis_runs (source_document_id);
CREATE UNIQUE INDEX ix_analysis_runs_job_id ON analysis_runs (job_id);
CREATE INDEX ix_voice_rights_profile_revision ON voice_rights_records (voice_profile_record_id, revision, id);
CREATE INDEX ix_voice_rights_records_provider_descriptor_id ON voice_rights_records (provider_descriptor_id);
CREATE INDEX ix_voice_rights_records_voice_profile_record_id ON voice_rights_records (voice_profile_record_id);
CREATE INDEX ix_scenes_project_id ON scenes (project_id);
CREATE INDEX ix_scenes_chapter_id ON scenes (chapter_id);
CREATE INDEX ix_scene_project_chapter_order ON scenes (project_id, chapter_id, ordinal, id);
CREATE INDEX ix_analysis_executions_run_id ON analysis_executions (run_id);
CREATE INDEX ix_analysis_executions_job_id ON analysis_executions (job_id);
CREATE INDEX ix_analysis_execution_project_run_attempt ON analysis_executions (project_id, run_id, attempt, id);
CREATE INDEX ix_analysis_executions_project_id ON analysis_executions (project_id);
CREATE INDEX ix_analysis_stage_checkpoints_project_id ON analysis_stage_checkpoints (project_id);
CREATE INDEX ix_analysis_stage_checkpoints_job_id ON analysis_stage_checkpoints (job_id);
CREATE INDEX ix_analysis_stage_checkpoints_run_id ON analysis_stage_checkpoints (run_id);
CREATE INDEX ix_analysis_stage_checkpoint_project_run_attempt ON analysis_stage_checkpoints (project_id, run_id, attempt, ordinal, id);
CREATE INDEX ix_analysis_correction_project_target ON analysis_corrections (project_id, category, target_key, revision, id);
CREATE INDEX ix_analysis_corrections_run_id ON analysis_corrections (run_id);
CREATE INDEX ix_analysis_corrections_project_id ON analysis_corrections (project_id);
CREATE INDEX ix_analysis_correction_project_run_recorded ON analysis_corrections (project_id, run_id, recorded_at, id);
CREATE INDEX ix_story_beats_project_id ON story_beats (project_id);
CREATE INDEX ix_beat_project_scene_order ON story_beats (project_id, scene_id, ordinal, id);
CREATE INDEX ix_story_beats_scene_id ON story_beats (scene_id);
CREATE INDEX ix_analysis_snapshot_project_run_order ON analysis_snapshots (project_id, run_id, ordinal, id);
CREATE INDEX ix_analysis_snapshots_run_id ON analysis_snapshots (run_id);
CREATE INDEX ix_analysis_snapshots_project_id ON analysis_snapshots (project_id);
CREATE INDEX ix_analysis_snapshots_execution_id ON analysis_snapshots (execution_id);
CREATE INDEX ix_analysis_agent_project_run_order ON analysis_agent_executions (project_id, run_id, ordinal, id);
CREATE INDEX ix_analysis_agent_executions_run_id ON analysis_agent_executions (run_id);
CREATE INDEX ix_analysis_agent_executions_project_id ON analysis_agent_executions (project_id);
CREATE INDEX ix_analysis_agent_executions_execution_id ON analysis_agent_executions (execution_id);
CREATE INDEX ix_dialogue_lines_scene_id ON dialogue_lines (scene_id);
CREATE INDEX ix_dialogue_lines_project_id ON dialogue_lines (project_id);
CREATE INDEX ix_dialogue_lines_beat_id ON dialogue_lines (beat_id);
CREATE INDEX ix_line_project_scene_order ON dialogue_lines (project_id, scene_id, ordinal, id);
CREATE INDEX ix_analysis_entity_project_run_identity ON analysis_entities (project_id, run_id, collection, identity_key, id);
CREATE INDEX ix_analysis_entities_run_id ON analysis_entities (run_id);
CREATE INDEX ix_analysis_entities_project_id ON analysis_entities (project_id);
CREATE INDEX ix_analysis_entities_snapshot_id ON analysis_entities (snapshot_id);
CREATE INDEX ix_analysis_entity_project_run_collection_order ON analysis_entities (project_id, run_id, collection, ordinal, id);
CREATE INDEX ix_analysis_review_project_run_gate_revision ON analysis_review_decisions (project_id, run_id, gate_id, revision, id);
CREATE INDEX ix_analysis_review_decisions_project_id ON analysis_review_decisions (project_id);
CREATE INDEX ix_analysis_review_decisions_run_id ON analysis_review_decisions (run_id);
CREATE INDEX ix_analysis_review_decisions_snapshot_id ON analysis_review_decisions (snapshot_id);
CREATE INDEX ix_casting_runs_catalog_revision_id ON casting_runs (catalog_revision_id);
CREATE INDEX ix_casting_run_project_created ON casting_runs (project_id, created_at, id);
CREATE INDEX ix_casting_runs_source_document_id ON casting_runs (source_document_id);
CREATE INDEX ix_casting_runs_analysis_snapshot_id ON casting_runs (analysis_snapshot_id);
CREATE INDEX ix_casting_run_project_analysis ON casting_runs (project_id, analysis_run_id, analysis_snapshot_id, created_at, id);
CREATE INDEX ix_casting_runs_project_id ON casting_runs (project_id);
CREATE INDEX ix_casting_runs_extraction_id ON casting_runs (extraction_id);
CREATE UNIQUE INDEX ix_casting_runs_job_id ON casting_runs (job_id);
CREATE INDEX ix_casting_runs_analysis_run_id ON casting_runs (analysis_run_id);
CREATE INDEX ix_casting_runs_casting_profile_id ON casting_runs (casting_profile_id);
CREATE INDEX ix_dialogue_attributions_project_id ON dialogue_attributions (project_id);
CREATE INDEX ix_attribution_project_line ON dialogue_attributions (project_id, line_id);
CREATE INDEX ix_analysis_evidence_spans_run_id ON analysis_evidence_spans (run_id);
CREATE INDEX ix_analysis_evidence_project_run_entity_order ON analysis_evidence_spans (project_id, run_id, entity_id, ordinal, id);
CREATE INDEX ix_analysis_evidence_spans_entity_id ON analysis_evidence_spans (entity_id);
CREATE INDEX ix_analysis_evidence_spans_project_id ON analysis_evidence_spans (project_id);
CREATE INDEX ix_production_roles_project_id ON production_roles (project_id);
CREATE INDEX ix_production_role_project_type ON production_roles (project_id, casting_run_id, role_type, ordinal, id);
CREATE INDEX ix_production_roles_casting_run_id ON production_roles (casting_run_id);
CREATE INDEX ix_production_roles_analysis_run_id ON production_roles (analysis_run_id);
CREATE INDEX ix_production_role_project_run_order ON production_roles (project_id, casting_run_id, ordinal, id);
CREATE INDEX ix_production_roles_analysis_snapshot_id ON production_roles (analysis_snapshot_id);
CREATE INDEX ix_approved_cast_snapshot_project_run_revision ON approved_cast_snapshots (project_id, casting_run_id, revision, id);
CREATE INDEX ix_approved_cast_snapshots_casting_run_id ON approved_cast_snapshots (casting_run_id);
CREATE INDEX ix_approved_cast_snapshots_project_id ON approved_cast_snapshots (project_id);
CREATE INDEX ix_approved_cast_snapshots_catalog_revision_id ON approved_cast_snapshots (catalog_revision_id);
CREATE INDEX ix_human_corrections_attribution_id ON human_corrections (attribution_id);
CREATE INDEX ix_human_corrections_project_id ON human_corrections (project_id);
CREATE INDEX ix_human_corrections_line_id ON human_corrections (line_id);
CREATE INDEX ix_correction_project_line_time ON human_corrections (project_id, line_id, recorded_at, id);
CREATE INDEX ix_casting_candidates_project_id ON casting_candidates (project_id);
CREATE INDEX ix_casting_candidates_voice_profile_record_id ON casting_candidates (voice_profile_record_id);
CREATE INDEX ix_casting_candidate_project_run_role_order ON casting_candidates (project_id, casting_run_id, role_id, role_revision, ordinal, id);
CREATE INDEX ix_casting_candidates_casting_run_id ON casting_candidates (casting_run_id);
CREATE INDEX ix_casting_candidates_role_id ON casting_candidates (role_id);
CREATE INDEX ix_casting_candidate_role_score ON casting_candidates (role_id, role_revision, compatibility_status, compatibility_score, ordinal, id);
CREATE INDEX ix_casting_conflict_run_roles ON casting_conflicts (casting_run_id, primary_role_id, secondary_role_id, category, id);
CREATE INDEX ix_casting_conflicts_casting_run_id ON casting_conflicts (casting_run_id);
CREATE INDEX ix_casting_conflicts_primary_role_id ON casting_conflicts (primary_role_id);
CREATE INDEX ix_casting_conflicts_project_id ON casting_conflicts (project_id);
CREATE INDEX ix_casting_conflict_project_run_status ON casting_conflicts (project_id, casting_run_id, status, severity, id);
CREATE INDEX ix_casting_conflicts_secondary_role_id ON casting_conflicts (secondary_role_id);
CREATE INDEX ix_casting_conflicts_voice_profile_record_id ON casting_conflicts (voice_profile_record_id);
CREATE INDEX ix_casting_corrections_casting_run_id ON casting_corrections (casting_run_id);
CREATE INDEX ix_casting_correction_project_recorded ON casting_corrections (project_id, recorded_at, id);
CREATE INDEX ix_casting_correction_project_run_role_revision ON casting_corrections (project_id, casting_run_id, role_id, revision, id);
CREATE INDEX ix_casting_corrections_project_id ON casting_corrections (project_id);
CREATE INDEX ix_casting_corrections_role_id ON casting_corrections (role_id);
CREATE INDEX ix_casting_gate_reviews_casting_run_id ON casting_gate_reviews (casting_run_id);
CREATE INDEX ix_casting_gate_review_project_run_gate_revision ON casting_gate_reviews (project_id, casting_run_id, gate_id, revision, id);
CREATE INDEX ix_casting_gate_reviews_project_id ON casting_gate_reviews (project_id);
CREATE INDEX ix_casting_gate_reviews_cast_snapshot_id ON casting_gate_reviews (cast_snapshot_id);
CREATE INDEX ix_cast_assignments_casting_run_id ON cast_assignments (casting_run_id);
CREATE INDEX ix_cast_assignments_role_id ON cast_assignments (role_id);
CREATE INDEX ix_cast_assignment_project_run_role_revision ON cast_assignments (project_id, casting_run_id, role_id, revision, id);
CREATE INDEX ix_cast_assignments_voice_profile_record_id ON cast_assignments (voice_profile_record_id);
CREATE INDEX ix_cast_assignments_catalog_revision_id ON cast_assignments (catalog_revision_id);
CREATE INDEX ix_cast_assignments_project_id ON cast_assignments (project_id);
CREATE UNIQUE INDEX ix_cast_assignments_correction_id ON cast_assignments (correction_id);
CREATE INDEX ix_casting_gate_decision_project_run_gate_revision ON casting_gate_decisions (project_id, casting_run_id, gate_id, revision, id);
CREATE INDEX ix_casting_gate_decisions_casting_run_id ON casting_gate_decisions (casting_run_id);
CREATE INDEX ix_casting_gate_decisions_cast_snapshot_id ON casting_gate_decisions (cast_snapshot_id);
CREATE INDEX ix_casting_gate_decisions_project_id ON casting_gate_decisions (project_id);
CREATE INDEX ix_casting_gate_decisions_gate_review_id ON casting_gate_decisions (gate_review_id);
CREATE INDEX ix_cast_assignment_invalidations_project_id ON cast_assignment_invalidations (project_id);
CREATE UNIQUE INDEX ix_cast_assignment_invalidations_assignment_id ON cast_assignment_invalidations (assignment_id);
CREATE INDEX ix_cast_assignment_invalidations_casting_run_id ON cast_assignment_invalidations (casting_run_id);
CREATE INDEX ix_cast_assignment_invalidations_role_id ON cast_assignment_invalidations (role_id);
CREATE INDEX ix_cast_assignment_invalidation_project_run_role ON cast_assignment_invalidations (project_id, casting_run_id, role_id, created_at, id);
COMMIT;
PRAGMA user_version=4;
PRAGMA foreign_keys=ON;
