"""
Respondent sourcing — an OPTIONAL extra stage on top of the qualitative track.

The one-pager stage stays exactly as it is: it does NOT browse and invents no
facts beyond the record. This stage is deliberately the opposite: it BROWSES
public professional sources to turn the one-pagers' abstract archetypes
(«customer of X in a mid-size bank») into named, reachable candidates — while
the archetypes themselves stay in the interview brief, untouched.

Two groups, saved next to the one-pagers:
  qual/_market_respondents.json    — market-level experts & respondents
  qual/<stem>_respondents.json     — company-specific candidates

Provenance is SEPARATE from the one-pager's, never mixed: a candidate is not a
fact about the company, it is a sourced claim about a person, carrying its own
public URL, confidence and verification date. The qual gate never sees these
files; validate_respondents() applies its own, stricter rules:

  * public professional information ONLY — an email address or phone number
    anywhere in the payload is a hard reject (emails are NEVER guessed);
  * every candidate needs a live public professional profile / contact route,
    at least one supporting source, and a verification date (role is current);
  * candidates must address hypotheses/questions that actually exist;
  * duplicates (same person at the same org) are rejected.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from . import gate, onepager, runs

MARKET_STEM = "_market"
PRIORITIES = (1, 2, 3)
CONFIDENCE = ("high", "medium", "low")

# Private contact data must never appear — the model may not guess an address.
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}")
# obfuscated addresses: «ivanov (at) bank.ru», «ivanov [собака] bank точка ru»
_EMAIL_OBFUSCATED_RE = re.compile(
    r"[\w.+-]+\s*[\(\[{]\s*(?:at|собака|эт)\s*[\)\]}]\s*[\w-]+", re.I)
# phones: international (+7 916 …), domestic 8-prefixed with separators
# (8 916 123-45-67 / 8 (916) 123-45-67), and bare 11-digit 7/8-runs. The
# look-arounds keep 10/12/13-digit registry numbers (ИНН/ОГРН) out of scope.
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+\d[\d\-.\s()]{8,}\d"
    r"|8[\s(-]\(?\d{3}\)?[\s)-]\d{3}[\s-]?\d{2}[\s-]?\d{2}"
    r"|[78]\d{10})(?!\d)")
# Key TOKENS that would carry private data even if the value looks harmless.
# Token-based (not substring): «addresses» — the hypotheses a person can speak
# to — is legitimate, while «home_address» / «personal_phone» are not.
_BANNED_KEY_TOKENS = {"email", "emails", "mail", "phone", "phones", "tel",
                      "telephone", "mobile", "whatsapp", "telegram", "personal",
                      "private", "address", "home"}

_REQUIRED = ("name", "role", "org", "why_relevant", "addresses", "priority",
             "profile_url", "sources", "confidence", "verified_on")


def resp_path(run_dir: Path, stem: str) -> Path:
    return onepager.qual_dir(run_dir) / f"{stem}_respondents.json"


def _load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _norm_person(c: dict) -> str:
    return runs._norm(f"{c.get('name', '')}|{c.get('org', '')}")


def ground_candidates(doc: dict, slog) -> list[str]:
    """DeepSeek app-tools only: a candidate's profile_url and sources must be
    URLs the pass actually saw (searched/fetched). Same philosophy as record
    grounding: an ungrounded profile_url is BLANKED and ungrounded sources are
    removed, so validate_respondents rejects the candidate as required-empty
    and routes it into the repair loop; a domain-only match keeps the URL but
    downgrades confidence. Returns detail strings (full URLs — event/debug
    logs only, never persisted in the file)."""
    from .web_tools import _norm
    with slog._lock:
        seen = set(slog.seen)
    domains = {n.split("/", 1)[0] for n in seen}
    details: list[str] = []
    for c in doc.get("candidates") or []:
        if not isinstance(c, dict):
            continue
        who = str(c.get("name") or "?")
        u = str(c.get("profile_url") or "")
        if u.startswith(("http://", "https://")):
            n = _norm(u)
            if n not in seen:
                dom = n.split("/", 1)[0] if n else ""
                if dom and dom in domains:
                    c["confidence"] = "low"
                    details.append(f"{who}: profile_url not opened this pass — "
                                   f"confidence lowered ({u})")
                else:
                    c["profile_url"] = ""
                    details.append(f"{who}: ungrounded profile_url removed ({u})")
        srcs = c.get("sources")
        if isinstance(srcs, list):
            kept = []
            for s in srcs:
                s2 = str(s)
                if s2.startswith(("http://", "https://")) and _norm(s2) in seen:
                    kept.append(s)
                else:
                    details.append(f"{who}: ungrounded source removed ({s2[:80]})")
            if len(kept) != len(srcs):
                c["sources"] = kept
    return details


# ── validation (own rules — the one-pager gate is untouched) ──────────────────
def validate_respondents(doc: dict, hyp_ids: set[str], scope: str,
                         entity: str = "") -> list[dict]:
    """All issues for one respondents file. `hyp_ids` = hypothesis ids that
    exist in the matching one-pager(s); `scope` = 'market' | 'company'."""
    issues: list[dict] = []

    def add(field, severity, code, reason):
        issues.append({"field": field, "severity": severity, "code": code,
                       "reason": reason})

    if doc.get("scope") != scope:
        add("scope", "reject", "bad-enum",
            f"scope «{doc.get('scope')}» — expected «{scope}»")
    if scope == "company" and runs._norm(doc.get("entity", "")) != runs._norm(entity):
        add("entity", "reject", "bad-enum",
            f"entity «{doc.get('entity')}» — expected «{entity}»")

    # private data anywhere in the payload (values AND keys)
    blob = json.dumps(doc, ensure_ascii=False)
    for m in _EMAIL_RE.findall(blob):
        add("_file", "reject", "private-contact",
            f"email «{m}» — public professional data only; emails are never guessed")
    for m in _PHONE_RE.findall(blob):
        add("_file", "reject", "private-contact",
            f"phone «{m.strip()}» — public professional data only")
    for m in _EMAIL_OBFUSCATED_RE.findall(blob):
        add("_file", "reject", "private-contact",
            f"obfuscated email «{m.strip()[:40]}» — public professional data only")

    def walk_keys(node, path="doc"):
        if isinstance(node, dict):
            for k, v in node.items():
                tokens = set(re.split(r"[^a-zа-яё]+", str(k).lower())) - {""}
                if tokens & _BANNED_KEY_TOKENS:
                    add(f"{path}.{k}", "reject", "private-contact",
                        f"field «{k}» may carry private contact data — remove it")
                walk_keys(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk_keys(v, f"{path}[{i}]")
    walk_keys(doc)

    cands = doc.get("candidates")
    if not isinstance(cands, list) or not cands:
        add("candidates", "reject", "counts",
            "no candidates — return at least one, or state the blocker in `note`")
        return issues

    seen: dict[str, int] = {}
    today = date.today().isoformat()
    for i, c in enumerate(cands):
        where = f"candidates[{i}]"
        if not isinstance(c, dict):
            add(where, "reject", "bad-json", "candidate is not an object")
            continue
        label = str(c.get("name") or where)
        for k in _REQUIRED:
            v = c.get(k)
            if v is None or (isinstance(v, str) and not v.strip()) or \
               (isinstance(v, list) and not v):
                add(f"{label}.{k}", "reject", "required-empty",
                    f"«{k}» is required for every candidate")

        key = _norm_person(c)
        if key in seen:
            add(f"{label}", "reject", "duplicate",
                f"same person/org already listed as candidate #{seen[key] + 1}")
        else:
            seen[key] = i

        pr = c.get("priority")
        if pr is not None and pr not in PRIORITIES:
            add(f"{label}.priority", "reject", "bad-enum",
                f"priority «{pr}» — use {PRIORITIES} (1 = contact first)")
        conf = str(c.get("confidence", "")).lower()
        if conf and conf not in CONFIDENCE:
            add(f"{label}.confidence", "reject", "bad-enum",
                f"confidence «{conf}» — use {CONFIDENCE}")

        for u_field in ("profile_url",):
            u = str(c.get(u_field) or "")
            if u and not u.startswith(("http://", "https://")):
                add(f"{label}.{u_field}", "reject", "bad-source",
                    f"«{u}» is not a live public URL")
            elif u and gate.is_search_url(u):
                add(f"{label}.{u_field}", "reject", "search-url",
                    "a search page is not a profile — link the person's actual page")
        srcs = c.get("sources") or []
        if isinstance(srcs, list):
            for s in srcs:
                s = str(s)
                if not s.startswith(("http://", "https://")):
                    add(f"{label}.sources", "reject", "bad-source",
                        f"source «{s[:60]}» is not a live public URL")
                elif gate.is_search_url(s):
                    add(f"{label}.sources", "reject", "search-url",
                        f"source «{s[:60]}» is a search page")

        addresses = c.get("addresses") or []
        if isinstance(addresses, list) and hyp_ids:
            unknown = [a for a in addresses
                       if str(a).startswith("H") and str(a) not in hyp_ids]
            if unknown:
                add(f"{label}.addresses", "reject", "orphan-ref",
                    f"references hypotheses that do not exist: {unknown}")
        if isinstance(addresses, list) and not addresses:
            add(f"{label}.addresses", "reject", "required-empty",
                "name the hypotheses (H*) or themes this person can address")

        v_on = str(c.get("verified_on") or "")
        if v_on:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v_on):
                add(f"{label}.verified_on", "reject", "bad-date",
                    f"«{v_on}» — use ISO YYYY-MM-DD (the day you checked the profile)")
            elif v_on > today:
                add(f"{label}.verified_on", "reject", "bad-date",
                    f"«{v_on}» is in the future")
        if str(c.get("role_current", "")).lower() in ("false", "no"):
            add(f"{label}.role_current", "reject", "stale-role",
                "role is not current — only list people in the stated role today")
        if conf == "low":
            add(f"{label}", "warn", "low-confidence",
                "low confidence — verify the role before reaching out")
    return issues


# ── prompts ───────────────────────────────────────────────────────────────────
def _archetypes(op: dict) -> list[dict]:
    return [{"type": r.get("type", ""), "who": r.get("who", ""),
             "why": r.get("why", ""), "priority": r.get("priority", "")}
            for r in ((op.get("interview_brief") or {}).get("respondents") or [])]


def _hyps(op: dict) -> list[dict]:
    return [{"id": h.get("id", ""), "text": str(h.get("text", ""))[:180],
             "validated_if": str(h.get("validated_if", ""))[:180]}
            for h in ((op.get("context") or {}).get("hypotheses") or [])]


_RULES = """
## Hard rules — public professional data ONLY
- **NO email addresses and NO phone numbers ANYWHERE in the JSON** — not
  personal, not corporate, not PR/press (`pr@…`, `info@…`), not guessed, not
  even ones printed on a public page. The `contact_route` is a text
  description of a PUBLIC route (e.g. «через форму на профиле», «через
  пресс-службу»), never an address. `profile_url` is a URL you actually
  opened. A single «@…» or phone number voids the whole file.
