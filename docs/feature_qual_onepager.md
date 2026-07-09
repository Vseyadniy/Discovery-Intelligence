# Feature design — Company Qualitative Research One-Pager (MVP)

Turns each gate-accepted company record into an interview-ready qualitative
research kit: research context, interview brief, a short respondent survey, a
semi-structured interview guide, and research priorities. The analyst gets a
director-ready one-pager per company without writing briefs by hand.

The feature reuses the pipeline the app already has — **no API key**: the app
builds a prompt from existing data, the analyst pastes it into ChatGPT, the
result is saved as JSON into the run folder, the ingest gate validates it (with
a repair loop), and the app renders the final one-pager.

The core design problem is trust: an interview brief mixes hard data with
guesses. The MVP makes the boundary structural — every statement carries a
`basis` (fact / inference / hypothesis), facts must point at record fields and
are cross-checked by the gate, and "what is known / unclear" is computed
deterministically by the app from the record, not written by the LLM.

---

## 1. Where it sits in the run lifecycle

```
discovery → research batches → repair → built ──→ qual (this feature) → published
                                          │
                                          └─ quant deliverable (research_table.xlsx)
```

Qual is a separate track that starts only from **gate-accepted** records — a
one-pager built on rejected data would inherit fabrications.

## 2. User flow

1. **Load a built run.** The app shows a new section «4 · Qualitative research».
2. **Research goal (one-time input, required).** A 1–3 sentence free-text field:
   what decision this research serves (e.g. «оцениваем выход на рынок проверки
   контрагентов с продуктом X»). Everything downstream — relevance, hypotheses,
   angle — is framed against this goal. Stored in `qual/qual_meta.json`.
3. **Company picker.** A list of accepted records (brand · segment · proposed
   angle). Multi-select, default = all. The angle is auto-proposed by rules
   (§5) and editable per company via a dropdown — the analyst confirms, the
   machine doesn't decide silently.
4. **Generate qual prompt ▶.** Produces the prompt for the next 1–2 selected
   companies (one-pagers are heavy; batch default 2), written to `prompt.md` +
   `steps/` exactly like research prompts.
5. **Paste into ChatGPT** → it saves `logs/<run>/qual/<Brand>_onepager.json`.
6. **Gate validates** on the normal poll; rejected one-pagers produce a repair
   prompt (same loop as research). Accepted ones are rendered to
   `qual/<Brand>_onepager.md`.
7. **Open / publish.** «Open one-pagers» opens `qual/`; Publish copies rendered
   `.md` files to `docs/<market>/qual/`.

CLI mirrors the app: `python -m src.runs qual <run_id> [--batch 2]`,
`qual-gate <run_id>`.

## 3. Required inputs (what the generation prompt injects)

Per company, the app assembles — the LLM does **not** browse and does **not**
receive the repo:

| block | origin | role |
|---|---|---|
| full `_record.json` (values + sources + confidence + review_flags + entity_match) | quant run | the ONLY permitted fact base |
| KNOWN list | computed by the app: fields with real values, their confidence + source domain | seeds «what is known» — LLM may rephrase, not extend |
| UNCLEAR list | computed by the app: blank fields, low-confidence fields, A/B conflicts, review_flags | seeds «what remains unclear» |
| market context | `companies.json` (goal market, segment taxonomy, coverage note) + the company's segment neighbors from the run table (brand, positioning, revenue tier) | lets questions name real competitors instead of «your competitors» |
| research goal | analyst input (§2) | frames relevance, hypotheses, angle |
| angle | rule-proposed, analyst-confirmed | selects question templates and respondent types |
| output_language | run meta | prose language; proper names verbatim |

Pre-computing KNOWN/UNCLEAR in the app is the single most important
anti-hallucination measure: the two sections the analyst trusts most are
derived from gated data deterministically.

## 4. Output format

### 4.1 Machine artifact — `qual/<Brand>_onepager.json` (strict, gated)

