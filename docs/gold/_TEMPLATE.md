# GOLD — {{SEED}}

> Hand-filled ground truth. This is the accuracy yardstick the pipeline is scored
> against (coverage, source quality, conflict detection, entity accuracy).
> Fill every field you can defend to a client. Leave `value:` blank if genuinely
> unknown — a blank gold field is honest; a guessed one corrupts the yardstick.
> One field-block below = one claim row, so it lines up 1:1 with a verifier output.

**Seed name:** {{SEED}}
**Website hint:** {{WEBSITE}}
**INN hint:** {{INN}}
**Analyst note / hint:** {{HINT}}
**Filled by:** ______   **Date:** ______   **Hand-time (min):** ______

---

## Entity match  (get this right FIRST — a wrong entity poisons every field)
- brand_name:
- legal_entity_name:
- inn:
- website:
- entity_type:            (brand / legal_entity / product / group)
- confidence_entity_match: (high / medium / low)
- disambiguation_note:    (why this is the right entity; note any same-name traps)

---

## Business fields
> For each: `value` + `source_url` + `source_tier` (1 official / 2 reputable / 3 blog)
> + `confidence` (high / medium / low) + `snippet` (exact evidence text).
> `year` for time-bound fields. `assumptions` REQUIRED when the value is estimated.

### description
- value:
- source_url:
- source_tier:
- confidence:
- snippet:

### segment
- value:
- source_url:
- source_tier:
- confidence:
- snippet:

### revenue   (RU: prefer the filed bo.nalog.ru / ГИР БО figure → high)
- value:
- source_url:
- source_tier:
- year:
- confidence:
- snippet:
- assumptions:

### headcount
- value:
- source_url:
- source_tier:
- confidence:
- snippet:
- assumptions:

### key_products
- value:
- source_url:
- source_tier:
- confidence:
- snippet:

### latest_news
- value:
- source_url:
- source_tier:
- year:
- confidence:
- snippet:

---

## Known A/B tension  (fill after you know the truth — used to score conflict detection)
> List any field where the official/registry view (Collector A) and the
> market/press view (Collector B) will legitimately disagree, so you can check
> the verifier actually flags it.
- field: ______  |  A (registry) says: ______  |  B (press) says: ______  |  correct: ______
