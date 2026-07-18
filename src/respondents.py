"""
Respondent sourcing — an OPTIONAL stage of the qualitative track, INDEPENDENT
of the one-pager stage: neither blocks the other, and both can be skipped.

The one-pager stage stays exactly as it is: it does NOT browse and invents no
facts beyond the record. This stage is deliberately the opposite: it BROWSES
public professional sources for named, reachable candidates — current or
former employees, publicly known customers and partners, integrators,
analysts and practitioners.

One-pagers are context, not a prerequisite:
  * without them, the search is grounded in the research goal, the selected
    targets (gate-accepted records or manual entries), angle, segment,
    website, user notes and the market taxonomy; `addresses` uses THEMES;
  * once one-pagers are accepted, their hypotheses/archetypes REFINE the
    shortlist (a `refine` pass that updates + merges, never discards accepted
    candidates) — the archetypes themselves stay in the brief, untouched.

Two groups, saved next to the one-pagers (plus a standalone markdown
shortlist, so the deliverable exists even when no .docx report does):
  qual/_market_respondents.json    — market-level experts & respondents
  qual/<stem>_respondents.json     — company-specific candidates
  qual/respondents_shortlist.md    — rendered shortlist of accepted files

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

# The deliverable is an outreach list, so PUBLISHED professional contact
# channels are allowed — but ONLY inside each candidate's `contacts` object,
# never in free-text fields, and only when copied verbatim from a public
# professional source (the model may never infer an address, username or
# number). Preferred acquisition order: telegram → phone → email → linkedin.
CONTACT_KEYS = ("telegram", "phone", "email", "linkedin")
_TELEGRAM_RE = re.compile(r"^(?:@[\w]{4,}|https?://t\.me/(?:s/)?[\w/]{4,})$", re.I)
_LINKEDIN_RE = re.compile(r"^https?://([\w-]+\.)?linkedin\.com/", re.I)

# Private contact data must never appear OUTSIDE the contacts object.
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


def ground_candidates(doc: dict, slog, prev_doc: dict | None = None) -> list[str]:
    """DeepSeek app-tools only: a candidate's profile_url and sources must be
    URLs the pass actually saw (searched/fetched). Same philosophy as record
    grounding: an ungrounded profile_url is BLANKED and ungrounded sources are
    removed, so validate_respondents rejects the candidate as required-empty
    and routes it into the repair loop; a domain-only match keeps the URL but
    downgrades confidence. Returns detail strings (full URLs — event/debug
    logs only, never persisted in the file).

    `prev_doc` MUST be passed on repair/refine passes: a candidate already
    present in the previous file with the SAME profile_url was grounded when
    first sourced — a repair that fixes an enum re-opens nothing, and without
    this scope the audit would wipe legitimately grounded URLs (the exact
    over-stripping livelock the quant track fixed with only_fields)."""
    from .web_tools import _norm
    with slog._lock:
        seen = set(slog.seen)
    domains = {n.split("/", 1)[0] for n in seen}
    prev_urls = {}
    if isinstance(prev_doc, dict):
        prev_urls = {_norm_person(c): str(c.get("profile_url") or "")
                     for c in prev_doc.get("candidates") or []
                     if isinstance(c, dict)}
    details: list[str] = []
    for c in doc.get("candidates") or []:
        if (isinstance(c, dict)
                and prev_urls.get(_norm_person(c)) == str(c.get("profile_url") or "")
                and _norm_person(c) in prev_urls):
            continue   # unchanged person — grounded in their original pass
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


# Codes a repair can fix WITHOUT any web research (pure format edits) — such
# repairs run on a tiny tool budget instead of a full browsing pass.
FORMAT_CODES = frozenset({"bad-enum", "private-contact", "duplicate",
                          "bad-date", "stale-role"})


def autofix_doc(doc: dict) -> list[str]:
    """Deterministic, zero-API repair of mechanical failures — the respondent
    counterpart of runs.autofix_records. Clamps priorities into 1–3, strips
    email/phone/obfuscated contact strings out of text fields (URLs excluded),
    drops keys that may carry private data, drops candidates whose role is
    explicitly not current, and drops in-file person duplicates. Returns fix
    notes; mutates `doc` in place."""
    notes: list[str] = []
    cands = doc.get("candidates")
    if not isinstance(cands, list):
        return notes
    kept, seen = [], set()
    for c in cands:
        if not isinstance(c, dict):
            kept.append(c)
            continue
        who = str(c.get("name") or "?")
        if str(c.get("role_current", "")).lower() in ("false", "no"):
            notes.append(f"{who}: dropped — role not current")
            continue
        key = _norm_person(c)
        if key.strip("|") and key in seen:
            notes.append(f"{who}: dropped in-file duplicate")
            continue
        seen.add(key)
        pr = c.get("priority")
        if isinstance(pr, (int, float)) and pr not in PRIORITIES:
            c["priority"] = min(max(int(pr), 1), 3)
            notes.append(f"{who}: priority {pr} → {c['priority']}")
        # top-level channel keys belong nested under `contacts` — migrate a
        # valid value there rather than dropping it, else remove the stray key
        contacts = c.get("contacts") if isinstance(c.get("contacts"), dict) else {}
        for k in [k for k in c if k != "contacts"
                  and set(re.split(r"[^a-zа-яё]+", str(k).lower())) - {""}
                  & _BANNED_KEY_TOKENS]:
            val = str(c.get(k) or "").strip()
            for ch, rx in (("email", _EMAIL_RE), ("telegram", _TELEGRAM_RE),
                           ("linkedin", _LINKEDIN_RE), ("phone", _PHONE_RE)):
                if not contacts.get(ch) and (rx.fullmatch(val) or rx.search(val)):
                    contacts[ch] = val
                    notes.append(f"{who}: moved «{k}» → contacts.{ch}")
                    break
            c.pop(k, None)
        if contacts:
            c["contacts"] = contacts
        for k, v in list(c.items()):
            if k == "contacts" or not isinstance(v, str) \
                    or v.startswith(("http://", "https://")):
                continue
            cleaned = _EMAIL_RE.sub("", v)
            cleaned = _PHONE_RE.sub("", cleaned)
            cleaned = _EMAIL_OBFUSCATED_RE.sub("", cleaned)
            if cleaned != v:
                cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,;:—-·")
                if k == "contact_route" and not cleaned:
                    cleaned = "через публичный профиль"
                c[k] = cleaned
                notes.append(f"{who}: private contact string removed from «{k}»")
        kept.append(c)
    doc["candidates"] = kept
    note = doc.get("note")
    if isinstance(note, str):
        cleaned = _EMAIL_OBFUSCATED_RE.sub("", _PHONE_RE.sub("", _EMAIL_RE.sub("", note)))
        if cleaned != note:
            doc["note"] = cleaned.strip()
            notes.append("note: private contact string removed")
    return notes


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

    # contact data is allowed ONLY inside each candidate's `contacts` object;
    # scan everything ELSE (a copy with contacts stripped) for leaks
    scrub = dict(doc)
    scrub["candidates"] = [{k: v for k, v in c.items() if k != "contacts"}
                           if isinstance(c, dict) else c
                           for c in (doc.get("candidates") or [])]
    blob = json.dumps(scrub, ensure_ascii=False)
    for m in _EMAIL_RE.findall(blob):
        add("_file", "reject", "private-contact",
            f"email «{m}» outside `contacts` — put published channels in `contacts` only")
    for m in _PHONE_RE.findall(blob):
        add("_file", "reject", "private-contact",
            f"phone «{m.strip()}» outside `contacts` — put published channels in `contacts` only")
    for m in _EMAIL_OBFUSCATED_RE.findall(blob):
        add("_file", "reject", "private-contact",
            f"obfuscated email «{m.strip()[:40]}» — public professional data only")

    def walk_keys(node, path="doc", in_contacts=False):
        if isinstance(node, dict):
            for k, v in node.items():
                if not in_contacts:
                    tokens = set(re.split(r"[^a-zа-яё]+", str(k).lower())) - {""}
                    if tokens & _BANNED_KEY_TOKENS:
                        add(f"{path}.{k}", "reject", "private-contact",
                            f"field «{k}» outside `contacts` — nest published "
                            f"channels under `contacts` instead")
                walk_keys(v, f"{path}.{k}", in_contacts or k == "contacts")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk_keys(v, f"{path}[{i}]", in_contacts)
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
        if isinstance(addresses, list):
            unknown = [a for a in addresses
                       if re.fullmatch(r"H\d+", str(a)) and str(a) not in hyp_ids]
            if unknown:
                add(f"{label}.addresses", "reject", "orphan-ref",
                    f"references hypotheses that do not exist: {unknown}"
                    + ("" if hyp_ids else " — no accepted one-pagers yet, so no "
                       "H* ids exist; name themes instead"))
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

        # published professional contact channels (all optional, format-checked;
        # a channel present with no source to back it is not defensible)
        contacts = c.get("contacts") or {}
        if contacts and not isinstance(contacts, dict):
            add(f"{label}.contacts", "reject", "bad-json", "`contacts` must be an object")
            contacts = {}
        stray = [k for k in contacts if k not in CONTACT_KEYS]
        if stray:
            add(f"{label}.contacts", "reject", "bad-enum",
                f"unknown contact channel(s) {stray} — use only {CONTACT_KEYS}")
        has_channel = False
        for ch in CONTACT_KEYS:
            v = str(contacts.get(ch) or "").strip()
            if not v:
                continue
            has_channel = True
            if ch == "email" and not _EMAIL_RE.fullmatch(v):
                add(f"{label}.contacts.email", "reject", "bad-contact",
                    f"«{v}» is not a valid email — copy it verbatim from a public page")
            elif ch == "phone" and not _PHONE_RE.search(v):
                add(f"{label}.contacts.phone", "reject", "bad-contact",
                    f"«{v}» is not a recognizable phone number")
            elif ch == "telegram" and not _TELEGRAM_RE.match(v):
                add(f"{label}.contacts.telegram", "reject", "bad-contact",
                    f"«{v}» — use a public @handle or https://t.me/… link")
            elif ch == "linkedin" and not _LINKEDIN_RE.match(v):
                add(f"{label}.contacts.linkedin", "reject", "bad-contact",
                    f"«{v}» is not a linkedin.com profile URL")
        if has_channel and not (c.get("sources") or []):
            add(f"{label}.contacts", "reject", "unsourced",
                "contact channels present but no `sources` back them — cite the "
                "public page each channel was published on")
        if not has_channel:
            add(f"{label}", "warn", "no-channel",
                "no usable public contact channel found — profile_url is the only route")
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


def _angle_of(e: dict, qmeta: dict) -> str:
    """Angle from the accepted one-pager, else the confirmed qual-track angle."""
    return (e.get("op", {}).get("angle")
            or (qmeta.get("companies") or {}).get(e["entity"], {}).get("angle", ""))


def _no_op_guidance(entries: list[dict]) -> str:
    """Extra prompt section for targets that have no one-pager yet — sourcing
    must not wait for hypotheses/archetypes to exist."""
    missing = [e["entity"] for e in entries
               if not _hyps(e["op"]) and not _archetypes(e["op"])]
    if not missing:
        return ""
    return (f"\n## Targets without one-pagers yet: {', '.join(missing)}\n"
            "No hypotheses or archetypes exist for them — sourcing does NOT "
            "wait for one-pagers. Ground relevance in the research goal, the "
            "angle and the given context: current or former employees, "
            "publicly known customers, partners, integrators, industry "
            "analysts and hands-on practitioners. In `addresses` name THEMES "
            f"(e.g. {', '.join(onepager.THEMES[:5])}, …) — do NOT invent H* "
            "ids: they only exist after a one-pager is accepted.\n")


_RULES = """
## Goal — an OUTREACH-READY list: named people + how to reach them
Spend your budget on finding USABLE PUBLIC CONTACT CHANNELS, not biographies.
Keep `why_relevant` to one short line. For EACH candidate, after you confirm
the person, do a small bounded contact search in this order and STOP as soon
as you have one or two usable channels:
  1. Telegram  2. public professional phone  3. public professional email
  4. LinkedIn / other professional profile

