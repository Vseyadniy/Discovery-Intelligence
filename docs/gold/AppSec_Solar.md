# GOLD — Solar   [AppSec]

> Perfect-example ground truth, built from the B2B market map (segment tab + `$$$`
> + `Num of Prod`). This is the accuracy yardstick the pipeline is scored against.
> One field-block = one claim row, so it lines up 1:1 with a verifier output.

**Seed name:** Solar
**Website:** rt-solar.ru
**INN:** 9724113301
**Subsector:** AppSec
**Filled by:** market-map v6.5   **Date:** 2026-07-03   **Hand-time (min):** n/a (reference)

---

## Entity match  (get this right FIRST — a wrong entity poisons every field)
- brand_name: Solar
- legal_entity_name: ООО «СОЛАР»
- inn: 9724113301
- website: rt-solar.ru
- entity_type: legal_entity
- confidence_entity_match: high
- disambiguation_note: Rostelecom's cybersecurity arm («Солар», ex-Ростелеком-Солар / ex-Solar Security). Attribute to ООО «Солар», not to Ростелеком ПАО.

---

## Business fields

### description
- value: Rostelecom's security group; MSSP + product portfolio incl. application-security testing.
- source_url: https://rt-solar.ru/products/
- source_tier: 2
- confidence: high
- snippet: (from company site / market map)

### segment
- value: Security / Application Security (SAST/DAST/SCA/ASOC)
- source_url: https://.../Num of Prod  (internal market map)
- source_tier: 2
- confidence: high
- snippet: 5 AppSec products. Cross-list: Security ALL 23 (Apps 5, Data 2, User 2, Network 7, Infra 7).

### revenue   (RU: prefer the filed bo.nalog.ru / ГИР БО figure → high)
- value: (blank — not in source; pull from bo.nalog.ru)
- source_url: https://bo.nalog.ru/  (ГИР БО, ИНН 9724113301)
- source_tier: 1
- year: 2024
- confidence: low
- snippet: (filed accounting statements)
- assumptions: NOT in the market-map source ($$$ list blank for Solar). Leave blank — pull the ООО «Солар» filing from bo.nalog.ru before quoting a number. Honest-blank example.

### headcount
- value:
- source_url:
- source_tier:
- confidence: low
- snippet:
- assumptions: not in the B2B market-map source — fill from ЕГРЮЛ/СБИС/HH if needed.

### key_products
- value: Solar appScreener (SAST/DAST/SCA/ASOC), Solar webProxy.
- source_url: https://rt-solar.ru/products/
- source_tier: 2
- confidence: high
- snippet: 5 AppSec products. Cross-list: Security ALL 23 (Apps 5, Data 2, User 2, Network 7, Infra 7).

### latest_news
- value: —
- source_url: —
- source_tier: 2
- year: 2024
- confidence: low
- snippet:

---

## Registry provenance
- legal_entity_name / inn source: https://egrul.nalog.ru/  (ЕГРЮЛ, ИНН 9724113301)  (tier 1)

## Known A/B tension  (scores the verifier's conflict detection)
- field: revenue  |  A (registry) says: (blank — no filing in source)  |  B (press) says: (press may quote Rostelecom-Solar group)  |  correct: Get ООО «Солар» filing; don't borrow the Rostelecom group figure
