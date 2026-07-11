"""
Ingest gate — machine validation of researcher records BEFORE they enter the
deliverable.

runs.analyze() *reports* quality after the fact; the gate *rejects* records that
show fabrication or rule-breaking, and its issues drive the auto-generated repair
prompt the operator pastes back to the researcher. This closes the loop that
previously ended in analysis.md.

Severity:
  reject — the record is excluded from the deliverable and lands in the repair
           prompt (fabrication signals, banned sources, empty required fields).
  warn   — surfaced in gate_report.md but does not block the record.

Checks:
  inn-invalid      inn present but not a checksum-valid 10/12-digit ИНН
  placeholder      «не подтверждено» / n/a / … used as a value
  search-url       a filled field sourced to a search page
  bad-source       source is a repo path or not a live URL
  unsourced        an evidence field has a value but no source at all
  meta-news        latest_news is a meta-comment, not a dated event
  required-empty   description / segment / key_products blank
  b-missing/b-empty/b-copy/b-no-new-source   Collector B pass fabricated or skipped
  product-source-missing   product revenue filled but its method column is empty
  fake-conflict    (warn) conflict recorded where a == b
  bo-nalog-unopened(warn) bo.nalog.ru cited without a figure-bearing snippet
  blank-no-flag    (warn) registry field blank with no review_flag naming it
  batch-write      (run-level warn) ≥3 record files written within seconds
"""
from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlparse

# Strings that are NOT real values — they hide a gap. Long phrases match as
# substrings; short tokens must equal the WHOLE value (an em-dash inside a real
# sentence is not a placeholder).
PLACEHOLDERS = ("не подтверж", "не найден", "нет данных", "неизвестн",
                "unknown", "отсутству", "не монетиз", "не раскры")
PLACEHOLDERS_EXACT = ("—", "-", "–", "н/д", "n/a", "na", "tbd", "none", "null")

# Must be filled for any active company (an honest blank is not possible here).
REQUIRED_FIELDS = ("description", "segment", "key_products",
                   "business_model", "target_customers", "positioning")
# Registry fields where an honest blank needs a review_flags entry naming it.
REGISTRY_FIELDS = ("legal_entity_name", "inn", "total_revenue_2025", "headcount")
# Judgment fields that don't need their own source URL.
_META_FIELDS = ("entity_type", "confidence_entity_match")

# Money fields — must read «N млн ₽» (normalize_money canonicalizes on export).
MONEY_FIELDS = tuple(f"{p}_{y}" for p in ("product_revenue", "total_revenue", "ebitda")
                     for y in range(2022, 2026)) + ("revenue_2026_projection",)
# YoY fields — must carry a % (normalize_percent canonicalizes on export).
PERCENT_FIELDS = ("revenue_yoy_24_25", "product_rev_yoy_24_25", "ebitda_yoy_24_25")
# Revenue history: if ALL of these are blank and no review_flag explains why,
# the researcher skipped the rusprofile/list-org history table.
HISTORY_FIELDS = ("total_revenue_2022", "total_revenue_2023", "total_revenue_2024")

# entity_type taxonomy = scope of fit to the research goal.
ENTITY_TYPES = ("product", "brand", "company", "group", "foreign_entity")
_ENTITY_TYPE_MAP = {                      # legacy / free-form labels → taxonomy
    "legal-entity": "company", "legal-entity-name": "company",
    "product-of-a-group": "product", "product-of-group": "product",
    "product-of-company": "product", "product-of-legal-entity": "product",
    "foreign-legal-entity": "foreign_entity", "foreign-entity": "foreign_entity",
    "holding": "group", "holding-group": "group",
}

_MAX_SEGMENT_LEN = 40    # a segment is a short label, not a sentence

# latest_news that mentions these is routine noise, not a significant event.
_INSIGNIFICANT_NEWS = ("реестр", "оквэд", "оферт", "вебинар", "свидетельств",
                       "госрегистрац", "зарегистрирован", "обновлена карточка",
                       "выпустил обзор", "опубликован обзор",
                       "опубликована новая редакция")

_B_SIMILARITY = 0.85     # A/B text similarity at or above this = copied pass