## Hard rules — PUBLISHED professional contacts only, never inferred
- Put every contact ONLY inside the candidate's `contacts` object
  ({telegram, phone, email, linkedin}). Never place an address, number or
  handle in name/role/why_relevant/note — that voids the file.
- Include a channel ONLY if it is **explicitly published on a public
  professional source you opened** (personal/company site, speaker/author
  page, verified social profile). **Never infer or construct** an email
  pattern, a username, or a phone number. If it is not published, leave it out.
- No `pr@…`/`info@…` generic inboxes as a person's contact, and no private
  numbers. When unsure, omit the channel — a profile_url alone is fine.
- Every candidate with any channel must cite in `sources` the page(s) the
  channel was published on.
- Only real, named people; verify the role is CURRENT (open the page, put the
  check date in `verified_on`, ISO YYYY-MM-DD) — drop anyone you cannot
  confirm today. One entry per person, no duplicates across files.
- `confidence`: high = profile + a corroborating source; medium = single
  solid source; low = indirect (say why). `addresses` = H* ids / themes.
"""

_SHAPE = """```json
{"scope": "market" OR "company"   (the literal word — nothing else),
 "entity": "…exact company name (company scope only; omit for market)…",
 "candidates": [
   {"name": "Иван Иванов",
    "role": "Директор по цифровизации", "org": "…",
    "why_relevant": "…one short line: why THIS person…",
    "addresses": ["H1", "buying_behavior"],
    "priority": 1,
    "profile_url": "https://…  (public professional page)",
    "contacts": {"telegram": "@handle or https://t.me/handle",
                 "phone": "+7 … (only if publicly published)",
                 "email": "name@org.ru (only if publicly published)",
                 "linkedin": "https://linkedin.com/in/…"},
    "sources": ["https://…  (page that published the role + contacts)"],
    "confidence": "high",
    "role_current": true,
    "verified_on": "YYYY-MM-DD"}],
 "note": "…blockers, if any…"}
```
Omit any `contacts` channel you could not find published — never guess it."""


def build_respondent_prompt(run_dir: Path, entries: list[dict], meta_run: dict,
                            qmeta: dict, market: bool) -> str:
    """entries = accepted one-pager entries (gate_qual output)."""
    lang = meta_run["output_language"]
    save = f"logs/{meta_run['run_id']}/qual"
    # the goal is OPTIONAL for sourcing (required only for one-pagers): without
    # it, relevance is judged by the market, the targets and their angles
    goal = qmeta.get("research_goal", "") or (
        "(not set — judge relevance by the market, the selected targets and "
        "their angles below)")
    if market:
        _brands, _note, segments = runs.manifest(run_dir)
        ctx = []
        for e in entries:
            f = e["record"].get("fields") or {}
            item = {"company": e["entity"], "angle": _angle_of(e, qmeta),
                    "segment": str(gate.value_of(f.get("segment")) or "")}
            notes = str(gate.value_of(f.get("user_notes")) or "")
            if notes:
                item["user_notes"] = notes[:300]
            if _hyps(e["op"]):
                item["hypotheses"] = _hyps(e["op"])
            if _archetypes(e["op"]):
                item["archetypes"] = _archetypes(e["op"])
            ctx.append(item)
        tax = (f"**Market segment taxonomy:** {', '.join(segments)}\n"
               if segments else "")
        return f"""# Respondent sourcing — MARKET level — {meta_run['market']}

