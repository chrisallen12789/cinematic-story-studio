-- Exact SQLAlchemy/SQLite schema emitted from Phase 0 commit
-- f56616858cca80d66726dd5f2e6ad4b3aa10663e plus its schema ledger.
PRAGMA foreign_keys=ON;

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

CREATE TABLE source_documents (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    project_id VARCHAR(36) NOT NULL,
    display_name VARCHAR(255) NOT NULL,
    media_type VARCHAR(80) NOT NULL,
    declared_format VARCHAR(16) NOT NULL,
    content_sha256 VARCHAR(64) NOT NULL,
    text_sha256 VARCHAR(64) NOT NULL,
    byte_length INTEGER NOT NULL,
    encoding VARCHAR(24) NOT NULL,
    newline_style VARCHAR(24) NOT NULL,
    storage_key VARCHAR(512) NOT NULL,
    imported_at VARCHAR(32) NOT NULL,
    revision INTEGER NOT NULL,
    provenance_json TEXT NOT NULL,
    warnings_json TEXT NOT NULL,
    CONSTRAINT uq_source_project_hash UNIQUE (project_id, content_sha256),
    CONSTRAINT ck_source_byte_length CHECK (byte_length >= 0),
    CONSTRAINT ck_source_revision CHECK (revision >= 1),
    FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE
);

CREATE TABLE imported_stories (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    project_id VARCHAR(36) NOT NULL,
    source_document_id VARCHAR(36) NOT NULL UNIQUE,
    title VARCHAR(255) NOT NULL,
    exact_text TEXT NOT NULL,
    content_fingerprint VARCHAR(64) NOT NULL,
    imported_at VARCHAR(32) NOT NULL,
    revision INTEGER NOT NULL,
    provenance_json TEXT NOT NULL,
    warnings_json TEXT NOT NULL,
    CONSTRAINT ck_story_revision CHECK (revision >= 1),
    FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE,
    FOREIGN KEY(source_document_id) REFERENCES source_documents (id) ON DELETE RESTRICT
);

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

CREATE TABLE idempotency_records (
    scope VARCHAR(80) NOT NULL,
    "key" VARCHAR(160) NOT NULL,
    request_hash VARCHAR(64) NOT NULL,
    resource_id VARCHAR(36) NOT NULL,
    created_at VARCHAR(32) NOT NULL,
    PRIMARY KEY (scope, "key")
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
    terminal_at VARCHAR(32),
    CONSTRAINT ck_job_input_revision CHECK (input_revision >= 1),
    CONSTRAINT ck_job_attempt CHECK (current_attempt >= 1),
    CONSTRAINT ck_job_progress CHECK (progress >= 0 AND progress <= 1000000),
    FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE
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

CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL,
    service_version TEXT NOT NULL
);

CREATE INDEX ix_source_documents_project_id ON source_documents (project_id);
CREATE INDEX ix_source_project_imported
    ON source_documents (project_id, imported_at, id);
CREATE INDEX ix_imported_stories_project_id ON imported_stories (project_id);
CREATE INDEX ix_story_project_imported
    ON imported_stories (project_id, imported_at, id);
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
