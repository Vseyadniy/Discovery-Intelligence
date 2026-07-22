# Architecture

Single-operator desktop app. `app.py` (Tkinter, 3 tabs) is a thin UI over a
file-based state machine in `src/`. There is no server and no live DB in the
main flow (the old `db/kb.sqlite` path is legacy and unused by the current
workflows). **Everything is driven off files in `logs/<run_id>/`;
`events.jsonl` is the append-only source of truth for all telemetry/counters.**

## Modules (`src/`)

| Module | Responsibility |
|---|---|
| `runs.py` | Quantitative state machine + run lifecycle: `create_run`, `next_prompt` (Prompt-mode driver), `run_gate`, `salvage_records`, `autofix_records`, `build_excel`, `build_discovery_prompt`/`build_research_prompt`, `telemetry_summary`, `diagnostics_prompt`, `_event`. Also hosts qual-meta helpers used across tracks. |
| `gate.py` | The ingest gate: `validate_record` (all reject/warn codes), ИНН checksum, money/headcount/percent normalizers, `record_quality`, `product_revenue_basis`, `is_placeholder`, repair-prompt rendering. Provider-agnostic. |
| `model_router.py` | Provider hub. `MODE` ∈ gpt/claude/grok/deepseek. `collect`/`verify` dispatch; streamed OpenAI Responses (shared by gpt+grok), streamed Claude Messages, DeepSeek Chat Completions with an app-side tool loop. Stage budgets, company concurrency, thread-local `SourceLog`. |
| `web_tools.py` | DeepSeek's browsing: `web_search` (Brave, `SEARCH_PROVIDER`-pluggable), `fetch_url` (requests+BeautifulSoup, truncated, never raises), `SourceLog` (records seen/fetched URLs + page text; `check_grounding`), sticky `QUOTA_EXHAUSTED`. |
| `api_runner.py` | API-mode executors that drive the same state machines: `run_next_step` (quant), `run_next_qual_step` (one-pagers), `run_respondent_step`. Grounding calls, failure taxonomy, telemetry events, repair caps, concurrency + backoff. |
| `auto.py` | Goal-based Auto v1 (quant only): a controller loop that repeatedly calls `run_next_step`/`build_excel` until a deterministic terminal state (`complete`, `complete-with-gaps`, `needs-review`, `awaiting-scope-approval`, `stopped-quota/provider/budget/no-progress`, `interrupted`, `blocked-input`). Snapshot-diff progress detection, run-level limits (steps/time/tool calls/tokens), one company per decision, decisions logged as `auto_*` events. Safety: paid work needs explicit approval (`--yes`/`--approve-scope`/interactive), a scope-approval stop after discovery, read-only `--plan`, zero-spend `--finalize-only`, and Ctrl-C → `auto_interrupted` + exit 130. DeepSeek-only, strictly sequential; adds no pipeline logic. |
| `onepager.py` | Qualitative track: qual-meta, targets resolution (`target_entries`, `sourcing_targets`), one-pager prompt/gate/render, `.docx` report, manual targets (`manual_entry`, `create_manual_run`). |
| `respondents.py` | Respondent sourcing: own validator (`validate_respondents`), `ground_candidates`, `autofix_doc`, `merge_candidates`, `gate_respondents` state machine, `build_contacts_xlsx`, rerun (`request_rerun`). |
| `export_excel.py` | `write_xlsx` (quant "Research" sheet, drill-down, highlights) and `write_respondents_sheet` (add/replace "Respondents"; formula-injection guard). |
| `orchestrator.py`, `render_prompts.py` | Prompt filling + emulation helpers shared by Prompt/API. |

`prompts/*.md` (collector_a, collector_b, verifier) are shared **byte-identical**
across Prompt mode, API mode, and emulation — never fork them per mode.

## State machines (all step-per-press; each reads gate state, emits one prompt/action)

- **Quantitative** (`runs.next_prompt` / `api_runner.run_next_step`):
  `discovery → per-company research (A ∥ B → verifier) → gate → repair* → build`.
  `salvage_records` + `autofix_records` run before each gate to heal mechanical
  problems with no model call.
- **Qualitative** (`onepager.next_qual_prompt` / `run_next_qual_step`):
  `research (one-pagers) → gate → repair* → report`. No browsing.
- **Respondent** (`respondents.next_respondent_prompt` / `run_respondent_step`):
  `pending (market → companies) → repair* → refine → rerun → done`. Browses.
  `refine` = auto-triggered when a one-pager is accepted after the file; `rerun`
  = user-triggered fresh pass (see below).

## Gate & repair invariants

- **Anti-fabrication is machine-enforced, not prompt-trusted.** A value needs a
  live non-search source URL; fabricated/placeholder/merge-loss values reject.
