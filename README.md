# Discovery Intelligence Layer

A desktop app (Python + Tkinter) for **B2B market & competitive intelligence**.
You give it a market; it produces three deliverables:

1. **Quantitative research** — a validated Excel table of companies (legal
   entity, ИНН, revenue history, headcount, products, positioning, significant
   news, …), built by multiple research agents behind a strict anti-fabrication
   gate.
2. **Qualitative research** — a per-company "one-pager" (context, hypotheses,
   interview brief, survey, interview guide, priorities) and a merged `.docx`
   report, with every claim marked as fact / inference / hypothesis.
3. **Respondent sourcing** — an outreach-ready contact list: named real people
   (market experts + company-specific candidates) with **published** Telegram /
   phone / email / LinkedIn, written into a **Respondents** sheet in the Excel
   workbook.

Every stage works in two modes: **Prompt** (copy a prompt, paste into a
ChatGPT/Claude web chat, paste the JSON back — no API key) or **API** (the app
calls the model itself — automatic ⚡). All three workflows are independent: you
can run any subset.

> Not a SaaS. A single-operator desktop tool. Your API keys live only in a local
> `.env` and never leave your machine.

---

## Install & launch

Requires **Python 3.11+**.

```bash
pip install -r requirements.txt      # anthropic, openai, pyyaml, openpyxl,
                                     # python-docx, requests, beautifulsoup4
python app.py                        # opens the 3-tab desktop app
```

For sharing, `python make_installer.py` builds a zip with double-click
installers for macOS (`install.command`) and Windows (`install.bat`); recipients
enter their own keys. The app runs without any key in **Prompt** mode.

The app has three tabs: **1 · Quantitative research**, **2 · Qualitative
research** (which also hosts respondent sourcing), and **Settings** (API keys,
Excel/report layout editors).

---

## Providers & modes

**Prompt mode** needs no keys: press *Next prompt ▶*, paste the prompt into any
capable web chat with browsing, save the returned JSON where the prompt says,
press again. **API mode** (⚡) drives the same state machine automatically.

Four API providers (set keys in Settings → saved to `.env`):

| Provider | Model (default) | Web research |
|---|---|---|
| ChatGPT | `gpt-5.5` (OpenAI Responses API) | server-side `web_search` |
| Claude | `claude-opus-4-8` | server-side `web_search` |
| Grok | `grok-4.20-0309-reasoning` (xAI) | server-side (Agent Tools API) |
| DeepSeek | `deepseek-chat` | **app-side** tools (needs a Search API key) |

DeepSeek has no server-side browsing, so the app gives it its own
`web_search` + `fetch_url` tools (client-side function calling). That requires a
**Brave Search API key** (`SEARCH_API_KEY`; free tier at
api-dashboard.search.brave.com). One medium-depth run can exhaust the Brave free
monthly quota — the app degrades gracefully (see Limitations).

---

## Workflow 1 — Quantitative research (tab 1)

1. **Configure**: enter the market, pick depth (superficial / medium /
   detailed), pick mode, set companies-per-step, press **Generate run ▶** (or
   **Load past run…**). A run folder is created under
   `logs/<date>_<market>_<depth>/`.
2. **Discovery**: the model returns the company cohort + a short **segment
   taxonomy** (`companies.json`).
3. **Per-company research**: for each company, **Collector A** (official /
   registry / financial sources) and **Collector B** (news / market / analyst)
   research independently, then a **Verifier** merges them into one record.
4. **Ingest gate**: every record is machine-validated (see below). Rejected
   records go to a bounded **repair loop**; mechanical problems are auto-fixed
   with no model call.
5. **Build Excel**: gate-accepted records → `<date>_<market>_research.xlsx`
   (+ `.csv`). Financial columns have a two-level drill-down; low-confidence
   cells are peach, unresolved cells yellow; a `data_quality` column summarizes
   each record.

In **API mode** you also get a live agent status line (which agent is
searching / reading / analyzing, and the query or URL), an ETA, and a **🩺
Diagnostics** button that writes `run_summary.md` and copies a ready-to-paste
ChatGPT analysis prompt.

**What the gate rejects** (a record must survive all of these to ship):
fabricated or missing sources (`unsourced`, `search-url`, `bad-source`),
placeholder values, invalid ИНН checksum, merge-loss (a value the collectors
found but the record dropped), off-taxonomy segments, insignificant "news",
product-revenue figures with no stated method, and Collector-B independence
failures (`b-copy`/`b-empty`/…, which trigger a fresh Collector-B pass, not a
record edit). Transparent **estimates** are allowed: a product-revenue figure
tagged `расчёт:`/`оценка:` in its method column is accepted without a per-figure
URL.

