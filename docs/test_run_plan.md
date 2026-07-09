# Stage 1 Test Run Plan — GPU / IaaS Russia (MVP v0.1)

Goal: prove the pipeline produces client-grade records **faster than doing it by
hand and by fresh research** on one narrow market, and capture where it breaks.

> **What the last run got wrong.** The first attempt (`docs/test_gpu/run_report.md`)
> covered all 15 companies across 3 subsectors and — worse — filled every field by
> **copying the gold files** (`sources: docs/gold/GPU_*.md`). That is not research;
> it is reading the answer key. This plan fixes both: **one subsector, researched
> from live public sources, with the gold set sealed.**

## Roles (fixed)
- **GPT-5.5 — researcher.** Performs all collection. Works only from live public
  sources. **Must not open, read, cite, or copy any file inside this repository**
  — `docs/gold/*`, the market map, prior run reports, or `inputs/*` beyond the
  seed row it is handed. A `docs/gold/...` path is never a valid `source`.
- **Claude — architect & administrator.** Owns this plan, the schema, prompts and
  source registry; adjudicates verifier escalations; and **is the only party that
  ever opens the gold set**, and only *after* a run, to score it. Claude does not
  do the routine research.

## Scope of this run — **GPU sector of the IaaS market in Russia only**
This is a standalone research of one market, not the mixed 15-company sweep.

| Subsector | Cohort (seeds) |
|---|---|
| IaaS / GPU (RU) | Cloud.ru, Selectel, K2 Cloud, MWS, Timeweb Cloud |

- Run **only** the `IaaS/GPU` rows (5 companies) as the Stage-1 sample for this
  market. Because that is a 5-company slice, treat it as a **focused single-market
  pilot**, not the generic "score a 5-slice is not allowed" case — the constraint
  in earlier drafts was about accidentally shrinking the *mixed* sample, not about
  researching one market deliberately.
- **Optional market discovery.** Before the run, the researcher may propose
  additional RU GPU/IaaS providers it finds in the market (e.g. beyond the 5
  seeds) with a one-line justification and a source; the administrator approves
  additions into `inputs/input_entities.csv` (subsector `IaaS/GPU`). Discovery is
  encouraged — the point is a real map of the market, not a fixed list.

The GPU cohort was chosen because it exercises the RU data edge (real filed
revenue on `bo.nalog.ru`) plus the hard entity traps: brand ≠ legal name (K2 Cloud),
one brand across several INNs (MWS), and cloud-arm revenue ≠ group revenue
(Timeweb Cloud, Cloud.ru).

## Step 0 — gold set stays sealed (done, do not touch during research)
15 hand-filled gold records exist in `docs/gold/` (5 are GPU). They are the
**accuracy yardstick and nothing else**. During a run they are **blinded**: the
researcher never sees them. Only the administrator opens the 5 GPU gold files
*after* the run to score it (Step 2). To add a company to the market, append it to
`inputs/input_entities.csv`; gold is not required to research it.

## Step 1 — run the GPU cohort
Two ways to execute; pick one and label the artifact accordingly.

**(a) Local pipeline (writes to `db/kb.sqlite`)**
```bash
cp .env.example .env      # fill CHEAP_* (GPT-5.5) and ANTHROPIC_API_KEY
pip install -r requirements.txt
python -m src.orchestrator --mode gpt --subsector IaaS/GPU
```
Review each card in chat; approve / edit / reject. Corrections you type with a
`| why` become rules and should stop the same mistake recurring later in the run.

**(b) Subscription-assisted research (GPT-5.5 in Codex/ChatGPT)**
Hand the researcher **`docs/researcher_codex_manual.md`**. Because there is no API
key, Codex **emulates the two-collector + verifier architecture**: per company it
runs three independent passes governed by the real `prompts/` files —
```bash
python -m src.render_prompts collectors --seed "K2 Cloud"          # prints filled A + B prompts
python -m src.render_prompts verifier --a ..._A.json --b ..._B.json  # prints filled verifier prompt
```
saving `_A.json`, `_B.json`, `_record.json` per company under
`outputs/agent_runs/`. The table is then a deterministic projection of the verifier
records:
```bash
python -m src.export_excel --from-records outputs/agent_runs --out docs/test_gpu/GPU_IaaS_RU.xlsx
```
This does **not** populate `db/kb.sqlite` unless imported afterward. **The same
blinding rule applies** — collectors research live sources only, never the gold
files, and A and B stay independent.

**Deliverable format — a table, not a Markdown report.** Every field goes into
`outputs/gpu_iaas_ru.csv` (one row per company, columns = schema fields + sources
+ confidence + notes). Then build the client-facing workbook:
```bash
python -m src.export_excel --csv outputs/gpu_iaas_ru.csv --out docs/test_gpu/GPU_IaaS_RU.xlsx
```
This produces `GPU_IaaS_RU.xlsx` (easy-to-read, frozen header, one row per
company). The CSV is also **Airtable-ready** — import it directly as a new table.
The old `run_report.md` is superseded and kept only for reference.

## Step 2 — measure (administrator opens gold here, for the first time)
Score the 5 GPU rows against the 5 GPU gold files.
| Metric | How to measure | Target v0.1 |
|---|---|---|
| **Coverage** | % of schema fields filled (non-null) per entity | ≥ 80% |
| **Source quality** | % of filled fields with a **live** tier-1/2 source + snippet (a repo path scores 0) | ≥ 70% |
| **Conflict detection** | across the GPU gold "Known A/B tension" rows, did the verifier flag every real disagreement? | all real ones |
| **Entity accuracy** | % of entities with the correct legal entity / INN vs gold | 100% (all 5) |
| **Time saved** | your hand-time for the 5 ÷ pipeline+review time for the same 5 | ≥ 3× faster |
| **Cost mix** | verifier escalations to Claude ÷ total verifications | ≤ 20% |
| **Independence** | % of fields whose source is a live public URL, not a repo file | 100% |

## Step 3 — decide
- If any `source` is a `docs/gold/...` or other repo path → the run was
  contaminated; the **Independence** metric failed. Re-run with the blinding rule
  enforced before scoring anything else.
- If entity accuracy < 100% → tighten disambiguation (lower the escalation
  threshold). The brand≠legal and multi-INN traps (K2 Cloud, MWS) are the ones to
  watch — a wrong entity poisons every field.
- If the verifier misses the cloud-arm-vs-group revenue tensions (Cloud.ru,
  Timeweb Cloud) → strengthen the verifier prompt on that split.
- If coverage is low but sources are good → the collectors aren't reaching the
  right sources; adjust `source_registry.yaml`.
- If escalation rate > 20% → the verifier is over-flagging; loosen
  `needs_escalation()` thresholds.
- Capture every correction rule that generalized. Only after GPU passes, repeat
  the same blinded, single-market procedure for the next subsector (DP-MDB, then
  AppSec).
