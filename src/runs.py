"""
Run-log manager — one folder per research run, so a run is fully recorded and the
architect (Claude) can analyze how the agent worked and improve it.

A run lives in  logs/<YYYY-MM-DD_HHMM>_<market-slug>_<depth>/  and contains:
  run.json        — metadata (market, depth, model, status, timestamps, paths)
  prompt.md       — the CURRENT prompt to paste (discovery → research batch → repair)
  steps/          — numbered history of every prompt issued
  companies.json  — the discovered cohort manifest (written by the researcher)
  agent_runs/     — <Brand>_A.json, _B.json, _record.json per company (the evidence)
  gate_report.md  — ingest-gate verdicts (accepted / rejected, per-field issues)
  events.jsonl    — timeline of what happened (created / ingested / built / opened)
  <date>_<market>_research.xlsx + .csv — the deliverable (gate-ACCEPTED records only)
  analysis.md     — architect-facing quality read (coverage, language, flags)

The unit of work is deliberately small: one prompt discovers the cohort, then each
research prompt covers only a few companies (a model can genuinely browse for 2–3
companies in one turn; it cannot for 15 — it fabricates instead). Records are
machine-validated by src.gate before they count; rejected records generate a repair
prompt automatically. Used by app.py and as a CLI:

  python -m src.runs create --market "Industrial AI RU" --depth detailed --model chatgpt
  python -m src.runs next   <run_id> [--batch 3]   # the next prompt to paste
  python -m src.runs gate   <run_id>               # validate records, write gate_report.md
  python -m src.runs build  <run_id>
  python -m src.runs analyze <run_id>
  python -m src.runs list
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import export_excel as xl
from . import gate

ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / "logs"
DOCS = ROOT / "docs"
CFG = ROOT / "config"

MODELS = {"chatgpt": "ChatGPT (GPT-5.5)", "claude": "Claude (Opus 4.8)"}


# ── config ────────────────────────────────────────────────────────────────────
def load_schema() -> dict:
    """schema.yaml + the app-managed custom fields (config/custom_fields.yaml)."""
    schema = yaml.safe_load((CFG / "schema.yaml").read_text(encoding="utf-8"))
    custom = CFG / "custom_fields.yaml"
    if custom.exists():
        data = yaml.safe_load(custom.read_text(encoding="utf-8")) or {}
        known = {f["name"] for f in schema["fields"]}
        for f in data.get("fields") or []:
            if f.get("name") and f["name"] not in known:
                f.setdefault("type", "string")
                f.setdefault("desc", f["name"])
                schema["fields"].append(f)
        # description overrides for BUILT-IN columns (edited via the Settings tab;
        # schema.yaml itself stays untouched so its comments survive)
        overrides = data.get("overrides") or {}
        for f in schema["fields"]:
            if f["name"] in overrides:
                f["desc"] = overrides[f["name"]]
    return schema


def _load_custom() -> dict:
    custom = CFG / "custom_fields.yaml"
    data = (yaml.safe_load(custom.read_text(encoding="utf-8")) or {}) if custom.exists() else {}
    data.setdefault("fields", [])
    data.setdefault("overrides", {})
    return data


def _save_custom(data: dict) -> None:
    out = {"fields": data.get("fields") or []}
    if data.get("overrides"):
        out["overrides"] = data["overrides"]
    (CFG / "custom_fields.yaml").write_text(
        "# custom_fields.yaml — extra research columns + description overrides\n"
        "# (managed by the app, tab «3 · Settings»). Merged into the schema at load\n"
        "# time: injected into every research prompt and the Excel deliverable.\n\n"
        + yaml.safe_dump(out, allow_unicode=True, sort_keys=False),
        encoding="utf-8")


def _check_col_name(name: str) -> None:
    if not re.fullmatch(r"[a-z][a-z0-9_]{2,40}", name):
        raise ValueError("column name must be snake_case ASCII, 3–41 chars (e.g. pricing_model)")


def add_custom_field(name: str, desc: str, estimate: bool = False) -> None:
    """Append a research column via the app. Validates the name; idempotent."""
    _check_col_name(name)
    if name in {f["name"] for f in load_schema()["fields"]}:
        raise ValueError(f"column '{name}' already exists")
    data = _load_custom()
    entry = {"name": name, "type": "string", "desc": desc.strip() or name}
    if estimate:
        entry["estimate"] = True
    data["fields"].append(entry)
    _save_custom(data)


def update_custom_field(old_name: str, new_name: str, desc: str) -> None:
    """Rename / re-describe a CUSTOM column (future runs only)."""
    data = _load_custom()
    for f in data["fields"]:
        if f["name"] == old_name:
            if new_name != old_name:
                _check_col_name(new_name)
                if new_name in {x["name"] for x in load_schema()["fields"]}:
                    raise ValueError(f"column '{new_name}' already exists")
                f["name"] = new_name
            f["desc"] = desc.strip() or f["desc"]
            _save_custom(data)
            return
    raise ValueError(f"'{old_name}' is not a custom column")


def delete_custom_field(name: str) -> None:
    data = _load_custom()
    before = len(data["fields"])
    data["fields"] = [f for f in data["fields"] if f["name"] != name]
    if len(data["fields"]) == before:
        raise ValueError(f"'{name}' is not a custom column")
    _save_custom(data)


def reset_custom_fields() -> None:
    """Settings «Default»: drop all custom columns AND description overrides —
    the Excel layout returns to schema.yaml as shipped (future runs only)."""
    _save_custom({"fields": [], "overrides": {}})


def override_field_desc(name: str, desc: str) -> None:
    """Edit what a BUILT-IN column asks the researcher to gather (schema.yaml
    stays untouched; the override lives in custom_fields.yaml)."""
    base = yaml.safe_load((CFG / "schema.yaml").read_text(encoding="utf-8"))
    names = {f["name"] for f in base["fields"]}
    if name not in names:
        raise ValueError(f"'{name}' is not a schema column")
    data = _load_custom()
    data["overrides"][name] = desc.strip()
    if not data["overrides"][name]:
        data["overrides"].pop(name, None)
    _save_custom(data)


def load_depths() -> dict:
    return yaml.safe_load((CFG / "depth_levels.yaml").read_text(encoding="utf-8"))["levels"]


def _slug(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE).strip().lower()
    return re.sub(r"[\s_-]+", "-", s) or "market"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── run lifecycle ─────────────────────────────────────────────────────────────
def create_run(market: str, depth: str, model: str) -> Path:
    depths = load_depths()
    if depth not in depths:
        raise ValueError(f"depth must be one of {list(depths)}")
    if model not in MODELS:
        raise ValueError(f"model must be one of {list(MODELS)}")
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    run_id = f"{stamp}_{_slug(market)}_{depth}"
    run_dir = LOGS / run_id
    (run_dir / "agent_runs").mkdir(parents=True, exist_ok=True)

    schema = load_schema()
    meta = {
        "run_id": run_id,
        "market": market,
        "depth": depth,
        "model": model,
        "output_language": schema.get("output_language", "English"),
        "geo": schema.get("geo"),
        "status": "prompt_ready",
        "created_at": _now(),
        "xlsx": None,
    }
    meta["status"] = "discovery"
    (run_dir / "run.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    _issue_prompt(run_dir, "discovery", build_discovery_prompt(meta, depths[depth]))
    _event(run_dir, "created", market=market, depth=depth, model=model)
    return run_dir


# ── staged prompts (discovery → research batches → repair) ────────────────────
def _issue_prompt(run_dir: Path, kind: str, text: str) -> None:
    """prompt.md = the current prompt; steps/ keeps the numbered history."""
    cur = run_dir / "prompt.md"
    if cur.exists() and cur.read_text(encoding="utf-8") == text:
        return                                   # same state, don't duplicate a step
    steps = run_dir / "steps"
    steps.mkdir(exist_ok=True)
    nn = len(list(steps.glob("*.md"))) + 1
    (steps / f"{nn:02d}_{kind}.md").write_text(text, encoding="utf-8")
    cur.write_text(text, encoding="utf-8")
    _event(run_dir, "prompt_issued", kind=kind, step=nn)


def build_discovery_prompt(meta: dict, depth: dict) -> str:
    run_id = meta["run_id"]
    min_rev = depth.get("min_revenue_rub")
    rev_line = (f"- **Revenue floor:** exclude companies with filed annual revenue below "
                f"{min_rev:,} ₽.\n".replace(",", " ")) if min_rev else ""
    return f"""# Research run — step 1: map the market — {meta['market']}  ({depth['label']})

