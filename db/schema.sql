-- Discovery Intelligence Data Layer — MVP v0.1
-- SQLite knowledge base. Claim-level provenance: one entity has many claims,
-- one claim per (field, source). The "consolidated record" is a view over the
-- best approved claim per field. Losing claims are kept as the evidence base.

PRAGMA foreign_keys = ON;

-- ─────────────────────────────────────────────────────────────────────────────
-- entities: the disambiguated subject. One row = one real-world thing.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS entities (
    entity_id              TEXT PRIMARY KEY,          -- uuid, assigned by orchestrator
    project_id             TEXT NOT NULL,             -- groups a delivery engagement
    -- disambiguation block (REQUIRED for MVP)
    brand_name             TEXT,                      -- market-facing name, e.g. "Kontur"
    legal_entity_name      TEXT,                      -- registered name, e.g. "ООО СКБ Контур"
    inn                    TEXT,                      -- RU/CIS taxpayer id (primary key for RU registries)
    website                TEXT,
    entity_type            TEXT CHECK (entity_type IN ('brand','legal_entity','product','group')),
    confidence_entity_match TEXT CHECK (confidence_entity_match IN ('high','medium','low')),
    -- context
    geo                    TEXT,                      -- 'ru_cis' | 'global'
    industry               TEXT,
    status                 TEXT DEFAULT 'pending'
                             CHECK (status IN ('pending','approved','rejected')),
    created_at             TEXT DEFAULT (datetime('now')),
    updated_at             TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_entities_project ON entities(project_id);
CREATE INDEX IF NOT EXISTS idx_entities_inn     ON entities(inn);

-- ─────────────────────────────────────────────────────────────────────────────
-- sources: every URL a claim was drawn from, with a trust tier.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sources (
    source_id      TEXT PRIMARY KEY,                  -- uuid
    url            TEXT,
    tier           INTEGER CHECK (tier IN (1,2,3)),   -- 1=official/primary 2=reputable 3=blog/self-reported
    source_group   TEXT,                              -- 'A' (registry/official) | 'B' (news/third-party)
    retrieved_at   TEXT DEFAULT (datetime('now'))
);

-- ─────────────────────────────────────────────────────────────────────────────
-- claims: the core. One field, one value, one source, with provenance.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS claims (
    claim_id     TEXT PRIMARY KEY,                    -- uuid
    entity_id    TEXT NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
    field        TEXT NOT NULL,                       -- schema field name, e.g. 'revenue'
    value        TEXT,
    confidence   TEXT CHECK (confidence IN ('high','medium','low')),
    source_id    TEXT REFERENCES sources(source_id),
    snippet      TEXT,                                -- the evidence quote the value came from
    year         INTEGER,                             -- period the value refers to (revenue year, etc.)
    assumptions  TEXT,                                -- explicit for any estimate
    collector    TEXT,                                -- 'A' | 'B' | 'verifier'
    status       TEXT DEFAULT 'pending'
                   CHECK (status IN ('pending','approved','rejected')),
    created_at   TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_claims_entity ON claims(entity_id);
CREATE INDEX IF NOT EXISTS idx_claims_field  ON claims(entity_id, field);

-- ─────────────────────────────────────────────────────────────────────────────
-- corrections: your edits become rules, injected into later prompts THIS project.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS corrections (
    correction_id TEXT PRIMARY KEY,                   -- uuid
    project_id    TEXT NOT NULL,
    scope         TEXT DEFAULT 'project'
                    CHECK (scope IN ('project','global')),
    field         TEXT,                               -- optional: field the rule applies to
    pattern       TEXT,                               -- what triggers the rule
    rule          TEXT NOT NULL,                      -- what to do instead
    created_at    TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_corrections_project ON corrections(project_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- consolidated_record: best approved claim per (entity, field).
-- Ranking: confidence (high>med>low), then source tier (1>2>3), then newest.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE VIEW IF NOT EXISTS consolidated_record AS
SELECT c.entity_id, c.field, c.value, c.confidence, c.year, c.assumptions,
       s.url AS source_url, s.tier AS source_tier
FROM claims c
LEFT JOIN sources s ON s.source_id = c.source_id
WHERE c.status = 'approved'
  AND c.claim_id = (
      SELECT c2.claim_id FROM claims c2
      LEFT JOIN sources s2 ON s2.source_id = c2.source_id
      WHERE c2.entity_id = c.entity_id AND c2.field = c.field AND c2.status = 'approved'
      ORDER BY CASE c2.confidence WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
               COALESCE(s2.tier, 9),
               c2.created_at DESC
      LIMIT 1
  );