# Codes derived from the _A/_B collector files — a record edit can never fix
# them; both prompt mode and API mode route these to a fresh Collector B pass.
B_CODES = frozenset({"b-missing", "b-empty", "b-copy", "b-no-new-source"})

# Product-revenue figures whose method must be named in product_revenue_source
_PRODUCT_REV_FIELDS = ("product_revenue_2022", "product_revenue_2023",
                       "product_revenue_2024", "product_revenue_2025",
                       "product_rev_yoy_24_25")


def _norm_name(s: str) -> str:
    """Comparable product/brand name: lowercase, quotes/brackets/punctuation
    stripped, whitespace collapsed."""
    s = re.sub(r"[«»\"'()\[\].,;:!?]", " ", str(s).lower())
    return re.sub(r"\s+", " ", s).strip()


def _is_brand_name(value: str, brand: str) -> bool:
    """True when a key_products value is just the brand's own (official) name —
    exact normalized match, or a short value (<40 chars) that contains the
    brand (e.g. «Контур.Эльба (онлайн-бухгалтерия)»). Longer text and text
    without the brand in it is NOT exempt: identical descriptive prose in
    key_products still counts as a copied pass."""
    v, b = _norm_name(value), _norm_name(brand)
    return bool(b) and (v == b or (len(v) < 40 and b in v))
_BATCH_WINDOW_S = 5      # record files written within this window = one batch
_BATCH_MIN = 3


# ── value / source primitives (shared with runs.analyze) ─────────────────────
def domain(url: str) -> str:
    try:
        net = urlparse(str(url)).netloc.lower()
        return net[4:] if net.startswith("www.") else net
    except Exception:
        return ""


def is_search_url(url) -> bool:
    u = str(url or "").lower()
    return "search?" in u or "/search" in u or "query=" in u


def is_placeholder(v) -> bool:
    """A stub standing in for data. Substring stubs («не раскрыва…», «нет
    данных») only count when the value is short or carries no figure at all —
    a long methodology note that SAYS the figure isn't published and then
    explains the estimate («…не раскрывается отдельно; оценка ~2% по структуре
    выручки…») is real content, not a placeholder."""
    s = str(v).strip().lower()
    if s in PLACEHOLDERS_EXACT:
        return True
    return ((len(s) <= 60 or not re.search(r"\d", s))
            and any(p in s for p in PLACEHOLDERS))


def is_insignificant_news(v) -> bool:
    s = str(v or "").lower()
    return any(k in s for k in _INSIGNIFICANT_NEWS)


def normalize_entity_type(v) -> str | None:
    """Map a label onto the entity-type taxonomy; None if it doesn't fit."""
    s = re.sub(r"[\s_]+", "-", str(v or "").strip().lower())
    s = _ENTITY_TYPE_MAP.get(s, s).replace("-", "_")
    return s if s in ENTITY_TYPES else None


# ── value normalization (money «N млн ₽», headcount, percent) ────────────────
_NUM = r"[+\-−]?\d[\d\s  ]*(?:[.,]\d+)?"
_MONEY_RE = re.compile(
    rf"^\s*(?P<est>[~≈]\s*|оцен\w*\W*)?(?P<num>{_NUM})\s*"
    rf"(?P<unit>млрд|млн|тыс)?\.?\s*(?P<cur>₽|руб\w*\.?|rub\.?)?\s*$", re.I)


def normalize_money(v) -> str | None:
    """Canonicalize any ruble amount to «N млн ₽» (~ prefix kept for estimates).
    None if the value can't be parsed confidently."""
    m = _MONEY_RE.match(str(v).strip())
    if not m:
        return None
    num = (m.group("num").replace(" ", "").replace(" ", "")
           .replace(" ", "").replace("−", "-").replace(",", "."))
    if num.count(".") > 1:                      # dots were thousands separators
        num = num.replace(".", "")
    try:
        x = float(num)
    except ValueError:
        return None
    unit = (m.group("unit") or "").lower()
    if unit == "млрд":
        x *= 1000
    elif unit == "тыс":
        x /= 1000
    elif unit != "млн":
        if m.group("cur") or abs(x) >= 1_000_000:   # plain rubles
            x /= 1_000_000
        else:
            return None                             # bare small number: ambiguous
    est = "~" if m.group("est") else ""
    sign = "-" if x < 0 else ""
    ax = abs(x)
    out = (f"{ax:,.0f}".replace(",", " ") if ax >= 100
           else f"{ax:.1f}".rstrip("0").rstrip(".").replace(".", ","))
    return f"{est}{sign}{out} млн ₽"