You are a **sourcer** with web search. Find REAL, NAMED people who can answer
this research at the MARKET level: independent experts, analysts, industry-body
and community figures, practitioners/buyers in this market who are not tied to
one vendor.

**Research goal:** {goal}
{tax}
Save to: `{save}/{MARKET_STEM}_respondents.json`
{_RULES}
- Market level = people whose view spans the market, not a single company's
  staff. 5–8 candidates is a good shortlist.
{_no_op_guidance(entries)}
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
        angle = _angle_of(e, qmeta)
        hyps, arch = _hyps(e["op"]), _archetypes(e["op"])
        extra = ""
        if hyps:
            extra += f"""
Hypotheses to address:
```json
{json.dumps(hyps, ensure_ascii=False)}
```"""
        if arch:
            extra += f"""
Respondent archetypes to make CONCRETE (keep them abstract in the brief — this
file names real people who fit them):
```json
{json.dumps(arch, ensure_ascii=False)}
```"""
        if not hyps and not arch:
            extra += (f"\nNo one-pager exists for this company yet — that does "
                      f"NOT block sourcing. Work from the research goal, the "
                      f"angle «{angle}» and the context above: its current or "
                      f"former employees/experts, publicly known customers and "
                      f"partners, integrators, analysts and practitioners who "
                      f"know it hands-on. In `addresses` name THEMES "
                      f"({', '.join(onepager.THEMES[:5])}, …) — do NOT invent "
                      f"H* ids.")
        blocks.append(f"""### 🎯 {e['entity']} — angle: **{angle}**
Save to: `{save}/{e['stem']}_respondents.json` (`"entity"` must be exactly «{e['entity']}»)

Company context:
```json
{json.dumps({"segment": str(gate.value_of(f.get("segment")) or ""),
             "positioning": str(gate.value_of(f.get("positioning")) or "")[:200],
             "key_products": str(gate.value_of(f.get("key_products")) or "")[:200],
             "website": str(gate.value_of(f.get("website")) or ""),
             "user_notes": str(gate.value_of(f.get("user_notes")) or "")[:300],
             "manual_target": bool(e["record"].get("manual_target"))},
            ensure_ascii=False)}
```{extra}""")
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
def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def sourcing_targets(run_dir: Path) -> tuple[dict, dict]:
    """entity → sourcing target for the optional stage. Accepted one-pagers
    give the richest context (hypotheses + archetypes) but are NOT a
    prerequisite: every selected qual target — gate-accepted record or manual
    entry — is sourceable from the research goal + its own context (op stays
    {} until a one-pager is accepted). Returns (targets, gate_qual output)."""
    q = onepager.gate_qual(run_dir)
    qmeta = onepager.load_meta(run_dir)
    out: dict[str, dict] = {}
    for e in q["accepted"]:
        out[e["entity"]] = {"entity": e["entity"], "stem": e["stem"],
                            "record": e["record"], "op": e["op"]}
    seen = {runs._norm(k) for k in out}
    for brand, e in onepager.target_entries(q["records_gate"], qmeta).items():
        if runs._norm(brand) not in seen:
            out[brand] = {"entity": brand, "stem": e["stem"],
                          "record": e["record"], "op": {}}
    return out, q