You are the researcher (with web browsing) working in this repo. This chat has ONE
task: **discover the market cohort**. Do NOT research individual companies yet —
the operator will hand you per-company research prompts next.

## Market & depth
- **Market:** {meta['market']}  (geo: {meta.get('geo')})
- **Coverage target:** {depth['coverage_target']}.
- **Include:** {depth['include'].strip()}
- **Exclude:** {depth['exclude'].strip()}
{rev_line}- **Expected size:** {depth.get('typical_count', 'as many as the market has')}.

## Rules
- Every candidate needs a live public source where you actually SAW it named as a
  player in this market — a ranking, analyst map, association member list, industry
  directory, or press roundup you opened in this chat. Memory may suggest a name;
  an opened page must confirm it. A `…search?query=…` URL is not a source.
- Never read repo files for candidates (`docs/gold/*`, the market map, prior runs).
- **Size the cohort to the market, not a round number** — fragmented markets
  (franchises, regional networks, many small players) need more names to reach the
  coverage target.
- List companies in rough order of market weight (largest first) — research will
  proceed in that order.

## Segment taxonomy (required)
Define **3–7 SHORT segment labels** (≤3 words each, in {meta['output_language']})
that partition this market — e.g. «Проверка контрагентов», «Тендерная аналитика»,
«Медиамониторинг». Every company gets EXACTLY one of them. These labels become the
only allowed values of the `segment` field for the whole run (machine-enforced), so
make them the segments a client would filter by — not descriptive sentences.

## Save exactly this file: `logs/{run_id}/companies.json`
```json
{{
  "market": "{meta['market']}",
  "coverage_note": "≈X% of the market, N players; what the uncovered tail looks like",
  "segments": ["…", "…", "…"],
  "companies": [
    {{"brand": "…", "segment": "one of segments[]", "reason": "one line — why in scope", "source": "https://…"}}
  ]
}}
```

## Then reply with
The company count, your coverage estimate, and which 2–3 sources anchored the map.
Then STOP and wait for the operator's next prompt.
"""


def build_research_prompt(meta: dict, brands: list[str], done_n: int, total_n: int,
                          segments: list[str] | None = None) -> str:
    run_id, lang = meta["run_id"], meta["output_language"]
    save = f"logs/{run_id}/agent_runs"
    fields = [f["name"] for f in load_schema()["fields"]]
    lst = "\n".join(f"{i}. **{b}**" for i, b in enumerate(brands, 1))
    lo, hi = done_n + 1, done_n + len(brands)
    seg_rule = (f"exactly ONE of the run's agreed labels: {', '.join('«' + s + '»' for s in segments)}"
                if segments else
                "ONE short label (≤3 words), the same small label set across all companies of the run")
    return f"""# Research run — {meta['market']}: companies {lo}–{hi} of {total_n}

Research ONLY these {len(brands)} companies, **one at a time** — finish a company's
three passes and save its three files before starting the next:

{lst}

Background rules: `docs/researcher_codex_manual.md`. This prompt is self-contained.

## Process rules (non-negotiable)
- Every value comes from a page you OPENED in this chat. A `…search?query=…` URL is
  never a source — open the company card / article it returns and cite that.
- **Never write a script that generates the JSON files.** Each file is saved after
  the research for that pass is actually done. Batch-written files are detected by
  timestamp and void the run.
- No placeholder values («не подтверждено», «н/д», n/a, …). Can't confirm → leave
  the field blank and add a `review_flags` note naming the URL you opened.
- **Language:** free-text values (`description`, `latest_news`, `key_products`,
  segment label) in **{lang}**; every proper name — brand, legal entity, product —
  stays verbatim.
- Schema fields per company: {", ".join(fields)}.

## Value formats (unified — machine-checked)
- **Money** (every revenue / EBITDA / projection field): **«N млн ₽»** — millions of
  rubles, space as thousands separator, comma decimals, `~` prefix for estimates.
  Examples: «737,7 млн ₽», «48 300 млн ₽», «~1 250 млн ₽», «-120 млн ₽».
  Never «737 668 000 ₽», «48,3 млрд ₽», «1 234 тыс. руб.».
- **headcount**: a bare number/range, optional «+»: «26», «70+», «12 000». No words.
- **YoY fields** (`revenue_yoy_24_25`, `product_rev_yoy_24_25`, `ebitda_yoy_24_25`):
  «+19,6%» / «-20,8%», `~` prefix for estimates.

## entity_type — exactly one of (scope of fit to the research goal)
- `product` — ONE specific product fits the research goal;
- `brand` — a set of products under one brand fits;
- `company` — almost all of the company's products fit;
- `group` — products across multiple companies of a holding fit;
- `foreign_entity` — has revenue in Russia, but the entity is foreign.

## segment
Set `segment` to {seg_rule}. Never a descriptive sentence.

## description — a structured profile, in THIS sequence
«Name — founded [year] by [founders] (as [original company], if there was a pivot).
Who they are + their mission + what problem they solve + for whom + how they make
money + which product is the main cash cow + why they win + where they are
vulnerable + why the company matters for this research.»
**Skip any element you could not confirm — never invent one.** Flowing prose, not a
bullet list. Format example (structure, not wording):
> «AlphaSense — основана в 2011 Jack Kokko и Raj Neervannan. Разрабатывает
> AI-платформу market intelligence, которая помогает strategy- и research-командам
> быстрее находить и интерпретировать внешние рыночные сигналы. Зарабатывает на
> enterprise SaaS-подписке; ядро выручки — поисковая платформа. Преимущество —
> база бизнес-контента, AI-поиск и workflow-интеграции; уязвимость — конкуренция
> универсальных AI-инструментов и дорогой enterprise sales. Для нашего исследования
> — benchmark того, как AI меняет decision intelligence.»

## business_model / target_customers / positioning (separate fields)
- `business_model` — how they earn: model (SaaS-подписка, комиссия, лицензии,
  freemium, услуги…), pricing tiers if public.
- `target_customers` — who buys: segments, buyer roles, company size
  (SMB / mid / enterprise / госсектор), industries; flagship clients if published.
- `positioning` — category claim, key differentiators (data, tech, price,
  coverage), price tier, who they are compared against.

## Financial history 2022–2025 (Collector A's core job)
The rusprofile.ru / list-org.com company CARD publishes the multi-year financials
table — copy each year, do not stop at the latest:
- `total_revenue_2022` … `total_revenue_2025` — filed total revenue per year;
- `product_revenue_2022` … `product_revenue_2025` — the researched segment's share
  per year. EVIDENCE HIERARCHY, in order: (1) a directly published product/segment
  figure; (2) deterministic calculation from sourced inputs — a mono-product /
  pure-play company's product revenue IS its total revenue; (3) an evidence-based
  estimate from business model, product portfolio, rankings, cases, reported
  segment shares; (4) blank + review_flag ONLY when no defensible estimate can be
  made. After a reasonable attempt to find a published figure, SWITCH to (2)/(3)
  instead of re-searching a number that is not public. Use a range
  («700–900 млн ₽») when uncertainty is high — never fake precision;
- `ebitda_2022` … `ebitda_2025` — only if published (statements/press); otherwise
  blank + review_flag;
- `revenue_yoy_24_25`, `product_rev_yoy_24_25`, `ebitda_yoy_24_25` — computed from
  the corresponding 2024/2025 pairs (blank when a pair is missing);
  `revenue_2026_projection` from stated guidance or extrapolation (say which in
  `assumptions`);
- `product_revenue_source` — REQUIRED whenever product figures are filled; START
  with the basis tag: «напрямую: <URL>» | «расчёт: <формула + sourced inputs>» |
  «оценка: <сигналы + допущения>». Derived YoY/projection values inherit the
  LOWEST confidence among their inputs.

## latest_news — significant events only
Counts: **acquisition/M&A, partnership, new product launch, new technology pilot,
leadership change, major funding, a major incident or public controversy.**
Does NOT count: software-registry entries, certificates, ОКВЭД changes, offer/policy
updates, webinars, blog overviews, being mentioned in a listicle. If nothing
significant happened in ~24 months, leave blank + review_flag.

