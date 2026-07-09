# Review Card — {{entity}}

**Project:** {{project_id}}  |  **Geo:** {{geo}}  |  **Industry:** {{industry}}
**Generated:** {{generated_at}}  |  **Verifier engine:** {{verifier_engine}}

## Entity match
- **Type:** {{entity_type}}
- **Match confidence:** {{entity_match_confidence}}  {{entity_match_flag}}
- **Note:** {{entity_match_note}}

---

## ⚠️ Needs your attention
{{review_section}}
<!-- Each conflicting or low-confidence field is expanded here with the competing
     A/B values, sources, and snippets so you can decide. -->

---

## ✅ Agreed / high-confidence  (collapsed — expand if you want to audit)
<details>
<summary>{{agreed_count}} fields agreed at medium/high confidence</summary>

{{agreed_section}}

</details>

---

## Actions
Reply in chat with one line per decision. Unlisted fields are **approved as-is**.

- `approve all`                        → write every field as shown
- `edit <field> = <value> [| why]`     → override a value (the *why* becomes a correction rule)
- `reject <field>`                     → drop this field (not written)
- `investigate <field>`                → keep pending, re-run collectors on it later

_Example:_
```
edit revenue = 1.2 млрд ₽ (2024, bo.nalog.ru) | list-org is stale, always prefer the filing
reject headcount
approve all
```
