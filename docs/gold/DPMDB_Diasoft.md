# GOLD — Diasoft   [DPMDB]

> Perfect-example ground truth, built from the B2B market map (segment tab + `$$$`
> + `Num of Prod`). This is the accuracy yardstick the pipeline is scored against.
> One field-block = one claim row, so it lines up 1:1 with a verifier output.

**Seed name:** Diasoft
**Website:** diasoft.ru
**INN:** 7715560268
**Subsector:** DPMDB
**Filled by:** market-map v6.5   **Date:** 2026-07-03   **Hand-time (min):** n/a (reference)

---

## Entity match  (get this right FIRST — a wrong entity poisons every field)
- brand_name: Diasoft
- legal_entity_name: ДИАСОФТ, ООО
- inn: 7715560268
- website: diasoft.ru
- entity_type: legal_entity
- confidence_entity_match: high
- disambiguation_note: Primarily a banking-software vendor; its database offering is the Digital Q line. Segment-classification risk: don't file the whole company under DP-MDB.

---

## Business fields

### description
- value: Russian IT-solutions vendor (core banking software) with an infrastructure/data platform line.
- source_url: https://q.diasoft.ru/products/infrastrukturnye-platformy
- source_tier: 2
- confidence: high
- snippet: (from company site / market map)

### segment
- value: PaaS / Data Platform — Managed Databases (MDB) [adjacent to core fintech-software business]
- source_url: https://.../Num of Prod  (internal market map)
- source_tier: 2
- confidence: high
- snippet: 2 products in DP-MDB.

### revenue   (RU: prefer the filed bo.nalog.ru / ГИР БО figure → high)
- value: 7.30 млрд ₽
- source_url: https://bo.nalog.ru/  (ГИР БО, ИНН 7715560268)
- source_tier: 1
- year: 2024
- confidence: high
- snippet: (filed accounting statements)
- assumptions: Filed 2024 = whole Diasoft (mostly banking software). 2025 ≈ 8.35 млрд ₽.

### headcount
- value:
- source_url:
- source_tier:
- confidence: low
- snippet:
- assumptions: not in the B2B market-map source — fill from ЕГРЮЛ/СБИС/HH if needed.

### key_products
- value: Digital Q.DataBase, Digital Q.ClientCatalog.
- source_url: https://q.diasoft.ru/products/infrastrukturnye-platformy
- source_tier: 2
- confidence: high
- snippet: 2 products in DP-MDB.

### latest_news
- value: IPO on MOEX (ticker DIAS), February 2024.
- source_url: MOEX / Diasoft IR
- source_tier: 2
- year: 2024
- confidence: high
- snippet:

---

## Registry provenance
- legal_entity_name / inn source: https://egrul.nalog.ru/  (ЕГРЮЛ, ИНН 7715560268)  (tier 1)

## Known A/B tension  (scores the verifier's conflict detection)
- field: segment  |  A (registry) says: DP-MDB vendor  |  B (press) says: core-banking software vendor  |  correct: Core business = banking software; DB is an adjacent line