```json
{
  "entity": "Alpha",
  "angle": "competitor",
  "research_goal": "…copied from qual_meta…",
  "context": {
    "summary":   {"text": "…", "basis": "fact", "source_fields": ["description", "segment"]},
    "relevance": {"text": "…", "basis": "inference", "rationale": "…"},
    "known":   [ {"text": "выручка 2025 — 250 млн ₽", "source_field": "total_revenue_2025", "confidence": "high"} ],
    "unclear": [ {"text": "EBITDA не публикуется", "source_field": "ebitda_2025", "reason": "blank"} ],
    "hypotheses": [
      {"id": "H1", "text": "…", "basis": "hypothesis",
       "grounds": ["total_revenue_2025", "positioning"],
       "validated_if": "…what an interview answer must show…",
       "status": "untested"}
    ]
  },
  "interview_brief": {
    "respondents": [ {"type": "customer", "who": "комплаенс-офицер банка топ-50", "why": "…", "priority": 1} ],
    "learn": ["…"],
    "sensitive": [ {"topic": "…", "why": "…", "approach": "avoid | ask_carefully | reframe"} ]
  },
  "survey": [
    {"id": "S1", "type": "multiple_choice", "text": "…", "options": ["…"],
     "validates": "H1"},
    {"id": "S2", "type": "ranking",  "text": "…", "options": ["…"], "validates": "buying_criteria"},
    {"id": "S3", "type": "open",     "text": "…", "validates": "differentiation"}
  ],
  "interview_guide": [
    {"theme": "market_perception", "questions": [ {"id": "Q1", "text": "…", "targets": ["H2"]} ]}
  ],
  "priorities": {
    "validate": ["3 items, each referencing an H*"],
    "risks":    ["3 items"],
    "next_step": {"action": "interview | desk_research | expert_call | customer_validation | partner_outreach | skip",
                  "why": "…"}
  }
}
```

Enums: `angle` ∈ competitor / customer / partner / benchmark / market_signal /
acquisition_target; survey `type` ∈ multiple_choice / ranking / open;
`validates` ∈ H* / awareness / perception / needs / differentiation /
buying_criteria; guide `theme` must cover ≥6 of: market_perception,
customer_problems, buying_behavior, competitors, positioning, product_value,
strengths, weaknesses, future_trends.

### 4.2 Human artifact — `qual/<Brand>_onepager.md`

Rendered by the app from the JSON (never hand-written), fixed template,
~1 page. Every line carries a provenance marker:

- **[Ф·high]** fact, with the source domain in parentheses — inherits the
  record's per-field confidence;
- **[В]** inference — states which facts it rests on;
- **[Г·untested]** hypothesis — with its validation condition.

Header: brand · segment · angle badge · recommended next step. Footer:
coverage stats (N fields known / M unclear), run date (data freshness), gate
verdict date. Survey and guide render as tables ready to copy into a form or
interview doc.

## 5. Angle proposal rules (deterministic default, analyst overrides)

| signal from the record | proposed angle |
|---|---|
| same segment as the research goal, comparable revenue tier | competitor |
| same segment, an order of magnitude larger | benchmark |
| adjacent segment, complementary `key_products` | partner |
| `entity_type=product`, small revenue, high YoY | acquisition_target |
| its `target_customers` ≈ our target buyer | customer |
| shrinking / tiny / unclear fit | market_signal |

Stored as `{"angle": "...", "angle_source": "rule" | "analyst"}` in
`qual_meta.json`; the prompt receives the confirmed value only.

## 6. Data model additions

```
logs/<run>/qual/
  qual_meta.json            # research goal, per-company angle + status
  <Brand>_onepager.json     # gated machine artifact
  <Brand>_onepager.md       # rendered deliverable
docs/<market>/qual/         # published one-pagers
```

`qual_meta.json`:
```json
{"research_goal": "…", "created_at": "…",
 "companies": {"Alpha": {"angle": "competitor", "angle_source": "analyst",
                          "status": "pending | accepted | rejected"}}}
```

`run.json` gains `qual: {selected, accepted, rejected}` counters. The company
schema (`config/schema.yaml`) is untouched — the one-pager is a derived
artifact, not new company data.

## 7. Generation logic (the prompt, sketch)

