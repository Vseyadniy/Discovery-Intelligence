# GOLD — Вебмониторэкс   [AppSec]

> Perfect-example ground truth, built from the B2B market map (segment tab + `$$$`
> + `Num of Prod`). This is the accuracy yardstick the pipeline is scored against.
> One field-block = one claim row, so it lines up 1:1 with a verifier output.

**Seed name:** Вебмониторэкс
**Website:** webmonitorx.ru
**INN:** 7735194651
**Subsector:** AppSec
**Filled by:** market-map v6.5   **Date:** 2026-07-03   **Hand-time (min):** n/a (reference)

---

## Entity match  (get this right FIRST — a wrong entity poisons every field)
- brand_name: Вебмониторэкс
- legal_entity_name: ВЕБМОНИТОРЭКС, ООО
- inn: 7735194651
- website: webmonitorx.ru
- entity_type: legal_entity
- confidence_entity_match: high
- disambiguation_note: Pure-play WAF/API-security vendor; single clear legal entity ООО «Вебмониторэкс».

---

## Business fields

### description
- value: Russian vendor of web-application and API protection products.
- source_url: https://webmonitorx.ru
- source_tier: 2
- confidence: high
- snippet: (from company site / market map)

### segment
- value: Security / Application Security (WAF + API Security)
- source_url: https://.../Num of Prod  (internal market map)
- source_tier: 2
- confidence: high
- snippet: 3 AppSec products. Cross-list: Security ALL 4 (Apps 3, Infra 1).

### revenue   (RU: prefer the filed bo.nalog.ru / ГИР БО figure → high)
- value: 571.75 млн ₽
- source_url: https://bo.nalog.ru/  (ГИР БО, ИНН 7735194651)
- source_tier: 1
- year: 2024
- confidence: high
- snippet: (filed accounting statements)
- assumptions: Filed 2024. 2025 ≈ 1.81 млрд ₽ (map) — ~3× jump; verify (round or a genuine scale-up).

### headcount
- value:
- source_url:
- source_tier:
- confidence: low
- snippet:
- assumptions: not in the B2B market-map source — fill from ЕГРЮЛ/СБИС/HH if needed.

### key_products
- value: ПроWAF (WAF), ПроAPI Security (API security).
- source_url: https://webmonitorx.ru
- source_tier: 2
- confidence: high
- snippet: 3 AppSec products. Cross-list: Security ALL 4 (Apps 3, Infra 1).

### latest_news
- value: Revenue roughly tripled 2024→2025 (571.75 млн → 1.81 млрд ₽ in source).
- source_url: market-map $$$ list — confirm the driver
- source_tier: 2
- year: 2024
- confidence: medium
- snippet:

---

## Registry provenance
- legal_entity_name / inn source: https://egrul.nalog.ru/  (ЕГРЮЛ, ИНН 7735194651)  (tier 1)

## Known A/B tension  (scores the verifier's conflict detection)
- (no material A/B tension expected — official and market views should agree)
