# GOLD — Timeweb Cloud   [GPU]

> Perfect-example ground truth, built from the B2B market map (segment tab + `$$$`
> + `Num of Prod`). This is the accuracy yardstick the pipeline is scored against.
> One field-block = one claim row, so it lines up 1:1 with a verifier output.

**Seed name:** Timeweb Cloud
**Website:** timeweb.cloud
**INN:** 7810945525
**Subsector:** GPU
**Filled by:** market-map v6.5   **Date:** 2026-07-03   **Hand-time (min):** n/a (reference)

---

## Entity match  (get this right FIRST — a wrong entity poisons every field)
- brand_name: Timeweb Cloud
- legal_entity_name: ТАЙМВЭБ.КЛАУД, ООО
- inn: 7810945525
- website: timeweb.cloud
- entity_type: legal_entity
- confidence_entity_match: high
- disambiguation_note: Cloud arm of Timeweb; legal entity ООО «Таймвэб.Клауд».

---

## Business fields

### description
- value: Russian cloud-infrastructure provider; large self-service GPU catalogue.
- source_url: https://timeweb.cloud/services/gpu
- source_tier: 2
- confidence: high
- snippet: (from company site / market map)

### segment
- value: IaaS / GPU (cloud GPU + bare metal)
- source_url: https://.../Num of Prod  (internal market map)
- source_tier: 2
- confidence: high
- snippet: 4 GPU products in map. Cross-list: IaaS ALL 17 (Compute 6, Storage 2, GPU 4, BM 1, Network 3), PaaS 10, Sec 3.

### revenue   (RU: prefer the filed bo.nalog.ru / ГИР БО figure → high)
- value: 1.48 млрд ₽
- source_url: https://bo.nalog.ru/  (ГИР БО, ИНН 7810945525)
- source_tier: 1
- year: 2024
- confidence: high
- snippet: (filed accounting statements)
- assumptions: Filed 2024 for ООО «Таймвэб.Клауд». 2025 ≈ 2.47 млрд ₽ (map). Cloud entity only, not the whole Timeweb group.

### headcount
- value:
- source_url:
- source_tier:
- confidence: low
- snippet:
- assumptions: not in the B2B market-map source — fill from ЕГРЮЛ/СБИС/HH if needed.

### key_products
- value: 17 GPU types (incl. GTX series), Cloud + Bare Metal.
- source_url: https://timeweb.cloud/services/gpu
- source_tier: 2
- confidence: high
- snippet: 4 GPU products in map. Cross-list: IaaS ALL 17 (Compute 6, Storage 2, GPU 4, BM 1, Network 3), PaaS 10, Sec 3.

### latest_news
- value: —
- source_url: —
- source_tier: 2
- year: 2024
- confidence: low
- snippet:

---

## Registry provenance
- legal_entity_name / inn source: https://egrul.nalog.ru/  (ЕГРЮЛ, ИНН 7810945525)  (tier 1)

## Known A/B tension  (scores the verifier's conflict detection)
- (no material A/B tension expected — official and market views should agree)