Built by `src/onepager.py::build_qual_prompt(meta, records, goal, angles)`;
one prompt covers 1–2 companies. Key rules stated to the LLM:

1. **Role:** you are a research designer, not a researcher. Do NOT browse, do
   NOT add facts. Work only from the record and market context below.
2. **Provenance is structural.** Three bases. `fact` requires `source_fields`
   that exist in the record, and its text may not contain a number or proper
   name absent from those fields. Anything else you believe is a `hypothesis`
   — and every hypothesis needs `validated_if`: what an answer must show to
   confirm or kill it.
3. **Counts.** 5–8 survey questions with ≥1 multiple-choice, ≥1 ranking, ≥1
   open; 10–15 guide questions across ≥6 required themes; exactly 3 validate
   priorities + 3 risks; ≥2 respondent types.
4. **Question quality.** No leading questions. Never ask what the record
   already knows at high confidence (filed revenue, INN…). Every hypothesis is
   targeted by ≥1 question; every question serves a hypothesis or a standard
   validation goal. Adapt wording to the company's segment and the confirmed
   angle; name real competitors from the market context.
5. **Sensitivity.** Consider: private-company finances, layoffs, litigation,
   sanctions exposure, ownership — mark avoid / ask_carefully / reframe.
6. Language: prose in `output_language`, proper names verbatim. Save to the
   exact path; strict JSON.

## 8. Quality checks (gate extension — `gate.validate_onepager`)

Same severity model and repair loop as the research gate.

| code | severity | check |
|---|---|---|
| `fact-not-in-record` | reject | a `basis:fact` item whose `source_fields` are missing from the record, or whose text contains a number not present in those fields' values |
| `untestable-hypothesis` | reject | hypothesis without `validated_if` |
| `counts` | reject | survey outside 5–8, guide outside 10–15, priorities ≠ 3+3, <6 themes, missing question-type in the mix |
| `bad-enum` | reject | angle / next_step / type / theme outside the enums |
| `options-missing` | reject | multiple_choice or ranking question without options |
| `orphan-question` | reject | a question targeting an H* id that doesn't exist |
| `redundant-question` | warn | asks for a field the record holds at high confidence |
| `orphan-hypothesis` | warn | hypothesis no question targets |
| `sensitive-empty` | warn | no sensitive topics for a private company with financial hypotheses |
| placeholders / language | reject / warn | reused from the research gate |

Rejected one-pagers appear in the same repair prompt flow with per-code hints.

## 9. Confidence labels & fact-vs-hypothesis separation (summary)

Three mechanisms, layered:

1. **Structural** — `basis` + `source_fields` in the JSON; the renderer cannot
   show an unlabeled claim because the template only prints labeled items.
2. **Deterministic seeding** — KNOWN/UNCLEAR lists come from the gated record,
   computed by the app; the LLM elaborates but cannot invent coverage.
3. **Gate cross-check** — numeric/name containment check of every `fact`
   against the referenced record fields (`fact-not-in-record`).

Confidence: facts inherit the record's per-field high/medium/low; hypotheses
carry `status` (untested → supported / refuted / mixed), which the analyst
updates in the JSON after interviews — re-rendering refreshes the one-pager,
so it doubles as a living research log.

## 10. MVP cut

**In:** everything above; Markdown rendering; publish to docs; CLI parity.
**Out (v2):** DOCX/PDF export; interview-transcript ingestion with automatic
hypothesis-status updates; respondent sourcing (contacts); cross-company
synthesis («market interview plan» aggregating all one-pagers); auto-export of
the survey to Google/Yandex Forms.

## 11. Implementation plan

1. `src/onepager.py` — prompt builder, KNOWN/UNCLEAR derivation, angle rules,
   MD renderer (~250 lines).
2. `gate.validate_onepager` + repair hints (~150).
3. `runs.py` — qual phase (prompt issuing, status, publish) + CLI verbs (~80).
4. `app.py` — section 4 UI: goal field, company/angle picker, generate button,
   progress (~90).
5. Lifecycle selftest mirroring the research selftests.

No new dependencies; tkinter + stdlib + openpyxl already cover it.
