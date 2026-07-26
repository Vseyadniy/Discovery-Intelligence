# Handoff

## Current stable commit

`9106072` — "Rerun respondent sourcing over the same targets after «done»".
Branch `main`, pushed to `origin` (github.com/Vseyadniy/Discovery-Intelligence).
Test suite: **283 tests, all passing offline** (`python -m unittest discover -s tests`).
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
- **Search providers (2026-07-26):** Brave (default) + **Tavily** are both
  operator-selectable via `SEARCH_PROVIDER` (no fallback, no key-based
  switching — a run stays on the chosen provider). Both normalise to the same
  `{title,url,snippet}` contract so DeepSeek tools / SourceLog / grounding /
  `fetch_url` are provider-independent. Tavily runs controlled/reproducible
  (basic depth, auto-params off, no answer, no raw content, existing limit),
  key in the Authorization header only. New search error taxonomy distinguishes
  transient rate limit (429, bounded retry, NOT sticky) / quota (402, sticky) /
  auth / config / timeout / network / malformed / empty; compact per-pass
  counts (`search_rate_limited`, `search_errors`) added to telemetry — no
  secrets, queries, or URLs logged. Search-only comparison harness
  `src/search_harness.py` (fixture-driven, brave|tavily|both, dry-run,
  `--max-queries`, offline `--analyze`, optional `--fetch-check`; never calls a
  model or touches a run). Example fixture: `config/search_fixture.example.yaml`.
  **Needs live validation:** no live Brave/Tavily call has been made — Tavily's
  real response shape/field names, its live status codes for rate-limit vs
  credit exhaustion (currently 429→rate-limit, 402→quota by assumption), and a
  real Brave-vs-Tavily quality/latency comparison via the harness are all
  unverified. Brave 402→sticky is unchanged and still covered offline; Brave
  429 is now transient (behaviour change — verify against the live free tier).
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
2. **Goal-based Auto mode** — v1 SHIPPED for the quantitative track
   (`src/auto.py`, `python -m src.auto <run_id>` or `--market "…"`):
   controller loop over `run_next_step`/`build_excel` with snapshot-diff
   progress detection, run-level limits (steps / wall time / tool calls /
   tokens), quota-safe `no_new_research` resolution path, and terminal states
   `complete`, `complete-with-gaps`, `needs-review`, `stopped-quota`,
   `stopped-provider`, `stopped-budget`, `stopped-no-progress`,
   `blocked-input` — all decisions logged as `auto_*` events. DeepSeek-only,
   strictly sequential, CLI-only (no UI button until Pause/Stop exists).
   **Safety hardening (after the first live run, 2026-07-21):** the run
   `2026-07-09_2346_saas-bpm_superficial` showed Auto spending without
   confirmation (discovery + 2 of 7 companies ≈ 2.4M tokens in before Ctrl-C,
   which crashed unlogged). Now: paid work requires `--yes`/`--approve-scope`/
   interactive confirmation; Auto stops at `awaiting-scope-approval` after
   discovery until the cohort is approved (persisted in run.json); one company
   per controller decision; `--plan` (read-only preview) and `--finalize-only`
   (deterministic gate + Excel, provably zero LLM/search calls); Ctrl-C →
   `auto_interrupted` event (action, company, snapshot, spend; in-flight pass
   marked incomplete) + clean exit 130. That interrupted run is resumable:
   BPMSoft + Directum records survive (both gate-rejected, repairable), 5
   companies pending — `--plan` shows the exact state.
   **App integration (2026-07-22):** tab 1 gained «4 · Auto» — Preview plan
   (read-only), Start/Resume, Pause, Stop. The buttons drive the same
   `auto_run`: approvals arrive as dialogs (paid start + scope review with
   cohort/segments), `AutoControl` pauses/stops cleanly between companies,
   `on_status` shows step/company/spend vs limits live, completion offers the
   Excel, and resume-after-app-restart works because all state is in the run
   folder. `create_auto_run` fixed the legacy-model mismatch: `model` stays a
   valid Prompt-mode paste target, the executor is recorded as
   `auto_provider: deepseek`. Manual ⚡/Build are parked while Auto owns the
   run.
   **Repair-safety fixes (2026-07-22 controlled run, СберКорус):** two
   defects confirmed from `logs/2026-07-22_2344_saas-работа-с-документами_
   superficial` and fixed.
   (a) *Repair damaged the record.* A repair response returned six schema
   fields at the record's TOP level with empty `fields` slots and dropped
   unrelated data; saved verbatim it produced fresh `merge-loss` +
   `required-empty` rejects (events at 21:24:51). Fix: repair now
   `lift_misplaced_fields` (top-level schema fields → `fields`, empty slots
   only, meta keys untouched) then `merge_repair` INTO the saved record —
   only the declared repair scope may overwrite, out-of-scope fields the
   model dropped/blanked are preserved, gap-fills still allowed, review_flags
   unioned. A malformed/parse-failing response now leaves the previous record
   byte-identical. `autofix_records` also lifts records already damaged on
   disk (no model call), so the existing run above is resumable. `only_fields`
   grounding, salvage, repair caps, and `unresolved:` blanking are unchanged.
   (b) *Capped reject blocked the queue.* СберКорус hit its repair cap yet
   kept being selected as the first rejected record, starving repairable ones
   until Auto quit as `stopped-no-progress` (21:29:41). Fix: `run_next_step`
   repair is now two passes — pass 1 settles cap-exhausted / B-rerun-exhausted
   records (blank+flag or MANUAL REVIEW) WITHOUT consuming a batch slot; pass
   2 spends the batch only on records that can still improve. Auto therefore
   works the eligible rejects before choosing a terminal state, and
   `needs-review` now names the quota context (unresearched companies) so it
   is distinct from genuine no-progress. Quota still blocks new company
   research (`no_new_research` path intact).
   Changed files: `src/gate.py` (lift/scope/merge helpers), `src/runs.py`
   (autofix lift), `src/api_runner.py` (two-pass queue + scoped merge),
   `src/auto.py` (terminal reason), `tests/test_repair_safety.py` (14 tests).
   **Still needs live validation:** an end-to-end app run reaching
   `complete`/`complete-with-gaps` on real DeepSeek output (offline tests use
   synthetic repair responses); confirm a real misplaced-field repair now
   converges instead of looping, and that a capped+repairable mix ends
   `needs-review`.
   **Next Auto milestones:** controlled live test through the app, qual +
   respondent orchestration, per-run spend cap in ₽/$.
3. **Repo hygiene**: remove the legacy KB/outputs paths and stale `docs/` MVP
   files so the tree matches the current product.

## Orientation for a new agent

Read `ARCHITECTURE.md`, then start at `src/runs.py` (`next_prompt` +
`build_excel`) and `src/api_runner.py` (`run_next_step`, `run_next_qual_step`,
`run_respondent_step`) — those three functions are the spine. `events.jsonl` in
any `logs/<run>/` folder shows exactly what happened. Reproduce any flow via the
CLI in the README without touching the UI.