def merge_candidates(old_doc, new_doc: dict) -> dict:
    """Rerun/refine merge: union of candidates keyed by person (name|org). The
    NEW pass wins for a person it re-sourced (fresher role/verification date);
    people it did not mention are KEPT — an accepted shortlist is never
    silently discarded by a rerun. The validator still applies in full to the
    merged file afterwards (incl. cross-file dedup)."""
    if not isinstance(old_doc, dict) or not isinstance(new_doc, dict):
        return new_doc
    merged: dict[str, dict] = {}
    order: list[str] = []
    for src in (old_doc.get("candidates") or [], new_doc.get("candidates") or []):
        for c in src:
            if not isinstance(c, dict):
                continue
            k = _norm_person(c)
            if not k.strip("|"):
                continue
            if k not in merged:
                order.append(k)
            merged[k] = c
    out = dict(new_doc)
    out["candidates"] = [merged[k] for k in order]
    return out


def gate_respondents(run_dir: Path) -> dict:
    """Validate the market file + every target's file. Targets do NOT require
    one-pagers; where an accepted one-pager exists its hypotheses bound the
    H* refs. `refine` lists accepted files whose one-pager context is newer
    than the file itself — a refinement pass would retarget them at the new
    hypotheses/archetypes (update + merge, never discard)."""
    targets, q = sourcing_targets(run_dir)
    out = {"accepted": [], "rejected": [], "pending": [], "refine": [],
           "qual": q, "targets": targets}
    if not targets:
        return out

    all_hyps = {h["id"] for e in targets.values() for h in _hyps(e["op"]) if h["id"]}
    checks = [("market", MARKET_STEM, "Market level", all_hyps)]
    checks += [("company", e["stem"], ent,
                {h["id"] for h in _hyps(e["op"]) if h["id"]})
               for ent, e in sorted(targets.items())]

    entries = []
    for scope, stem, label, hyp_ids in checks:
        p = resp_path(run_dir, stem)
        doc = _load(p)
        if doc is None:
            out["pending"].append(label)
            continue
        fixes = autofix_doc(doc)          # deterministic, zero-API healing
        if fixes:
            p.write_text(json.dumps(doc, ensure_ascii=False, indent=2),
                         encoding="utf-8")
            runs._event(run_dir, "respondents_autofixed", scope=label,
                        fixes=len(fixes))
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

    # refinement staleness: an accepted respondents file sourced BEFORE the
    # matching one-pager(s) were accepted can be retargeted at the hypotheses
    op_mtimes = {ent: _mtime(onepager.qual_dir(run_dir) / f"{t['stem']}_onepager.json")
                 for ent, t in targets.items() if t["op"]}
    newest_op = max(op_mtimes.values(), default=0.0)
    for e in out["accepted"]:
        ref = newest_op if e["scope"] == "market" else op_mtimes.get(e["label"], 0.0)
        if ref > _mtime(e["path"]):
            out["refine"].append(e)

    if out["accepted"] or out["rejected"]:
        _write_shortlist(run_dir, out)    # every target's state stays visible
    return out


