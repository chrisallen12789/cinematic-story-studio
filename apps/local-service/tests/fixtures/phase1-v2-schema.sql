BEGIN TRANSACTION;
CREATE TABLE chapters (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    project_id VARCHAR(36) NOT NULL,
    story_id VARCHAR(36) NOT NULL,
    ordinal INTEGER NOT NULL,
    title VARCHAR(255),
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL,
    revision INTEGER NOT NULL,
    provenance_json TEXT NOT NULL,
    CONSTRAINT uq_chapter_story_ordinal UNIQUE (story_id, ordinal),
    CONSTRAINT ck_chapter_ordinal CHECK (ordinal >= 0),
    CONSTRAINT ck_chapter_span CHECK (start_offset >= 0 AND end_offset >= start_offset),
    FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE,
    FOREIGN KEY(story_id) REFERENCES imported_stories (id) ON DELETE CASCADE
);
INSERT INTO "chapters" VALUES('chapter-1','project-1','story-2',0,'Second',0,14,1,'{"origin":"analysis"}');
CREATE TABLE characters (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
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
    CONSTRAINT uq_character_story_name UNIQUE (story_id, normalized_name),
    FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE,
    FOREIGN KEY(story_id) REFERENCES imported_stories (id) ON DELETE CASCADE
);
INSERT INTO "characters" VALUES('character-1','project-1','story-2','Narrator','narrator','[]','[]',1,'{"score":0.8}','[]','{"origin":"analysis"}');
CREATE TABLE dialogue_attributions (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    project_id VARCHAR(36) NOT NULL,
    line_id VARCHAR(36) NOT NULL UNIQUE,
    proposed_speaker_id VARCHAR(36),
    effective_speaker_id VARCHAR(36),
    effective_authority VARCHAR(24) NOT NULL,
    evidence_json TEXT NOT NULL,
    revision INTEGER NOT NULL,
    confidence_json TEXT NOT NULL,
    warnings_json TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    updated_at VARCHAR(32) NOT NULL,
    CONSTRAINT ck_attribution_revision CHECK (revision >= 1),
    FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE,
    FOREIGN KEY(line_id) REFERENCES dialogue_lines (id) ON DELETE CASCADE,
    FOREIGN KEY(proposed_speaker_id) REFERENCES characters (id) ON DELETE RESTRICT,
    FOREIGN KEY(effective_speaker_id) REFERENCES characters (id) ON DELETE RESTRICT
);
INSERT INTO "dialogue_attributions" VALUES('attribution-1','project-1','line-1',NULL,'character-1','human','[]',2,'{"score":1.0}','[]','{"origin":"human"}','2026-01-01T00:00:00Z');
CREATE TABLE dialogue_lines (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
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
    CONSTRAINT uq_line_scene_ordinal UNIQUE (scene_id, ordinal),
    CONSTRAINT ck_line_ordinal CHECK (ordinal >= 0),
    CONSTRAINT ck_line_span CHECK (start_offset >= 0 AND end_offset >= start_offset),
    FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE,
    FOREIGN KEY(scene_id) REFERENCES scenes (id) ON DELETE CASCADE,
    FOREIGN KEY(beat_id) REFERENCES story_beats (id) ON DELETE CASCADE
);
INSERT INTO "dialogue_lines" VALUES('line-1','project-1','scene-1','beat-1',0,9,13,'Text','dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd',2,'{"origin":"analysis"}');
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
INSERT INTO "document_extractions" VALUES('source-1','project-1','source-1',1,NULL,'complete','markdown','legacy_phase0_import','1.0.0','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',12,NULL,'utf-8','lf','# First
Text',NULL,'{"legacyPhase0":true,"schemaVersion":1,"warning":"parser limits were not recorded"}','[]','[]','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa','[]','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z');
INSERT INTO "document_extractions" VALUES('source-2','project-1','source-2',1,NULL,'complete','markdown','legacy_phase0_import','1.0.0','bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb','bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',14,NULL,'utf-8','crlf','# Second
Text',NULL,'{"legacyPhase0":true,"schemaVersion":1,"warning":"parser limits were not recorded"}','[]','[]','bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb','[]','2026-01-02T00:00:00Z','2026-01-02T00:00:00Z');
CREATE TABLE human_corrections (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
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
    CONSTRAINT uq_correction_line_revision UNIQUE (line_id, line_revision),
    CONSTRAINT ck_correction_line_revision CHECK (line_revision >= 2),
    FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE,
    FOREIGN KEY(line_id) REFERENCES dialogue_lines (id) ON DELETE RESTRICT,
    FOREIGN KEY(attribution_id) REFERENCES dialogue_attributions (id) ON DELETE RESTRICT,
    FOREIGN KEY(supersedes_correction_id) REFERENCES human_corrections (id) ON DELETE RESTRICT
);
INSERT INTO "human_corrections" VALUES('correction-1','project-1','line-1','attribution-1','cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc',NULL,'character-1','Synthetic human choice','local-human',2,'2026-01-01T00:00:00Z',NULL);
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
INSERT INTO "import_reviews" VALUES('story-1','story-1','project-1','source-1','source-1','story-1',1,'pending','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa','# First
Text',0,'[]','[]','{"origin":"migration","actorId":"schema-migrator@2"}',NULL,NULL,NULL,NULL,NULL,NULL,NULL,'2026-01-01T00:00:00Z');
INSERT INTO "import_reviews" VALUES('story-2','story-2','project-1','source-2','source-2','story-2',1,'pending','bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb','# Second
Text',0,'[]','[]','{"origin":"migration","actorId":"schema-migrator@2"}',NULL,NULL,NULL,NULL,NULL,NULL,NULL,'2026-01-02T00:00:00Z');
INSERT INTO "import_reviews" VALUES('review-story-2-r2','story-2','project-1','source-2','source-2','story-2',2,'approved','bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb','# Second
Text',0,'[]','[]','{"origin":"human","actorId":"local-human","inputEvidenceFingerprint":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}','decision-story-2-r2','Approved the deterministic synthetic extraction.','Synthetic import review approval.','local-human','fixture-import-review-approval','2026-01-02T00:01:00Z','story-2','2026-01-02T00:01:00Z');
CREATE TABLE "imported_stories" (id VARCHAR(36) NOT NULL PRIMARY KEY, project_id VARCHAR(36) NOT NULL, source_document_id VARCHAR(36) NOT NULL, extraction_id VARCHAR(36) NOT NULL, extraction_revision INTEGER NOT NULL, title VARCHAR(255) NOT NULL, exact_text TEXT NOT NULL, content_fingerprint VARCHAR(64) NOT NULL, imported_at VARCHAR(32) NOT NULL, revision INTEGER NOT NULL, provenance_json TEXT NOT NULL, warnings_json TEXT NOT NULL, CONSTRAINT uq_story_extraction UNIQUE (extraction_id), CONSTRAINT ck_story_revision CHECK (revision >= 1), CONSTRAINT ck_story_extraction_revision CHECK (extraction_revision >= 1), FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE, FOREIGN KEY(source_document_id) REFERENCES source_documents (id) ON DELETE RESTRICT, FOREIGN KEY(extraction_id) REFERENCES document_extractions (id) ON DELETE RESTRICT);
INSERT INTO "imported_stories" VALUES('story-1','project-1','source-1','source-1',1,'First','# First
Text','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa','2026-01-01T00:00:00Z',1,'{"origin":"import"}','[]');
INSERT INTO "imported_stories" VALUES('story-2','project-1','source-2','source-2',1,'Second','# Second
Text','bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb','2026-01-02T00:00:00Z',1,'{"origin":"import"}','[]');
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
    CONSTRAINT ck_event_progress CHECK (
        progress IS NULL OR (progress >= 0 AND progress <= 1000000)
    ),
    FOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE CASCADE
);
CREATE TABLE jobs (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    project_id VARCHAR(36) NOT NULL,
    type VARCHAR(40) NOT NULL,
    state VARCHAR(24) NOT NULL,
    input_revision INTEGER NOT NULL,
    input_fingerprint VARCHAR(64) NOT NULL,
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
    terminal_at VARCHAR(32), target_type VARCHAR(40) NOT NULL DEFAULT 'story', target_id VARCHAR(36), payload_json TEXT NOT NULL DEFAULT '{}',
    CONSTRAINT ck_job_input_revision CHECK (input_revision >= 1),
    CONSTRAINT ck_job_attempt CHECK (current_attempt >= 1),
    CONSTRAINT ck_job_progress CHECK (progress >= 0 AND progress <= 1000000),
    FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE
);
INSERT INTO "jobs" VALUES('job-1','project-1','analyze_story','succeeded',1,'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',1,'completed',1000000,1,0,0,'[]',NULL,NULL,NULL,'2026-01-01T00:00:00Z','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z','story','story-2','{"kind":"analyze_story","legacySchemaVersion":1}');
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
INSERT INTO "parser_executions" VALUES('source-1','project-1','source-1','source-1',NULL,1,'legacy_phase0_import','1.0.0','succeeded','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa','0000000000000000000000000000000000000000000000000000000000000000','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa','{"legacyPhase0":true,"schemaVersion":1,"warning":"parser limits were not recorded"}','[]','[]','[]',NULL,NULL,0,'2026-01-01T00:00:00Z','2026-01-01T00:00:00Z');
INSERT INTO "parser_executions" VALUES('source-2','project-1','source-2','source-2',NULL,1,'legacy_phase0_import','1.0.0','succeeded','bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb','0000000000000000000000000000000000000000000000000000000000000000','bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb','{"legacyPhase0":true,"schemaVersion":1,"warning":"parser limits were not recorded"}','[]','[]','[]',NULL,NULL,0,'2026-01-02T00:00:00Z','2026-01-02T00:00:00Z');
CREATE TABLE projects (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    status VARCHAR(24) NOT NULL,
    revision INTEGER NOT NULL,
    story_id VARCHAR(36),
    created_at VARCHAR(32) NOT NULL,
    updated_at VARCHAR(32) NOT NULL,
    CONSTRAINT ck_projects_revision CHECK (revision >= 1)
);
INSERT INTO "projects" VALUES('project-1','Synthetic Phase 0','analysis',3,'story-2','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z');
CREATE TABLE scenes (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
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
    CONSTRAINT uq_scene_chapter_ordinal UNIQUE (chapter_id, ordinal),
    CONSTRAINT ck_scene_ordinal CHECK (ordinal >= 0),
    CONSTRAINT ck_scene_span CHECK (start_offset >= 0 AND end_offset >= start_offset),
    FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE,
    FOREIGN KEY(chapter_id) REFERENCES chapters (id) ON DELETE CASCADE
);
INSERT INTO "scenes" VALUES('scene-1','project-1','chapter-1',0,'Second',NULL,NULL,0,14,1,'{"score":0.9}','[]','{"origin":"analysis"}');
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL,
    service_version TEXT NOT NULL
);
INSERT INTO "schema_migrations" VALUES(1,'2026-01-01T00:00:00Z','0.1.0');
INSERT INTO "schema_migrations" VALUES(2,'2026-07-30T14:29:15.936Z','0.1.0');
CREATE TABLE "source_documents" (id VARCHAR(36) NOT NULL PRIMARY KEY, project_id VARCHAR(36) NOT NULL, display_name VARCHAR(255) NOT NULL, media_type VARCHAR(80) NOT NULL, declared_format VARCHAR(16) NOT NULL, content_sha256 VARCHAR(64) NOT NULL, text_sha256 VARCHAR(64), byte_length INTEGER NOT NULL, encoding VARCHAR(24), newline_style VARCHAR(24), storage_key VARCHAR(512) NOT NULL, imported_at VARCHAR(32) NOT NULL, revision INTEGER NOT NULL, source_revision INTEGER NOT NULL, supersedes_document_id VARCHAR(36), extraction_status VARCHAR(24) NOT NULL, provenance_json TEXT NOT NULL, warnings_json TEXT NOT NULL, CONSTRAINT uq_source_project_source_revision UNIQUE (project_id, source_revision), CONSTRAINT ck_source_byte_length CHECK (byte_length >= 0), CONSTRAINT ck_source_revision CHECK (revision >= 1), CONSTRAINT ck_source_logical_revision CHECK (source_revision >= 1), CONSTRAINT ck_source_extraction_status CHECK (extraction_status IN ('pending', 'running', 'complete', 'partial', 'failed')), FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE, FOREIGN KEY(supersedes_document_id) REFERENCES source_documents (id) ON DELETE RESTRICT);
INSERT INTO "source_documents" VALUES('source-1','project-1','first.md','text/markdown','markdown','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',13,'utf-8','lf','projects/project-1/sources/a.md','2026-01-01T00:00:00Z',1,1,NULL,'complete','{"origin":"import"}','[]');
INSERT INTO "source_documents" VALUES('source-2','project-1','second.md','text/markdown','markdown','bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb','bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',14,'utf-8','crlf','projects/project-1/sources/b.md','2026-01-02T00:00:00Z',1,2,'source-1','complete','{"origin":"import"}','[]');
CREATE TABLE story_beats (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
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
    CONSTRAINT uq_beat_scene_ordinal UNIQUE (scene_id, ordinal),
    CONSTRAINT ck_beat_ordinal CHECK (ordinal >= 0),
    CONSTRAINT ck_beat_span CHECK (start_offset >= 0 AND end_offset >= start_offset),
    FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE,
    FOREIGN KEY(scene_id) REFERENCES scenes (id) ON DELETE CASCADE
);
INSERT INTO "story_beats" VALUES('beat-1','project-1','scene-1',0,'dialogue',9,13,NULL,NULL,1,'{"origin":"analysis"}');
CREATE INDEX ix_chapters_project_id ON chapters (project_id);
CREATE INDEX ix_chapters_story_id ON chapters (story_id);
CREATE INDEX ix_chapter_project_story_order
    ON chapters (project_id, story_id, ordinal, id);
CREATE INDEX ix_scenes_project_id ON scenes (project_id);
CREATE INDEX ix_scenes_chapter_id ON scenes (chapter_id);
CREATE INDEX ix_scene_project_chapter_order
    ON scenes (project_id, chapter_id, ordinal, id);
CREATE INDEX ix_story_beats_project_id ON story_beats (project_id);
CREATE INDEX ix_story_beats_scene_id ON story_beats (scene_id);
CREATE INDEX ix_beat_project_scene_order
    ON story_beats (project_id, scene_id, ordinal, id);
CREATE INDEX ix_characters_project_id ON characters (project_id);
CREATE INDEX ix_characters_story_id ON characters (story_id);
CREATE INDEX ix_character_project_story_name
    ON characters (project_id, story_id, normalized_name);
CREATE INDEX ix_dialogue_lines_project_id ON dialogue_lines (project_id);
CREATE INDEX ix_dialogue_lines_scene_id ON dialogue_lines (scene_id);
CREATE INDEX ix_dialogue_lines_beat_id ON dialogue_lines (beat_id);
CREATE INDEX ix_line_project_scene_order
    ON dialogue_lines (project_id, scene_id, ordinal, id);
CREATE INDEX ix_dialogue_attributions_project_id
    ON dialogue_attributions (project_id);
CREATE INDEX ix_attribution_project_line
    ON dialogue_attributions (project_id, line_id);
CREATE INDEX ix_human_corrections_project_id ON human_corrections (project_id);
CREATE INDEX ix_human_corrections_line_id ON human_corrections (line_id);
CREATE INDEX ix_human_corrections_attribution_id
    ON human_corrections (attribution_id);
CREATE INDEX ix_correction_project_line_time
    ON human_corrections (project_id, line_id, recorded_at, id);
CREATE INDEX ix_jobs_project_id ON jobs (project_id);
CREATE INDEX ix_job_project_created ON jobs (project_id, created_at, id);
CREATE INDEX ix_job_queue ON jobs (state, created_at, id);
CREATE INDEX ix_event_job_attempt_sequence
    ON job_events (job_id, attempt, sequence);
CREATE INDEX ix_source_documents_project_id ON source_documents (project_id);
CREATE INDEX ix_source_project_imported ON source_documents (project_id, imported_at, id);
CREATE INDEX ix_source_project_hash ON source_documents (project_id, content_sha256);
CREATE INDEX ix_imported_stories_project_id ON imported_stories (project_id);
CREATE INDEX ix_imported_stories_source_document_id ON imported_stories (source_document_id);
CREATE INDEX ix_story_project_imported ON imported_stories (project_id, imported_at, id);
CREATE INDEX ix_job_target ON jobs (target_type, target_id, created_at, id);
CREATE INDEX ix_document_extractions_project_id ON document_extractions (project_id);
CREATE INDEX ix_extraction_project_source_created ON document_extractions (project_id, source_document_id, created_at, id);
CREATE INDEX ix_document_extractions_source_document_id ON document_extractions (source_document_id);
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
PRAGMA user_version=2;
COMMIT;
