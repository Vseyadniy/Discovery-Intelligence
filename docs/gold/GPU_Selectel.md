# GOLD — Selectel   [GPU]

> Perfect-example ground truth, built from the B2B market map (segment tab + `$$$`
> + `Num of Prod`). This is the accuracy yardstick the pipeline is scored against.
> One field-block = one claim row, so it lines up 1:1 with a verifier output.

**Seed name:** Selectel
**Website:** selectel.ru
**INN:** 7810962785
**Subsector:** GPU
**Filled by:** market-map v6.5   **Date:** 2026-07-03   **Hand-time (min):** n/a (reference)

---

## Entity match  (get this right FIRST — a wrong entity poisons every field)
- brand_name: Selectel
- legal_entity_name: СЕЛЕКТЕЛ, АО
- inn: 7810962785
- website: selectel.ru
- entity_type: legal_entity
- confidence_entity_match: high
- disambiguation_note: Single clear legal entity АО «Селектел», founded 2008.

---

## Business fields

### description
- value: IT-infrastructure provider founded 2008 (cloud since 2010). Own data centers; broad IaaS + bare-metal GPU offering.
- source_url: https://selectel.ru/services/gpu/
- source_tier: 2
- confidence: high
- snippet: (from company site / market map)

### segment
- value: IaaS / GPU (cloud GPU + bare metal)
- source_url: https://.../Num of Prod  (internal market map)
- source_tier: 2
- confidence: high
- snippet: 4 GPU products in map (widest GPU line-up among mid-caps).

### revenue   (RU: prefer the filed bo.nalog.ru / ГИР БО figure → high)
- value: 13.05 млрд ₽
- source_url: https://bo.nalog.ru/  (ГИР БО, ИНН 7810962785)
- source_tier: 1
- year: 2024
- confidence: high
- snippet: (filed accounting statements)
- assumptions: Filed 2024. 2025 ≈ 16.05 млрд ₽ (map).

### headcount
- value:
- source_url:
- source_tier:
- confidence: low
- snippet:
- assumptions: not in the B2B market-map source — fill from ЕГРЮЛ/СБИС/HH if needed.

### key_products
- value: 16 GPU types across Cloud VMs and Bare Metal servers.
- source_url: https://selectel.ru/services/gpu/
- source_tier: 2
- confidence: high
- snippet: 4 GPU products in map (widest GPU line-up among mid-caps).

### latest_news
- value: Active MOEX bond issuer; IPO widely discussed 2024–2025.
- source_url: press / MOEX bond disclosures — confirm before use
- source_tier: 2
- year: 2024
- confidence: medium
- snippet:

---

## Registry provenance
- legal_entity_name / inn source: https://egrul.nalog.ru/  (ЕГРЮЛ, ИНН 7810962785)  (tier 1)

## Known A/B tension  (scores the verifier's conflict detection)
- (no material A/B tension expected — official and market views should agree)
