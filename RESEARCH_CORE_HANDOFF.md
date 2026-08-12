# Research Core — Handoff

Context for a **new** Claude Code chat that will build shared research
infrastructure and, next, the first specialised pack (Funds Intelligence). Pair
this with `ARCHITECTURE.md` (how Company Intelligence works today) and
`COMPANY_INTELLIGENCE_BASELINE.md` (the preserved product).

## Branch & starting commit

- **Branch:** `research-core`, pushed to `origin`
  (github.com/Vseyadniy/Discovery-Intelligence).
- **Starting commit:** `b8a878b` — identical to tag `company-intelligence-v1.0`
  (the preserved Company Intelligence baseline).
- **Do not merge `research-core` into `main`.** `main` stays the shippable
  Company Intelligence product.

## Purpose & non-goals

**Purpose.** Extract/curate the genuinely reusable *mechanisms* of the proven
Company Intelligence pipeline into a small, deterministic, provider-agnostic
core (`research_core/`) that future intelligence products (Funds, Assets,
Trends/Cases, Technology, Pricing, Telegram…) can share.

**Non-goals (deliberately).**
- Not a universal framework — only mechanisms already proven reusable or needed
  for the next pack.
- **No company migration.** Company Intelligence keeps running on `src/`,
  untouched and unrouted through the core.
- **No live/paid requests** anywhere in the core, the Funds pack, or the tests.
- No desktop UI and no natural-language planner yet.

## Module boundaries (`research_core/`, pure stdlib)

| Module | Provides | Mirrors / generalises |
|---|---|---|
| `runstore.py` | `RunStore`, `RunHandle` — create/open/list runs, `run.json` meta, path/subdir helpers | company `logs/<run_id>/` + `runs.create_run`/`_load_meta` (company semantics removed) |
| `ledger.py` | `EventLedger` — append-only `events.jsonl`, read/iter/count/last, thread-safe | company `runs._event` (byte-identical line shape) |
| `validation.py` | `Issue`, `ValidationResult`, `RuleRegistry` — deterministic **code** rules selected by id; verdict = rejected iff any reject | company `gate` issue shape `{field,severity,code,reason}` + accept/reject verdict |
| `repair.py` | `RepairPatch`, `apply_patch` — scoped, non-destructive merge (in-scope overwrite, gap-fill, preserve-dropped, union flags) | company `gate.merge_repair` (the fix for real repair data-loss) |
| `control.py` | terminal-state vocab, `Limits`, `ControllerSnapshot` (snapshot-diff), `RunControl` (file-backed pause/stop/resume) | company `src.auto` controller |
| `gateways.py` | **`ResearchPass`** (browse+extract+ground pass) + `ModelGateway`/`RetrievalGateway`/`Grounding` contracts + `Null*`/`InMemorySourceLog`/`ScriptedResearchPass` offline adapters | company `model_router.collect` / `verify` / `web_tools` / `SourceLog` (wrapped, not imported) |
| `spec.py` | `ResearchSpec`, `OutputSpec`, `ResearchPack` (protocol), `PackRegistry`, `REGISTRY` | the extension seam (new) |

Public surface is re-exported from `research_core/__init__.py`.

## What is reused vs. what stays company-specific

**Reused (now in the core as contracts + deterministic utilities):** run
persistence, event ledger, deterministic validation framework, scoped repair /
safe merge, controller terminal-states + limits + snapshot-diff + pause/stop/
resume, model & retrieval & grounding *contracts*, budgets/limits, output
descriptors, the pack/spec registry seam.

**Stays company-specific (remains in `src/`, outside the core):** company
discovery semantics; Collector A / Collector B / Verifier; ИНН validation; RU/CIS
registries; RUB-specific fields; the company financial schema; company gate
codes; company Excel columns; qualitative one-pager & respondent semantics; the
concrete provider adapters (`model_router`, `web_tools`) and their `.env`/paid
surface.

## Current implementation status

- **Built & tested (executable):** all seven core modules above, plus the first
  pack — **`funds_intelligence/`**, a complete OFFLINE vertical slice that
  pressure-tested the core against a genuinely different domain.
- **Production adapter WIRED, not executed:** `funds_intelligence/live_pass.py`
  implements `ResearchPass` over `src.model_router.collect()`. **No live call has
  been made**; the whole suite runs with sockets blocked. `ModelGateway` /
  `RetrievalGateway` remain unimplemented Protocols (not needed yet).
- **Company Intelligence is untouched** and not routed through the core: `src/`,
  `prompts/`, `config/`, `app.py` are byte-identical to
  `company-intelligence-v1.0`.