- Only real, named people you found on public sources. No invented people, no
  «Head of X (name unknown)» placeholders.
- **Verify the role is current**: open the profile/company page, confirm the
  person still holds the stated role, and put the date you checked in
  `verified_on` (ISO YYYY-MM-DD). If you cannot confirm it today, drop them.
- One entry per person: no duplicates within or across the two groups.
- `sources` = the pages that support role + relevance (never search pages).
- `confidence`: high = profile + a second corroborating source, both current;
  medium = single solid public source; low = indirect evidence (say why).
- `addresses` = the hypothesis ids (H*) / themes this person can actually speak
  to. Prefer few strong candidates over a long weak list.
"""

_SHAPE = """```json
{"scope": "market" OR "company"   (the literal word — nothing else),
 "entity": "…exact company name (company scope only; omit for market)…",
 "candidates": [
   {"name": "Иван Иванов",
    "role": "Директор по цифровизации", "org": "…",
    "why_relevant": "…why THIS person for the research goal / angle…",
    "addresses": ["H1", "buying_behavior"],
    "priority": 1,
    "profile_url": "https://…  (public professional page = the contact route)",
    "contact_route": "…how to reach out publicly, e.g. «через форму на профиле»…",
    "sources": ["https://…", "https://…"],
    "confidence": "high",
    "role_current": true,
    "verified_on": "YYYY-MM-DD"}],
 "note": "…blockers, if any…"}
```"""


def build_respondent_prompt(run_dir: Path, entries: list[dict], meta_run: dict,
                            qmeta: dict, market: bool) -> str:
    """entries = accepted one-pager entries (gate_qual output)."""
    lang = meta_run["output_language"]
    save = f"logs/{meta_run['run_id']}/qual"
    goal = qmeta.get("research_goal", "")
    if market:
        ctx = []
        for e in entries:
            ctx.append({"company": e["entity"], "angle": e["op"].get("angle", ""),
                        "segment": str(gate.value_of(
                            (e["record"].get("fields") or {}).get("segment")) or ""),
                        "hypotheses": _hyps(e["op"]),
                        "archetypes": _archetypes(e["op"])})
        return f"""# Respondent sourcing — MARKET level — {meta_run['market']}

