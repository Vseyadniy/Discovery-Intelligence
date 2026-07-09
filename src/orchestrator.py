"""
Discovery Intelligence Data Layer — MVP v0.1 orchestrator.

Flow per entity:
  load inputs -> run 2 collectors in parallel -> verify (cheap, escalate on flag)
  -> generate review card -> manual approve/edit/reject -> write approved claims
  -> save correction rules.

Usage:
  python -m src.orchestrator                 # run all rows in inputs/input_entities.csv
  python -m src.orchestrator --limit 15      # Stage 1 baseline sample
  python -m src.orchestrator --auto           # skip interactive review (approve all) — for smoke tests
"""
from __future__ import annotations

import argparse
import csv
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import db
from . import model_router as mr
from .model_router import extract_json

ROOT = Path(__file__).resolve().parent.parent
CFG = ROOT / "config"
PROMPTS = ROOT / "prompts"
TEMPLATES = ROOT / "templates"
OUT = ROOT / "outputs" / "review_cards"

DISAMBIG_FIELDS = {
    "brand_name", "legal_entity_name", "inn", "website",
    "entity_type", "confidence_entity_match",
}


# ── config loading ────────────────────────────────────────────────────────────
def load_config():
    schema = yaml.safe_load((CFG / "schema.yaml").read_text(encoding="utf-8"))
    registry = yaml.safe_load((CFG / "source_registry.yaml").read_text(encoding="utf-8"))
    corrections = json.loads((CFG / "corrections.json").read_text(encoding="utf-8"))
    return schema, registry, corrections


def format_sources(registry, geo, industry, mode, group):
    rows = list(registry.get(geo, {}).get(group, []))
    lines = [f"- {r['url']} (tier {r['tier']})" + (f" — {r['note']}" if r.get("note") else "")
             for r in rows]
    if mode == "industry_specific":
        overlay = registry.get("industries", {}).get(industry, {}).get(geo, [])
        lines += [f"- {u} (industry overlay)" for u in overlay]
    return "\n".join(lines)


def format_fields(schema):
    out = []
    for f in schema["fields"]:
        tag = " [ESTIMATE — fill assumptions if derived]" if f.get("estimate") else ""
        out.append(f"- {f['name']} ({f['type']}): {f['desc']}{tag}")
    return "\n".join(out)


def format_corrections(corrections):
    rules = corrections.get("rules", [])
    if not rules:
        return "(none yet)"
    return "\n".join(f"- [{r.get('field','*')}] {r['rule']}" for r in rules)


def fill(template: str, mapping: dict) -> str:
    for k, v in mapping.items():
        template = template.replace("{{" + k + "}}", str(v))
    return template


# ── pipeline steps ────────────────────────────────────────────────────────────
def run_collector(letter, row, schema, registry, corrections):
    prompt = (PROMPTS / f"collector_{letter.lower()}.md").read_text(encoding="utf-8")
    group = letter.upper()
    filled = fill(prompt, {
        "seed_name": row["seed_name"],
        "inn": row.get("inn", ""),
        "website": row.get("website", ""),
        "hint": row.get("hint", ""),
        "geo": schema["geo"],
        "industry": schema["industry"],
        "output_language": schema.get("output_language", "English"),
        f"sources_{letter.lower()}": format_sources(
            registry, schema["geo"], schema["industry"], schema["mode"], group),
        "schema_fields": format_fields(schema),
        "corrections": format_corrections(corrections),
    })
    raw, _ = mr.collect("You are a precise data-extraction agent. Output only valid JSON.", filled)
    return extract_json(raw)


