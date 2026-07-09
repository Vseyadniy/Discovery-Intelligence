# Discovery Intelligence Data Layer — MVP v0.1

An internal accelerator for B2B market-intelligence delivery. Takes a list of
companies, runs **2 parallel collector agents** (official/registry vs
news/third-party), a **verifier** that merges them and flags conflicts, shows you
a **review card**, and writes the claims you approve into a **SQLite knowledge
base** with full provenance.

Not a SaaS. No web app, no dashboards, no monitoring. You operate it; it speeds up
paid delivery.

## Roles (fixed)
- **GPT-5.5 — researcher.** Runs the collectors: live web research on official,
  registry, financial, news and market sources. This is the engine that produces
  claims.
- **Claude — architect & administrator.** Designs and maintains the schema,
  prompts, source registry and pipeline; adjudicates the verifier's escalations
  (entity ambiguity, A/B conflicts, strategy calls); scores each run against the
  blinded gold set; and imports approved claims into the KB. Claude does not stand
  in for the researcher on routine collection.

## Folder structure
```
discovery-intelligence-layer/
├── README.md
├── requirements.txt
├── app.py                    # desktop launcher (Tkinter): pick model/market/depth → prompt → Excel
├── build_macos_app.py        # build a real double-clickable "Discovery Research.app"
├── launch_app.command        # dev fallback: run the app from Terminal
├── .env.example              # copy to .env, add local API keys if running the parser
├── config/
│   ├── schema.yaml           # the data schema each entity is filled against (+ output_language)
│   ├── depth_levels.yaml     # superficial / medium / detailed market-depth criteria
│   ├── source_registry.yaml  # geo × group × industry → sources (with tiers)
│   └── corrections.json      # your standing correction rules
├── logs/                     # one folder per research run (the record the architect analyzes)
├── inputs/
│   └── input_entities.csv    # the company list to process
├── db/
│   ├── schema.sql            # entities / claims / sources / corrections (+ view)
│   └── kb.sqlite             # created on first run
├── prompts/
│   ├── collector_a.md        # official / registry / financial
│   ├── collector_b.md        # news / market / third-party
│   └── verifier.md           # merge + conflict + confidence
├── templates/
│   └── review_card.md        # the human-review card
├── src/
│   ├── model_router.py       # GPT-5.5 research by default, escalate-to-Claude-on-a-flag
│   ├── db.py                 # SQLite helpers
│   ├── render_prompts.py     # print the real filled A/B/verifier prompts (emulated runs)
│   ├── runs.py               # run-log manager: create run + prompt, progress, build Excel, analyze
│   ├── export_excel.py       # verifier records / KB → .xlsx (Excel / Airtable-ready)
│   └── orchestrator.py       # the pipeline
├── outputs/
│   ├── review_cards/         # one .md card per entity
│   ├── agent_runs/           # emulated run: per-company A/B/verifier prompts + JSON
│   └── gpu_iaas_ru.csv       # the GPU research table (feeds the .xlsx; Airtable-ready)
└── docs/
    ├── test_run_plan.md            # Stage-1 plan (GPU / IaaS Russia, worked example)
    ├── researcher_codex_manual.md  # market-agnostic operating manual for GPT-5.5 in Codex
    └── test_gpu/
        └── GPU_IaaS_RU.xlsx        # client-facing deliverable (one row per company)
```

## Data model (claim-level provenance)
- **entities** — the disambiguated subject (brand/legal_entity/product/group), with
  `brand_name`, `legal_entity_name`, `inn`, `website`, `entity_type`,
  `confidence_entity_match`.
- **claims** — one field, one value, one source, with `confidence`, `snippet`,
  `year`, `assumptions`. Losing claims are kept as the evidence base.
- **sources** — every URL with a trust `tier` (1 official → 3 blog).
- **corrections** — your edits become rules, injected into later prompts.
- **consolidated_record** (view) — best approved claim per (entity, field),
  ranked by confidence → source tier → recency.

## Two modes
Pick per run with `--mode` (or `AGENT_MODE` in `.env`):

| Mode | Collectors | Verification | Needs |
|---|---|---|---|
| `gpt` | **GPT-5.5** (researcher) **with web search** | GPT-5.5; **escalates to Claude** on a flag | an OpenAI key |
| `claude` | Claude (Opus by default) **with web search** | Claude | nothing extra — runs here |

- **`gpt`** is the production division of labour: **GPT-5.5 researches** (OpenAI Responses API + `web_search` tool, real live sources) and default verification runs on GPT-5.5, escalating to `ESCALATION_MODEL` (Claude Opus) only on a flag — entity ambiguity, an unresolved A/B conflict, or a strategic call (`src/model_router.py`, `needs_escalation()`). Set `CHEAP_MODEL=gpt-5.5` and paste `CHEAP_API_KEY`. `CHEAP_BASE_URL` can point at any OpenAI-compatible endpoint; leave blank for OpenAI. (`cheap` is accepted as a back-compat alias for `gpt`.)
- **`claude`** runs fully on Claude and works out of the box (`ant auth login` or `ANTHROPIC_API_KEY`). Collectors use the server-side web-search tool so they research rather than answer from memory. Set `CLAUDE_MODEL` to `claude-opus-4-8` (default), `claude-sonnet-5`, or `claude-haiku-4-5`. Use this as the in-house quality baseline for the gold set.

Subscription-assisted research done directly in ChatGPT (GPT-5.5) can be saved under
`docs/`, but it is not a local parser run and will not write SQLite claims unless
the generated records are imported separately.

Escalation flag and mode routing live in `src/model_router.py` (`collect()`, `verify()`).

