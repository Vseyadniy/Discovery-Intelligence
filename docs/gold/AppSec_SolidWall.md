# GOLD — SolidWall   [AppSec]

> Perfect-example ground truth, built from the B2B market map (segment tab + `$$$`
> + `Num of Prod`). This is the accuracy yardstick the pipeline is scored against.
> One field-block = one claim row, so it lines up 1:1 with a verifier output.

**Seed name:** SolidWall
**Website:** solidwall.ru
**INN:** 7714944046
**Subsector:** AppSec
**Filled by:** market-map v6.5   **Date:** 2026-07-03   **Hand-time (min):** n/a (reference)

---

## Entity match  (get this right FIRST — a wrong entity poisons every field)
- brand_name: SolidWall
- legal_entity_name: СОЛИДСОФТ, ООО
- inn: 7714944046
- website: solidwall.ru
- entity_type: brand
- confidence_entity_match: medium
- disambiguation_note: Brand 'SolidWall'; legal entity ООО «Солидсофт». Map lists parent = Яндекс — treat as a Yandex-affiliated entity and confirm the current ownership before publishing.

---

## Business fields

### description
- value: Russian vendor of an intelligent web-application firewall and DAST tooling.
- source_url: https://www.solidwall.ru
- source_tier: 2
- confidence: high
- snippet: (from company site / market map)

### segment
- value: Security / Application Security (WAF + DAST)
- source_url: https://.../Num of Prod  (internal market map)
- source_tier: 2
- confidence: high
- snippet: 3 AppSec products. Cross-list: Security ALL 3 (Apps 3).

### revenue   (RU: prefer the filed bo.nalog.ru / ГИР БО figure → high)
- value: 902.6 млн ₽
- source_url: https://bo.nalog.ru/  (ГИР БО, ИНН 7714944046)
- source_tier: 1
- year: 2024
- confidence: high
- snippet: (filed accounting statements)
- assumptions: Filed 2024 for ООО «Солидсофт». 2025 ≈ 860.8 млн ₽ (map).

### headcount
- value:
- source_url:
- source_tier:
- confidence: low
- snippet:
- assumptions: not in the B2B market-map source — fill from ЕГРЮЛ/СБИС/HH if needed.

### key_products
- value: SolidWall WAF, SolidPoint DAST.
- source_url: https://www.solidwall.ru
- source_tier: 2
- confidence: high
- snippet: 3 AppSec products. Cross-list: Security ALL 3 (Apps 3).

### latest_news
- value: Listed with parent = Яндекс in the market map (confirm ownership/date).
- source_url: market-map Apps list — confirm
- source_tier: 2
- year: 2024
- confidence: low
- snippet:

---

## Registry provenance
- legal_entity_name / inn source: https://egrul.nalog.ru/  (ЕГРЮЛ, ИНН 7714944046)  (tier 1)

## Known A/B tension  (scores the verifier's conflict detection)
- field: legal_entity_name  |  A (registry) says: ООО «Солидсофт»  |  B (press) says: 'SolidWall' / Яндекс  |  correct: ООО «Солидсофт» (brand SolidWall; parent per map: Яндекс — verify)
