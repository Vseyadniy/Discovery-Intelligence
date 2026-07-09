# GOLD — СберТех   [DPMDB]

> Perfect-example ground truth, built from the B2B market map (segment tab + `$$$`
> + `Num of Prod`). This is the accuracy yardstick the pipeline is scored against.
> One field-block = one claim row, so it lines up 1:1 with a verifier output.

**Seed name:** СберТех
**Website:** sbertech.ru
**INN:** 7736632467
**Subsector:** DPMDB
**Filled by:** market-map v6.5   **Date:** 2026-07-03   **Hand-time (min):** n/a (reference)

---

## Entity match  (get this right FIRST — a wrong entity poisons every field)
- brand_name: СберТех
- legal_entity_name: СБЕРТЕХ, АО
- inn: 7736632467
- website: sbertech.ru
- entity_type: legal_entity
- confidence_entity_match: high
- disambiguation_note: AO «СберТех», Sber's technology subsidiary. Databases are ONE product line of a very large company — the total-revenue trap below is the key risk here.

---

## Business fields

### description
- value: Sber's in-house technology company; ships the Platform V stack including its DBMS/data-grid.
- source_url: https://pangolin.sbertech.ru/
- source_tier: 2
- confidence: high
- snippet: (from company site / market map)

### segment
- value: PaaS / Data Platform — Managed Databases (MDB), on-prem (part of a platform)
- source_url: https://.../Num of Prod  (internal market map)
- source_tier: 2
- confidence: high
- snippet: 2 products in DP-MDB.

### revenue   (RU: prefer the filed bo.nalog.ru / ГИР БО figure → high)
- value: 20.56 млрд ₽
- source_url: https://bo.nalog.ru/  (ГИР БО, ИНН 7736632467)
- source_tier: 1
- year: 2024
- confidence: high
- snippet: (filed accounting statements)
- assumptions: Filed 2024 = WHOLE СберТех; 2025 ≈ 28.27 млрд ₽. The Pangolin/DataGrid product line ≈ 905 млн ₽ (2024, map). Never publish the company total as the DB-product revenue.

### headcount
- value:
- source_url:
- source_tier:
- confidence: low
- snippet:
- assumptions: not in the B2B market-map source — fill from ЕГРЮЛ/СБИС/HH if needed.

### key_products
- value: Platform V Pangolin DB, Platform V DataGrid.
- source_url: https://pangolin.sbertech.ru/
- source_tier: 2
- confidence: high
- snippet: 2 products in DP-MDB.

### latest_news
- value: —
- source_url: —
- source_tier: 2
- year: 2024
- confidence: low
- snippet:

---

## Registry provenance
- legal_entity_name / inn source: https://egrul.nalog.ru/  (ЕГРЮЛ, ИНН 7736632467)  (tier 1)

## Known A/B tension  (scores the verifier's conflict detection)
- field: revenue  |  A (registry) says: 20.56 млрд ₽ (whole СберТех, ГИР БО)  |  B (press) says: ~0.9 млрд ₽ (Pangolin DB line, model)  |  correct: Company 20.56 млрд ₽; DB product line ~0.9 млрд ₽ — keep separate