_HC_RE = re.compile(r"^\s*(?P<est>[~≈]|оцен\w*\W*)?\s*(?P<num>\d[\d\s ]*)"
                    r"(?P<k>\s*тыс\.?)?\s*(?P<plus>\+)?[a-zа-яё&.\s\-/()]*$", re.I)


def normalize_headcount(v) -> str | None:
    """«12 тыс. сотрудников» → «12 000», «700+ R&D сотрудников» → «700+», …"""
    m = _HC_RE.match(str(v).strip())
    if not m:
        return None
    n = int(m.group("num").replace(" ", "").replace(" ", ""))
    if m.group("k"):
        n *= 1000
    est = "~" if m.group("est") else ""
    return est + f"{n:,}".replace(",", " ") + ("+" if m.group("plus") else "")


def normalize_percent(v) -> str | None:
    """Light cleanup for YoY: ≈→~, «оценка»→~; None when there's no % at all."""
    s = re.sub(r"оцен\w*\s*", "~", str(v).strip(), flags=re.I)
    s = s.replace("≈", "~").replace("−", "-")
    return s if "%" in s else None


def is_meta_news_text(v) -> bool:
    s = str(v or "").lower()
    if not s:
        return False
    metaish = any(k in s for k in ("подтвержда", "присутстви", "публичн",
                                   "confirm", "sources indicate"))
    return metaish and not re.search(r"20\d\d", s)


def value_of(f):
    v = f.get("value") if isinstance(f, dict) else f
    return None if v in (None, "", "null") else v