def shortlist_path(run_dir: Path) -> Path:
    return onepager.qual_dir(run_dir) / "respondents_shortlist.md"


def _write_shortlist(run_dir: Path, r: dict) -> None:
    """Standalone markdown deliverable for the ACCEPTED respondent files — it
    exists even when no one-pagers (and hence no .docx report) do; the report,
    when built, embeds the same shortlist."""
    goal = onepager.load_meta(run_dir).get("research_goal", "")
    lines = ["# Respondent shortlist", ""]
    if goal:
        lines += [f"**Research goal:** {goal}", ""]
    # every target reaches a visible state — the shortlist never leaves the
    # reader guessing which files made it in and which are still failing
    states = ([f"- ✓ {e['label']} — accepted "
               f"({len(e['doc'].get('candidates') or [])} candidates)"
               for e in r["accepted"]]
              + [f"- ✗ {e['label']} — rejected "
                 f"({len([i for i in e['issues'] if i['severity'] == 'reject'])} "
                 f"issue(s), see repair)" for e in r["rejected"]]
              + [f"- ⏳ {b} — not sourced yet" for b in r["pending"]])
    lines += ["## Target states", *states, ""]
    lines += ["Публичные профессиональные данные; контакт — только через "
              "публичный профиль. Порядок: приоритет 1 → 3.", ""]
    for e in sorted(r["accepted"], key=lambda e: (e["scope"] != "market", e["label"])):
        lines.append(f"## {e['label']}")
        for c in sorted(e["doc"].get("candidates") or [],
                        key=lambda c: (c.get("priority") or 9, str(c.get("name", "")))):
            lines.append(f"- {format_candidate(c)}")
        note = str(e["doc"].get("note") or "")
        if note:
            lines.append(f"- _note: {note}_")
        lines.append("")
    shortlist_path(run_dir).write_text("\n".join(lines), encoding="utf-8")


