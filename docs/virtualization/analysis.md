# Run analysis — Virtualization  (superficial)

Model: ChatGPT (GPT-5.5)  |  Language: Russian
Run: `2026-07-07_1702_virtualization_superficial`  |  Companies: **8**  |  **Real** coverage: **87%**
(“Real” = placeholders like «не подтверждено» and invalid INNs are counted as empty.)

## Registry / financial coverage (Collector A's core job — real values only)
| field | filled |
|---|---|
| legal_entity_name | 8/8 |
| inn | 8/8 |
| total_revenue_2025 | 8/8 |
| product_revenue_2025 | 8/8 |
| headcount | 8/8 |

## Agent activity (did A, B and the Verifier each do their job?)
`A#`/`B#` = non-empty fields each collector returned; `B indep` = did B use a source A
didn't; `conflicts` = fields the verifier marked as an A/B disagreement.

| company | A# | B# | B indep | conflicts |
|---|---|---|---|---|
| Группа Астра | 33 | 31 | ✓ | 1 |
| ITKey | 26 | 26 | ✓ | 0 |
| НТЦ ИТ РОСА | 26 | 26 | ✓ | 1 |
| Orion soft | 26 | 26 | ✓ | 2 |
| РЕД СОФТ | 30 | 30 | ✓ | 2 |
| Space | 28 | 27 | ✓ | 9 |
| vStack | 28 | 26 | ✓ | 0 |
| Базис | 34 | 29 | ✓ | 8 |

## Sources actually consulted (domain histogram)
| domain | # field-sources | share |
|---|---|---|
| cnews.ru | 63 | 24% |
| tadviser.ru | 27 | 10% |
| companies.rbc.ru | 23 | 9% |
| checko.ru | 23 | 9% |
| tbank.ru | 18 | 7% |
| astra.ru | 17 | 7% |
| b2b.house | 12 | 5% |
| spacevm.ru | 9 | 3% |
| mont.ru | 6 | 2% |
| interfax.ru | 5 | 2% |
| softline.ru | 5 | 2% |
| orionsoft.ru | 5 | 2% |
| rosa.ru | 4 | 2% |
| red-soft.ru | 4 | 2% |
| companium.ru | 4 | 2% |
| itkey.com | 3 | 1% |
| habr.com | 3 | 1% |
| ru.vstack.com | 3 | 1% |
| itglobal.com | 3 | 1% |
| safe.cnews.ru | 3 | 1% |
| smart-lab.ru | 3 | 1% |
| anti-malware.ru | 2 | 1% |
| servernews.ru | 2 | 1% |
| galex.ru | 2 | 1% |
| store.softline.ru | 2 | 1% |
| staff.spacevm.ru | 2 | 1% |
| novostiitkanala.ru | 2 | 1% |
| dataru.ru | 1 | 0% |
| globalcio.ru | 1 | 0% |
| bo.nalog.gov.ru | 1 | 0% |
| vedomosti.ru | 1 | 0% |

## Per-company breakdown
| company | coverage | INN | 2025 rev | products | news | # src | main domains | missing |
|---|---|---|---|---|---|---|---|---|
| Группа Астра | 100% | ✓ | ✓ | ✓ | ✓ | 9 | anti-malware.ru, astra.ru, cnews.ru… | — |
| ITKey | 79% | ✓ | ✓ | ✓ | ✓ | 9 | checko.ru, cnews.ru, companies.rbc.ru… | — |
| НТЦ ИТ РОСА | 79% | ✓ | ✓ | ✓ | ✓ | 9 | cnews.ru, companies.rbc.ru, habr.com… | — |
| Orion soft | 79% | ✓ | ✓ | ✓ | ✓ | 10 | checko.ru, companies.rbc.ru, galex.ru… | — |
| РЕД СОФТ | 91% | ✓ | ✓ | ✓ | ✓ | 8 | cnews.ru, companies.rbc.ru, red-soft.ru… | — |
| Space | 85% | ✓ | ✓ | ✓ | ✓ | 9 | b2b.house, cnews.ru, spacevm.ru… | — |
| vStack | 85% | ✓ | ✓ | ✓ | ✓ | 9 | checko.ru, cnews.ru, companies.rbc.ru… | — |
| Базис | 100% | ✓ | ✓ | ✓ | ✓ | 13 | bo.nalog.gov.ru, cnews.ru, companium.ru… | — |

## Quality flags
- **INN placeholder / invalid (counted as empty):** none
- **Revenue is estimate-only (no filed/rating figure):** none
- **key_products missing:** none
- **latest_news is a meta-comment, not a dated event:** none
- **Collector B not independent (no new source vs A):** none
- **Empty Collector-B passes:** none
- **Over-reliant on own website (≥80% of sources):** none
- **Language not in Russian:** none
- **Entity-match / product-vs-legal:** 8
    - Группа Астра: entity_match=high — «Группа Астра» — холдинг ПАО «Группа Астра», ИНН 7726476459; исследуемые продукты Termidesk и VMmanager принадлежат компаниям внутри группы, поэтому рыночная запись ведется на уровне группы, а не одного продуктового юрлица.
    - ITKey: entity_match=high — ITKey сопоставлен с ООО «Ключевые ИТ решения», ИНН 7707659016; официальный сайт прямо связывает ITKey, ООО «Ключевые ИТ решения», KeyStack и KeyVirt.
    - НТЦ ИТ РОСА: entity_match=high — Запись привязана к текущему АО «НТЦ ИТ РОСА», ИНН 7735201059, зарегистрированному 18.10.2023; более старое ООО «НТЦ ИТ РОСА» с другим ИНН не смешивалось с текущим юрлицом.
    - Orion soft: entity_match=high — Orion soft сопоставлен с ООО «ОРИОН», ИНН 9704113582; официальный сайт, T-Bank, РБК Компании и TAdviser согласуются по связке бренда, юрлица и продуктов zVirt/Termit.
    - РЕД СОФТ: entity_match=high — РЕД СОФТ сопоставлен с ООО «Ред Софт», ИНН 9705000373; исследуемый продукт — «РЕД Виртуализация», часть широкой продуктовой экосистемы компании.
    - Space: entity_match=high — Оба коллектора описывают один и тот же бренд Space, связанный с ООО «ДАКОМ М»; совпадают юридическое лицо по смыслу и ИНН 7734235312, сайт https://spacevm.ru/ и продуктовая экосистема SpaceVM/Space Cloud/Space VDI/Space Client.
    - vStack: entity_match=high — vStack/vStack HCP является продуктовым направлением ООО «ИТГЛОБАЛКОМ ЛАБС», ИНН 7841483359; связь подтверждена официальными реквизитами ITGLOBAL.COM, сайтом vStack, RBC Companies и TAdviser.
    - Базис: entity_match=high — Оба коллектора описывают один рыночный бренд и одну группу: операционное ООО «БАЗИС» с ИНН 7731316059 связано с ПАО «Группа компаний «Базис», которое выступает публичной головной структурой. Расхождения относятся к уровню описания — операционное юрлицо против группы, а не к другой компании.
- **Contaminated (repo-path) sources:** none

## Architect notes
Recurring failures → tighten the rule in `prompts/` or the run prompt. INN placeholders
mean the agent hit a rusprofile *search* page and gave up → reinforce "read the company
requisites (footer/контакты/оферта/политика конфиденциальности), then open the CARD."
Estimate-only revenue → push industry rating sources (edtechs.ru / smart-ranking.ru).
Missing products / meta-news → Collector B under-delivered.
