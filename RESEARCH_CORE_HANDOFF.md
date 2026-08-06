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
- **No Funds Intelligence yet** — this task builds the seam, not the pack.
- No live/paid requests anywhere in the core or its tests.

## Module boundaries (`research_core/`, pure stdlib)

| Module | Provides | Mirrors / generalises |
|---|---|---|
| `runstore.py` | `RunStore`, `RunHandle` — create/open/list runs, `run.json` meta, path/subdir helpers | company `logs/<run_id>/` + `runs.create_run`/`_load_meta` (company semantics removed) |
| `ledger.py` | `EventLedger` — append-only `events.jsonl`, read/iter/count/last, thread-safe | company `runs._event` (byte-identical line shape) |
| `validation.py` | `Issue`, `ValidationResult`, `RuleRegistry` — deterministic **code** rules selected by id; verdict = rejected iff any reject | company `gate` issue shape `{field,severity,code,reason}` + accept/reject verdict |
| `repair.py` | `RepairPatch`, `apply_patch` — scoped, non-destructive merge (in-scope overwrite, gap-fill, preserve-dropped, union flags) | company `gate.merge_repair` (the fix for real repair data-loss) |
| `control.py` | terminal-state vocab, `Limits`, `ControllerSnapshot` (snapshot-diff), `RunControl` (file-backed pause/stop/resume) | company `src.auto` controller |
| `gateways.py` | `ModelGateway`/`RetrievalGateway`/`Grounding` **contracts** + `Null*`/`InMemorySourceLog` offline adapters | company `model_router` / `web_tools` / `SourceLog` (wrapped, not imported) |
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

- **Built & tested (executable):** all seven modules above, as thin contracts
  plus three genuinely-reusable deterministic utilities (`EventLedger.append`,
  `apply_patch`, `ControllerSnapshot`) and offline Null/InMemory adapters.
- **Contracts only (no wiring yet):** `ModelGateway`/`RetrievalGateway`/
  `Grounding` are Protocols. Production adapters are **not** written — a pack
  wraps `src.model_router` / `src.web_tools` behind them when it needs live work.
- **No pack registered.** Company Intelligence is intentionally not adapted into
  a pack in this task.
- **Tests:** `tests/test_research_core.py` — **25 offline tests** (ledger incl.
  concurrency; run store; validation incl. code-only + selection; scoped repair;
  control incl. file-backed resume; gateway null adapters + grounding; spec/
  registry; an end-to-end example-pack flow; and a **src-isolation guard**).

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
# core only (offline)
python -m unittest tests.test_research_core                 # 25 tests, OK
# full suite = company (unchanged) + core, offline
SEARCH_PROVIDER=brave python -m unittest discover -s tests  # 319 tests, OK, 2 skipped
```
(`SEARCH_PROVIDER=brave` avoids the one company env-dependent test — see
`COMPANY_INTELLIGENCE_BASELINE.md` §discrepancies.)

## Recommended first milestone — Funds Intelligence as the first pack

Build `research_core`-based Funds Intelligence **without** touching `src/`:

1. **Define the fund schema & spec.** A `FundsPack` (`id="funds"`) with its
   `default_spec()` (geo, output_language, params) and `outputs()`
   (e.g. a fund table). Register it in `REGISTRY`.
2. **Write the Funds gate as code rules.** Register deterministic rules
   (e.g. `fund-name-required`, `aum-sourced`, `vintage-plausible`,
   `strategy-enum`, INN-for-manager where RU) in a `RuleRegistry`; the spec
   selects them. Reuse `apply_patch` for scoped repair of fund records.
3. **Provide gateway adapters.** Thin wrappers implementing `ModelGateway`
   (over `src.model_router.collect/verify`) and `RetrievalGateway`/`Grounding`
   (over `src.web_tools`) — the first real adapters. Keep them in the funds
   package, not the core.
4. **Drive it with a controller** over `RunStore` + `EventLedger` + `Limits` +
   `RunControl`, reaching the shared terminal states. Mirror the Auto safety
   model (paid-work/scope approval before any live step).
5. **Deliverable** via an `OutputSpec` builder (its own Excel/table writer;
   don't reuse the company columns).

Start read-only and offline (Null gateways) to validate the state machine, then
wire one live adapter behind an explicit approval. Prove each Funds gate rule
with an offline test before any live request.
