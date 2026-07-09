# Gold set — hand-filled ground truth

The accuracy yardstick for the pipeline (test_run_plan Step 0). Each file is one
company's schema filled with values you'd defend to a client, plus the source and
evidence behind every field.

## What's here
**15 filled "perfect example" gold records**, built from your B2B market map
(`docs/str_B2B market map WIP v6.5.xlsx`), across 3 subsectors — 5 each:

| Subsector | Companies |
|---|---|
| `GPU` (IaaS / GPU) | Cloud.ru, Selectel, K2 Cloud, MWS, Timeweb Cloud |
| `DPMDB` (PaaS / Data Platform — Managed DBs) | Arenadata, Postgres Pro, Tantor Labs, СберТех, Diasoft |
| `AppSec` (Security / Application Security) | Positive Technologies, Solar, Вебмониторэкс, SolidWall, Servicepipe |

Each record was **cross-referenced across multiple tabs** of the map — the segment
tab (`1.3 GPU` / `2.2.1 DP-MDB` / `Apps`) for description, key products and
product count, the **`$$$`** list for real INN, legal name and filed revenue
(2022–2025), and the **`Num of Prod`** list for the per-segment product counts.
E.g. **Arenadata** = 1 IaaS Compute + 12 PaaS products (1 Kubernetes + 6 DP-MDB +
5 DP-tools), revenue 5.18 → 8.8 ₽bn, ИНН 7713468845 (АРЕНАДАТА СОФТВЕР, ООО).

## Files
- `<SUBSECTOR>_<brand>.md` — the 15 filled examples.
- `build_examples.py` — regenerates the 15 from the market-map data (source of record; `--force` to overwrite).
- `_TEMPLATE.md` — blank fill-in form (for adding new companies).
- `generate_gold.py` — stamps blank templates from `inputs/input_entities.csv` (skips existing).

## Why these make good yardsticks
They deliberately include the traps the pipeline must get right:
- **brand ≠ legal name** — K2 Cloud (АО «К2 Интеграция»), Postgres Pro (ООО «ППГ»), SolidWall (ООО «Солидсофт»).
- **entity spans multiple INNs** — MWS (МТС brand across several legal entities).
- **company revenue ≠ product-line revenue** — СберТех, Positive Technologies, Arenadata (each has an A/B tension row spelling out the correct answer).
- **honest blank** — Solar revenue is absent from the source, so it's left blank (low), not guessed.

Every file ends with a **Known A/B tension** section — that's what scores the
verifier's conflict-detection metric in `../test_run_plan.md`.

## Workflow
1. (done) `python docs/gold/build_examples.py` → the 15 filled records.
2. Run the pipeline: `python -m src.orchestrator --limit 20` (inputs in `inputs/input_entities.csv` match these companies).
3. Diff each pipeline output against the matching gold file to score coverage / source quality / conflict detection / entity accuracy per `../test_run_plan.md`.

Add a new company: append it to `inputs/input_entities.csv`, then either hand-fill
from `_TEMPLATE.md` (`python docs/gold/generate_gold.py`) or extend the data list
in `build_examples.py`.
