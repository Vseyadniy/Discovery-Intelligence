# Handoff

## Current stable commit

`9106072` — "Rerun respondent sourcing over the same targets after «done»".
Branch `main`, pushed to `origin` (github.com/Vseyadniy/Discovery-Intelligence).
Test suite: **178 tests, all passing offline** (`python -m unittest discover -s tests`).
Standing rule: commit + push every iteration; never commit `.env`,
`db/kb.sqlite`, `logs/`, `dist/`, built `.app`.

## How the current architecture was built (milestones, oldest → newest)

1. **Staged pipeline + ingest gate.** Replaced fabricated single-turn
   multi-agent research with a per-company state machine (discovery →
   collectors A/B → verifier → gate → repair → build), a machine gate, and
   deterministic `salvage`/`autofix`.
2. **Data model maturation.** Unified number formats, entity-type taxonomy,
   17-column financial history with two-level Excel drill-down, structured
   descriptions, product-vs-other product split.
3. **Qualitative track.** One-pager generation (no browsing; fact/inference/
   hypothesis provenance), qual gate, `.docx` report; integrated as tab 2.
4. **App productization.** 3-tab UI, installers (mac+win), API mode with 4
   providers (GPT / Claude / Grok / DeepSeek), live agent status, ETA, Settings
   editors, git-based versioning.
5. **DeepSeek app-side browsing.** `web_tools` (Brave `web_search` + `fetch_url`)
   + `SourceLog` grounding, so DeepSeek can do quantitative research; later made
   thread-local for concurrency.
6. **Reliability hardening.** Streamed loops + timeouts (no wedged runs),
   resume-from-saved-artifacts, bounded repair (record cap 3, B-rerun cap 2),
   forced-finish at the tool budget, quota (HTTP 402) graceful fallback,
   deterministic autofix, product-revenue evidence hierarchy.
7. **Budgets, concurrency, telemetry, diagnostics.** Per-stage tool budgets with
   earned extension + field-aware early stop; `DS_COMPANY_CONCURRENCY` + jittered
   backoff; `events.jsonl` telemetry → `telemetry_summary`/`run_summary.md`;
   🩺 Diagnostics prompt; record/field quality states.
8. **Manual qualitative targets.** Add companies with user-provided context,
   with or without a quantitative run; standalone manual-only runs.
9. **Respondent sourcing.** Optional browsing stage → outreach contact list:
   own gate, URL + contact grounding, privacy net, cross-file dedup, bounded
   repair, Excel "Respondents" sheet, and a user-triggered rerun.

## Tested

- **Offline (178 tests):** gate codes + salvage/autofix; DeepSeek tool loop
  (streaming, budget, quota, dedup); grounding (records + contacts, prev_doc
  scope); repair caps + livelock guards; concurrency isolation + backoff;
  telemetry aggregation + n/a semantics; quality states; product-revenue basis;
  manual targets; respondent validation/privacy/dedup/Excel/rerun (API→Prompt,
  Prompt→API). UI checked by headless instantiation.
- **Live (DeepSeek + Brave):** quantitative runs end-to-end (discovery →
  research → autofix/repair → 100% gate → Excel), interrupted+resumed research,
  correlated-failure retries, the budget/early-stop/grounding paths, and
  respondent sourcing (market + companies, real published contacts found,
  privacy net caught guessed PR emails, Excel sheet written).

## Known issues / deferred

- **Qualitative one-pager loop** has full offline coverage + one live company,
  **not** a full multi-company live run — the highest-value thing to validate
  next.
- **OpenAI (gpt) path** verified by construction only (owner billing inactive).
- **Contact/URL grounding is DeepSeek-only** by design (Prompt/server-search
  providers expose no per-pass browsing log).
- **Brave free quota** ≈ one medium run; upgrade or add a `SEARCH_PROVIDER`
  (Tavily slot exists) for heavier use.
- **Legacy `db/kb.sqlite` + `outputs/`** are unused by current workflows; a
  cleanup pass could remove them and the old MVP docs under `docs/`.
- Low-severity respondent items from the audits (staleness window on
  `verified_on`, UI double-click guard, per-channel source attribution) remain
  as optional polish.

## Recommended next steps

1. **Full qualitative live run** (2–3 run-backed + 1 manual target, DeepSeek):
   one-pagers → repairs → report, then respondents → Excel. Confirm the qual
   repair loop converges and the report renders; this is the last major
   unvalidated live path.
2. **Goal-based Auto mode** (deferred feature, not yet built): one action that
   drives quant → qual → respondents to completion, deciding per stage whether
   to advance / repair / stop, using the existing telemetry (failure categories
   + spend, repair outcomes sig/sig_after, run_complete, quality states, budget
   signals). The needed signals are already recorded; Auto mode is the
   orchestration layer on top. Keep the existing bounded-repair and
   spend-guard invariants — do not let Auto mode loop or exhaust budgets.
3. **Repo hygiene**: remove the legacy KB/outputs paths and stale `docs/` MVP
   files so the tree matches the current product.

## Orientation for a new agent

Read `ARCHITECTURE.md`, then start at `src/runs.py` (`next_prompt` +
`build_excel`) and `src/api_runner.py` (`run_next_step`, `run_next_qual_step`,
`run_respondent_step`) — those three functions are the spine. `events.jsonl` in
any `logs/<run>/` folder shows exactly what happened. Reproduce any flow via the
CLI in the README without touching the UI.
