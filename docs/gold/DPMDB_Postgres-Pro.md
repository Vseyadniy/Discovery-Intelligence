# GOLD — Postgres Pro   [DPMDB]

> Perfect-example ground truth, built from the B2B market map (segment tab + `$$$`
> + `Num of Prod`). This is the accuracy yardstick the pipeline is scored against.
> One field-block = one claim row, so it lines up 1:1 with a verifier output.

**Seed name:** Postgres Pro
**Website:** postgrespro.ru
**INN:** 7729445882
**Subsector:** DPMDB
**Filled by:** market-map v6.5   **Date:** 2026-07-03   **Hand-time (min):** n/a (reference)

---

## Entity match  (get this right FIRST — a wrong entity poisons every field)
- brand_name: Postgres Pro
- legal_entity_name: ППГ, ООО
- inn: 7729445882
- website: postgrespro.ru
- entity_type: brand
- confidence_entity_match: high
- disambiguation_note: Brand 'Postgres Professional / Postgres Pro'; legal entity ООО «ППГ». Brand ≠ legal name.

---

## Business fields

### description
- value: Russian PostgreSQL vendor; enterprise-grade distribution and DBMS tooling for gov/enterprise.
- source_url: https://postgrespro.ru/products/postgrespro/enterprise
- source_tier: 2
- confidence: high
- snippet: (from company site / market map)

### segment
- value: PaaS / Data Platform — Managed Databases (MDB), on-prem DBMS vendor
- source_url: https://.../Num of Prod  (internal market map)
- source_tier: 2
- confidence: high
- snippet: 9 products in DP-MDB. Cross-list: PaaS ALL 11 (DP-MDB 9, Analytics-platform 1, Dev tools 1), IaaS 1, Infra SW 1.

### revenue   (RU: prefer the filed bo.nalog.ru / ГИР БО figure → high)
- value: 9.29 млрд ₽
- source_url: https://bo.nalog.ru/  (ГИР БО, ИНН 7729445882)
- source_tier: 1
- year: 2024
- confidence: high
- snippet: (filed accounting statements)
- assumptions: Filed 2024 for ООО «ППГ». 2025 ≈ 6.66 млрд ₽ (map) — a DECREASE vs 2024; verify against the filing before publishing.

### headcount
- value:
- source_url:
- source_tier:
- confidence: low
- snippet:
- assumptions: not in the B2B market-map source — fill from ЕГРЮЛ/СБИС/HH if needed.

### key_products
- value: Postgres Pro Enterprise, Postgres Pro Standard, Postgres Pro Certified, Postgres Pro Shardman.
- source_url: https://postgrespro.ru/products/postgrespro/enterprise
- source_tier: 2
- confidence: high
- snippet: 9 products in DP-MDB. Cross-list: PaaS ALL 11 (DP-MDB 9, Analytics-platform 1, Dev tools 1), IaaS 1, Infra SW 1.

### latest_news
- value: —
- source_url: —
- source_tier: 2
- year: 2024
- confidence: low
- snippet:

---

## Registry provenance
- legal_entity_name / inn source: https://egrul.nalog.ru/  (ЕГРЮЛ, ИНН 7729445882)  (tier 1)

## Known A/B tension  (scores the verifier's conflict detection)
- field: legal_entity_name  |  A (registry) says: ООО «ППГ»  |  B (press) says: 'Postgres Professional'  |  correct: ООО «ППГ» (brand Postgres Pro)