def inn_problem(v) -> str | None:
    """None if the INN is a checksum-valid 10/12-digit number, else the reason."""
    digits = re.sub(r"\D", "", str(v))
    if len(digits) not in (10, 12):
        return f"'{v}' is not a 10- or 12-digit ИНН"

    def ctrl(digs: str, weights: list[int]) -> int:
        return sum(int(d) * w for d, w in zip(digs, weights)) % 11 % 10

    if len(digits) == 10:
        ok = ctrl(digits[:9], [2, 4, 10, 3, 5, 9, 4, 6, 8]) == int(digits[9])
    else:
        ok = (ctrl(digits[:10], [7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) == int(digits[10])
              and ctrl(digits[:11], [3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) == int(digits[11]))
    if not ok:
        return (f"'{v}' fails the official ИНН checksum — mistyped or invented; "
                f"re-read it from the registry card")
    return None


# ── per-record validation ─────────────────────────────────────────────────────
def validate_record(rec: dict, a: dict | None, b: dict | None,
                    segments: list[str] | None = None,
                    schema_fields: list[str] | None = None) -> list[dict]:
    """All issues for one verifier record (+ its collector A/B files).
    `segments` = the run's agreed segment taxonomy (from companies.json);
    `schema_fields` enables the merge-loss check (record vs its collectors)."""
    issues: list[dict] = []
    fields = rec.get("fields") or {}
    flags_text = " ".join(str(x) for x in (rec.get("review_flags") or [])).lower()

    def add(field: str, severity: str, code: str, reason: str) -> None:
        issues.append({"field": field, "severity": severity, "code": code, "reason": reason})

    for name, f in fields.items():
        v = value_of(f)
        if v is None:
            continue
        if is_placeholder(v):
            add(name, "reject", "placeholder",
                f"value «{v}» is a placeholder — leave blank + review_flag instead")
        src = f.get("source") if isinstance(f, dict) else None
        if src:
            if is_search_url(src):
                add(name, "reject", "search-url",
                    f"source is a search page ({src}) — a value must come from the card/article the search returns")
            elif not str(src).startswith(("http://", "https://")):
                add(name, "reject", "bad-source",
                    f"source «{src}» is not a live public URL")
            elif "bo.nalog" in str(src) and not re.search(r"\d", str(f.get("snippet") or "")):
                add(name, "warn", "bo-nalog-unopened",
                    "bo.nalog.ru cited without a figure-bearing snippet — was the page actually opened?")
        elif name not in _META_FIELDS:
            add(name, "reject", "unsourced", "value has no source URL")
        conflict = f.get("conflict") if isinstance(f, dict) else None
        if isinstance(conflict, dict):
            av, bv = str(conflict.get("a", "")).strip(), str(conflict.get("b", "")).strip()
            if av and av == bv:
                add(name, "warn", "fake-conflict",
                    "conflict recorded but a == b — a real conflict has two different values")

    inn_v = value_of(fields.get("inn"))
    if inn_v is not None and not is_placeholder(inn_v):
        why = inn_problem(inn_v)
        if why:
            add("inn", "reject", "inn-invalid", why)

    news_v = value_of(fields.get("latest_news"))
    if news_v and is_meta_news_text(news_v):
        add("latest_news", "reject", "meta-news",
            "a meta-comment, not a dated event — find a significant dated event, or leave blank")
    elif news_v and is_insignificant_news(news_v):
        add("latest_news", "reject", "insignificant-news",
            "routine noise (registry entry / certificate / ОКВЭД / offer update / webinar / "
            "blog overview) — only M&A, partnership, product launch, tech pilot, leadership "
            "change, major funding or a major incident count")

    # entity_type must fit the taxonomy (scope of fit to the research goal);
    # some records write it under entity_match.type — read both keys
    em = rec.get("entity_match") or {}
    et = em.get("entity_type") or em.get("type") or value_of(fields.get("entity_type"))
    if et and normalize_entity_type(et) is None:
        add("entity_type", "reject", "entity-type",
            f"«{et}» is not in the taxonomy {'/'.join(ENTITY_TYPES)}")

    # key_products must stay scoped to the researched market — a portfolio dump
    # belongs in other_products
    kp_v = value_of(fields.get("key_products"))
    if kp_v is not None:
        kp_s = str(kp_v)
        n_items = len([p for p in kp_s.split(";") if p.strip()])
        if n_items > 5 or (";" not in kp_s and len(kp_s) > 300):
            add("key_products", "reject", "products-bloat",
                f"{n_items if n_items > 5 else len(kp_s)} {'items' if n_items > 5 else 'chars'} — "
                "keep ONLY the researched market's products (1–4 names); move the rest to other_products")

    # merge-loss: a value the record's OWN collectors found must survive the
    # merge — this is what makes 'repair only the listed fields' safe
    if schema_fields:
        lost = []
        for name in schema_fields:
            if value_of(fields.get(name)) is not None:
                continue
            for coll in (a, b):
                cf = (coll or {}).get("fields") or {}
                if value_of(cf.get(name)) is not None and not is_placeholder(value_of(cf.get(name))):
                    lost.append(name)
                    break
        if lost:
            add(", ".join(lost[:8]) + ("…" if len(lost) > 8 else ""), "reject", "merge-loss",
                f"{len(lost)} field(s) have a sourced value in _A.json/_B.json but are missing "
                f"from the record — the record must keep the full A+B merge")

    # segment must be a short label from the run's agreed taxonomy
    seg_v = value_of(fields.get("segment"))
    if seg_v is not None:
        seg_s = str(seg_v).strip()
        if segments:
            allowed = {s.casefold().strip() for s in segments}
            if seg_s.casefold() not in allowed:
                add("segment", "reject", "segment-taxonomy",
                    f"«{seg_s[:60]}…» is not one of the run's segments: {', '.join(segments)}"
                    if len(seg_s) > 60 else
                    f"«{seg_s}» is not one of the run's segments: {', '.join(segments)}")
        elif len(seg_s) > _MAX_SEGMENT_LEN:
            add("segment", "reject", "segment-long",
                f"a descriptive sentence ({len(seg_s)} chars) — use one short label (≤3 words), "
                f"the same label set across all companies of the run")

    # unified number formats (export canonicalizes what it can parse)
    for name in MONEY_FIELDS:
        v = value_of(fields.get(name))
        if v is not None and not is_placeholder(v) and normalize_money(v) is None:
            add(name, "warn", "num-format",
                f"«{v}» is not a parseable ruble amount — write «N млн ₽» (e.g. «737,7 млн ₽»)")
    hc_v = value_of(fields.get("headcount"))
    if hc_v is not None and not is_placeholder(hc_v) and normalize_headcount(hc_v) is None:
        add("headcount", "warn", "num-format",
            f"«{hc_v}» — write a bare number/range, e.g. «26», «70+», «12 000»")
    for name in PERCENT_FIELDS:
        v = value_of(fields.get(name))
        if v is not None and not is_placeholder(v) and normalize_percent(v) is None:
            add(name, "warn", "num-format",
                f"«{v}» — write a percentage: «+19,6%» / «-20,8%», «~» for estimates")

    # description should follow the structured profile sequence — a one-liner
    # almost certainly skipped it
    desc_v = value_of(fields.get("description"))
    if desc_v is not None and len(str(desc_v)) < 120:
        add("description", "warn", "description-thin",
            "too short for the profile sequence (founded/founders → who they are → "
            "mission → problem → for whom → how they earn → cash cow → why they win → "
            "vulnerability → why it matters for this research); skip unknown parts, "
            "never invent them")

    for name in REQUIRED_FIELDS:
        if value_of(fields.get(name)) is None:
            add(name, "reject", "required-empty",
                "required for an active company — an empty value means the pass was not done")

    # revenue history 2022–2024: rusprofile/list-org cards show it — skipping
    # the history table is a coverage failure, an honest blank needs a flag
    if all(value_of(fields.get(k)) is None for k in HISTORY_FIELDS):
        # only a flag that speaks to the HISTORY years (or non-filing) counts —
        # a note about the 2025 estimate does not excuse skipping 2022–2024
        if not any(w in flags_text for w in ("2022", "2023", "2024", "истори",
                                             "не публик", "не файлит", "not filed",
                                             "не сдает", "не сдаёт", "не раскрыва")):
            add("total_revenue_2022–2024", "reject", "history-missing",
                "no revenue history at all — the rusprofile/list-org company card shows "
                "2022–2024 revenue; copy each year, or add a review_flag saying why it is "
                "genuinely unavailable")

    for name in REGISTRY_FIELDS:
        v = value_of(fields.get(name))
        if (v is None or is_placeholder(v)) and name not in flags_text:
            add(name, "warn", "blank-no-flag",
                "blank with no review_flags entry naming the registry source that was opened")

    # product-revenue figures without their method column: a reader cannot tell
    # a filed number from a calculated estimate
    prs = value_of(fields.get("product_revenue_source"))
    if (prs is None or is_placeholder(prs)) and any(
            value_of(fields.get(n)) is not None for n in _PRODUCT_REV_FIELDS):
        add("product_revenue_source", "reject", "product-source-missing",
            "product revenue figures are filled but product_revenue_source is empty — "
            "state whether they come directly from a filing/rating or are estimated, and how")

    # Collector B independence (the most-faked part of past runs)
    if b is None:
        add("collector_B", "reject", "b-missing",
            "no _B.json saved — the Collector B pass was not run")
    else:
        b_fields = b.get("fields") or {}
        if not any(value_of(f) is not None for f in b_fields.values()):
            add("collector_B", "reject", "b-empty", "Collector B returned no values — a failed pass")
        elif a is not None:
            a_fields = a.get("fields") or {}
            brand = str(rec.get("entity") or a.get("entity") or b.get("entity") or "")
            for name in ("description", "latest_news", "key_products"):
                av, bv = value_of(a_fields.get(name)), value_of(b_fields.get(name))
                if not (av and bv):
                    continue
                # key_products of a single-product brand IS the brand's own
                # name — both collectors reporting it honestly is not copying.
                # Prose fields keep the strict check; short non-brand text too.
                if name == "key_products" and _is_brand_name(str(av), brand):
                    continue
                if SequenceMatcher(None, str(av), str(bv)).ratio() >= _B_SIMILARITY:
                    add("collector_B", "reject", "b-copy",
                        f"B's {name} is a copy of A's text — B must research independently")
                    break
            a_doms = {domain(f.get("source", "")) for f in a_fields.values() if isinstance(f, dict)} - {""}
            b_doms = {domain(f.get("source", "")) for f in b_fields.values() if isinstance(f, dict)} - {""}
            if b_doms and not (b_doms - a_doms):
                add("collector_B", "reject", "b-no-new-source",
                    "B cited no source A didn't use — not an independent pass")
    return issues


# ── whole-run gate ────────────────────────────────────────────────────────────
def _load(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def gate_run(agent_dir: Path, segments: list[str] | None = None,
             schema_fields: list[str] | None = None) -> dict:
    """Validate every *_record.json; split into accepted / rejected + run flags."""
    out = {"records": [], "accepted": [], "rejected": [], "run_flags": []}
    mtimes = []
    for rp in sorted(agent_dir.glob("*_record.json")):
        stem = rp.name[: -len("_record.json")]
        rec = _load(rp)
        if rec is None:
            entry = {"entity": stem, "path": rp, "stem": stem, "verdict": "rejected",
                     "record": {}, "issues": [{"field": "_file", "severity": "reject",
                                               "code": "bad-json", "reason": "file is not valid JSON"}]}
        else:
            issues = validate_record(rec, _load(agent_dir / f"{stem}_A.json"),
                                     _load(agent_dir / f"{stem}_B.json"),
                                     segments, schema_fields)
            verdict = "rejected" if any(i["severity"] == "reject" for i in issues) else "accepted"
            entry = {"entity": rec.get("entity", stem), "path": rp, "stem": stem,
                     "verdict": verdict, "record": rec, "issues": issues}
        out["records"].append(entry)
        out["accepted" if entry["verdict"] == "accepted" else "rejected"].append(entry)
        mtimes.append(rp.stat().st_mtime)

    mtimes.sort()
    for i in range(len(mtimes)):
        j = i
        while j + 1 < len(mtimes) and mtimes[j + 1] - mtimes[i] <= _BATCH_WINDOW_S:
            j += 1
        if j - i + 1 >= _BATCH_MIN:
            out["run_flags"].append(
                f"{j - i + 1} record files were written within {_BATCH_WINDOW_S}s of each other — "
                "either generated in one batch instead of researched one by one, or bulk-copied "
                "into the repo (check the prompt timeline in events.jsonl before judging)")
            break
    return out


# ── rendering: gate report + repair prompt ────────────────────────────────────
def render_gate_report(market: str, g: dict) -> str:
    lines = [f"# Ingest gate report — {market}",
             "",
             f"Records: **{len(g['records'])}** · accepted: **{len(g['accepted'])}** · "
             f"rejected: **{len(g['rejected'])}**", ""]
    for fl in g["run_flags"]:
        lines.append(f"> ⚠️ **Run flag:** {fl}")
    if g["run_flags"]:
        lines.append("")
    lines += ["| company | verdict | rejects | warns | issue codes |", "|---|---|---|---|---|"]
    for e in g["records"]:
        rej = [i for i in e["issues"] if i["severity"] == "reject"]
        wrn = [i for i in e["issues"] if i["severity"] == "warn"]
        codes = ", ".join(sorted({i["code"] for i in e["issues"]})) or "—"
        lines.append(f"| {e['entity']} | {e['verdict']} | {len(rej)} | {len(wrn)} | {codes} |")
    lines.append("")
    for e in g["records"]:
        if not e["issues"]:
            continue
        lines.append(f"## {e['entity']}  ({e['verdict']})")
        for i in e["issues"]:
            mark = "❌" if i["severity"] == "reject" else "⚠️"
            lines.append(f"- {mark} **{i['field']}** [{i['code']}]: {i['reason']}")
        lines.append("")
    return "\n".join(lines)


_HINTS = {
    "inn-invalid": "Open the company's OWN site requisites (footer, «Контакты», «Реквизиты», оферта, "
                   "политика конфиденциальности — RU sites publish ИНН/ОГРН there), then confirm on the "
                   "rusprofile.ru / list-org.com company CARD. The number must pass the official ИНН checksum.",
    "placeholder": "Remove the placeholder. Research the real value; if it truly is not published, leave the "
                   "field blank and add a review_flags entry naming the URL you opened.",
    "search-url": "Open the actual company card / article that the search returns and cite THAT page + a snippet from it.",
    "bad-source": "Every source must be a live public URL you opened this session — never a repo path.",
    "unsourced": "Add the live URL + snippet the value came from, or blank the field.",
    "meta-news": "Find one significant dated event (M&A, partnership, product launch, tech pilot, leadership "
                 "change, major funding, major incident) with its date and source. If none exists, leave the "
                 "field blank and add a review_flag.",
    "insignificant-news": "Replace with a SIGNIFICANT dated event: acquisition/M&A, partnership, new product "
                          "launch, new technology pilot, leadership change, major funding, or a major "
                          "incident/controversy. Registry entries, certificates, ОКВЭД changes, offer updates, "
                          "webinars and blog overviews do not count. Nothing significant in ~24 months → blank + review_flag.",
    "entity-type": "Pick exactly one of: product (one specific product fits the research goal), brand (a set of "
                   "products under one brand fits), company (almost all of the company's products fit), group "
                   "(products across multiple companies of a holding fit), foreign_entity (revenue in Russia, "
                   "but a foreign entity).",
    "segment-taxonomy": "Set `segment` to exactly one label from the run's agreed segment list (companies.json → segments).",
    "segment-long": "Replace the sentence with ONE short segment label (≤3 words). Use the same small label set "
                    "across all companies of this run so the column can be filtered.",
    "history-missing": "Open the rusprofile.ru / list-org.com company card — its financials table lists revenue "
                       "by year. Fill total_revenue_2022/2023/2024 («N млн ₽»), plus product_revenue_* estimates "
                       "and ebitda_* if published. If the company genuinely does not file, add a review_flag saying so.",
    "num-format": "Write money as «N млн ₽» (space thousands separator, comma decimals, ~ for estimates); "
                  "headcount as a bare number/range («26», «70+», «12 000»).",
    "merge-loss": "EDIT the existing _record.json IN PLACE: keep every field that is already there, and "
                  "restore the listed fields from this company's _A.json / _B.json (they hold sourced "
                  "values). Never rewrite a record from scratch — a record that drops its collectors' "
                  "findings is void.",
    "products-bloat": "Split the list: key_products keeps ONLY the 1–4 products of the researched market; "
                      "everything else (adjacent lines, modules, consulting, training) moves to other_products.",
    "required-empty": "Open the company's site (product pages, pricing, «О компании») and its press coverage. "
                      "key_products = actual product names; business_model = how they earn (model + pricing); "
                      "target_customers = who buys (segments, roles, size, industries); positioning = category "
                      "+ differentiators vs competitors. An active company always has these.",
    "description-thin": "Rewrite as the structured profile: «Name — founded [year] by [founders] (as [original "
                        "company] if pivoted). Who they are + mission + problem they solve + for whom + how they "
                        "make money + main cash cow + why they win + where vulnerable + why it matters for this "
                        "research.» Skip elements you could not confirm — never invent.",
    "b-missing": "Run the Collector B pass properly: fresh reasoning, ≥2 third-party sources (press, industry portal, "
                 "ranking, analyst) — then re-merge the record.",
    "b-empty": "Run the Collector B pass properly: fresh reasoning, ≥2 third-party sources — an empty pass is a failure.",
    "b-copy": "Redo the Collector B pass from scratch: fresh wording taken from third-party sources, not A's text.",
    "b-no-new-source": "Collector B must cite at least one source A did not use (press, ranking, analyst, industry portal).",
    "bad-json": "Re-save the file as strict JSON (no prose around it).",
    "product-source-missing": "Fill product_revenue_source with the URL you used + one line on the method: "
                              "either «напрямую из <отчётность/рейтинг>» or «оценка: <метод и допущения>». "
                              "If the product-line figures cannot be traced to any source, blank them and "
                              "add a review_flags note instead.",
}


def render_b_redo_prompt(market: str, lang: str, save_dir: str,
                         rejected: list[dict]) -> str:
    """Paste-back prompt for Collector-B failures (B_CODES): these are derived
    from the _A/_B files, so a record edit can never clear them — the fix is a
    fresh, genuinely independent Collector B pass + verifier re-merge."""
    blocks = []
    for e in rejected:
        items = [f"- {i['reason']}\n  → {_HINTS.get(i['code'], '')}"
                 for i in e["issues"]
                 if i["severity"] == "reject" and i["code"] in B_CODES]
        blocks.append(f"## {e['entity']}\n" + "\n".join(items)
                      + f"\n- overwrite `{save_dir}/{e['stem']}_B.json`, then re-merge "
                        f"into `{save_dir}/{e['stem']}_record.json`")
    body = "\n\n".join(blocks)
    return f"""# Redo Collector B — {market}

The records below failed the Collector-B INDEPENDENCE checks. Editing the record
cannot fix this: the gate re-reads each company's `_A.json`/`_B.json` files. For
EACH company below, do a genuinely fresh Collector B pass:

1. Research the company from scratch in this chat — do NOT open or reuse the
   `_A.json` text. Fresh wording throughout; ≥2 third-party sources (press,
   industry portal, ranking, analyst), at least one that `_A.json` does not cite.
2. Overwrite the company's `_B.json` with the full collector JSON
   (`"collector": "B"`, `"entity"` = the exact brand, fields with value/source).
3. Re-merge A+B into the `_record.json` per the verifier rules: union of all
   fields, better-sourced value wins, disagreements recorded as conflicts +
   `review_flags`. Keep every existing record field that the merge does not
   improve. Prose in {lang}.

{body}

Then re-run the gate (Next prompt ▶ in the app) — remaining record-level issues
get their own repair pass afterwards.
"""


def render_repair_prompt(market: str, lang: str, save_dir: str, rejected: list[dict],
                         segments: list[str] | None = None) -> str:
    """A paste-back prompt that fixes ONLY the rejected fields, company by company."""
    preamble = ""
    seg_codes = {"segment-long", "segment-taxonomy"}
    if not segments and any(i["code"] in seg_codes for e in rejected for i in e["issues"]):
        companies_json = save_dir.rsplit("/agent_runs", 1)[0] + "/companies.json"
        preamble = f"""
## Step 0 — agree the segment taxonomy FIRST (once for the whole run)
This run has no segment taxonomy yet. Before fixing any record, define **3–7 SHORT
labels** (≤3 words each, in {lang}) that partition this market, and rewrite
`{companies_json}` in the object form (keep every existing company entry):
```json
{{"market": "{market}", "coverage_note": "…", "segments": ["…", "…"],
  "companies": [ …existing entries, each gaining "segment": "one of segments[]"… ]}}
```
Every record's `segment` (in this and later repair passes) must then be exactly one
of those labels — the gate enforces it.
"""
    blocks = []
    for e in rejected:
        rej = [i for i in e["issues"] if i["severity"] == "reject"]
        seen, items = set(), []
        for i in rej:
            key = (i["field"], i["code"])
            if key in seen:
                continue
            seen.add(key)
            hint = _HINTS.get(i["code"], "")
            items.append(f"- **{i['field']}** — {i['reason']}\n  → {hint}")
        blocks.append(f"## {e['entity']}  —  `{save_dir}/{e['stem']}_record.json`\n" + "\n".join(items))
    body = "\n\n".join(blocks)
    return f"""# Repair pass — {market}

The records below FAILED machine validation and are EXCLUDED from the deliverable
until fixed. Fix ONLY the fields listed — by actually opening sources in this chat —
then EDIT the existing JSON files IN PLACE. **Editing means modifying the named
fields inside the file while keeping every other field exactly as it is.** Never
re-save a record containing only the fixed fields — the gate compares each record
against its _A.json/_B.json and voids any record that dropped its collectors'
findings (merge-loss). Do not re-research whole companies, and never write a script
that regenerates files.

Rules still apply: live opened pages only (no `…search?query=…` URLs, no repo paths),
no placeholder strings, an honest blank + `review_flags` note beats an invented value,
prose in **{lang}**, proper names verbatim. All money values as «N млн ₽». Every fix
is re-validated by the same checks that rejected it (including the ИНН checksum and
the A/B-independence check).
{preamble}
{body}

## When done
Update each company's `_record.json` (and its `_A.json` / `_B.json` where the fix
originates there). Reply with one line per company saying what changed and from
which opened URL. The operator re-runs the gate.
"""