## Run
```bash
pip install -r requirements.txt
cp .env.example .env                          # claude mode works with just ANTHROPIC creds
python -m src.orchestrator --mode claude --limit 20                 # fully-Claude baseline (runs here)
python -m src.orchestrator --mode gpt    --limit 20                 # GPT-5.5 research + Claude escalation
python -m src.orchestrator --mode gpt --subsector IaaS/GPU          # scope a run to one subsector
```
Each entity produces a review card in `outputs/review_cards/`. In interactive mode
you approve/edit/reject in the terminal:
```
edit revenue = 1.2 млрд ₽ (2024, bo.nalog.ru) | list-org is stale, prefer the filing
reject headcount
approve all
```
Edits with a `| why` are stored as correction rules. Flagged (low-confidence /
conflicting) fields are **not** written unless you `approve all` or edit them.

Smoke-test without prompts: `python -m src.orchestrator --limit 2 --auto`.
Stage-1 run (GPU / IaaS Russia): `python -m src.orchestrator --mode gpt --subsector IaaS/GPU`.

## Desktop app — one button, no re-typing the prompt
`app.py` is a small Tkinter launcher so you don't log in and paste the manual every
time. It's the **prep + ingest** flow (no API key, uses your ChatGPT/Claude
subscription).

**Make it a real macOS app** (double-click from Launchpad/Dock, no Terminal):
```bash
python build_macos_app.py                    # → "Discovery Research.app" in the repo
python build_macos_app.py --into /Applications   # install it
```
This generates a proper `.app` bundle whose launcher runs `app.py` with the same
Python you built with (so tkinter/openpyxl/pyyaml are guaranteed present) — the repo
path and interpreter are baked in, so it keeps working after you move it. It's
**unsigned**, so on first launch use right-click → **Open** once to clear Gatekeeper.
Rebuild after moving the repo or changing Python environments. For a fully
self-contained app that bundles Python (to share with a machine that has no Python),
use `py2app` or `pyinstaller --windowed app.py` instead.

Or just run it from a terminal during development:
```bash
python app.py           # or double-click launch_app.command
```

1. Pick **model** (ChatGPT / Claude), type the **market**, choose **depth**
   (`config/depth_levels.yaml`):
   - **superficial** — large players only.
   - **medium** — large + niche + regional strong players (~80% of the market).
   - **detailed** — all real players (~90–95%), no one-person shops, filed revenue ≥ 25 млн ₽.
2. **Generate run** → creates `logs/<run>/` with a ready-to-paste **multi-agent
   `prompt.md`** — it runs all three agents in one go (🟦 Collector A registry/
   financial → 🟩 Collector B news/market, independent → 🟨 Verifier merge), with the
   market, depth, language, financial fields and save paths baked in. You paste
   **one** prompt; no separate Collector-B prompt.
3. Watch **progress %** (records collected ÷ discovered cohort in `companies.json`).
4. **Build Excel** → `research_table.xlsx` + `analysis.md`; **Open Excel** opens it.
5. **Publish to docs/ →** copies the final `.xlsx` + `analysis.md` into
   `docs/<market>/` (the clean deliverable; `logs/` stays as history).

Same steps from the terminal: `python -m src.runs create|progress|build|analyze|publish`.

## Run logs (history) vs docs/ (final deliverable)
Every run is a folder under `logs/` — a full record of *how* the research was done:
`prompt.md`, `companies.json` (discovered cohort), `agent_runs/` (per-company A/B/
verifier JSON with sources + snippets + flags), `events.jsonl` (timeline),
`research_table.xlsx/.csv`, and **`analysis.md`**. **Publish** promotes the finished
result to `docs/<market>/` — that folder is the deliverable; `logs/` is history.

`analysis.md` is the architect's read: registry/financial fill (legal name / INN /
2025 revenue / headcount), a **source-domain histogram** (over-reliance on company
sites is the usual cause of empty INN/revenue), a per-company breakdown, plus flags
for wrong-language prose, entity/product-vs-legal tensions, empty Collector-B passes,
and contaminated repo-path sources. Recurring flags → tighten the rule in `prompts/`
or the run prompt, not just the one run.

## Output — a table, not a Markdown report
The client-facing deliverable is a **spreadsheet, one row per company**. Either
render the researcher's filled CSV, or pivot a local pipeline run straight from the
KB:
```bash
python -m src.export_excel --template outputs/gpu_iaas_ru.csv        # blank GPU table to fill
python -m src.export_excel --csv outputs/gpu_iaas_ru.csv --out docs/test_gpu/GPU_IaaS_RU.xlsx
python -m src.export_excel --from-kb --out docs/test_gpu/GPU_IaaS_RU.xlsx   # after a real run
```
`GPU_IaaS_RU.xlsx` has a frozen header, auto-filter, wrapped cells, and highlights
low-confidence rows. The CSV is **Airtable-ready** — import it directly as a table.

See `docs/researcher_codex_manual.md` for the **market-agnostic** manual the
researcher (GPT-5.5) follows in Codex for *any* market, and `docs/test_run_plan.md`
for the GPU worked example. To point the pipeline at a new market, the administrator
edits three files: `config/schema.yaml` (fields, `geo`, and **`output_language`** —
the language of free-text values; proper names stay verbatim), `inputs/input_entities.csv`
(the seed companies), and `config/source_registry.yaml` (trusted sources).

**Blinding rule:** the researcher works from live public sources only and never
reads `docs/gold/*`; the gold set is the administrator's yardstick, opened only to
score a finished run. **Coverage rule:** Collector A must actually open the registry
(ЕГРЮЛ/Rusprofile) and filing (bo.nalog.ru) — a blank INN/revenue because "no
source was opened" is a coverage failure, not an honest blank.