- **Tests: 406 total, 2 skipped** — 294 company (unchanged) + 33 core + 45 Funds
  + 34 adapter/preview/CLI. The adapter tests run with `socket.socket` replaced,
  so a real network call raises instead of leaking.

## Funds Intelligence — the first pack (`funds_intelligence/`)

Offline vertical slice. Purpose: prove the core against a domain that is a
**graph of distinct entity kinds**, not one flat record per subject.

| Module | Role |
|---|---|
| `model.py` | typed graph + four-valued claim/relationship states |
| `rules.py` | 8 deterministic semantic rules (registered callables) |
| `pack.py` | `FundsPack` (implements `ResearchPack`) + `FundsMandate` |
| `controller.py` | landscape → scope approval → one fund per safe step → gate → scoped repair → linked deep dive; paid-work gate + per-pass usage telemetry |
| `live_pass.py` | **the only module that imports `src/`** — `ResearchPass` over `model_router.collect()`, `SourceLog`→`Grounding`, usage + failure classification, provider pinning |
| `preview.py` | zero-call exposure report (`LivePlan` → paid passes / tool-call ceiling / stop conditions) |
| `__main__.py` | CLI: `preview` (free) · `live` / `research` / `deepdive` (paid, double-gated) |
| `fixtures/` | 4 deliberately tricky synthetic mandates (scripted passes, no network) |

### Production adapter boundary

```
funds_intelligence.LiveResearchPass   ← translation ONLY (no browsing logic)
        │ calls
        ▼
src.model_router.collect()            ← provider + tool-loop orchestration
        └─ src.web_tools               ← web_search / fetch_url / SourceLog / quota
```

`SourceLog` cannot be used as a `Grounding` directly — its `seen`/`fetched` are
**dicts** while the protocol needs **methods**, so `SourceLogGrounding` wraps it
and reuses `web_tools._norm` for URL comparison. Failure classification reuses
`api_runner._error_category`. Enforced by tests: `src/` may be imported **only**
by `live_pass.py`, and a fresh interpreter running the whole offline flow pulls
in **no** `src.*` module.

### Key data relationships

Six node kinds that must never be collapsed, plus evidence:

```
management_company ──manages──▶ fund_vehicle ──invested_in──▶ portfolio_company
        │                            ▲                              ▲
     employs                     continues                      deal_target
        ▼                            │                              │
     person            continuation_vehicle          deal ──deal_of─┘
```

* **AUM belongs to the management company; fund size belongs to a vehicle.**
* **A person's role is time-bound** — an `employs` edge with a `to` date is not
  current leadership.
* **Relationships carry their own evidence + state** — "X is in the portfolio"
  is a claim, not a by-product of context.
* **A continuation vehicle is its own vehicle**, linked to its predecessor.
* Every value is a `Claim` with state ∈ `confirmed` / `partially_confirmed` /
  `inferred` / `unresolved`, pointing at `Evidence` (url, tier, excerpt).

### Flow (all state file-derived; resumes across restarts)

```
create_run(mandate: geo, vintage window, filters, depth, target_count)
  → run_landscape()            → landscape.json  + awaiting-scope-approval  ← STOPS
  → approve_scope([names])     → scope.json
  → run_until_terminal()       → ONE fund per safe step → targets/ | rejected/
                                 terminal: complete | needs-review | stopped-*
  → repair_target(scope patch) → promotes rejected → targets/
  → create_deep_dive(target)   → CHILD run (parent_run_id, parent_target),
                                 inherits the parent's accepted graph+evidence
  → expand_deep_dive()         → adds deals / portfolio companies / people
```

Run folder (identical shape to a company run: `run.json` + `events.jsonl`):

```
logs/<date>_<label>_funds/
  run.json          meta incl. pack_id, pack_version, spec (mandate, rule_ids)
  events.jsonl      created · mandate_defined · landscape_built ·
                    awaiting_scope_approval · scope_approved · target_researched* ·
                    target_repaired* · auto_terminal
  landscape.json    candidates
  scope.json        approved names (the explicit human gate)
  targets/<t>.json  accepted graph + verdict + issues
  rejected/<t>.json rejected graph + issues (the repair queue)

logs/<date>_<target>-deep-dive_funds/
  run.json          parent_run_id, parent_target, kind=deep_dive
  events.jsonl      deep_dive_created · deep_dive_expanded
  targets/<t>.json  inherited parent graph, expanded
```

### The 8 deterministic Funds rules

`aum-vs-fund-size` · `manco-vs-vehicle` · `stale-person-role` ·
`unsupported-relationship` · `marketing-as-strategy` · `absence-as-negative` ·
`unsupported-claim` · `evidence-integrity` (warn).

