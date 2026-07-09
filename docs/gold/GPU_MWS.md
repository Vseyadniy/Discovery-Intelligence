# GOLD — MWS   [GPU]

> Perfect-example ground truth, built from the B2B market map (segment tab + `$$$`
> + `Num of Prod`). This is the accuracy yardstick the pipeline is scored against.
> One field-block = one claim row, so it lines up 1:1 with a verifier output.

**Seed name:** MWS
**Website:** mws.ru
**INN:** 7707767501
**Subsector:** GPU
**Filled by:** market-map v6.5   **Date:** 2026-07-03   **Hand-time (min):** n/a (reference)

---

## Entity match  (get this right FIRST — a wrong entity poisons every field)
- brand_name: MWS
- legal_entity_name: ООО «МВС»
- inn: 7707767501
- website: mws.ru
- entity_type: brand
- confidence_entity_match: medium
- disambiguation_note: MWS = MTS Web Services, MTS's cloud/AI brand launched 2024–2025. Spans MULTIPLE legal entities — main is ООО «МВС» (ИНН 7707767501); note also «МВС Облачные решения» ООО (ИНН 7841468537, ex-1Cloud/ИТ-Град). Do not attribute to МТС ПАО.

---

## Business fields

### description
- value: MTS's cloud & AI brand (MWS), launched its own GPU cloud in 2025.
- source_url: https://mws.ru/services/virtual-infrastructure-gpu/
- source_tier: 2
- confidence: high
- snippet: (from company site / market map)

### segment
- value: IaaS / GPU (cloud GPU)
- source_url: https://.../Num of Prod  (internal market map)
- source_tier: 2
- confidence: high
- snippet: 3 GPU products in map.

### revenue   (RU: prefer the filed bo.nalog.ru / ГИР БО figure → high)
- value: 51.37 млрд ₽
- source_url: https://bo.nalog.ru/  (ГИР БО, ИНН 7707767501)
- source_tier: 1
- year: 2024
- confidence: medium
- snippet: (filed accounting statements)
- assumptions: Filed for ООО «МВС» 2024; 2025 ≈ 50.07 млрд ₽. Confirm which MWS legal entity the figure belongs to — the brand is split across several INNs.

### headcount
- value:
- source_url:
- source_tier:
- confidence: low
- snippet:
- assumptions: not in the B2B market-map source — fill from ЕГРЮЛ/СБИС/HH if needed.

### key_products
- value: 3 GPU types; Cloud GPU with up to 4 configs.
- source_url: https://mws.ru/services/virtual-infrastructure-gpu/
- source_tier: 2
- confidence: high
- snippet: 3 GPU products in map.

### latest_news
- value: MTS consolidated cloud/AI assets under the MWS brand (2024–2025).
- source_url: MTS press — confirm
- source_tier: 2
- year: 2024
- confidence: medium
- snippet:

---

## Registry provenance
- legal_entity_name / inn source: https://egrul.nalog.ru/  (ЕГРЮЛ, ИНН 7707767501)  (tier 1)

## Known A/B tension  (scores the verifier's conflict detection)
- field: inn  |  A (registry) says: 7707767501 (ООО «МВС»)  |  B (press) says: 7841468537 (МВС Облачные решения)  |  correct: 7707767501 for the MWS cloud brand entity — verify per product
