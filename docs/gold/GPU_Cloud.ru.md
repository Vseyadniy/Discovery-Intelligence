# GOLD — Cloud.ru   [GPU]

> Perfect-example ground truth, built from the B2B market map (segment tab + `$$$`
> + `Num of Prod`). This is the accuracy yardstick the pipeline is scored against.
> One field-block = one claim row, so it lines up 1:1 with a verifier output.

**Seed name:** Cloud.ru
**Website:** cloud.ru
**INN:** 7736279160
**Subsector:** GPU
**Filled by:** market-map v6.5   **Date:** 2026-07-03   **Hand-time (min):** n/a (reference)

---

## Entity match  (get this right FIRST — a wrong entity poisons every field)
- brand_name: Cloud.ru
- legal_entity_name: ОБЛАЧНЫЕ ТЕХНОЛОГИИ, ООО
- inn: 7736279160
- website: cloud.ru
- entity_type: group
- confidence_entity_match: high
- disambiguation_note: Ex-SberCloud (Sber + Huawei Cloud), spun out into a standalone company. Brand 'Cloud.ru' operates over legal entity ООО «Облачные технологии». Press still sometimes calls it 'SberCloud' — do not merge with Sber's other entities.

---

## Business fields

### description
- value: Russian hyperscaler spun out of SberCloud; 80+ IaaS/PaaS services. >50% of clients are internal Sber-ecosystem and B2G; Tier III DCs, 152-ФЗ, КИИ certs.
- source_url: https://cloud.ru/products/vychislitelnyye-moschnosti-s-gpu
- source_tier: 2
- confidence: high
- snippet: (from company site / market map)

### segment
- value: IaaS / GPU (cloud GPU + bare metal)
- source_url: https://.../Num of Prod  (internal market map)
- source_tier: 2
- confidence: high
- snippet: 3 GPU products in map. Cross-list (Num of Prod): IaaS ALL 52 (Compute 6, Storage 9, GPU 3, BM 2, Network 23), PaaS ALL 25, Security 11.

### revenue   (RU: prefer the filed bo.nalog.ru / ГИР БО figure → high)
- value: 50.74 млрд ₽
- source_url: https://bo.nalog.ru/  (ГИР БО, ИНН 7736279160)
- source_tier: 1
- year: 2024
- confidence: high
- snippet: (filed accounting statements)
- assumptions: Filed 2024. 2025 ≈ 76.21 млрд ₽ (map). Whole legal entity, not GPU-line only.

### headcount
- value:
- source_url:
- source_tier:
- confidence: low
- snippet:
- assumptions: not in the B2B market-map source — fill from ЕГРЮЛ/СБИС/HH if needed.

### key_products
- value: 6 GPU types (H100, A100, V100, A40, A16, T4); Evolution ECS GPU cloud product + Bare Metal (HGX 8×A100). Configs of 1/2/4/8/16 GPU with NVLink/Infiniband.
- source_url: https://cloud.ru/products/vychislitelnyye-moschnosti-s-gpu
- source_tier: 2
- confidence: high
- snippet: 3 GPU products in map. Cross-list (Num of Prod): IaaS ALL 52 (Compute 6, Storage 9, GPU 3, BM 2, Network 23), PaaS ALL 25, Security 11.

### latest_news
- value: —
- source_url: —
- source_tier: 2
- year: 2024
- confidence: low
- snippet:

---

## Registry provenance
- legal_entity_name / inn source: https://egrul.nalog.ru/  (ЕГРЮЛ, ИНН 7736279160)  (tier 1)

## Known A/B tension  (scores the verifier's conflict detection)
- field: legal_entity_name  |  A (registry) says: ООО «Облачные технологии»  |  B (press) says: 'SberCloud'/'Cloud.ru'  |  correct: ООО «Облачные технологии» (brand Cloud.ru)