## Machine validation — rejected records come back to you as a repair pass
The app validates every `_record.json`. These cause REJECTION:
- an `inn` that is not a checksum-valid 10/12-digit ИНН (invented numbers fail the checksum);
- any source that is a search page, a repo path, or not a live URL;
- `latest_news` that is not a significant dated event (registry entries, webinars,
  overviews are auto-rejected);
- `entity_type` outside the five labels above; `segment` outside the agreed labels;
- an empty revenue history (no total_revenue_2022–2024 and no review_flag why);
- empty `description` / `segment` / `key_products`;
- a Collector B pass that copies A's text or cites no source A didn't;
- placeholder strings anywhere.
Fabricating a value only creates more work — an honest blank + review_flag passes.

## The three passes per company

### 🟦 Pass A — Collector A (official / registry / financial)
Sources: **rusprofile.ru, list-org.com, tadviser.ru, navigator.sk.ru**, ЕГРЮЛ,
bo.nalog.ru (if they render), and the company site.
- **INN / legal entity:** first the company's OWN requisites — footer, «Контакты»,
  «Реквизиты», оферта, «Политика конфиденциальности» — then confirm on the
  **rusprofile/list-org company CARD** (not a search page).
- **Financials:** the full 2022–2025 history per the «Financial history» section
  above, plus `headcount` — from the rusprofile/list-org card's financials table,
  tadviser and industry rating sources. All money in «N млн ₽».
- Every value carries a real `source` URL + `snippet`. Save `{save}/<Brand>_A.json`
  (strict JSON: `{{"entity","collector":"A","fields":{{...}}}}`).

### 🟩 Pass B — Collector B (news / market / third-party), INDEPENDENT of A
Fresh reasoning; do **not** reuse A's text or sources. Open **≥2 independent**
third-party sources (press, industry portal / ranking, analyst, vc.ru/habr). Required:
- **`description`** — the structured profile per the «description» section above
  (skip unconfirmed elements, never invent).
- **`business_model`**, **`target_customers`**, **`positioning`** — per their
  section above; third-party views (reviews, comparisons, press) beat self-praise.
- **`key_products`** — ONLY the 1–4 products of the RESEARCHED market (real names).
  Everything else the company sells — adjacent lines, modules, consulting,
  training — goes to **`other_products`** (short list; blank for pure-plays).
  A portfolio dump in key_products is auto-rejected (products-bloat).
- **`latest_news`** — a SIGNIFICANT, DATED event with source (see the latest_news
  section above for what counts).