def needs_escalation(a: dict, b: dict, schema) -> bool:
    """Decide whether the verifier should use the expensive model."""
    fa, fb = a.get("fields", {}), b.get("fields", {})
    # 1) entity ambiguity: mismatched legal name or INN between collectors
    for key in ("inn", "legal_entity_name"):
        va = (fa.get(key) or {}).get("value")
        vb = (fb.get(key) or {}).get("value")
        if va and vb and str(va).strip() != str(vb).strip():
            return True
    ca = (fa.get("confidence_entity_match") or {}).get("value")
    cb = (fb.get("confidence_entity_match") or {}).get("value")
    if "low" in (ca, cb):
        return True
    # 2) conflict on a factual field
    for f in ("revenue", "headcount", "segment"):
        va = (fa.get(f) or {}).get("value")
        vb = (fb.get(f) or {}).get("value")
        if va and vb and str(va).strip() != str(vb).strip():
            return True
    return False


def run_verifier(a, b, corrections, schema=None):
    prompt = (PROMPTS / "verifier.md").read_text(encoding="utf-8")
    filled = fill(prompt, {
        "collector_a_json": json.dumps(a, ensure_ascii=False, indent=2),
        "collector_b_json": json.dumps(b, ensure_ascii=False, indent=2),
        "output_language": (schema or {}).get("output_language", "English"),
        "corrections": format_corrections(corrections),
    })
    system = "You are a rigorous verification agent. Output only valid JSON."
    escalate = needs_escalation(a, b, None)
    raw, engine = mr.verify(system, filled, escalate)
    return extract_json(raw), engine


# ── review card ───────────────────────────────────────────────────────────────
def render_card(record, schema, engine):
    tmpl = (TEMPLATES / "review_card.md").read_text(encoding="utf-8")
    em = record.get("entity_match", {})
    fields = record.get("fields", {})

    review_lines, agreed_lines = [], []
    agreed_count = 0
    for name, f in fields.items():
        conf = f.get("confidence", "low")
        conflict = f.get("conflict")
        block = (f"**{name}** — `{f.get('value')}`  "
                 f"(confidence: {conf}, {f.get('year') or 'n/a'})\n"
                 f"  - source: {f.get('source')}\n"
                 f"  - snippet: {f.get('snippet')}\n")
        if f.get("assumptions"):
            block += f"  - assumptions: {f['assumptions']}\n"
        if conflict or conf == "low":
            if conflict:
                block += f"  - ⚔️ A said: {conflict.get('a')}  |  B said: {conflict.get('b')}\n"
            review_lines.append(block)
        else:
            agreed_count += 1
            agreed_lines.append(block)

    em_conf = em.get("confidence", "low")
    return fill(tmpl, {
        "entity": record.get("entity", ""),
        "project_id": schema["project_id"],
        "geo": schema["geo"],
        "industry": schema["industry"],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "verifier_engine": engine,
        "entity_type": em.get("entity_type", "?"),
        "entity_match_confidence": em_conf,
        "entity_match_flag": "🚩 CHECK ENTITY" if em_conf == "low" else "",
        "entity_match_note": em.get("note", ""),
        "review_section": "\n".join(review_lines) or "_Nothing flagged — all fields agreed._",
        "agreed_count": agreed_count,
        "agreed_section": "\n".join(agreed_lines) or "_none_",
    })


# ── approval + write ──────────────────────────────────────────────────────────
def parse_decisions(text: str):
    """Return (approve_all, edits{field:(value,why)}, rejects{field}, investigates{field})."""
    approve_all = False
    edits, rejects, investigates = {}, set(), set()
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        low = line.lower()
        if low in ("approve all", "approve", "a"):
            approve_all = True
        elif low.startswith("edit "):
            body = line[5:]
            field, _, rest = body.partition("=")
            value, _, why = rest.partition("|")
            edits[field.strip()] = (value.strip(), why.strip())
        elif low.startswith("reject "):
            rejects.add(line[7:].strip())
        elif low.startswith("investigate "):
            investigates.add(line[12:].strip())
    return approve_all, edits, rejects, investigates