def _render_refine(run_dir: Path, meta_run: dict, entries: list[dict],
                   targets: dict) -> str:
    """Refinement prompt: one-pagers were accepted AFTER these files were
    sourced — update each shortlist against the new hypotheses/archetypes,
    merging (never restarting from scratch)."""
    save = f"logs/{meta_run['run_id']}/qual"
    blocks = []
    for e in entries:
        if e["scope"] == "market":
            hyps = [h for _ent, t in sorted(targets.items()) for h in _hyps(t["op"])]
            arch = [{**a, "company": ent} for ent, t in sorted(targets.items())
                    for a in _archetypes(t["op"])]
        else:
            t = targets.get(e["label"]) or {}
            hyps = _hyps(t.get("op") or {})
            arch = _archetypes(t.get("op") or {})
        blocks.append(f"""## {e['label']} — `{save}/{e['stem']}_respondents.json`
Existing candidates (KEEP everyone still valid — update, don't restart):
```json
{json.dumps(e['doc'].get('candidates') or [], ensure_ascii=False, indent=1)}
```
New hypotheses from the accepted one-pager(s):
```json
{json.dumps(hyps, ensure_ascii=False)}
```
Archetypes to cover:
```json
{json.dumps(arch, ensure_ascii=False)}
```""")
    return f"""# Respondent refinement pass — {meta_run['market']}

One-pagers were accepted AFTER the respondent files below were sourced. UPDATE
each file against the new hypotheses/archetypes: re-check that every existing
candidate is still relevant and current, point their `addresses` at the H* ids
they can actually speak to, add candidates for uncovered archetypes, and remove
only people who no longer fit. MERGE — do not discard the existing shortlist.
{_RULES}

{chr(10).join(blocks)}

Save each corrected file to its path above. Then re-run the check.
"""


