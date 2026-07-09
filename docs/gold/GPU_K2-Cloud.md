# GOLD — K2 Cloud   [GPU]

> Perfect-example ground truth, built from the B2B market map (segment tab + `$$$`
> + `Num of Prod`). This is the accuracy yardstick the pipeline is scored against.
> One field-block = one claim row, so it lines up 1:1 with a verifier output.

**Seed name:** K2 Cloud
**Website:** k2.cloud
**INN:** 7701829110
**Subsector:** GPU
**Filled by:** market-map v6.5   **Date:** 2026-07-03   **Hand-time (min):** n/a (reference)

---

## Entity match  (get this right FIRST — a wrong entity poisons every field)
- brand_name: K2 Cloud
- legal_entity_name: АО «К2 ИНТЕГРАЦИЯ»
- inn: 7701829110
- website: k2.cloud
- entity_type: brand
- confidence_entity_match: high
- disambiguation_note: Brand 'K2 Cloud' (cloud arm of K2Тех), legal entity is АО «К2 Интеграция» — brand ≠ legal name. Formerly «Крок Облачные сервисы».

---

## Business fields

### description
- value: Cloud division of K2Тех (ex-«Крок Облачные сервисы»). Enterprise IaaS/PaaS.
- source_url: https://k2.cloud/products/gpu/
- source_tier: 2
- confidence: high
- snippet: (from company site / market map)

### segment
- value: IaaS / GPU (cloud GPU)
- source_url: https://.../Num of Prod  (internal market map)
- source_tier: 2
- confidence: high
- snippet: 2 GPU products in map. Cross-list: IaaS ALL 20 (Compute 2, Storage 9, GPU 2, BM 2, Network 5), PaaS 6.

### revenue   (RU: prefer the filed bo.nalog.ru / ГИР БО figure → high)
- value: 14.16 млрд ₽
- source_url: https://bo.nalog.ru/  (ГИР БО, ИНН 7701829110)
- source_tier: 1
- year: 2024
- confidence: high
- snippet: (filed accounting statements)
- assumptions: Filed 2024. 2025 ≈ 18.87 млрд ₽ (map).

### headcount
- value:
- source_url:
- source_tier:
- confidence: low
- snippet:
- assumptions: not in the B2B market-map source — fill from ЕГРЮЛ/СБИС/HH if needed.

### key_products
- value: 4 GPU types (NVIDIA), Cloud GPU.
- source_url: https://k2.cloud/products/gpu/
- source_tier: 2
- confidence: high
- snippet: 2 GPU products in map. Cross-list: IaaS ALL 20 (Compute 2, Storage 9, GPU 2, BM 2, Network 5), PaaS 6.

### latest_news
- value: —
- source_url: —
- source_tier: 2
- year: 2024
- confidence: low
- snippet:

---

## Registry provenance
- legal_entity_name / inn source: https://egrul.nalog.ru/  (ЕГРЮЛ, ИНН 7701829110)  (tier 1)

## Known A/B tension  (scores the verifier's conflict detection)
- field: legal_entity_name  |  A (registry) says: АО «К2 Интеграция»  |  B (press) says: 'K2 Cloud'  |  correct: АО «К2 Интеграция» (brand K2 Cloud)