def write_record(conn, record, schema, decisions):
    approve_all, edits, rejects, investigates = decisions
    fields = record.get("fields", {})

    disambig = {k: (fields.get(k) or {}).get("value") for k in DISAMBIG_FIELDS}
    em = record.get("entity_match", {})
    disambig.setdefault("entity_type", em.get("entity_type"))
    disambig["confidence_entity_match"] = em.get("confidence")
    eid = db.insert_entity(conn, schema["project_id"], schema["geo"], schema["industry"], disambig)

    written = 0
    for name, f in fields.items():
        if name in rejects or name in investigates or name in DISAMBIG_FIELDS:
            continue
        claim = dict(f)
        if name in edits:
            value, why = edits[name]
            claim["value"] = value
            claim["confidence"] = "high"        # human-confirmed
            claim["assumptions"] = (claim.get("assumptions") or "") + " [human-edited]"
            if why:
                db.insert_correction(conn, schema["project_id"], name,
                                     f"verifier value for {name}", why)
        elif not approve_all and (f.get("confidence") == "low" or f.get("conflict")):
            # in interactive mode, flagged fields aren't auto-approved unless 'approve all'
            continue
        sid = None
        if f.get("source"):
            sid = db.upsert_source(conn, f["source"], f.get("source_tier"), None)
        db.insert_claim(conn, eid, name, claim, "verifier", sid)
        written += 1
    conn.commit()
    return eid, written


# ── main ──────────────────────────────────────────────────────────────────────
def process_row(row, schema, registry, corrections, conn, auto):
    print(f"\n=== {row['seed_name']} ===")
    with ThreadPoolExecutor(max_workers=2) as ex:
        fa = ex.submit(run_collector, "A", row, schema, registry, corrections)
        fb = ex.submit(run_collector, "B", row, schema, registry, corrections)
        a, b = fa.result(), fb.result()

    record, engine = run_verifier(a, b, corrections, schema)
    card = render_card(record, schema, engine)
    card_path = OUT / f"{row['seed_name']}.md"
    card_path.write_text(card, encoding="utf-8")
    print(f"  verifier engine: {engine}")
    print(f"  review card: {card_path}")

    if auto:
        decisions = (True, {}, set(), set())
    else:
        print("\n" + card + "\n")
        print("Enter decisions (blank line to finish; 'approve all' to accept):")
        lines = []
        while True:
            try:
                ln = input("> ")
            except EOFError:
                break
            if ln.strip() == "":
                break
            lines.append(ln)
        decisions = parse_decisions("\n".join(lines))

    eid, written = write_record(conn, record, schema, decisions)
    print(f"  written: {written} claims  (entity {eid[:8]})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--auto", action="store_true", help="approve all, no interactive review")
    ap.add_argument("--subsector", default=None,
                    help="only process rows whose 'subsector' matches (e.g. 'IaaS/GPU'). "
                         "Case-insensitive; scopes a run to one market.")
    ap.add_argument("--mode", choices=["gpt", "cheap", "claude"], default=None,
                    help="gpt = GPT-5.5 research (+Claude escalation); claude = fully Claude. "
                         "'cheap' is a back-compat alias for 'gpt'. Default: AGENT_MODE env, else gpt.")
    args = ap.parse_args()

    if args.mode:
        mr.set_mode(args.mode)
    print(f"[router] {mr.banner()}")

    db.init_db()
    schema, registry, corrections = load_config()
    rows = list(csv.DictReader((ROOT / "inputs" / "input_entities.csv").open(encoding="utf-8")))
    if args.subsector:
        want = args.subsector.strip().lower()
        rows = [r for r in rows if (r.get("subsector") or "").strip().lower() == want]
        print(f"[scope] subsector={args.subsector} → {len(rows)} companies")
        if not rows:
            print("  no matching rows; check the 'subsector' column in inputs/input_entities.csv")
    if args.limit:
        rows = rows[: args.limit]

    conn = db.connect()
    for row in rows:
        try:
            process_row(row, schema, registry, corrections, conn, args.auto)
        except Exception as e:  # keep the batch going; one bad entity shouldn't halt delivery
            print(f"  ERROR on {row['seed_name']}: {e}")
    conn.close()
    print(f"\nDone. KB at {db.DB_PATH}")


if __name__ == "__main__":
    main()