All are Python callables registered by id; a spec *selects* ids. There is no
code path that compiles a rule from data, so an LLM can never author executable
validation.

## Two defects found by the smoke run, and fixed

1. **False `stopped-no-progress`.** `pending_targets` derived "attempted" only
   from `targets/` (accepted), so a **rejected** target stayed in the research
   queue forever; the controller re-researched it, the snapshot correctly saw no
   change, and the run died on step 2 with one target attempted.
   *Fix:* a target is attempted once it has **either** an accepted graph **or** a
   rejected record (rejected → repair queue, via `rejected_targets()`); the
   progress snapshot now fingerprints **meaningful persisted state**
   (per-target verdict + node/edge/evidence counts, so an in-place repair also
   counts as progress), and the baseline snapshot is taken **before** the first
   step. Regression: `test_defect1_successful_first_step_is_progress`.

2. **Incorrect `absence-as-negative`.** One rule conflated two different errors,
   firing on any factual claim without evidence — including a vintage that was
   simply unsourced. It also made "we found nothing" indistinguishable from
   "this does not exist".
   *Fix:* split into **`unsupported-claim`** (a factual claim with no evidence →
   record it as unresolved/inferred) and **`absence-as-negative`** (a *negative*
   asserted as fact while resting on absent evidence). An **evidence-backed
   negative** ("dissolved on 2023-04-01, per the register") remains a legitimate
   factual conclusion. Regressions:
   `test_unsupported_factual_claim_is_its_own_code_not_a_negative`,
   `test_evidence_backed_negative_conclusion_is_allowed`.

Separately, the Caspian fixture was made **harder** (not weaker) after it
initially passed: both bad claims are now quoted **verbatim** from their sources,
so grounding accepts them and only the semantic rules can catch them — the
realistic failure mode.

## What changed in the core this iteration, and why

Four changes, 107 inserted lines, all domain-neutral (no Funds vocabulary or
imports in `research_core/`):

| Change | Why it is genuinely reusable |
|---|---|
| `ledger.py`: append lock keyed by **resolved path**, shared process-wide | The documented "appended under a lock" guarantee was **per-instance**, but `RunHandle` builds a fresh `EventLedger` — so a controller and a UI holding two handles to one run had two locks and no mutual exclusion. Any pack with a UI/controller pair hits this. |
| `runstore.py` + `spec.py`: `pack_version` persisted in `run.json` and the `created` event; `version` on the `ResearchPack` protocol | A persisted run must identify the **exact** pack version that produced it, or it cannot be reproduced/resumed safely as the pack evolves. Applies to every pack. |
| `gateways.py`: new **`ResearchPass`** contract (`run_pass → text + grounding + engine + tokens`) | See the gateway-boundary section below. Any browse-based pack needs it. |
| `gateways.py`: `ScriptedResearchPass` offline adapter | Lets any pack run its full state machine deterministically with zero network. |

Also `tests/test_autofix_and_quota.py` (a **company test**, not product code):
`test_brave_402_sets_sticky_flag` mocks the Brave endpoint but did not pin
`SEARCH_PROVIDER`, so with `.env` on `tavily` it made a **live** call. It now
pins `brave` in its patch, making the suite offline regardless of `.env`.

## Gateway boundary — reviewed, deliberately not wired

Reading `src/model_router.collect()` settled the question: it is **not** a plain
model call and **not** a pack-driven search. It is a *single high-level research
pass* in which the provider owns the entire orchestration — server-side
`web_search` for gpt/claude/grok, and for DeepSeek a full app-side
search→fetch→budget→`SourceLog` tool loop — returning model text **plus** its
grounding log.

Expressing that as `ModelGateway.complete()` + `RetrievalGateway.search()` would
force every pack to **re-implement the tool loop**. So the core gained one
contract, `ResearchPass.run_pass()`, which mirrors `collect()`'s real shape. The
other two contracts keep their narrower jobs: `ModelGateway` for a no-browse call
(the company `verify` path), `RetrievalGateway` for a pack that drives its own
searches. **No production adapter is written yet** — that is the live milestone.

## What stays Funds-specific (never in the core)

Fund/vehicle/manager taxonomy; AUM-vs-fund-size semantics; vintage windows;
continuation vehicles; deal and portfolio-relationship modelling; person
role-currency; the 8 semantic rules; mandate shape (geography/window/filters/
depth/target_count); landscape and scope-approval semantics; the deep-dive
expansion policy; all fixtures.

## Remaining limitations

- **Offline only.** No live model/search adapter; `ResearchPass` has exactly one
  implementation (`ScriptedResearchPass`).