You are a **sourcer** with web search. Find REAL, NAMED people who can answer
this research at the MARKET level: independent experts, analysts, industry-body
and community figures, practitioners/buyers in this market who are not tied to
one vendor.

**Research goal:** {goal}

Save to: `{save}/{MARKET_STEM}_respondents.json`
{_RULES}
- Market level = people whose view spans the market, not a single company's
  staff. 5–8 candidates is a good shortlist.

## Market context — companies, hypotheses and the archetypes to make concrete
```json
{json.dumps(ctx, ensure_ascii=False, indent=1)}
```

## Output — STRICT JSON only, no prose
{_SHAPE}
Prose in {lang}; names verbatim as published.
"""
    blocks = []
    for e in entries:
        f = e["record"].get("fields") or {}
        blocks.append(f"""### 🎯 {e['entity']} — angle: **{e['op'].get('angle', '')}**
Save to: `{save}/{e['stem']}_respondents.json` (`"entity"` must be exactly «{e['entity']}»)

Company context:
```json
{json.dumps({"segment": str(gate.value_of(f.get("segment")) or ""),
             "positioning": str(gate.value_of(f.get("positioning")) or "")[:200],
             "key_products": str(gate.value_of(f.get("key_products")) or "")[:200],
             "website": str(gate.value_of(f.get("website")) or ""),
             "manual_target": bool(e["record"].get("manual_target"))},
            ensure_ascii=False)}
```
Hypotheses to address:
```json
{json.dumps(_hyps(e["op"]), ensure_ascii=False)}
```
Respondent archetypes to make CONCRETE (keep them abstract in the brief — this
file names real people who fit them):
```json
{json.dumps(_archetypes(e["op"]), ensure_ascii=False)}
```""")
    return f"""# Respondent sourcing — COMPANY level — {meta_run['market']}