- **revenue** from industry rating sources (sector revenue ratings — e.g. for RU
  EdTech: edtechs.ru / smart-ranking.ru; use this market's equivalent), cite year.
An empty `fields: {{}}` is a FAILED pass. Save `{save}/<Brand>_B.json`.

### 🟨 Pass V — Verifier (merge A + B)
Merge into one record: tie-break by source tier, record `conflict` ONLY where the
values genuinely differ (identical values are not a conflict), set per-field
`confidence`, drop placeholders to blank, add `review_flags` (coverage gaps, low
confidence, entity ambiguity), set `entity_match` (brand vs legal entity vs
product-of-a-group). Save `{save}/<Brand>_record.json` — its `entity` value must
match the brand EXACTLY as listed above (the app matches records to the cohort by
this string). Use a Latin/ASCII spelling of the brand for the file names.

## When these {len(brands)} are done
Reply with one line per company (INN confirmed from…? revenue source? news date?)
and STOP. The operator will paste the next batch — or a repair prompt if the gate
rejected something.
"""


def manifest(run_dir: Path) -> tuple[list[str], str | None, list[str]]:
    """Cohort brands + coverage note + segment taxonomy; handles both the new
    dict format and the legacy list format (whose `_coverage_note` entry is NOT
    a company)."""
    f = run_dir / "companies.json"
    if not f.exists():
        return [], None, []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return [], None, []
    segments: list[str] = []
    if isinstance(data, dict):
        comps, note = data.get("companies") or [], data.get("coverage_note")
        segments = [str(s).strip() for s in (data.get("segments") or []) if str(s).strip()]
    else:
        comps = [c for c in data if isinstance(c, dict) and c.get("brand")]
        note = next((c.get("_coverage_note") for c in data
                     if isinstance(c, dict) and "_coverage_note" in c), None)
    brands = [str(c.get("brand", "")).strip() for c in comps if str(c.get("brand", "")).strip()]
    return brands, note, segments


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[«»\"'`]", "", str(s))).strip().lower()


def _pending_brands(run_dir: Path, brands: list[str]) -> list[str]:
    """Cohort brands that have no record yet (matched by the record's `entity`
    string, with the file-name slug as a fallback for renamed files)."""
    ents, stems = set(), set()
    for rp in _records(run_dir):
        stems.add(_slug(rp.name[: -len("_record.json")]))
        try:
            ents.add(_norm(json.loads(rp.read_text(encoding="utf-8")).get("entity", "")))
        except Exception:
            pass
    return [b for b in brands if _norm(b) not in ents and _slug(b) not in stems]


def run_gate(run_dir: Path, write_report: bool = True) -> dict:
    _brands, _note, segments = manifest(run_dir)
    schema_fields = [f["name"] for f in load_schema()["fields"]]
    g = gate.gate_run(run_dir / "agent_runs", segments=segments,
                      schema_fields=schema_fields)
    if write_report and g["records"]:
        meta = _load_meta(run_dir)
        (run_dir / "gate_report.md").write_text(
            gate.render_gate_report(meta["market"], g), encoding="utf-8")
    return g


def _norm_label(s) -> str:
    """Comparable segment label: lowercase, ё→е, hyphen/underscore→space,
    punctuation stripped (+ kept for labels like «CRM+BPMS»), spaces collapsed."""
    s = str(s or "").lower().replace("ё", "е")
    s = re.sub(r"[-_/]+", " ", s)
    s = re.sub(r"[^\w\s+]", " ", s, flags=re.UNICODE)
    return re.sub(r"\s+", " ", s).strip()


def autofix_records(run_dir: Path) -> dict:
    """Deterministic, web-free repair of mechanical gate failures — applied to
    gate-REJECTED records only (accepted records are never touched). Handles:
    segment near-misses vs the taxonomy («Low-code BPM-платформы» → «Low-code
    BPM платформы»), unmapped entity_type labels, several ИНН in one field,
    YoY fields computable from their sourced year pair, and money values whose
    numeric head parses but a prose tail breaks the «N млн ₽» format. Only
    failures that genuinely need new evidence are left for the repair loop."""
    _brands, _note, segments = manifest(run_dir)
    g = run_gate(run_dir, write_report=False)
    seg_by_norm: dict[str, str] = {}
    for s in segments or []:
        seg_by_norm.setdefault(_norm_label(s), s)
    fixed: dict[str, list[str]] = {}
    for e in g["rejected"]:
        rec = e["record"]
        fields = rec.get("fields") or {}
        codes = {(i["field"], i["code"]) for i in e["issues"]
                 if i["severity"] == "reject"}
        notes: list[str] = []

        def val(name):
            f = fields.get(name)
            return f.get("value") if isinstance(f, dict) else f

        # segment → snap to the taxonomy label it obviously means
        if any(c in ("segment-taxonomy", "segment-long") for _f, c in codes) and seg_by_norm:
            f = fields.get("segment")
            if isinstance(f, dict):
                n = _norm_label(f.get("value"))
                target = seg_by_norm.get(n)
                if target is None and n:
                    cands = {s for k, s in seg_by_norm.items() if n in k or k in n}
                    if len(cands) == 1:
                        target = cands.pop()
                if target and str(f.get("value")) != target:
                    notes.append(f"segment: «{f.get('value')}» → «{target}»")
                    f["value"] = target

        # entity_type → canonical taxonomy label when the map knows it
        em = rec.get("entity_match") or {}
        raw_et = em.get("entity_type") or em.get("type") or val("entity_type")
        canon = gate.normalize_entity_type(raw_et)
        if raw_et and canon and str(raw_et) != canon:
            em["entity_type"] = canon
            rec["entity_match"] = em
            if isinstance(fields.get("entity_type"), dict):
                fields["entity_type"]["value"] = canon
            notes.append(f"entity_type: «{raw_et}» → «{canon}»")

        # several ИНН in one field → keep the first checksum-valid one
        if ("inn", "inn-invalid") in codes:
            f = fields.get("inn")
            if isinstance(f, dict):
                digits = re.findall(r"\d{10}|\d{12}", str(f.get("value") or ""))
                valid = [d for d in digits if gate.inn_problem(d) is None]
                if len(digits) > 1 and valid:
                    notes.append(f"inn: kept {valid[0]} of «{f.get('value')}»")
                    rec.setdefault("review_flags", []).append(
                        f"inn: несколько ИНН в поле, взят первый валидный — "
                        f"исходно «{f.get('value')}»")
                    f["value"] = valid[0]

        # YoY fields computable from their sourced year pair
        def _mln(name):
            m = gate.normalize_money(val(name) or "")
            if m:
                digits2 = re.sub(r"[^\d-]", "", m.split("млн")[0])
                try:
                    return float(digits2)
                except ValueError:
                    return None
            return None

        for yoy, (i1, i2) in gate._COMPUTED_INPUTS.items():
            if not yoy.endswith("_yoy_24_25") or val(yoy) is not None:
                continue
            v1, v2 = _mln(i1), _mln(i2)
            if v1 and v2 is not None and v1 != 0:
                pct = round((v2 - v1) / abs(v1) * 100)
                # derived values inherit the LOWEST confidence of their inputs
                confs = [str(fields[i].get("confidence", "")).lower()
                         for i in (i1, i2) if isinstance(fields.get(i), dict)]
                new_f = {"value": f"{pct}%", "source": ""}
                if "low" in confs:
                    new_f["confidence"] = "low"
                elif "medium" in confs:
                    new_f["confidence"] = "medium"
                fields[yoy] = new_f
                notes.append(f"{yoy}: computed {pct}% from {i1}/{i2}")

        # money value whose numeric head parses but a prose tail breaks it
        for name in gate.MONEY_FIELDS:
            f = fields.get(name)
            if not isinstance(f, dict) or not f.get("value"):
                continue
            v = str(f["value"])
            if gate.normalize_money(v):
                continue
            m = re.match(r"^(.*?₽)", v)
            canon_v = gate.normalize_money(m.group(1)) if m else None
            if canon_v and v != canon_v:
                rec.setdefault("review_flags", []).append(f"{name}: исходно «{v}»")
                f["value"] = canon_v
                notes.append(f"{name}: «{v[:40]}…» → «{canon_v}»")

        if notes:
            Path(e["path"]).write_text(
                json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
            fixed[e["entity"]] = notes
    if fixed:
        _event(run_dir, "autofixed", companies=len(fixed),
               fields=sum(len(v) for v in fixed.values()))
    return fixed


def salvage_records(run_dir: Path) -> dict:
    """Deterministic merge repair: copy into each record any schema field it is
    missing but its own _A.json/_B.json holds (a sourced value the verifier —
    or a careless repair pass — dropped). A/B conflicts are NOT auto-picked;
    they stay missing for the repair loop. Returns {entity: [fields copied]}."""
    schema_fields = [f["name"] for f in load_schema()["fields"]]
    ar = run_dir / "agent_runs"
    salvaged: dict[str, list[str]] = {}
    for rp in _records(run_dir):
        stem = rp.name[: -len("_record.json")]
        try:
            rec = json.loads(rp.read_text(encoding="utf-8"))
        except Exception:
            continue
        colls = {}
        for tag in ("A", "B"):
            p = ar / f"{stem}_{tag}.json"
            if p.exists():
                try:
                    colls[tag] = json.loads(p.read_text(encoding="utf-8")).get("fields") or {}
                except Exception:
                    pass
        if not colls:
            continue
        fields = rec.setdefault("fields", {})
        flags_text = " ".join(str(x) for x in (rec.get("review_flags") or [])).lower()
        copied = []
        for name in schema_fields:
            if gate.value_of(fields.get(name)) is not None:
                continue
            if f"unresolved: {name}".lower() in flags_text:
                continue   # deliberately blanked — do not resurrect from A/B
            hits = {tag: cf[name] for tag, cf in colls.items()
                    if gate.value_of(cf.get(name)) is not None
                    and not gate.is_placeholder(gate.value_of(cf.get(name)))}
            if not hits:
                continue
            if len(hits) == 2:
                va, vb = (str(gate.value_of(h)).strip() for h in hits.values())
                if va != vb:
                    continue                      # real conflict → verifier's call
            tag, f = sorted(hits.items())[0]      # prefer A (registry) on ties
            f = dict(f) if isinstance(f, dict) else {"value": f}
            f["salvaged_from"] = tag
            fields[name] = f
            copied.append(name)
        if copied:
            rp.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
            salvaged[rec.get("entity", stem)] = copied
    if salvaged:
        _event(run_dir, "salvaged",
               companies=len(salvaged), fields=sum(map(len, salvaged.values())))
    return salvaged


def next_prompt(run_dir: Path, batch: int = 3) -> tuple[str, str]:
    """The state machine: what to paste next. Returns (kind, prompt_text)."""
    batch = max(1, int(batch))
    meta = _load_meta(run_dir)
    depth = load_depths()[meta["depth"]]
    brands, _note, segments = manifest(run_dir)

    if not brands:
        kind, text = "discovery", build_discovery_prompt(meta, depth)
    else:
        salvage_records(run_dir)      # heal mechanical merge-loss before gating
        autofix_records(run_dir)      # deterministic fixes need no repair pass
        pending = _pending_brands(run_dir, brands)
        if pending:
            done_n = len(brands) - len(pending)
            kind = "research"
            text = build_research_prompt(meta, pending[:batch], done_n, len(brands), segments)
        else:
            g = run_gate(run_dir)
            if g["rejected"]:
                # repair in batches too — a 30-company repair pass would just
                # re-trigger the single-turn overload the app exists to avoid
                kind = "repair"
                # Collector-B failures first: they are computed from the _A/_B
                # files, so a record repair can never clear them — they need a
                # fresh B pass (mirrors the API-mode routing in api_runner)
                b_fail = [e for e in g["rejected"]
                          if {i["code"] for i in e["issues"]
                              if i["severity"] == "reject"} & gate.B_CODES]
                if b_fail:
                    text = gate.render_b_redo_prompt(
                        meta["market"], meta["output_language"],
                        f"logs/{meta['run_id']}/agent_runs",
                        b_fail[:max(batch, 2)])
                else:
                    text = gate.render_repair_prompt(
                        meta["market"], meta["output_language"],
                        f"logs/{meta['run_id']}/agent_runs",
                        g["rejected"][:max(batch, 2)], segments)
            else:
                kind = "done"
                text = (f"# All {len(g['accepted'])} records passed the ingest gate.\n\n"
                        f"Nothing left to paste — click **Build Excel** (or run "
                        f"`python -m src.runs build {meta['run_id']}`).\n")
    _issue_prompt(run_dir, kind, text)
    if meta.get("status") != kind:
        meta["status"] = kind
        _save_meta(run_dir, meta)
    return kind, text


# ── progress / ingest ─────────────────────────────────────────────────────────
def _records(run_dir: Path) -> list[Path]:
    return sorted((run_dir / "agent_runs").glob("*_record.json"))


def progress(run_dir: Path) -> dict:
    """Live state for the app: gate-accepted records ÷ discovered cohort."""
    brands, _note, _segments = manifest(run_dir)
    n_records = len(_records(run_dir))
    if not brands:
        if n_records:      # legacy run without a parseable manifest
            return {"done": n_records, "total": None, "pct": 50,
                    "rejected": 0, "phase": "researching (no manifest yet)"}
        return {"done": 0, "total": None, "pct": 3, "rejected": 0,
                "phase": "step 1 — discovery (paste prompt.md)"}
    g = run_gate(run_dir, write_report=False)
    accepted, rejected = len(g["accepted"]), len(g["rejected"])
    pending = len(_pending_brands(run_dir, brands))
    total = len(brands)
    pct = min(100, round(100 * accepted / max(total, 1)))
    if pending:
        phase = f"researching ({pending} to go)"
    elif rejected:
        phase = f"repair — {rejected} rejected by the gate (paste the repair prompt)"
    else:
        phase = "ready to build"
    return {"done": accepted, "total": total, "pct": pct,
            "rejected": rejected, "pending": pending, "phase": phase}


_TELEMETRY_STAGES = {           # event name → stage label (+failed twin)
    "api_discovery": "discovery", "api_company": "research",
    "api_company_failed": "research", "api_repair": "repair",
    "api_repair_failed": "repair", "api_collector_b_rerun": "b-rerun",
}
_TELEMETRY_SUMS = ("seconds", "tool_calls", "searches", "fetches",
                   "search_denied", "budget_rounds", "requests",
                   "tokens_in", "tokens_out", "grounding_affected",
                   "early_stop", "extended")


# event keys that only exist since telemetry landed — their absence in older
# events means «not recorded», never «zero»
_SPLIT_KEYS = ("searches", "fetches", "search_denied", "budget_rounds", "requests")


def _telemetry_data(run_dir: Path) -> dict | None:
    """Deterministic aggregation of events.jsonl (no LLM): per-stage sums with
    presence flags for the metrics historical events could not have recorded."""
    try:
        lines = (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return None
    from collections import Counter
    stages: dict[str, dict] = {}
    gates, autofixed, salvaged = [], 0, 0
    failed_brands: dict[str, set] = {}
    passed_brands: dict[str, set] = {}
    repair_brands: dict[str, int] = {}
    fail_cats: Counter = Counter()      # explicit error taxonomy of failures
    waste: dict[str, int] = {}          # spend burned by FAILED passes
    outcomes: Counter = Counter()       # repair effectiveness (sig vs sig_after)
    for line in lines:
        try:
            ev = json.loads(line)
        except Exception:
            continue
        name = ev.get("event", "")
        if name == "gate":
            gates.append(ev)
        elif name == "autofixed":
            autofixed += ev.get("fields", 0)
        elif name == "salvaged":
            salvaged += ev.get("fields", 0)
        stage = _TELEMETRY_STAGES.get(name)
        if not stage:
            continue
        s = stages.setdefault(stage, {"passes": 0, "failed": 0, "resumed": 0,
                                      "_has_tokens": False, "_has_seconds": False,
                                      "_has_split": False,
                                      **{k: 0 for k in _TELEMETRY_SUMS}})
        brand = ev.get("brand", "")
        if name.endswith("_failed"):
            s["failed"] += 1
            failed_brands.setdefault(stage, set()).add(brand)
            fail_cats[ev.get("category", "uncategorized")] += 1
            for k in ("seconds", "tool_calls", "tokens_in", "tokens_out"):
                v = ev.get(k)
                if isinstance(v, (int, float)) and v:
                    waste[k] = waste.get(k, 0) + v
            continue
        s["passes"] += 1
        passed_brands.setdefault(stage, set()).add(brand)
        if name == "api_repair" and brand:
            repair_brands[brand] = repair_brands.get(brand, 0) + 1
            if "sig_after" in ev:
                before = set(str(ev.get("sig", "")).split(",")) - {""}
                after = set(str(ev.get("sig_after", "")).split(",")) - {""}
                outcomes["cleared" if not after else
                         "improved" if len(after) < len(before) else
                         "no change"] += 1
        if ev.get("resumed"):
            s["resumed"] += 1
        if "tokens_in" in ev or "tokens_out" in ev:
            s["_has_tokens"] = True
        if "seconds" in ev:
            s["_has_seconds"] = True
        if any(k in ev for k in _SPLIT_KEYS):
            s["_has_split"] = True
        for k in _TELEMETRY_SUMS:
            v = ev.get(k)
            if isinstance(v, (int, float)):
                s[k] += v
    return {"stages": stages, "gates": gates, "autofixed": autofixed,
            "salvaged": salvaged, "failed_brands": failed_brands,
            "passed_brands": passed_brands, "repair_brands": repair_brands,
            "fail_cats": fail_cats, "waste": waste, "outcomes": outcomes}


def telemetry_summary(run_dir: Path) -> str:
    """Deterministic run summary (markdown) from events.jsonl — works for
    partial and completed runs, paste-ready for later manual analysis.
    Metrics that pre-telemetry events could not record read «n/a», never 0.
    Cost appears only when TOKEN_PRICE_IN/TOKEN_PRICE_OUT (USD per 1M tokens)
    are configured; source-yield only where the search/fetch split exists."""
    d = _telemetry_data(run_dir)
    if d is None:
        return "no events.jsonl yet — nothing recorded for this run"
    stages, gates = d["stages"], d["gates"]

    # live run state — meaningful for partial runs (pending) and finished ones
    out = [f"# Run summary — {run_dir.name}", ""]
    unres_live: dict[str, list] = {}
    try:
        meta = _load_meta(run_dir)
        brands, _n, _s = manifest(run_dir)
        g = run_gate(run_dir, write_report=False)
        pending = len(_pending_brands(run_dir, brands))
        out += [f"market: {meta.get('market', '?')} · depth: {meta.get('depth', '?')} "
                f"· status: {meta.get('status', '?')}",
                f"cohort: {len(brands)} · accepted: {len(g['accepted'])} · "
                f"rejected: {len(g['rejected'])} · pending research: {pending}"]
        # quality layer on top of the gate: complete vs accepted-with-gaps
        if g["accepted"]:
            schema_fields = [f["name"] for f in load_schema()["fields"]]
            pairs = [(e["entity"], gate.record_quality(e["record"], schema_fields))
                     for e in g["accepted"]]
            qs = [q for _e, q in pairs]
            unres_live = {ent: q["unresolved"] for ent, q in pairs
                          if q["unresolved"]}
            complete = sum(1 for q in qs if q["status"] == "complete")
            gaps = sum(1 for q in qs if q["missing"] or q["unresolved"])
            low = sum(1 for q in qs if q["low_confidence"])
            mand = sum(1 for q in qs if q["mandatory_gaps"])
            avg_cov = round(sum(q["coverage_pct"] for q in qs) / len(qs))
            out.append(f"quality of accepted: complete {complete} · with gaps {gaps} "
                       f"(mandatory-field gaps {mand}) · with low-confidence "
                       f"evidence {low} · avg field coverage {avg_cov}%")
            from collections import Counter as _C
            bases = _C((q["product_basis"] or "no figures") for q in qs)
            out.append("product revenue basis: " + " · ".join(
                f"{b} {n}" for b, n in sorted(bases.items(), key=lambda kv: -kv[1])))
        out.append("")
    except Exception:
        out += ["(run state unavailable — events only)", ""]

    hdr = ("| stage | passes | failed | resumed | time | tool calls | "
           "tokens in+out | searches | denied | fetches | budget hits | "
           "grounding stripped |")
    out += ["## Stages", hdr, "|" + "---|" * 12]
    tot = {k: 0 for k in _TELEMETRY_SUMS}
    tot_flags = {"tokens": False, "seconds": False, "split": False}
    gap_notes = []
    for stage in ("discovery", "research", "repair", "b-rerun"):
        s = stages.get(stage)
        if not s:
            continue
        na = "n/a"
        tokens = f"{s['tokens_in']}+{s['tokens_out']}" if s["_has_tokens"] else na
        time_c = f"{s['seconds']}s" if s["_has_seconds"] else na
        split = [str(s[k]) if s["_has_split"] else na
                 for k in ("searches", "search_denied", "fetches", "budget_rounds")]
        out.append(f"| {stage} | {s['passes']} | {s['failed']} | {s['resumed']} "
                   f"| {time_c} | {s['tool_calls']} | {tokens} | {split[0]} "
                   f"| {split[1]} | {split[2]} | {split[3]} "
                   f"| {s['grounding_affected']} |")
        for k in _TELEMETRY_SUMS:
            tot[k] += s[k]
        tot_flags["tokens"] |= s["_has_tokens"]
        tot_flags["seconds"] |= s["_has_seconds"]
        tot_flags["split"] |= s["_has_split"]
        gaps = [g_ for g_, ok in (("tokens", s["_has_tokens"]),
                                  ("timings", s["_has_seconds"]),
                                  ("search/fetch split", s["_has_split"])) if not ok]
        if gaps:
            gap_notes.append(f"{stage}: {', '.join(gaps)}")
    tokens_t = (f"{tot['tokens_in']}+{tot['tokens_out']}"
                if tot_flags["tokens"] else "n/a")
    out += ["", f"**Totals**: time {tot['seconds']}s"
            + (" (partial — some stages unrecorded)" if not all(
                s.get("_has_seconds") for s in stages.values()) else "")
            + f" · tool calls {tot['tool_calls']} · tokens {tokens_t}"
            + f" · failures {sum(s['failed'] for s in stages.values())}"
            + f" · resumes {sum(s['resumed'] for s in stages.values())}"
            + (f" · early stops {tot['early_stop']}" if tot["early_stop"] else "")
            + (f" · budget extensions {tot['extended']} calls" if tot["extended"] else "")]

    # cost — only with configured pricing AND recorded usage
    try:
        pin = float(os.environ.get("TOKEN_PRICE_IN", "") or 0)
        pout = float(os.environ.get("TOKEN_PRICE_OUT", "") or 0)
    except ValueError:
        pin = pout = 0
    if pin > 0 and pout > 0 and tot_flags["tokens"]:
        cost = tot["tokens_in"] / 1e6 * pin + tot["tokens_out"] / 1e6 * pout
        out.append(f"**Estimated cost** (recorded usage only, "
                   f"${pin}/{pout} per 1M in/out): ${cost:.2f}")

    # source yield — only derivable where the split was recorded
    if tot_flags["split"] and tot["searches"]:
        out.append(f"**Source yield** (split-recorded passes only): "
                   f"{tot['fetches']} pages opened over {tot['searches']} searches "
                   f"({tot['fetches'] / tot['searches']:.1f} per search) · "
                   f"{tot['grounding_affected']} cited sources stripped by grounding")

    # failures & retries — with the spend they burned and their categories
    if d["waste"]:
        w = d["waste"]
        out.append(f"**Failure waste** (spend of failed passes): "
                   f"{w.get('tool_calls', 0)} tool calls · "
                   f"{w.get('tokens_in', 0)}+{w.get('tokens_out', 0)} tokens · "
                   f"{w.get('seconds', 0)}s")
    if d["fail_cats"]:
        out.append("**Failures by category**: " + " · ".join(
            f"{c} {n}" for c, n in d["fail_cats"].most_common()))
    fb = d["failed_brands"].get("research", set())
    if fb:
        rec = fb & d["passed_brands"].get("research", set())
        out += ["", f"**Research retries**: {len(fb)} companies failed at least "
                f"once; {len(rec)} recovered on retry"
                + (f"; still missing: {', '.join(sorted(fb - rec))}" if fb - rec else "")]
    if d["repair_brands"]:
        top = sorted(d["repair_brands"].items(), key=lambda kv: -kv[1])[:3]
        out.append(f"**Repairs**: {sum(d['repair_brands'].values())} passes over "
                   f"{len(d['repair_brands'])} companies (most: "
                   + ", ".join(f"{b} ×{n}" for b, n in top) + ")"
                   + (" · outcomes: " + " / ".join(
                       f"{k} {v}" for k, v in d["outcomes"].most_common())
                      if d["outcomes"] else ""))
    if d["autofixed"] or d["salvaged"]:
        out.append(f"**Local healing** (no API): autofixed fields "
                   f"{d['autofixed']} · salvaged fields {d['salvaged']}")

    if gates:
        out += ["", "## Gate",
                "trajectory: " + " → ".join(
                    f"{g_['accepted']}✓/{g_['rejected']}✗" for g_ in gates[-8:])]
        last_codes = gates[-1].get("codes")
        if last_codes:
            out.append("last reject codes: " + ", ".join(
                f"{c}×{n}" for c, n in sorted(last_codes.items())))
    if unres_live:
        out += ["", "## Unresolved fields (live gate state)"]
        out += [f"- {ent}: {', '.join(flds)}"
                for ent, flds in list(unres_live.items())[:12]]
    if gap_notes:
        out += ["", "## Data gaps (recorded before telemetry — n/a, not zero)",
                *[f"- {g_}" for g_ in gap_notes]]
    return "\n".join(out)


def diagnostics_prompt(run_dir: Path) -> str:
    """Manual diagnostics workflow: refresh run_summary.md and build a concise
    ready-to-paste ChatGPT analysis prompt around it. Fully deterministic and
    offline — nothing is called or sent anywhere; the user pastes it themselves.
    Works for partial and completed runs (the summary reflects live state)."""
    summary = telemetry_summary(run_dir)
    (run_dir / "run_summary.md").write_text(summary + "\n", encoding="utf-8")
    prompt = f"""# Diagnose this market-research run

Below is the telemetry summary of one automated market-research run (multi-agent
pipeline: discovery → per-company collectors A/B → verifier merge → validation
gate → repair loop). Analyze it and answer:

1. **Run health** — failures, retries, repair concentration (livelock suspects),
   tool-budget hits, stream/timeout problems. What went wrong and where?
2. **Efficiency** — time and token/tool-call spend per stage and per accepted
   record; where is the waste?
3. **Data quality** — gate trajectory, reject codes, grounding strips, unresolved
   (blanked) fields. How trustworthy is the deliverable?
4. **Top 3–5 concrete improvements** for the next run (batch size, provider,
   search quota, repair strategy), each justified by a number from the data.

Rules: metrics marked «n/a» were not recorded (older run) — treat them as
unknown, NEVER as zero. Do not invent numbers absent from the data. If you need
per-event detail, ask me to paste events.jsonl (same folder).

---

{summary}
"""
    (run_dir / "diagnostics_prompt.md").write_text(prompt, encoding="utf-8")
    return prompt


def eta_seconds(run_dir: Path, pending: int, rejected: int) -> int | None:
    """Rough time-to-finish from this run's recorded API timings (None when
    nothing is left). Falls back to ~5 min per company before the first
    measurement; repairs count as ~40% of a research pass."""
    if pending <= 0 and rejected <= 0:
        return None
    secs = []
    try:
        for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines():
            ev = json.loads(line)
            if ev.get("event") == "api_company" and ev.get("seconds"):
                secs.append(ev["seconds"])
    except Exception:
        pass
    avg = (sum(secs) / len(secs)) if secs else 300
    return int(pending * avg + rejected * avg * 0.4)


def fmt_eta(seconds: int | None) -> str:
    if seconds is None:
        return ""
    if seconds < 90:
        return "≈1 min left"
    return f"≈{round(seconds / 60)} min left"


def deliverable_name(meta: dict) -> str:
    """`<date>_<market-slug>_research` — the client-facing file base name."""
    date = meta["run_id"].split("_", 1)[0]
    return f"{date}_{_slug(meta['market'])}_research"


def build_excel(run_dir: Path) -> Path:
    """Build the deliverable from gate-ACCEPTED records only."""
    salvage_records(run_dir)
    autofix_records(run_dir)
    g = run_gate(run_dir)
    if not g["accepted"]:
        raise SystemExit(
            f"All {len(g['records'])} records were rejected by the ingest gate — "
            f"see gate_report.md, then paste the repair prompt (Next prompt ▶).")
    headers, rows = xl.read_records([e["path"] for e in g["accepted"]])
    base = deliverable_name(_load_meta(run_dir))
    csv_out = run_dir / f"{base}.csv"
    xlsx_out = run_dir / f"{base}.xlsx"
    xl.write_csv(headers, rows, csv_out)
    xl.write_xlsx(headers, rows, xlsx_out)
    meta = _load_meta(run_dir)
    meta.update(status="built", xlsx=str(xlsx_out), built_at=_now(),
                companies=len(rows), rejected=len(g["rejected"]))
    _save_meta(run_dir, meta)
    _event(run_dir, "built_excel", rows=len(rows), rejected=len(g["rejected"]),
           xlsx=str(xlsx_out))
    return xlsx_out


# ── analysis (for the architect) ──────────────────────────────────────────────
_REGISTRY_FIELDS = ["legal_entity_name", "inn", "total_revenue_2025",
                    "product_revenue_2025", "headcount"]
# value/source primitives live in src.gate (shared with the ingest gate)
_PLACEHOLDERS = gate.PLACEHOLDERS
_domain = gate.domain
_is_search_url = gate.is_search_url


def _cyrillic_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 1.0
    cyr = sum(1 for c in letters if "Ѐ" <= c <= "ӿ")
    return cyr / len(letters)


def _real(name: str, f, em_vals: dict) -> bool:
    """True only for a genuine value — rejects placeholders, and validates INN digits."""
    if name in em_vals:
        v = em_vals[name]
    else:
        v = (f.get("value") if isinstance(f, dict) else None)
    if v in (None, "", "null"):
        return False
    s = str(v).strip().lower()
    if any(p in s for p in _PLACEHOLDERS):
        return False
    if name == "inn":
        digits = re.sub(r"\D", "", str(v))
        return bool(re.fullmatch(r"\d{10}|\d{12}", digits))
    return True


def _is_estimate(f) -> bool:
    if not isinstance(f, dict):
        return False
    v = str(f.get("value") or "").lower()
    return f.get("confidence") == "low" or "~" in v or "оцен" in v or "estimate" in v or "≈" in v


def _is_meta_news(f) -> bool:
    v = (f or {}).get("value") if isinstance(f, dict) else ""
    return gate.is_meta_news_text(v)


def _agent_activity(path: Path) -> tuple[int, set]:
    """(# non-empty fields, {source domains}) for an _A.json / _B.json file."""
    try:
        f = (json.loads(path.read_text(encoding="utf-8")).get("fields") or {})
    except Exception:
        return 0, set()
    n = sum(1 for x in f.values() if isinstance(x, dict) and x.get("value") not in (None, "", "null"))
    doms = {_domain((x or {}).get("source", "")) for x in f.values() if isinstance(x, dict)}
    doms.discard("")
    return n, doms


def analyze(run_dir: Path) -> dict:
    from collections import Counter

    meta = _load_meta(run_dir)
    lang = meta.get("output_language", "English")
    ar = run_dir / "agent_runs"
    rec_paths = _records(run_dir)
    n = len(rec_paths)
    schema_fields = [f["name"] for f in load_schema()["fields"]]

    filled_total = filled_slots = 0
    registry_hits = {k: 0 for k in _REGISTRY_FIELDS}
    lang_issues, entity_flags, repo_sources, empty_b, site_reliant = [], [], [], [], []
    inn_placeholder, rev_estimate, no_products, meta_news, weak_b = [], [], [], [], []
    per_company, agent_rows, domain_counts = [], [], Counter()

    for rp in rec_paths:
        rec = json.loads(rp.read_text(encoding="utf-8"))
        brand = rec.get("entity", "?")
        fields = rec.get("fields", {}) or {}
        em = rec.get("entity_match", {}) or {}
        em_vals = {"entity_type": em.get("entity_type"), "confidence_entity_match": em.get("confidence")}

        c_real = sum(1 for fn in schema_fields if _real(fn, fields.get(fn), em_vals))
        filled_slots += c_real
        filled_total += len(schema_fields)
        for k in _REGISTRY_FIELDS:
            if _real(k, fields.get(k), em_vals):
                registry_hits[k] += 1

        # honesty flags
        inn_f = fields.get("inn")
        if isinstance(inn_f, dict) and inn_f.get("value") and not _real("inn", inn_f, em_vals):
            inn_placeholder.append(f"{brand}: inn='{inn_f.get('value')}' (not a valid ИНН / placeholder)")
        if _real("total_revenue_2025", fields.get("total_revenue_2025"), em_vals) and _is_estimate(fields.get("total_revenue_2025")):
            rev_estimate.append(f"{brand}: revenue is estimate-only ({(fields.get('total_revenue_2025') or {}).get('value')})")
        if not _real("key_products", fields.get("key_products"), em_vals):
            no_products.append(brand)
        if _is_meta_news(fields.get("latest_news")):
            meta_news.append(f"{brand}: latest_news is a meta-comment, not a dated event")

        # sources
        srcs = []
        for f in fields.values():
            src = (f or {}).get("source") if isinstance(f, dict) else None
            if src:
                srcs.append(str(src))
                d = _domain(src)
                if d:
                    domain_counts[d] += 1
                if "docs/gold" in str(src) or str(src).startswith(("inputs/", "docs/")):
                    repo_sources.append(f"{brand}: repo-path source {src}")
        uniq_domains = sorted({_domain(s) for s in srcs if _domain(s)})
        own = _domain((fields.get("website") or {}).get("value", "")) if isinstance(fields.get("website"), dict) else ""
        own_share = (sum(1 for s in srcs if _domain(s) == own) / len(srcs)) if srcs and own else 0
        if srcs and own_share >= 0.8:
            site_reliant.append(f"{brand}: {round(own_share*100)}% of sources are its own site ({own})")

        if lang.lower().startswith("russ"):
            for fn in ("description", "latest_news"):
                v = (fields.get(fn) or {}).get("value") if isinstance(fields.get(fn), dict) else None
                if v and len(str(v)) > 25 and _cyrillic_ratio(str(v)) < 0.3:
                    lang_issues.append(f"{brand}.{fn}: looks English, not {lang}")
        if em.get("confidence") == "low" or rec.get("needs_review"):
            entity_flags.append(f"{brand}: entity_match={em.get('confidence')} — {em.get('note','')}")

        # per-agent activity (A / B / verifier)
        stem = rp.name.replace("_record.json", "")
        a_n, a_dom = _agent_activity(ar / f"{stem}_A.json")
        b_n, b_dom = _agent_activity(ar / f"{stem}_B.json")
        conflicts = sum(1 for f in fields.values() if isinstance(f, dict) and f.get("conflict"))
        b_indep = "✓" if (b_dom - a_dom) else ("—" if b_n else "empty")
        if b_n == 0:
            empty_b.append(brand)
        elif not (b_dom - a_dom):
            weak_b.append(f"{brand}: Collector B used no source A didn't (not independent)")
        agent_rows.append({"brand": brand, "a_n": a_n, "b_n": b_n,
                           "b_indep": b_indep, "conflicts": conflicts})

        missing = [k for k in ("inn", "total_revenue_2025", "product_revenue_2025", "headcount")
                   if not _real(k, fields.get(k), em_vals)]
        per_company.append({
            "brand": brand, "coverage": round(100 * c_real / len(schema_fields)),
            "inn": "✓" if _real("inn", inn_f, em_vals) else "—",
            "total_rev": "✓" if _real("total_revenue_2025", fields.get("total_revenue_2025"), em_vals) else "—",
            "products": "✓" if _real("key_products", fields.get("key_products"), em_vals) else "—",
            "news": "✓" if (_real("latest_news", fields.get("latest_news"), em_vals) and not _is_meta_news(fields.get("latest_news"))) else "—",
            "n_sources": len(set(srcs)), "domains": uniq_domains, "missing": missing,
        })

    coverage = round(100 * filled_slots / filled_total) if filled_total else 0
    metrics = {
        "companies": n, "coverage_pct": coverage,
        "registry_fill": {k: f"{v}/{n}" for k, v in registry_hits.items()},
        "domain_histogram": domain_counts.most_common(),
        "per_company": per_company, "agent_rows": agent_rows,
        "language_issues": lang_issues, "entity_flags": entity_flags,
        "repo_path_sources": repo_sources, "empty_collector_b": empty_b, "weak_b": weak_b,
        "site_reliant": site_reliant, "inn_placeholder": inn_placeholder,
        "rev_estimate": rev_estimate, "no_products": no_products, "meta_news": meta_news,
    }
    (run_dir / "analysis.md").write_text(_render_analysis(meta, metrics), encoding="utf-8")
    _event(run_dir, "analyzed", coverage_pct=coverage, companies=n)
    return metrics


def _render_analysis(meta: dict, m: dict) -> str:
    rf = m["registry_fill"]

    def flag_block(title, items):
        if not items:
            return f"- **{title}:** none\n"
        return f"- **{title}:** {len(items)}\n" + "".join(f"    - {i}\n" for i in items)

    total_src = sum(c for _, c in m["domain_histogram"]) or 1
    hist = "".join(f"| {d or '(none)'} | {c} | {round(100*c/total_src)}% |\n"
                   for d, c in m["domain_histogram"]) or "| (no sources) | 0 | 0% |\n"

    rows = ""
    for c in m["per_company"]:
        doms = ", ".join(c["domains"][:3]) + ("…" if len(c["domains"]) > 3 else "")
        miss = ", ".join(c["missing"]) or "—"
        rows += (f"| {c['brand']} | {c['coverage']}% | {c['inn']} | {c['total_rev']} | "
                 f"{c['products']} | {c['news']} | {c['n_sources']} | {doms} | {miss} |\n")

    agent = ""
    for a in m["agent_rows"]:
        agent += f"| {a['brand']} | {a['a_n']} | {a['b_n']} | {a['b_indep']} | {a['conflicts']} |\n"

    return f"""# Run analysis — {meta['market']}  ({meta['depth']})

Model: {MODELS.get(meta['model'], meta['model'])}  |  Language: {meta['output_language']}
Run: `{meta['run_id']}`  |  Companies: **{m['companies']}**  |  **Real** coverage: **{m['coverage_pct']}%**
(“Real” = placeholders like «не подтверждено» and invalid INNs are counted as empty.)

## Registry / financial coverage (Collector A's core job — real values only)
| field | filled |
|---|---|
| legal_entity_name | {rf['legal_entity_name']} |
| inn | {rf['inn']} |
| total_revenue_2025 | {rf['total_revenue_2025']} |
| product_revenue_2025 | {rf['product_revenue_2025']} |
| headcount | {rf['headcount']} |

## Agent activity (did A, B and the Verifier each do their job?)
`A#`/`B#` = non-empty fields each collector returned; `B indep` = did B use a source A
didn't; `conflicts` = fields the verifier marked as an A/B disagreement.

| company | A# | B# | B indep | conflicts |
|---|---|---|---|---|
{agent}
## Sources actually consulted (domain histogram)
| domain | # field-sources | share |
|---|---|---|
{hist}
## Per-company breakdown
| company | coverage | INN | 2025 rev | products | news | # src | main domains | missing |
|---|---|---|---|---|---|---|---|---|
{rows}
## Quality flags
{flag_block("INN placeholder / invalid (counted as empty)", m['inn_placeholder'])}{flag_block("Revenue is estimate-only (no filed/rating figure)", m['rev_estimate'])}{flag_block("key_products missing", m['no_products'])}{flag_block("latest_news is a meta-comment, not a dated event", m['meta_news'])}{flag_block("Collector B not independent (no new source vs A)", m['weak_b'])}{flag_block("Empty Collector-B passes", m['empty_collector_b'])}{flag_block("Over-reliant on own website (≥80% of sources)", m['site_reliant'])}{flag_block("Language not in " + meta['output_language'], m['language_issues'])}{flag_block("Entity-match / product-vs-legal", m['entity_flags'])}{flag_block("Contaminated (repo-path) sources", m['repo_path_sources'])}
## Architect notes
Recurring failures → tighten the rule in `prompts/` or the run prompt. INN placeholders
mean the agent hit a rusprofile *search* page and gave up → reinforce "read the company
requisites (footer/контакты/оферта/политика конфиденциальности), then open the CARD."
Estimate-only revenue → push industry rating sources (edtechs.ru / smart-ranking.ru).
Missing products / meta-news → Collector B under-delivered.
"""


# ── publish: the clean final deliverable in docs/ (logs stay as history) ──────
def publish_run(run_dir: Path) -> Path:
    """Build+analyze if needed, then copy the FINAL deliverable to docs/<market>/."""
    meta = _load_meta(run_dir)
    base = deliverable_name(meta)
    if not (run_dir / f"{base}.xlsx").exists():
        build_excel(run_dir)
        meta = _load_meta(run_dir)
    analyze(run_dir)
    dest = docs_dir_for(run_dir)
    dest.mkdir(parents=True, exist_ok=True)
    copies = {
        f"{base}.xlsx": f"{base}.xlsx",
        f"{base}.csv": f"{base}.csv",
        "analysis.md": "analysis.md",
        "companies.json": "companies.json",
    }
    for src, dst in copies.items():
        if (run_dir / src).exists():
            shutil.copy2(run_dir / src, dest / dst)
    (dest / "README.md").write_text(
        f"# {meta['market']} — final deliverable\n\n"
        f"Depth: {meta['depth']}  ·  Model: {MODELS.get(meta['model'], meta['model'])}  ·  "
        f"Language: {meta['output_language']}\n\n"
        f"- **{base}.xlsx** — the research table (one row per company).\n"
        f"- **analysis.md** — quality read of the run.\n"
        f"- **companies.json** — the discovered cohort + segment taxonomy.\n\n"
        f"Source run (history): `logs/{meta['run_id']}/`.\n",
        encoding="utf-8")
    meta.update(status="published", published_to=str(dest))
    _save_meta(run_dir, meta)
    _event(run_dir, "published", dest=str(dest))
    return dest


# ── small io helpers ──────────────────────────────────────────────────────────
def _load_meta(run_dir: Path) -> dict:
    return json.loads((run_dir / "run.json").read_text(encoding="utf-8"))


def _save_meta(run_dir: Path, meta: dict) -> None:
    (run_dir / "run.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


_EVENT_LOCK = threading.Lock()   # concurrent company threads append safely


def _event(run_dir: Path, event: str, **kw) -> None:
    line = json.dumps({"ts": _now(), "event": event, **kw}, ensure_ascii=False)
    with _EVENT_LOCK, (run_dir / "events.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def list_runs() -> list[dict]:
    if not LOGS.exists():
        return []
    out = []
    for d in sorted(LOGS.iterdir(), reverse=True):
        meta_f = d / "run.json"
        if d.is_dir() and meta_f.exists():
            out.append(_load_meta(d))
    return out


def run_dir_for(run_id: str) -> Path:
    d = LOGS / run_id
    if not (d / "run.json").exists():
        raise SystemExit(f"no run '{run_id}' under {LOGS}")
    return d


def docs_dir_for(run_dir: Path) -> Path:
    """The docs/ deliverable folder for a run's market (created on publish)."""
    return DOCS / _slug(_load_meta(run_dir)["market"])


# ── CLI ───────────────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(description="Research run-log manager (prep + ingest).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--market", required=True)
    c.add_argument("--depth", required=True, choices=list(load_depths()))
    c.add_argument("--model", default="chatgpt", choices=list(MODELS))
    sub.add_parser("list")
    for name in ("build", "analyze", "progress", "prompt", "publish", "gate",
                 "salvage", "telemetry"):
        p = sub.add_parser(name)
        p.add_argument("run_id")
    n = sub.add_parser("next")
    n.add_argument("run_id")
    n.add_argument("--batch", type=int, default=3,
                   help="companies per research prompt (default 3)")
    args = ap.parse_args()

    if args.cmd == "create":
        d = create_run(args.market, args.depth, args.model)
        print(f"created {d}\n→ paste {d/'prompt.md'} into {MODELS[args.model]}")
    elif args.cmd == "next":
        kind, text = next_prompt(run_dir_for(args.run_id), args.batch)
        print(f"--- next prompt: {kind} (also written to prompt.md) ---\n")
        print(text)
    elif args.cmd == "gate":
        g = run_gate(run_dir_for(args.run_id))
        print(f"accepted {len(g['accepted'])} / rejected {len(g['rejected'])} "
              f"of {len(g['records'])} records → gate_report.md")
        for fl in g["run_flags"]:
            print(f"⚠️  {fl}")
    elif args.cmd == "salvage":
        s = salvage_records(run_dir_for(args.run_id))
        if not s:
            print("nothing to salvage — records already carry their collectors' fields")
        for ent, flds in s.items():
            print(f"{ent}: restored {len(flds)} fields ({', '.join(flds[:6])}{'…' if len(flds) > 6 else ''})")
    elif args.cmd == "telemetry":
        rd = run_dir_for(args.run_id)
        text = telemetry_summary(rd)
        (rd / "run_summary.md").write_text(text + "\n", encoding="utf-8")
        print(text)
        print(f"\n[export] written to {rd / 'run_summary.md'} — paste-ready markdown")
    elif args.cmd == "list":
        for m in list_runs():
            print(f"{m['run_id']:50}  {m['status']:12}  {m['market']} [{m['depth']}]")
    elif args.cmd == "prompt":
        print((run_dir_for(args.run_id) / "prompt.md").read_text(encoding="utf-8"))
    elif args.cmd == "progress":
        print(progress(run_dir_for(args.run_id)))
    elif args.cmd == "build":
        print(f"built {build_excel(run_dir_for(args.run_id))}")
    elif args.cmd == "analyze":
        m = analyze(run_dir_for(args.run_id))
        print(json.dumps(m, ensure_ascii=False, indent=2))
    elif args.cmd == "publish":
        print(f"published → {publish_run(run_dir_for(args.run_id))}")


if __name__ == "__main__":
    main()