def next_respondent_prompt(run_dir: Path, batch: int = 2) -> tuple[str, str]:
    """Prompt-mode driver for the optional sourcing stage — INDEPENDENT of the
    one-pager track: it needs a goal + targets (run-backed or manual), NOT
    accepted one-pagers."""
    meta_run = runs._load_meta(run_dir)
    qmeta = onepager.load_meta(run_dir)
    r = gate_respondents(run_dir)
    targets = r["targets"]
    if not targets:
        raise SystemExit(
            "Respondent sourcing needs at least one target — load a run or add "
            "companies first (tab «2 · Qualitative research»). The research "
            "goal is optional here; accepted one-pagers are NOT required.")

    if r["pending"]:
        if "Market level" in r["pending"]:
            kind = "respondents-market"
            entries = sorted(targets.values(), key=lambda e: e["entity"])
            text = build_respondent_prompt(run_dir, entries, meta_run,
                                           qmeta, market=True)
        else:
            todo = [targets[b] for b in r["pending"] if b in targets][:max(1, int(batch))]
            kind = "respondents-company"
            text = build_respondent_prompt(run_dir, todo, meta_run, qmeta,
                                           market=False)
    elif r["rejected"]:
        kind, text = "respondents-repair", _render_repair(meta_run, r["rejected"])
    elif r["refine"]:
        kind = "respondents-refine"
        text = _render_refine(run_dir, meta_run,
                              r["refine"][:max(1, int(batch))], targets)
    else:
        kind = "done"
        n = sum(len(e["doc"].get("candidates") or []) for e in r["accepted"])
        where = (("Also embedded in the qual report («Open report»): market "
                  "level in the executive summary, company candidates in each "
                  "company's section.")
                 if r["qual"]["accepted"] else
                 ("The one-pager track has not been run — it stays optional "
                  "and independent; start it any time (or skip it)."))
        text = (f"# Respondent sourcing complete — {len(r['accepted'])} file(s), "
                f"{n} candidates.\n\n"
                f"Shortlist: `qual/{shortlist_path(run_dir).name}`. {where}\n")
    onepager._issue_qual_prompt(run_dir, kind, text)
    return kind, text


def progress(run_dir: Path) -> dict:
    r = gate_respondents(run_dir)
    a, rej, p = len(r["accepted"]), len(r["rejected"]), len(r["pending"])
    n = sum(len(e["doc"].get("candidates") or []) for e in r["accepted"])
    contacts = sum(1 for e in r["accepted"]
                   for c in (e["doc"].get("candidates") or [])
                   if (c.get("contacts") or {}))
    phase = ("not started — set the goal and load/add targets "
             "(one-pagers NOT required)" if not r["targets"] else
             f"sourcing ({p} to go)" if p else
             f"repair — {rej} rejected" if rej else
             f"refine available — {n} candidates, new one-pagers to target"
             if r["refine"] else
             f"done — {n} candidates")
    return {"accepted": a, "rejected": rej, "pending": p, "candidates": n,
            "contacts": contacts, "refine": len(r["refine"]), "phase": phase}


