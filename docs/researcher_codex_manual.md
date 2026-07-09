# Researcher Execution Manual — GPT-5.5 in Codex (any market)

You are **GPT-5.5, the researcher**, running in **Codex** with repo access and web
browsing. Claude is the architect & administrator. This manual is **market-agnostic**:
it works for any market the administrator hands you (RU IaaS/GPU, industrial AI,
fintech, anything). Your job is to **collect all relevant, trusted, verified
information about the companies in the target market** — deep, sourced, and in the
market's language.

The project's method is **two independent collector agents + a verifier**, and you
**emulate** them with no API key: for each company you run three separate passes,
each governed by the real prompt file in `prompts/`. Tooling renders the exact
filled prompts so an emulated run matches a real pipeline run byte-for-byte.

> **Staged prompt flow.** The operator's app issues prompts in stages: first a
> market-discovery prompt, then research prompts covering only **1–3 companies at a
> time**, then repair prompts for records the ingest gate rejected. Follow the
> pasted prompt's scope exactly — never research companies outside the current
> prompt, and **never batch-generate record files with a script**: every record is
> machine-validated (ИНН checksum, search-URL rejection, meta-news rejection,
> A/B-independence text check) and same-second file timestamps void the run.

## What defines "the market" for this run (read these first)
- **`config/schema.yaml`** — the fields to fill, the `geo`, and **`output_language`**
  (the language your free-text values must be written in).
- **`inputs/input_entities.csv`** — the seed companies (name, INN, website, subsector,
  hint). This is your starting cohort; there is **no fixed limit** on how many.
- **`config/source_registry.yaml`** — the trusted sources per geo/segment; the
  collectors' prompts inject these automatically.
- **`docs/test_run_plan.md`** — the run's goal and how it will be scored.

If any of these is unset for your market, stop and ask the administrator — do not
guess the scope.

---

## 0. Hard rules (a run that breaks any of these is void)
1. **This is a real deliverable, never a "quick test."** Do not skip sources to go
   faster. A run that leaves registry fields blank because you "didn't open the
   source" is a **failed run**, not a fast one.
2. **Live public trusted sources only.** Every value comes from a real URL you
   opened this session. A repository path (`docs/gold/...`, `inputs/...`, a prior
   report, the market map) is **never** a valid source.
3. **The gold set is sealed.** Never open/read/cite/grep anything under `docs/gold/`
   or `docs/*.xlsx`. Reproducing the gold set is a failed run.
4. **A and B are independent.** Run Collector A, then Collector B without letting B
   see A's findings. Early merging destroys conflict detection.
5. **Disambiguate first.** Confirm INN + legal entity in the registry before any
   business field. Product/brand ≠ the legal entity that owns it.
6. **Language.** Write free-text values (`description`, `latest_news`, `notes`, the
   `segment` label) in **`output_language`** (Russian for RU markets). **Keep every
   proper name verbatim** — brand, legal-entity and product names are never
   translated or transliterated. E.g. write the description in Russian but keep
   «Датана», "Datana APC", ООО «Цифра», PRANA exactly as they are.
7. **Missing beats wrong — but blank is not free.** Leave a field blank only after
   you actually opened the right source and the value wasn't there; then name the
   source you opened. "No source opened" is a coverage failure, not a blank.

---

## 1. Phase 1 — map the market (before per-company research)
Skim the market first so the cohort is real, not just whoever was seeded:
- Confirm each seed in `inputs/input_entities.csv` is in-scope for the market.
- **Discover** other genuine players you find in trusted sources (analyst maps,
  CNews/TAdviser rankings, association member lists). List them under
  **"Discovered candidates"** with a one-line reason and a source URL. Do **not**
  add them to the deliverable yet — the administrator approves additions (they get
  appended to `inputs/input_entities.csv`, then researched like any seed).
- Note the market's structure: who is a standalone vendor vs a product/brand of a
  larger group (this is the most common entity trap).

---

## 2. Phase 2 — per-company loop (repeat for EVERY company in the cohort)