- **No UI, no NL planner, no deliverable writer** (outputs are declared in
  `OutputSpec` but no xlsx/docx builder exists for Funds).
- Landscape candidate generation is fixture-driven; there is no real discovery
  query strategy yet.
- Repair is API-level (`repair_target` + a scoped patch); there is no repair
  *prompt* loop, no repair cap, and no autofix equivalent yet.
- Entity resolution across runs (same manager found twice) is not implemented —
  duplicates are prevented only within a run, by target slug.
- `Limits` currently enforces steps only in the Funds controller; wall-time /
  tokens / tool-calls are defined but unused offline.
- **Pre-existing flaky company test (not a regression).**
  `tests/test_concurrency.py::test_two_companies_parallel_with_per_thread_attribution`
  asserts `len(seen_threads) == 2`, i.e. that both workers *genuinely* ran in
  parallel. Under CPU contention (e.g. several suites at once) the pool can
  serialise and the assertion fails `1 != 2`. Verified by running the test at
  tag `company-intelligence-v1.0` in a separate worktree under the same load —
  it fails there too, with this branch's code absent. Serial runs of the full
  suite are consistently green (14/14 clean). Left unfixed: it is company code,
  outside this branch's scope.

## Architectural decisions

1. **Pure stdlib; zero `src/` imports.** Enforced by
   `TestCoreIsolation` (static scan + a fresh-interpreter subprocess check), so
   the boundary can't erode and the core stays offline. This is *why* the
   company suite is provably unaffected.
2. **Format-compatible persistence.** `run.json` (pretty JSON) and
   `events.jsonl` (`{"ts","event",…}` per line) match the company on-disk shapes
   byte-for-byte, so tooling reads both and a future migration is low-friction.
3. **Deterministic validation, never LLM-authored.** Rules are registered Python
   callables; a spec *selects* rule ids; there is no code path that compiles a
   rule from data. Unknown ids raise (a plan can't silently drop a required
   rule).
4. **Gateways as contracts, adapters at the edge.** The core never pulls provider
   SDKs or `.env`; packs inject a gateway. Offline tests use the Null adapters.
5. **Thin over broad.** Reused the *policies* (scoped merge, terminal states,
   ledger) as small functions/value objects rather than copying mature company
   modules or refactoring them.

## Tests / commands

```bash
# Funds pack only (offline)
python -m unittest tests.test_funds_intelligence            # 44 tests, OK
# core only (offline)
python -m unittest tests.test_research_core                 # 33 tests, OK
# full suite = company (unchanged) + core + Funds
python -m unittest discover -s tests                        # 371 tests, OK, 2 skipped
```

The suite is now offline **regardless of `.env`** (verified green under
`SEARCH_PROVIDER=tavily`, `=brave`, and unset, each with a dead proxy).

## Next milestone — the first CONTROLLED LIVE Funds test (READY, not executed)

**Mandate.** "Find European private equity managers investing in B2B software and
tech-enabled services, with evidence of active buy-and-build activity during
2022–2026."

**Configured exposure** (from `python -m funds_intelligence preview`; all numbers
derived from configuration, none estimated):

| | |
|---|---|
| providers | `deepseek:deepseek-chat` + `tavily`, **no fallback**, concurrency 1 |
| paid passes | **5** = 1 landscape + 3 manager research + 1 deep dive |
| tool-call ceiling | **78** = 12 + 3×18 + 12 |
| searches | **≤ 78** (upper bound: every tool call could be a search) |
| max output tokens | **16,000** / pass → **80,000** / run |
| earned budget extension | **0** (disabled for run #1) |
| controller `max_steps` | 4 |
| cost | **not reported** — `TOKEN_PRICE_IN/OUT` are not configured |

**Gates before any spend:** zero-call Preview → `--approve-paid --yes` (both
required) → provider pinning (`ProviderMismatch` if not deepseek+tavily) →
landscape scope approval → one manager per safe step → pause/stop/resume via
`control.json`.

**Flow**

```bash
python -m funds_intelligence preview                       # free, read-only
python -m funds_intelligence live --approve-paid --yes     # creates run + landscape, then STOPS
python -m funds_intelligence approve <run_id> --targets "A" "B" "C"
python -m funds_intelligence research <run_id> --yes       # one manager per step
python -m funds_intelligence deepdive <run_id> --target "A" --yes
python -m funds_intelligence status <run_id>
```

**What to inspect afterwards:** `pass_usage` events (tokens/tool calls/searches
per stage), `target_researched` verdicts + codes, whether the 8 rules fire on
real sources and any false positives, whether grounding downgraded an ungrounded
AUM/fund-size, manager-vs-vehicle typing in `landscape.json`, and the
search error/rate-limit counters.
