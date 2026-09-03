from __future__ import annotations

import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE = ROOT / "data" / "database" / "catalog.sqlite"


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE data_releases (
    release_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    source_digest TEXT NOT NULL,
    source_file_count INTEGER NOT NULL,
    record_count INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('building', 'ready', 'failed'))
);

CREATE TABLE source_files (
    source_file TEXT PRIMARY KEY,
    release_id TEXT NOT NULL REFERENCES data_releases(release_id),
    sha256 TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    venue TEXT NOT NULL,
    venue_type TEXT NOT NULL,
    year INTEGER NOT NULL,
    list_status TEXT NOT NULL
);

CREATE TABLE paper_records (
    record_id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL,
    venue TEXT NOT NULL,
    venue_type TEXT NOT NULL,
    year INTEGER NOT NULL,
    track TEXT NOT NULL,
    title TEXT NOT NULL,
    normalized_title TEXT NOT NULL,
    authors TEXT NOT NULL,
    abstract TEXT NOT NULL,
    abstract_source_name TEXT NOT NULL,
    abstract_source_url TEXT NOT NULL,
    abstract_source_tier TEXT NOT NULL,
    abstract_fetched_at TEXT NOT NULL,
    doi TEXT NOT NULL,
    arxiv_id TEXT NOT NULL,
    paper_url TEXT NOT NULL,
    pdf_url TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_tier TEXT NOT NULL,
    verification_status TEXT NOT NULL,
    list_status TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    source_file TEXT NOT NULL REFERENCES source_files(source_file),
    imported_at TEXT NOT NULL,
    release_id TEXT NOT NULL REFERENCES data_releases(release_id)
);

CREATE TABLE paper_entities (
    entity_id TEXT PRIMARY KEY,
    canonical_record_id TEXT NOT NULL REFERENCES paper_records(record_id),
    canonical_title TEXT NOT NULL,
    first_year INTEGER NOT NULL,
    last_year INTEGER NOT NULL,
    record_count INTEGER NOT NULL,
    venue_count INTEGER NOT NULL,
    entity_status TEXT NOT NULL CHECK (entity_status IN ('single', 'merged')),
    created_at TEXT NOT NULL,
    release_id TEXT NOT NULL REFERENCES data_releases(release_id)
);

CREATE TABLE entity_memberships (
    record_id TEXT PRIMARY KEY REFERENCES paper_records(record_id),
    entity_id TEXT NOT NULL REFERENCES paper_entities(entity_id),
    match_method TEXT NOT NULL,
    evidence_value TEXT NOT NULL,
    confidence TEXT NOT NULL CHECK (confidence IN ('exact', 'high', 'single')),
    decision_status TEXT NOT NULL CHECK (decision_status = 'auto_accepted'),
    is_canonical INTEGER NOT NULL CHECK (is_canonical IN (0, 1)),
    created_at TEXT NOT NULL,
    release_id TEXT NOT NULL REFERENCES data_releases(release_id)
);

CREATE TABLE entity_review_candidates (
    candidate_id TEXT PRIMARY KEY,
    left_record_id TEXT NOT NULL REFERENCES paper_records(record_id),
    right_record_id TEXT NOT NULL REFERENCES paper_records(record_id),
    candidate_method TEXT NOT NULL,
    evidence_value TEXT NOT NULL,
    reason TEXT NOT NULL,
    confidence TEXT NOT NULL CHECK (confidence IN ('high', 'conflict')),
    review_status TEXT NOT NULL CHECK (review_status IN ('pending', 'accepted', 'rejected')),
    created_at TEXT NOT NULL,
    release_id TEXT NOT NULL REFERENCES data_releases(release_id),
    CHECK (left_record_id < right_record_id)
);

CREATE TABLE entity_aliases (
    alias_entity_id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL REFERENCES paper_entities(entity_id),
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    release_id TEXT NOT NULL REFERENCES data_releases(release_id)
);

CREATE TABLE analysis_runs (
    run_id TEXT PRIMARY KEY,
    analysis_type TEXT NOT NULL,
    config_version TEXT NOT NULL,
    config_digest TEXT NOT NULL,
    catalog_release_id TEXT NOT NULL REFERENCES data_releases(release_id),
    created_at TEXT NOT NULL,
    assignment_count INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('building', 'ready', 'failed'))
);

CREATE TABLE topic_definitions (
    topic_id TEXT PRIMARY KEY,
    parent_topic_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    config_version TEXT NOT NULL,
    sort_order INTEGER NOT NULL
);

CREATE TABLE entity_topic_assignments (
    entity_id TEXT NOT NULL REFERENCES paper_entities(entity_id),
    topic_id TEXT NOT NULL REFERENCES topic_definitions(topic_id),
    score REAL NOT NULL,
    evidence_json TEXT NOT NULL,
    classifier_type TEXT NOT NULL CHECK (classifier_type = 'rules'),
    classifier_version TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES analysis_runs(run_id),
    created_at TEXT NOT NULL,
    catalog_release_id TEXT NOT NULL REFERENCES data_releases(release_id),
    role TEXT NOT NULL CHECK (role IN ('primary', 'extra')),
    PRIMARY KEY (entity_id, topic_id)
);

