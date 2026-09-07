PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO schema_migrations (version) VALUES (1);

CREATE TABLE IF NOT EXISTS documents (
    document_id TEXT PRIMARY KEY,
    document_type TEXT NOT NULL
        CHECK (document_type IN ('law', 'decree', 'rule', 'case', 'interp', 'guide')),
    title TEXT NOT NULL,
    agency TEXT NOT NULL,
    source_url TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    checksum TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('current', 'historical', 'repealed')),
    file_path TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source_url, checksum)
);

CREATE TABLE IF NOT EXISTS laws (
    law_id TEXT PRIMARY KEY,
    law_name TEXT NOT NULL,
    law_type TEXT NOT NULL,
    ministry TEXT NOT NULL,
    law_code TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS law_versions (
    law_version_id TEXT PRIMARY KEY,
    law_id TEXT NOT NULL REFERENCES laws(law_id) ON DELETE CASCADE,
    document_id TEXT NOT NULL UNIQUE REFERENCES documents(document_id) ON DELETE RESTRICT,
    proclamation_number TEXT NOT NULL,
    proclaimed_at TEXT NOT NULL,
    effective_from TEXT NOT NULL,
    effective_to TEXT,
    status TEXT NOT NULL CHECK (status IN ('current', 'historical', 'repealed')),
    UNIQUE (law_id, proclamation_number)
);

CREATE TABLE IF NOT EXISTS law_articles (
    article_id TEXT PRIMARY KEY,
    law_version_id TEXT NOT NULL REFERENCES law_versions(law_version_id) ON DELETE CASCADE,
    article_number TEXT NOT NULL,
    article_title TEXT NOT NULL DEFAULT '',
    paragraph_number TEXT NOT NULL DEFAULT '',
    item_number TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    UNIQUE (
        law_version_id,
        article_number,
        paragraph_number,
        item_number
    )
);

CREATE TABLE IF NOT EXISTS cases (
    case_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL UNIQUE REFERENCES documents(document_id) ON DELETE RESTRICT,
    case_number TEXT NOT NULL,
    court_name TEXT NOT NULL,
    decision_date TEXT NOT NULL,
    case_type TEXT NOT NULL,
    case_name TEXT NOT NULL,
    holding TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    full_text TEXT NOT NULL,
    summary_type TEXT NOT NULL DEFAULT 'official'
        CHECK (summary_type IN ('official', 'generated')),
    summary_model TEXT
);

CREATE INDEX IF NOT EXISTS idx_cases_case_number ON cases(case_number);

CREATE TABLE IF NOT EXISTS case_law_citations (
    case_id TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    article_id TEXT NOT NULL REFERENCES law_articles(article_id) ON DELETE RESTRICT,
    citation_type TEXT NOT NULL
        CHECK (citation_type IN ('cited', 'issue', 'applied', 'reference')),
    quoted_text TEXT NOT NULL DEFAULT '',
    relation_summary TEXT NOT NULL DEFAULT '',
    verified INTEGER NOT NULL DEFAULT 0 CHECK (verified IN (0, 1)),
    PRIMARY KEY (case_id, article_id, citation_type)
);

CREATE TABLE IF NOT EXISTS guides (
    guide_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL UNIQUE REFERENCES documents(document_id) ON DELETE RESTRICT,
    guide_type TEXT NOT NULL,
    published_at TEXT NOT NULL,
    updated_at TEXT,
    topic TEXT NOT NULL,
    content TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS guide_law_references (
    guide_id TEXT NOT NULL REFERENCES guides(guide_id) ON DELETE CASCADE,
    article_id TEXT NOT NULL REFERENCES law_articles(article_id) ON DELETE RESTRICT,
    relation_type TEXT NOT NULL DEFAULT 'reference',
    PRIMARY KEY (guide_id, article_id, relation_type)
);

CREATE TABLE IF NOT EXISTS risk_rules (
    rule_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    section TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('high', 'caution', 'info')),
    guidance TEXT NOT NULL,
    severity_basis TEXT NOT NULL,
    version TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    reviewed_at TEXT
);

CREATE TABLE IF NOT EXISTS risk_rule_keywords (
    keyword_id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id TEXT NOT NULL REFERENCES risk_rules(rule_id) ON DELETE CASCADE,
    keyword TEXT NOT NULL,
    normalized_keyword TEXT NOT NULL,
    UNIQUE (rule_id, normalized_keyword)
);

CREATE TABLE IF NOT EXISTS rule_evidence (
    rule_evidence_id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id TEXT NOT NULL REFERENCES risk_rules(rule_id) ON DELETE CASCADE,
    article_id TEXT REFERENCES law_articles(article_id) ON DELETE RESTRICT,
    case_id TEXT REFERENCES cases(case_id) ON DELETE RESTRICT,
    guide_id TEXT REFERENCES guides(guide_id) ON DELETE RESTRICT,
    reason TEXT NOT NULL,
    verified INTEGER NOT NULL DEFAULT 0 CHECK (verified IN (0, 1)),
    priority INTEGER NOT NULL DEFAULT 100,
    CHECK (
        (article_id IS NOT NULL) +
        (case_id IS NOT NULL) +
        (guide_id IS NOT NULL) = 1
    )
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    source_type TEXT NOT NULL
        CHECK (source_type IN ('law', 'decree', 'rule', 'case', 'interp', 'guide')),
    article_id TEXT REFERENCES law_articles(article_id) ON DELETE CASCADE,
    case_id TEXT REFERENCES cases(case_id) ON DELETE CASCADE,
    guide_id TEXT REFERENCES guides(guide_id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
    content TEXT NOT NULL,
    token_count INTEGER NOT NULL CHECK (token_count >= 0),
    checksum TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (
        (article_id IS NOT NULL) +
        (case_id IS NOT NULL) +
        (guide_id IS NOT NULL) <= 1
    ),
    UNIQUE (document_id, chunk_index, checksum)
);

CREATE TABLE IF NOT EXISTS evaluation_questions (
    question_id TEXT PRIMARY KEY,
    split TEXT NOT NULL CHECK (split IN ('dev', 'holdout')),
    question TEXT NOT NULL,
    expected_behavior TEXT NOT NULL
        CHECK (expected_behavior IN ('answer', 'abstain', 'refuse')),
    answerable INTEGER NOT NULL CHECK (answerable IN (0, 1))
);

CREATE TABLE IF NOT EXISTS evaluation_evidence (
    question_id TEXT NOT NULL
        REFERENCES evaluation_questions(question_id) ON DELETE CASCADE,
    article_id TEXT REFERENCES law_articles(article_id) ON DELETE RESTRICT,
    case_id TEXT REFERENCES cases(case_id) ON DELETE RESTRICT,
    guide_id TEXT REFERENCES guides(guide_id) ON DELETE RESTRICT,
    expected_rank INTEGER CHECK (expected_rank IS NULL OR expected_rank > 0),
    CHECK (
        (article_id IS NOT NULL) +
        (case_id IS NOT NULL) +
        (guide_id IS NOT NULL) = 1
    ),
    UNIQUE (question_id, article_id, case_id, guide_id)
);

CREATE INDEX IF NOT EXISTS idx_documents_type_status
    ON documents(document_type, status);
CREATE INDEX IF NOT EXISTS idx_law_versions_effective
    ON law_versions(law_id, effective_from, effective_to);
CREATE INDEX IF NOT EXISTS idx_law_articles_number
    ON law_articles(law_version_id, article_number);
CREATE INDEX IF NOT EXISTS idx_cases_decision_date
    ON cases(decision_date);
CREATE INDEX IF NOT EXISTS idx_case_law_article
    ON case_law_citations(article_id);
CREATE INDEX IF NOT EXISTS idx_guides_topic
    ON guides(topic);
CREATE INDEX IF NOT EXISTS idx_rule_evidence_rule
    ON rule_evidence(rule_id, priority);
CREATE INDEX IF NOT EXISTS idx_chunks_document
    ON chunks(document_id, chunk_index);