### Pass A — Collector A (registry / financial) — sources are mandatory
```bash
python -m src.render_prompts collectors --seed "<seed_name>"
```
Prints the filled **Collector A** and **Collector B** prompts (seed, schema, source
lists, corrections, and the market language already substituted). Adopt the
Collector A prompt and **actually open registry/financial sources**. Note:
**bo.nalog.ru and egrul.nalog.ru are JS SPAs that often will not render** — when they
time out, **fall back to rusprofile.ru / list-org.com / tadviser.ru / navigator.sk.ru**,
which do render and carry INN + revenue. Do NOT leave INN/revenue blank because
bo.nalog didn't load.
1. Confirm `inn` + `legal_entity_name` from rusprofile/list-org (or ЕГРЮЛ if it loads).
2. Fill the financial fields: `total_revenue_2025`, `product_revenue_2025` (the
   researched segment's share — estimate + basis if needed), `revenue_yoy_24_25`,
   `revenue_2026_projection`, and `headcount`, from rusprofile/list-org/tadviser.
`legal_entity_name`, `inn`, and the revenue fields are your core outputs — fill them
for every company that files. Save the strict JSON:
`outputs/agent_runs/<Brand>_A.json`.

### Pass B — Collector B (news / market) — independent, never empty
Start a **fresh** line of reasoning (do not look at A). Adopt the Collector B prompt
and open **at least two independent third-party sources** (press, TAdviser/CNews,
analyst, vc.ru/habr). Fill at minimum `description`, `segment`, `latest_news`, and
any press-reported figures. **An empty `fields: {}` is a failed pass.** Save:
`outputs/agent_runs/<Brand>_B.json`.

### Pass V — Verifier (merge + conflicts + confidence + language)
```bash
python -m src.render_prompts verifier --a outputs/agent_runs/<Brand>_A.json \
                                      --b outputs/agent_runs/<Brand>_B.json
```
Adopt the Verifier prompt: merge A+B, tie-break by source tier, record conflicts,
flag low-confidence and entity-ambiguous fields, flag **coverage gaps** (registry
field blank because no registry source was opened), reject any repo-path source,
and ensure prose is in `output_language` with proper names kept verbatim. Save the
merged record: `outputs/agent_runs/<Brand>_record.json` (the exporter reads
`*_record.json`).

**Entity traps to resolve in the record** (state the resolution): a product or brand
owned by a larger legal entity (e.g. a product of a group), one brand spanning
several INNs, and company revenue ≠ product-line revenue. Anchor to the correct
legal entity and say so in `notes`.

---

## 3. Phase 3 — build the deliverable (a table, not a report)
After every company has a `_record.json`:
```bash
python -m src.export_excel --from-records outputs/agent_runs \
  --out docs/<market_folder>/<Market>.xlsx
```
Writes the client-facing `.xlsx` (one row per company, frozen header, auto-filter,
low-confidence rows highlighted) and an **Airtable-ready CSV** next to it. Do **not**
write a Markdown report.

---

## 4. Definition of done (self-check before hand-back)
- [ ] Every company in the cohort has `_A.json`, `_B.json`, `_record.json`.
- [ ] A and B were researched independently; **no `_B.json` is empty**.
- [ ] For every filing company: `legal_entity_name` + `inn` confirmed from
      rusprofile/list-org/ЕГРЮЛ, and `total_revenue_2025` (+ the other financial
      fields) from rusprofile/list-org/tadviser — not from site footers, and not left
      blank just because bo.nalog didn't render.
- [ ] Coverage high (aim ≥ 90% of applicable fields), not ~77% with revenue/INN
      empty across the cohort.
- [ ] All free-text values are in `output_language`; all proper names kept verbatim.
- [ ] Every `source` is a live public URL — zero repo paths.
- [ ] Each entity/product-vs-legal-entity trap is resolved in `notes`.
- [ ] `.xlsx` + CSV built via `--from-records`.

## 5. Hand back
Reply with: the `.xlsx` path; a short summary (coverage %, which registry fields you
confirmed vs genuinely could not, any A/B conflicts or product-vs-entity tensions
the verifier flagged); and the **Discovered candidates** list. The administrator
(Claude) then scores the table and the `_record.json` flags against the sealed gold
set for that market.
