# GOLD — Arenadata   [DPMDB]

> Perfect-example ground truth, built from the B2B market map (segment tab + `$$$`
> + `Num of Prod`). This is the accuracy yardstick the pipeline is scored against.
> One field-block = one claim row, so it lines up 1:1 with a verifier output.

**Seed name:** Arenadata
**Website:** arenadata.tech
**INN:** 7713468845
**Subsector:** DPMDB
**Filled by:** market-map v6.5   **Date:** 2026-07-03   **Hand-time (min):** n/a (reference)

---

## Entity match  (get this right FIRST — a wrong entity poisons every field)
- brand_name: Arenadata
- legal_entity_name: АРЕНАДАТА СОФТВЕР, ООО
- inn: 7713468845
- website: arenadata.tech
- entity_type: group
- confidence_entity_match: high
- disambiguation_note: Public data-platform vendor. Holding «Группа Аренадата» (ПАО, MOEX ticker DATA) over operating entity ООО «Аренадата Софтвер». Revenue below is the group; the DP-MDB product line is only part of it.

---

## Business fields

### description
- value: Russian data-platform vendor (from the 2016 Hadoop-based distribution). Full on-prem data stack across MDB, streaming, DWH and tooling.
- source_url: https://arenadata.tech/products/arenadata-db/
- source_tier: 2
- confidence: high
- snippet: (from company site / market map)

### segment
- value: PaaS / Data Platform — Managed Databases (MDB), on-prem vendor
- source_url: https://.../Num of Prod  (internal market map)
- source_tier: 2
- confidence: high
- snippet: 6 products in DP-MDB. Cross-list (Num of Prod) — the multi-segment footprint: PaaS ALL 12 = Kubernetes 1 + DP-MDB 6 + DP-tools 5, plus IaaS Compute 1.

### revenue   (RU: prefer the filed bo.nalog.ru / ГИР БО figure → high)
- value: 5.18 млрд ₽
- source_url: https://bo.nalog.ru/  (ГИР БО, ИНН 7713468845)
- source_tier: 1
- year: 2024
- confidence: high
- snippet: (filed accounting statements)
- assumptions: Group filed revenue 2024; 2025 ≈ 8.8 млрд ₽. DP-MDB product-line revenue alone ≈ 2.1 млрд ₽ (2024, map) — do NOT equate the two.

### headcount
- value:
- source_url:
- source_tier:
- confidence: low
- snippet:
- assumptions: not in the B2B market-map source — fill from ЕГРЮЛ/СБИС/HH if needed.

### key_products
- value: Arenadata DB (ADB/Greenplum), Arenadata Postgres (ADPG), Arenadata One (AD.ONE), Arenadata Prosperity (ADP), Arenadata Streaming (ADS), Arenadata QuickMarts (ADQM).
- source_url: https://arenadata.tech/products/arenadata-db/
- source_tier: 2
- confidence: high
- snippet: 6 products in DP-MDB. Cross-list (Num of Prod) — the multi-segment footprint: PaaS ALL 12 = Kubernetes 1 + DP-MDB 6 + DP-tools 5, plus IaaS Compute 1.

### latest_news
- value: IPO on MOEX (ticker DATA), October 2024.
- source_url: MOEX / Arenadata IR
- source_tier: 2
- year: 2024
- confidence: high
- snippet:

---

## Registry provenance
- legal_entity_name / inn source: https://egrul.nalog.ru/  (ЕГРЮЛ, ИНН 7713468845)  (tier 1)

## Known A/B tension  (scores the verifier's conflict detection)
- field: revenue  |  A (registry) says: 5.18 млрд ₽ (group, ГИР БО)  |  B (press) says: ~2.1 млрд ₽ (DP-MDB product line, model)  |  correct: 5.18 млрд ₽ for the company; keep the product-line figure separate