You are a **sourcer** with web search. For EACH company below find REAL, NAMED
people who fit its respondent archetypes: its executives/experts (for
competitor/benchmark angles), its publicly known customers or partners (for
customer/partner angles), ex-employees or integrators who worked with it.

**Research goal:** {goal}
{_RULES}
- 3–6 candidates per company.

## Companies
{"".join(b + chr(10) + chr(10) for b in blocks)}
## Output — STRICT JSON per company, saved to the path given above
{_SHAPE}
Prose in {lang}; names verbatim as published.
"""


def _render_repair(meta_run: dict, rejected: list[dict]) -> str:
    save = f"logs/{meta_run['run_id']}/qual"
    blocks = []
    for e in rejected:
        items = "\n".join(f"- **{i['field']}** [{i['code']}]: {i['reason']}"
                          for i in e["issues"] if i["severity"] == "reject")
        blocks.append(f"## {e['label']} — `{save}/{e['stem']}_respondents.json`\n{items}")
    return f"""# Respondent repair pass — {meta_run['market']}

The files below FAILED respondent validation. EDIT each JSON in place: fix ONLY
the listed problems, keep every valid candidate as-is.
{_RULES}

{chr(10).join(blocks)}

Return the corrected files. Then re-run the check.
"""


# ── gate / state machine ──────────────────────────────────────────────────────
def gate_respondents(run_dir: Path) -> dict:
    """Validate the market file + every company file against their one-pagers."""
    q = onepager.gate_qual(run_dir)
    ops = {e["entity"]: e for e in q["accepted"]}
    out = {"accepted": [], "rejected": [], "pending": [], "qual": q}
    if not ops:
        return out

    all_hyps = {h["id"] for e in ops.values() for h in _hyps(e["op"]) if h["id"]}
    targets = [("market", MARKET_STEM, "Market level", all_hyps)]
    targets += [("company", e["stem"], ent, {h["id"] for h in _hyps(e["op"])})
                for ent, e in sorted(ops.items())]

    entries = []
    for scope, stem, label, hyp_ids in targets:
        p = resp_path(run_dir, stem)
        doc = _load(p)
        if doc is None:
            out["pending"].append(label)
            continue
        issues = validate_respondents(doc, hyp_ids, scope,
                                      entity="" if scope == "market" else label)
        entries.append({"label": label, "scope": scope, "stem": stem,
                        "path": p, "doc": doc, "issues": issues})

    # cross-file dedup: one entry per person ACROSS market + company files —
    # the earliest file (market first, then companies alphabetically) keeps
    # the person; later files get a reject and go through repair
    first_seen: dict[str, str] = {}
    for e in entries:
        for c in e["doc"].get("candidates") or []:
            if not isinstance(c, dict):
                continue
            key = _norm_person(c)
            if not key.strip("|"):
                continue
            owner = first_seen.setdefault(key, e["label"])
            if owner != e["label"]:
                e["issues"].append({
                    "field": str(c.get("name") or "?"), "severity": "reject",
                    "code": "duplicate",
                    "reason": f"same person/org already listed in «{owner}» — "
                              f"keep ONE entry per person across all files "
                              f"(remove this one)"})

    for e in entries:
        e["verdict"] = ("rejected" if any(i["severity"] == "reject"
                                          for i in e["issues"]) else "accepted")
        out[e["verdict"]].append(e)
    return out


def next_respondent_prompt(run_dir: Path, batch: int = 2) -> tuple[str, str]:
    """Prompt-mode driver for the optional sourcing stage."""
    q = onepager.gate_qual(run_dir)
    if not q["accepted"]:
        raise SystemExit("Respondent sourcing needs accepted one-pagers — finish "
                         "the qualitative track first.")
    meta_run = runs._load_meta(run_dir)
    qmeta = onepager.load_meta(run_dir)
    r = gate_respondents(run_dir)
    ops = {e["entity"]: e for e in q["accepted"]}

    if r["pending"]:
        if "Market level" in r["pending"]:
            kind = "respondents-market"
            text = build_respondent_prompt(run_dir, q["accepted"], meta_run,
                                           qmeta, market=True)
        else:
            todo = [ops[b] for b in r["pending"] if b in ops][:max(1, int(batch))]
            kind = "respondents-company"
            text = build_respondent_prompt(run_dir, todo, meta_run, qmeta,
                                           market=False)
    elif r["rejected"]:
        kind, text = "respondents-repair", _render_repair(meta_run, r["rejected"])
    else:
        kind = "done"
        text = (f"# Respondent sourcing complete — {len(r['accepted'])} file(s) "
                f"validated.\n\nThe shortlist is in the qual report "
                f"(«Open report»): market level in the executive summary, "
                f"company candidates in each company's section.\n")
    onepager._issue_qual_prompt(run_dir, kind, text)
    return kind, text


def progress(run_dir: Path) -> dict:
    r = gate_respondents(run_dir)
    a, rej, p = len(r["accepted"]), len(r["rejected"]), len(r["pending"])
    n = sum(len(e["doc"].get("candidates") or []) for e in r["accepted"])
    phase = ("no accepted one-pagers yet" if not r["qual"]["accepted"] else
             f"sourcing ({p} to go)" if p else
             f"repair — {rej} rejected" if rej else
             f"done — {n} candidates")
    return {"accepted": a, "rejected": rej, "pending": p, "candidates": n,
            "phase": phase}


# ── report helpers (consumed by onepager.build_report) ───────────────────────
def accepted_docs(run_dir: Path) -> dict:
    """{'market': doc|None, entity: doc} — accepted respondent files only, so a
    half-finished/rejected sourcing pass never leaks into the report."""
    out: dict = {}
    r = gate_respondents(run_dir)
    for e in r["accepted"]:
        out["market" if e["scope"] == "market" else e["label"]] = e["doc"]
    return out


def format_candidate(c: dict) -> str:
    """One-line rendering shared by the .docx report and the markdown pages."""
    addr = ", ".join(str(a) for a in (c.get("addresses") or []))
    return (f"[P{c.get('priority', '?')}·{c.get('confidence', '?')}] "
            f"{c.get('name', '?')} — {c.get('role', '')}, {c.get('org', '')} · "
            f"{c.get('why_relevant', '')} · addresses: {addr} · "
            f"{c.get('profile_url', '')} · проверено {c.get('verified_on', '?')}")
