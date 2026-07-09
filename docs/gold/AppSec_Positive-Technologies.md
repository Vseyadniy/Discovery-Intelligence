# GOLD — Positive Technologies   [AppSec]

> Perfect-example ground truth, built from the B2B market map (segment tab + `$$$`
> + `Num of Prod`). This is the accuracy yardstick the pipeline is scored against.
> One field-block = one claim row, so it lines up 1:1 with a verifier output.

**Seed name:** Positive Technologies
**Website:** ptsecurity.com
**INN:** 7718668887
**Subsector:** AppSec
**Filled by:** market-map v6.5   **Date:** 2026-07-03   **Hand-time (min):** n/a (reference)

---

## Entity match  (get this right FIRST — a wrong entity poisons every field)
- brand_name: Positive Technologies
- legal_entity_name: ПОЗИТИВ ТЕКНОЛОДЖИЗ, АО
- inn: 7718668887
- website: ptsecurity.com
- entity_type: group
- confidence_entity_match: high
- disambiguation_note: Public group ПАО «Группа Позитив» (MOEX POSI) over operating АО «Позитив Текнолоджиз». AppSec is one of many segments; flagship products are VM/SIEM (MaxPatrol), not AppSec.

---

## Business fields

### description
- value: Leading Russian cybersecurity product vendor; broad portfolio across VM, SIEM, NGFW, sandbox and application security.
- source_url: https://ptsecurity.com/products/
- source_tier: 2
- confidence: high
- snippet: (from company site / market map)

### segment
- value: Security / Application Security (WAF, SAST/DAST)
- source_url: https://.../Num of Prod  (internal market map)
- source_tier: 2
- confidence: high
- snippet: 7 AppSec products. Cross-list (Num of Prod): Security ALL 33 (Apps 7, Data 1, Network 5, Cloud Platform 1, Infra 18, Endpoint 1), PaaS 1.

### revenue   (RU: prefer the filed bo.nalog.ru / ГИР БО figure → high)
- value: 24.47 млрд ₽
- source_url: https://bo.nalog.ru/  (ГИР БО, ИНН 7718668887)
- source_tier: 1
- year: 2024
- confidence: high
- snippet: (filed accounting statements)
- assumptions: Filed 2024 = WHOLE company. AppSec-segment product revenue ≈ 17.27 млрд ₽ (2024) is a MODEL estimate, not filed — flag as low/estimate. No 2025 figure in source.

### headcount
- value:
- source_url:
- source_tier:
- confidence: low
- snippet:
- assumptions: not in the B2B market-map source — fill from ЕГРЮЛ/СБИС/HH if needed.

### key_products
- value: PT Application Firewall (PT AF), PT Cloud Application Firewall, PT BlackBox (DAST), PT Application Inspector (SAST).
- source_url: https://ptsecurity.com/products/
- source_tier: 2
- confidence: high
- snippet: 7 AppSec products. Cross-list (Num of Prod): Security ALL 33 (Apps 7, Data 1, Network 5, Cloud Platform 1, Infra 18, Endpoint 1), PaaS 1.

### latest_news
- value: Listed on MOEX (ticker POSI) since 2021.
- source_url: MOEX / Positive IR
- source_tier: 2
- year: 2024
- confidence: high
- snippet:

---

## Registry provenance
- legal_entity_name / inn source: https://egrul.nalog.ru/  (ЕГРЮЛ, ИНН 7718668887)  (tier 1)

## Known A/B tension  (scores the verifier's conflict detection)
- field: revenue  |  A (registry) says: 24.47 млрд ₽ (whole company, ГИР БО)  |  B (press) says: ~17.27 млрд ₽ (AppSec line, model estimate)  |  correct: Company 24.47 млрд ₽ filed; AppSec-line figure is a model estimate, mark low