---

## Workflow 2 — Qualitative research (tab 2)

1. Load a run (or add targets), enter the **research goal**, confirm an
   **angle** per company (competitor / customer / partner / benchmark / …).
2. **Next qual prompt ▶** / ⚡ generates one-pagers. The model does **not**
   browse here — it works only from the gate-accepted record, so it cannot
   invent facts. Each claim carries a provenance basis: **[Ф] fact** (must cite
   record fields), **[В] inference**, **[Г] hypothesis** (must carry
   `validated_if`).
3. A qual gate validates counts, enums, and the fact-not-in-record rule; a
   bounded repair loop fixes rejects.
4. **Open report** builds a `.docx`: executive summary + one page per company
   (context, hypotheses, interview brief, 5–8 question survey, 10–15 question
   interview guide, priorities).

**Manual targets:** you can add a company that has no quantitative record via
**➕ Add company…** (name + market segment + optional notes). Its context is
exactly what you type (marked provenance `user-provided`); everything else is
treated as unclear/hypothesis. Manual and run-backed targets coexist. You can
also do qualitative/respondent work on a standalone manual-only run with no
quantitative research at all.

---

## Workflow 3 — Respondent sourcing → outreach contact list (tab 2, own section)

Turns the one-pagers' abstract respondent archetypes into **named, reachable
people**. Independent of the one-pager track — needs only targets.

- **Step 1** — *⚡ Find respondents* (API) or *▶ Prompt* (manual). Two groups
  are sourced: **market-level** experts/analysts and **company-specific**
  candidates. This stage **browses** (unlike one-pager design). For each
  candidate it runs a small bounded contact search (Telegram → phone → email →
  LinkedIn) and stops at one or two usable published channels.
- **🔄 Run again** — start a fresh pass over the same targets even after "done";
  new people are merged into the shortlist, previous results preserved.
- **Step 2** — *📇 Open Excel* (the **Respondents** sheet) or *Shortlist (md)*.

**Privacy is enforced by code, not just the prompt.** Contact channels are
allowed **only** inside a candidate's `contacts` field, must be a *published*
professional channel, and (in DeepSeek/API mode) must actually appear in a page
the pass fetched — a fabricated address is stripped, not shipped. Emails/phones
anywhere else in the payload are a hard reject; generic inboxes
(`info@`/`pr@`/…) and non-current roles are rejected; duplicates are removed
within and across files.

**Excel behavior:** if the quantitative workbook exists, a **Respondents** sheet
is added/refreshed in it; if the targets are manual-only (no workbook), a new
workbook is created with **Respondents** as its first sheet. Manual companies
appear only in the Respondents sheet — quantitative research is never run for
them. Columns: target, name, role, org, why_relevant, priority, telegram, phone,
email, linkedin, profile_url, sources, confidence, verified_on.

---

## Outputs (in each `logs/<run>/` folder)

| File | What |
|---|---|
| `<date>_<market>_research.xlsx` / `.csv` | quantitative deliverable (+ Respondents sheet) |
| `qual/*_onepager.json` / `.md` | per-company qualitative one-pagers |
| `qual/<date>_<market>_qual_report.docx` | merged qualitative report |
| `qual/*_respondents.json` | respondent files (market + per company) |
| `qual/respondents_shortlist.md` | human-readable respondent shortlist + target states |
| `run_summary.md` / `diagnostics_prompt.md` | telemetry summary + paste-ready analysis prompt |
| `events.jsonl` | append-only per-run telemetry (source of truth for all counters) |
| `gate_report.md`, `companies.json`, `agent_runs/` | gate report, cohort, raw A/B/record JSON |

---

## Configuration (`.env`, edited via Settings)

- **Keys/models:** `CHEAP_API_KEY`/`CHEAP_MODEL` (OpenAI), `ANTHROPIC_API_KEY`/
  `CLAUDE_MODEL`, `GROK_API_KEY`/`GROK_MODEL`, `DEEPSEEK_API_KEY`/`DEEPSEEK_MODEL`,
  `SEARCH_API_KEY`/`SEARCH_PROVIDER` (Brave), `AGENT_MODE` (default provider).
