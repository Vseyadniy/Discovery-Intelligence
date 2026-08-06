# Company Intelligence — Preserved Baseline (v1.0)

This document lets a **separate** Claude Code session return to the Company
Intelligence product without depending on the research-core work or this
conversation. Company Intelligence is frozen behind a durable tag; the new
`research-core` branch does not touch it.

## Preserved Git reference

- **Tag:** `company-intelligence-v1.0` (annotated) → commit
  **`b8a878b8943a7cd535877847ff9558bf9b5badc6`**
  ("Fix: search harness CLI must load .env (Tavily smoke blocker)").
- This is `main` HEAD at preservation time; **`main` is unchanged** by the
  research-core task.
- Return to it: `git checkout company-intelligence-v1.0` (or `git checkout main`
  — currently the same commit). Continue Company Intelligence work on `main`.

## Architecture & entry points

See `ARCHITECTURE.md` (accurate and current at this commit). Spine:
`src/runs.py` (`next_prompt`, `build_excel`) + `src/api_runner.py`
(`run_next_step`, `run_next_qual_step`, `run_respondent_step`).

- **Desktop app:** `python app.py` — Tkinter, 3 tabs (1 · Quantitative,
  2 · Qualitative + respondents, Settings).
- **CLI (same state machine):**
  - `python -m src.runs create|next|gate|build|telemetry <…>`
  - `python -m src.api_runner <run_id> [--qual|--respondents] --provider deepseek`
  - `python -m src.auto <run_id>` (Auto v1; `--plan` read-only, `--finalize-only`
    zero-spend)
  - `python -m src.search_harness <fixture> …` (search-only; never calls a model)
- **Run-folder format:** `logs/<date>_<market>_<depth>/` with `run.json`
  (metadata), **`events.jsonl` (append-only ledger — source of truth for
  telemetry/counters)**, `agent_runs/<Brand>_{A,B,record}.json`,
  `companies.json` (cohort + segments), `qual/` (one-pagers + `.docx`), `steps/`,
  gate/summary reports, and the Excel deliverable.

## Test command & result

```bash
SEARCH_PROVIDER=brave python -m unittest discover -s tests
```
→ **294 tests, OK, 2 skipped** (fully offline). `SEARCH_PROVIDER=brave` is
required — see the discrepancy note below.

## Current product behaviour (all preserved in the tag)

Quantitative company research (discovery → Collector A/B → Verifier → gate →
bounded repair → Excel); **Prompt and API modes** (GPT / Claude / Grok /
DeepSeek); **Auto** (goal-based v1, quant); **paid-work + scope approvals**;
**gate, grounding, repair limits, budgets, telemetry**; **file-derived resume**;
**qualitative** one-pagers + `.docx`; **respondent sourcing** (contacts Excel);
current run folders and exports; **RU/CIS assumptions and the company financial
schema** (ИНН, RUB revenue history, headcount, …).

## Important invariants (do not break when resuming)

- **Files, not memory.** Every stage is resumable/inspectable from
  `logs/<run>/`; `events.jsonl` is the single ledger.
- **Machine gate over trust.** A value needs a live non-search source URL;
  fabricated/placeholder/merge-loss values reject. Anti-fabrication is
  machine-enforced, not prompt-trusted.
- **Bounded repair.** Record repair caps at 3 attempts / 2 identical failure
  signatures, then blanks + flags `unresolved:` instead of looping;
  Collector-B codes trigger a fresh B pass (cap 2), never record repair.
  Repair is **scoped**: only the declared scope may overwrite; a malformed
  repair leaves the prior record byte-identical (`gate.merge_repair`).
- **Prompt/API parity.** `prompts/{collector_a,collector_b,verifier}.md` are
  **byte-identical** across modes — never fork per mode.
- **Thread-local grounding.** `SourceLog` is per-attempt/thread so concurrent
  companies don't cross-attribute browsing.
- **Deterministic salvage/autofix** run before every gate with no model call.

## Known limitations (unchanged from `HANDOFF.md`)

- Qualitative one-pager loop has full offline coverage + one live company, **not**
  a full multi-company live run (highest-value thing to validate next).
- OpenAI (gpt) path verified by construction only (billing inactive).
- Contact/URL grounding is DeepSeek-only by design.
- Search: Brave (default) + Tavily operator-selectable via `SEARCH_PROVIDER`;
  some Tavily live status codes still assumption-based (see `HANDOFF.md`).
- Legacy `db/kb.sqlite` + `outputs/` and old MVP `docs/` are unused by current
  workflows (a hygiene cleanup is deferred).

## Repository documentation discrepancies (found during preservation)

1. **`HANDOFF.md` is stale vs HEAD.** It names stable commit `9106072` and
   "283 tests"; HEAD is `b8a878b` (7 commits newer: Auto app-integration +
   Tavily provider) and the suite is **294 tests**. `HANDOFF.md` is also
   internally inconsistent ("283 tests" vs "Offline (178 tests)").
   `ARCHITECTURE.md` **is** current. `README.md` also says "283 tests" (→ 294).
2. **One environment-dependent test.**
   `tests/test_autofix_and_quota.py::TestQuotaFallback.test_brave_402_sets_sticky_flag`
   mocks the **Brave** endpoint but does not pin `SEARCH_PROVIDER` in its
   `patch.dict`, so when `.env` selects `SEARCH_PROVIDER=tavily` the code routes
   to a **live Tavily** call and the test errors (HTTP 400). Run the suite with
   `SEARCH_PROVIDER=brave` for a clean offline result. This is a latent
   test-hygiene defect (pin the provider in that test's patch); it was **not**
   fixed here to keep the preserved Company Intelligence code unchanged.

## How to resume Company Intelligence safely

1. `git checkout company-intelligence-v1.0` (or `main`).
2. `SEARCH_PROVIDER=brave python -m unittest discover -s tests` → expect 294 OK.
3. Work on `main`. **Do not route the company workflow through `research_core`**
   — the new core is a separate, additive foundation on the `research-core`
   branch and is not a dependency of any company module.