- **Repair is bounded** to stop livelocks (they were observed and fixed): record
  repairs cap at 3 attempts / two identical failure signatures, then evidence
  fields are blanked + flagged `unresolved:` (yellow in Excel) rather than
  looped forever. Collector-B codes (`b-copy`…) never go to record repair — they
  trigger a fresh Collector-B pass (cap 2), because the gate re-reads the
  `_A/_B` files a record edit can't change.
- **Computed/estimate fields are exempt from `unsourced`** when their inputs are
  sourced (YoY, 2026 projection) or their method column is tagged
  (`расчёт:`/`оценка:` for product revenue). Derived values inherit the lowest
  input confidence.
- **`autofix_records`** deterministically fixes segment near-misses, entity-type
  mapping, multi-ИНН fields, computable YoY, and money formatting — no API.

## Grounding (DeepSeek app-tools only)

`SourceLog` is **thread-local** (`mr.get_source_log`), reset per attempt, so
companies can run concurrently without cross-attributing browsing. After each
`extract_json`, `_ground` strips any cited source the pass never saw → gate
rejects it as `unsourced` → repair. Respondent **contacts** get their own
grounding: `ground_candidates` requires each channel value to appear in the text
of a page the pass actually **fetched** (phone by last-10 digits, telegram/
linkedin vanity, email verbatim) — a fabricated contact next to a real URL is
dropped. Repair/refine/rerun pass `prev_doc` so unchanged candidates/channels
are trusted and only new/changed ones are re-grounded (scoped, like the quant
`only_fields`).

## Budgets, concurrency, telemetry, providers

- **Per-stage tool budgets** (DeepSeek): defaults discovery 20 / collector_a 25 /
  collector_b 25 / repair 12 / respondents 18, env-overridable. A pass may
  **earn** `DS_BUDGET_EXTEND` extra calls only while required fields are
  unresolved AND new URLs keep appearing; it **stops early** when the last N
  calls surface nothing new (quota denials and duplicate queries count as
  non-novel). Sourcing never extends (people-URLs always look novel).
- **Company concurrency** `DS_COMPANY_CONCURRENCY` (1–4, default 1); correlated
  transient failures (≥2 timeout/stream/provider in a wave) trigger one jittered
  15–30 s backoff then retry only the affected companies from saved artifacts.
- **Resume**: research/respondent passes save incrementally; a retry reuses
  saved collector/record files and redoes only what's missing.
- **Telemetry** (`telemetry_summary`, `run_summary.md`): per-stage passes/
  failures/resumes, time, tokens (DeepSeek `include_usage`), search/fetch/denied,
  budget hits, grounding strips, repair outcomes (sig vs sig_after), failure
  categories + spend, `run_complete` with final unresolved fields. **Metrics a
  pre-telemetry event could not record render `n/a`, never 0.** Cost only with
  `TOKEN_PRICE_*` set. Failure error strings are URL-masked before persisting.
- **Providers**: gpt+grok share the streamed OpenAI Responses runner (web_search
  tool); Claude uses streamed Messages (server web_search, pause_turn loop);
  DeepSeek uses the app-side tool loop (streamed, 25-call hard cap, idle +
  pass-deadline timeouts, tools stay in the request past the cap to avoid the
  DSML-markup leak). `verify` (qual) runs without browsing on any provider.

## Respondent-specific invariants

- **Contacts live only in `contacts`** ({telegram,phone,email,linkedin}),
  format-checked, source-backed; anywhere else an email/phone is a hard reject.
  Generic inboxes and non-current roles reject; dedup within and across files.
- **`merge_candidates` preserves the file's scope/entity** from the existing doc
  — a rerun/refine returning a wrong scope cannot flip a file's identity. Old
  accepted candidates are always kept (superset merge).
- **Rerun** (`request_rerun`): stamps a request time + persistent attempt
  counter + mode into qual-meta; `gate_respondents` exposes a `rerun` bucket =
  accepted files with mtime ≤ request time. Each is re-sourced once (merging),
  the request auto-expires, `respondents_rerun_complete` is logged. The
  Excel/report rebuild only at the clean terminal branch, so the deliverable is
  preserved until the new pass succeeds.
- **Excel**: `write_respondents_sheet` loads an existing workbook and
  adds/replaces the "Respondents" sheet (other sheets preserved); `build_excel`
  re-attaches it so a quant rebuild doesn't drop it.

## Why non-obvious things are the way they are

- **Files, not memory**: every stage is resumable and inspectable; the app can
  crash or the user can reload a past run at any point.
- **Machine gate over trust**: the original failure mode was fabricated
  multi-agent research; the gate + salvage + bounded repair replaced trust.
- **Prompt/API parity**: the same prompts and gates run both modes so a pasted
  run matches an API run; only *who executes* changes.
- **Shared telemetry**: `events.jsonl` is the single ledger so diagnostics, run
  summaries, repair caps, ETA, and a future Auto mode all read one source.
