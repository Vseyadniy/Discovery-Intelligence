"""SQLite knowledge-base helpers."""
from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "db" / "kb.sqlite"
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "db" / "schema.sql"


def uid() -> str:
    return uuid.uuid4().hex


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create tables/view from schema.sql. Idempotent."""
    conn = connect()
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


def upsert_source(conn, url, tier, group) -> str:
    sid = uid()
    conn.execute(
        "INSERT INTO sources (source_id, url, tier, source_group) VALUES (?,?,?,?)",
        (sid, url, tier, group),
    )
    return sid


def insert_entity(conn, project_id, geo, industry, disambig: dict) -> str:
    eid = uid()
    conn.execute(
        """INSERT INTO entities
           (entity_id, project_id, brand_name, legal_entity_name, inn, website,
            entity_type, confidence_entity_match, geo, industry, status)
           VALUES (?,?,?,?,?,?,?,?,?,?,'approved')""",
        (
            eid, project_id,
            disambig.get("brand_name"), disambig.get("legal_entity_name"),
            disambig.get("inn"), disambig.get("website"),
            disambig.get("entity_type"), disambig.get("confidence_entity_match"),
            geo, industry,
        ),
    )
    return eid


def insert_claim(conn, entity_id, field, claim: dict, collector, source_id=None) -> str:
    cid = uid()
    conn.execute(
        """INSERT INTO claims
           (claim_id, entity_id, field, value, confidence, source_id, snippet,
            year, assumptions, collector, status)
           VALUES (?,?,?,?,?,?,?,?,?,?,'approved')""",
        (
            cid, entity_id, field,
            claim.get("value"), claim.get("confidence"), source_id,
            claim.get("snippet"), claim.get("year"), claim.get("assumptions"),
            collector,
        ),
    )
    return cid


def insert_correction(conn, project_id, field, pattern, rule, scope="project") -> str:
    rid = uid()
    conn.execute(
        """INSERT INTO corrections (correction_id, project_id, scope, field, pattern, rule)
           VALUES (?,?,?,?,?,?)""",
        (rid, project_id, scope, field, pattern, rule),
    )
    return rid