- **Cost line:** `TOKEN_PRICE_IN`/`TOKEN_PRICE_OUT` (USD per 1M tokens) — only
  then does the run summary show an estimated cost.
- **DeepSeek tuning:** `DS_BUDGET_DISCOVERY|COLLECTOR_A|COLLECTOR_B|REPAIR`
  (per-stage tool-call budgets; respondents defaults to 18), `DS_BUDGET_EXTEND`
  (earned extension), `DS_COMPANY_CONCURRENCY` (1–4 companies at once, default 1).

Schema and layout are editable in the app (Settings) and in
`config/schema.yaml`, `config/custom_fields.yaml`, `config/onepager_blocks.yaml`,
`config/source_registry.yaml`, `config/depth_levels.yaml`.

---

## CLI (same state machine as the app; handy for testing)

```bash
python -m src.runs create --market "…" --depth medium         # new run
python -m src.runs next <run_id> --batch 3                    # next Prompt-mode prompt
python -m src.runs gate <run_id>                              # validate records
python -m src.runs build <run_id>                             # build Excel
python -m src.runs telemetry <run_id>                         # run summary
python -m src.api_runner <run_id> --provider deepseek --batch 3        # ⚡ quantitative step
python -m src.api_runner <run_id> --qual --provider deepseek           # ⚡ qualitative step
python -m src.api_runner <run_id> --respondents --provider deepseek    # ⚡ respondent step
python -m src.auto <run_id> --plan                            # read-only preview (no API)
python -m src.auto <run_id>                                   # Auto v1: drive the quant
                                                              # run to a terminal state
python -m src.auto --market "…" --depth medium --yes          # …creating the run first
python -m src.auto <run_id> --finalize-only                   # gate + Excel only, zero spend
```

Auto v1 (quantitative only, DeepSeek, **one company per step**) repeats the ⚡
step until a deterministic terminal state: `complete` / `complete-with-gaps`
(Excel built), `needs-review` (repair caps exhausted),
`awaiting-scope-approval` (see below), `stopped-quota` / `-provider` /
`-budget` / `-no-progress`, `interrupted` (Ctrl-C, exit 130), or
`blocked-input`. Limits: `--max-steps`, `--max-minutes`, `--max-tool-calls`,
`--max-tokens`; limits, stop signals, and snapshots are re-checked between
every company. Every decision is logged to `events.jsonl`.

**Spending is opt-in.** Paid work (discovery/research/repair) needs `--yes`
(alias `--unattended` — the fully autonomous behavior), `--approve-scope`, or
an interactive y/N confirmation. After discovery Auto **stops at
`awaiting-scope-approval`**, shows the cohort + segments, and researches
nothing until the scope is approved (persisted in the run, so a re-run
continues). `--plan` previews state, next actions, and an upper-bound plan
without any API call; `--finalize-only` allows only deterministic gate checks
and Excel and refuses if research or model repair would be needed. On Brave
quota exhaustion Auto finishes the repair/blank path for already-researched
companies and stops before starting new ones. Ctrl-C logs `auto_interrupted`
(with observable spend; in-flight pass usage marked incomplete) and preserves
all completed artifacts.

Tests: `python -m unittest discover -s tests` (222 tests, offline).

---

## Current limitations

- **Live coverage is partial.** Quantitative + respondent flows are validated
  live on DeepSeek; the qualitative one-pager loop is exercised in tests and one
  live company, not a full multi-company live run. See `HANDOFF.md`.
- **Brave free quota** (~2,000 req/month) ≈ one medium-depth DeepSeek run. On
  HTTP 402 the app stops searching, keeps using already-opened pages, leaves
  unresolved fields blank (yellow), and says so — it does not silently degrade.
- **Contact grounding is DeepSeek-only.** In Prompt mode the app cannot verify
  what the web chat browsed, so contact text-grounding applies to the app-tools
  (DeepSeek) path.
- **OpenAI billing** on the owner's account is currently inactive (external), so
  the gpt provider path is verified by construction, not live.
- **Goal-based Auto mode** is partially built: v1 (`python -m src.auto`) drives
  the **quantitative** track to a terminal state (CLI only, DeepSeek only, no
  Pause/Stop). Qual/respondent orchestration and the one-button UI are next.

---

For system internals and invariants see **`ARCHITECTURE.md`**; for project state,
milestones, and next steps see **`HANDOFF.md`**.