# ── report helpers (consumed by onepager.build_report) ───────────────────────
def accepted_docs(run_dir: Path) -> dict:
    """{'market': doc|None, entity: doc} — accepted respondent files only, so a
    half-finished/rejected sourcing pass never leaks into the report."""
    out: dict = {}
    r = gate_respondents(run_dir)
    for e in r["accepted"]:
        out["market" if e["scope"] == "market" else e["label"]] = e["doc"]
    return out


def contact_channels(c: dict) -> str:
    """«telegram: @x · phone: … · email: …» — published channels only."""
    ct = c.get("contacts") or {}
    return " · ".join(f"{k}: {ct[k]}" for k in CONTACT_KEYS if ct.get(k))


def format_candidate(c: dict) -> str:
    """One-line rendering shared by the .docx report and the markdown pages."""
    addr = ", ".join(str(a) for a in (c.get("addresses") or []))
    ch = contact_channels(c)
    return (f"[P{c.get('priority', '?')}·{c.get('confidence', '?')}] "
            f"{c.get('name', '?')} — {c.get('role', '')}, {c.get('org', '')} · "
            f"{c.get('why_relevant', '')} · addresses: {addr} · "
            + (f"contacts: {ch} · " if ch else "")
            + f"{c.get('profile_url', '')} · проверено {c.get('verified_on', '?')}")


# ── outreach Excel deliverable ────────────────────────────────────────────────
RESP_COLUMNS = ["target", "name", "role", "org", "why_relevant", "priority",
                "telegram", "phone", "email", "linkedin", "profile_url",
                "sources", "confidence", "verified_on"]


def contact_rows(run_dir: Path) -> list[dict]:
    """Flat outreach rows from ACCEPTED respondent files (market + companies,
    incl. manual-only targets). One row per candidate, priority-sorted."""
    r = gate_respondents(run_dir)
    rows = []
    for e in sorted(r["accepted"], key=lambda e: (e["scope"] != "market", e["label"])):
        target = "Market" if e["scope"] == "market" else e["label"]
        for c in sorted(e["doc"].get("candidates") or [],
                        key=lambda c: (c.get("priority") or 9, str(c.get("name", "")))):
            ct = c.get("contacts") or {}
            rows.append({
                "target": target, "name": c.get("name", ""),
                "role": c.get("role", ""), "org": c.get("org", ""),
                "why_relevant": str(c.get("why_relevant", ""))[:200],
                "priority": c.get("priority", ""),
                "telegram": ct.get("telegram", ""), "phone": ct.get("phone", ""),
                "email": ct.get("email", ""), "linkedin": ct.get("linkedin", ""),
                "profile_url": c.get("profile_url", ""),
                "sources": " ; ".join(str(s) for s in (c.get("sources") or [])),
                "confidence": c.get("confidence", ""),
                "verified_on": c.get("verified_on", "")})
    return rows


def contacts_xlsx_path(run_dir: Path) -> Path:
    """The quant workbook if it exists (we add a sheet), else a standalone
    workbook whose FIRST sheet is Respondents (manual-only flow)."""
    meta = runs._load_meta(run_dir)
    xlsx = meta.get("xlsx")
    if xlsx and Path(xlsx).exists():
        return Path(xlsx)
    return run_dir / f"{runs.deliverable_name(meta)}.xlsx"


def build_contacts_xlsx(run_dir: Path) -> Path | None:
    """Write/refresh the «Respondents» sheet. Returns the workbook path, or
    None when no accepted candidates exist yet."""
    from . import export_excel as xl
    rows = contact_rows(run_dir)
    if not rows:
        return None
    out = contacts_xlsx_path(run_dir)
    xl.write_respondents_sheet(out, RESP_COLUMNS, rows)
    runs._event(run_dir, "respondents_xlsx", rows=len(rows), path=str(out))
    return out
