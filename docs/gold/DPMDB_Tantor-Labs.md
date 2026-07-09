# GOLD — Tantor Labs   [DPMDB]

> Perfect-example ground truth, built from the B2B market map (segment tab + `$$$`
> + `Num of Prod`). This is the accuracy yardstick the pipeline is scored against.
> One field-block = one claim row, so it lines up 1:1 with a verifier output.

**Seed name:** Tantor Labs
**Website:** tantorlabs.ru
**INN:** 9701183207
**Subsector:** DPMDB
**Filled by:** market-map v6.5   **Date:** 2026-07-03   **Hand-time (min):** n/a (reference)

---

## Entity match  (get this right FIRST — a wrong entity poisons every field)
- brand_name: Tantor Labs
- legal_entity_name: ТАНТОР ЛАБС, ООО
- inn: 9701183207
- website: tantorlabs.ru
- entity_type: legal_entity
- confidence_entity_match: high
- disambiguation_note: Subsidiary of Astra Group (ПАО «Группа Астра», MOEX ASTR). Founded 2021. Attribute Tantor's own revenue to ООО «Тантор Лабс», not to the Astra parent.

---

## Business fields

### description
- value: PostgreSQL-focused vendor inside Astra Group; DBMS + database-management platform.
- source_url: https://tantorlabs.ru/
- source_tier: 2
- confidence: high
- snippet: (from company site / market map)

### segment
- value: PaaS / Data Platform — Managed Databases (MDB), on-prem DBMS vendor
- source_url: https://.../Num of Prod  (internal market map)
- source_tier: 2
- confidence: high
- snippet: 2 products in DP-MDB (on-prem).

### revenue   (RU: prefer the filed bo.nalog.ru / ГИР БО figure → high)
- value: 1.38 млрд ₽
- source_url: https://bo.nalog.ru/  (ГИР БО, ИНН 9701183207)
- source_tier: 1
- year: 2024
- confidence: high
- snippet: (filed accounting statements)
- assumptions: Filed 2024 for ООО «Тантор Лабс». 2025 ≈ 1.32 млрд ₽ (map).

### headcount
- value:
- source_url:
- source_tier:
- confidence: low
- snippet:
- assumptions: not in the B2B market-map source — fill from ЕГРЮЛ/СБИС/HH if needed.

### key_products
- value: Tantor Postgres, Tantor PipelineDB, Tantor Platform (DB management).
- source_url: https://tantorlabs.ru/
- source_tier: 2
- confidence: high
- snippet: 2 products in DP-MDB (on-prem).

### latest_news
- value: Parent Astra Group IPO'd on MOEX (ASTR), October 2023.
- source_url: MOEX / Astra IR
- source_tier: 2
- year: 2024
- confidence: high
- snippet:

---

## Registry provenance
- legal_entity_name / inn source: https://egrul.nalog.ru/  (ЕГРЮЛ, ИНН 9701183207)  (tier 1)

## Known A/B tension  (scores the verifier's conflict detection)
- field: legal_entity_name  |  A (registry) says: ООО «Тантор Лабс»  |  B (press) says: 'Группа Астра'  |  correct: ООО «Тантор Лабс» (parent: Astra Group)