CREATE TABLE topic_ancestors (
    topic_id TEXT NOT NULL REFERENCES topic_definitions(topic_id),
    ancestor_topic_id TEXT NOT NULL REFERENCES topic_definitions(topic_id),
    depth INTEGER NOT NULL CHECK (depth >= 0),
    PRIMARY KEY (topic_id, ancestor_topic_id)
);

CREATE TABLE entity_tech_tags (
    entity_id TEXT NOT NULL REFERENCES paper_entities(entity_id),
    tag TEXT NOT NULL,
    category TEXT NOT NULL,
    match_term TEXT NOT NULL,
    match_field TEXT NOT NULL CHECK (match_field IN ('title', 'abstract')),
    run_id TEXT NOT NULL REFERENCES analysis_runs(run_id),
    catalog_release_id TEXT NOT NULL REFERENCES data_releases(release_id),
    PRIMARY KEY (entity_id, tag)
);

CREATE TABLE topic_evaluation_sets (
    evaluation_version TEXT PRIMARY KEY,
    classifier_version TEXT NOT NULL,
    sampled_catalog_release_id TEXT NOT NULL,
    sample_count INTEGER NOT NULL,
    reviewer_type TEXT NOT NULL,
    reviewer_name TEXT NOT NULL,
    evidence_level TEXT NOT NULL,
    reviewed_count INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE topic_evaluation_samples (
    sample_id TEXT PRIMARY KEY,
    evaluation_version TEXT NOT NULL REFERENCES topic_evaluation_sets(evaluation_version),
    topic_id TEXT NOT NULL REFERENCES topic_definitions(topic_id),
    entity_id TEXT NOT NULL REFERENCES paper_entities(entity_id),
    score REAL NOT NULL,
    score_band TEXT NOT NULL CHECK (score_band IN ('high', 'middle', 'boundary')),
    title_snapshot TEXT NOT NULL,
    authors_snapshot TEXT NOT NULL,
    abstract_snapshot TEXT NOT NULL,
    appearances_snapshot TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    verdict TEXT NOT NULL CHECK (verdict IN ('pending', 'relevant', 'mention_only', 'wrong_signal', 'uncertain')),
    error_type TEXT NOT NULL,
    rationale TEXT NOT NULL,
    reviewer_type TEXT NOT NULL,
    reviewer_name TEXT NOT NULL,
    evidence_level TEXT NOT NULL,
    reviewed_at TEXT NOT NULL,
    sampled_catalog_release_id TEXT NOT NULL,
    UNIQUE (evaluation_version, topic_id, entity_id)
);

CREATE TABLE quality_issues (
    issue_id INTEGER PRIMARY KEY AUTOINCREMENT,
    release_id TEXT NOT NULL REFERENCES data_releases(release_id),
    severity TEXT NOT NULL CHECK (severity IN ('error', 'warning', 'info')),
    issue_type TEXT NOT NULL,
    source_file TEXT NOT NULL,
    record_id TEXT NOT NULL,
    field_name TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE INDEX idx_records_venue_year ON paper_records(venue, year);
CREATE INDEX idx_records_venue_type ON paper_records(venue_type);
CREATE INDEX idx_records_list_status ON paper_records(list_status);
CREATE INDEX idx_records_source_tier ON paper_records(source_tier);
CREATE INDEX idx_records_doi ON paper_records(doi) WHERE doi <> '';
CREATE INDEX idx_records_arxiv ON paper_records(arxiv_id) WHERE arxiv_id <> '';
CREATE INDEX idx_records_title ON paper_records(normalized_title);
CREATE INDEX idx_records_abstract_source ON paper_records(abstract_source_tier)
    WHERE abstract <> '';
CREATE INDEX idx_entities_status ON paper_entities(entity_status, record_count);
CREATE INDEX idx_memberships_entity ON entity_memberships(entity_id);
CREATE INDEX idx_memberships_method ON entity_memberships(match_method);
CREATE INDEX idx_entity_candidates_status ON entity_review_candidates(review_status, candidate_method);
CREATE INDEX idx_topic_definitions_parent ON topic_definitions(parent_topic_id, sort_order);
CREATE INDEX idx_topic_assignments_topic ON entity_topic_assignments(topic_id, score DESC);
CREATE INDEX idx_topic_assignments_entity ON entity_topic_assignments(entity_id);
CREATE INDEX idx_topic_assignments_role ON entity_topic_assignments(role, topic_id);
CREATE INDEX idx_topic_ancestors_node ON topic_ancestors(ancestor_topic_id, depth);
CREATE INDEX idx_tech_tags_tag ON entity_tech_tags(tag, category);
CREATE INDEX idx_tech_tags_entity ON entity_tech_tags(entity_id);
CREATE INDEX idx_topic_eval_samples_topic ON topic_evaluation_samples(evaluation_version, topic_id, score_band);
CREATE INDEX idx_topic_eval_samples_verdict ON topic_evaluation_samples(evaluation_version, verdict, error_type);
CREATE INDEX idx_issues_type ON quality_issues(severity, issue_type);
"""


def connect(path: Path = DEFAULT_DATABASE, *, read_only: bool = False) -> sqlite3.Connection:
    path = path.resolve()
    if read_only:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)
