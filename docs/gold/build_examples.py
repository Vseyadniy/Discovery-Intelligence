"""
Build the 15 filled 'perfect example' gold records from the B2B market map
(docs/str_B2B market map WIP v6.5.xlsx). Data was cross-referenced across the
segment tabs (1.3 GPU / 2.2.1 DP-MDB / Apps) plus the `$$$` (INN, legal name,
filed revenue) and `Num of Prod` (per-segment product counts) lists — the same
multi-list aggregation you'd do by hand (e.g. Arenadata appears on Compute,
Kuber, DP-MDB, DP-tools, Num of Prod, and $$$).

Run:  python docs/gold/build_examples.py [--force]
Writes <SUBSECTOR>_<brand>.md per company. Skips existing unless --force.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent


def reg(inn):  # registry source for legal name / INN
    return f"https://egrul.nalog.ru/  (ЕГРЮЛ, ИНН {inn})"


def fin(inn):  # filed-financials source for revenue
    return f"https://bo.nalog.ru/  (ГИР БО, ИНН {inn})"


# Each company: values taken verbatim/aggregated from the market map.
COMPANIES = [
    # ─────────────────────────── IaaS / GPU ────────────────────────────────
    dict(
        sub="GPU", brand="Cloud.ru", legal="ОБЛАЧНЫЕ ТЕХНОЛОГИИ, ООО",
        inn="7736279160", website="cloud.ru", etype="group", ematch="high",
        disambig="Ex-SberCloud (Sber + Huawei Cloud), spun out into a standalone company. "
                 "Brand 'Cloud.ru' operates over legal entity ООО «Облачные технологии». "
                 "Press still sometimes calls it 'SberCloud' — do not merge with Sber's other entities.",
        description="Russian hyperscaler spun out of SberCloud; 80+ IaaS/PaaS services. "
                    ">50% of clients are internal Sber-ecosystem and B2G; Tier III DCs, 152-ФЗ, КИИ certs.",
        segment="IaaS / GPU (cloud GPU + bare metal)",
        products="6 GPU types (H100, A100, V100, A40, A16, T4); Evolution ECS GPU cloud product + "
                 "Bare Metal (HGX 8×A100). Configs of 1/2/4/8/16 GPU with NVLink/Infiniband.",
        prodlink="https://cloud.ru/products/vychislitelnyye-moschnosti-s-gpu",
        numprod="3 GPU products in map. Cross-list (Num of Prod): IaaS ALL 52 "
                "(Compute 6, Storage 9, GPU 3, BM 2, Network 23), PaaS ALL 25, Security 11.",
        rev="50.74 млрд ₽", rev_year="2024", rev_conf="high",
        rev_assump="Filed 2024. 2025 ≈ 76.21 млрд ₽ (map). Whole legal entity, not GPU-line only.",
        news="—", news_conf="low", news_src="—",
        ab=[dict(f="legal_entity_name", a="ООО «Облачные технологии»", b="'SberCloud'/'Cloud.ru'", c="ООО «Облачные технологии» (brand Cloud.ru)")],
    ),
    dict(
        sub="GPU", brand="Selectel", legal="СЕЛЕКТЕЛ, АО",
        inn="7810962785", website="selectel.ru", etype="legal_entity", ematch="high",
        disambig="Single clear legal entity АО «Селектел», founded 2008.",
        description="IT-infrastructure provider founded 2008 (cloud since 2010). "
                    "Own data centers; broad IaaS + bare-metal GPU offering.",
        segment="IaaS / GPU (cloud GPU + bare metal)",
        products="16 GPU types across Cloud VMs and Bare Metal servers.",
        prodlink="https://selectel.ru/services/gpu/",
        numprod="4 GPU products in map (widest GPU line-up among mid-caps).",
        rev="13.05 млрд ₽", rev_year="2024", rev_conf="high",
        rev_assump="Filed 2024. 2025 ≈ 16.05 млрд ₽ (map).",
        news="Active MOEX bond issuer; IPO widely discussed 2024–2025.", news_conf="medium",
        news_src="press / MOEX bond disclosures — confirm before use",
        ab=[],
    ),
    dict(
        sub="GPU", brand="K2 Cloud", legal="АО «К2 ИНТЕГРАЦИЯ»",
        inn="7701829110", website="k2.cloud", etype="brand", ematch="high",
        disambig="Brand 'K2 Cloud' (cloud arm of K2Тех), legal entity is АО «К2 Интеграция» — "
                 "brand ≠ legal name. Formerly «Крок Облачные сервисы».",
        description="Cloud division of K2Тех (ex-«Крок Облачные сервисы»). Enterprise IaaS/PaaS.",
        segment="IaaS / GPU (cloud GPU)",
        products="4 GPU types (NVIDIA), Cloud GPU.",
        prodlink="https://k2.cloud/products/gpu/",
        numprod="2 GPU products in map. Cross-list: IaaS ALL 20 (Compute 2, Storage 9, GPU 2, BM 2, Network 5), PaaS 6.",
        rev="14.16 млрд ₽", rev_year="2024", rev_conf="high",
        rev_assump="Filed 2024. 2025 ≈ 18.87 млрд ₽ (map).",
        news="—", news_conf="low", news_src="—",
        ab=[dict(f="legal_entity_name", a="АО «К2 Интеграция»", b="'K2 Cloud'", c="АО «К2 Интеграция» (brand K2 Cloud)")],
    ),
    dict(
        sub="GPU", brand="MWS", legal="ООО «МВС»",
        inn="7707767501", website="mws.ru", etype="brand", ematch="medium",
        disambig="MWS = MTS Web Services, MTS's cloud/AI brand launched 2024–2025. Spans MULTIPLE "
                 "legal entities — main is ООО «МВС» (ИНН 7707767501); note also «МВС Облачные "
                 "решения» ООО (ИНН 7841468537, ex-1Cloud/ИТ-Град). Do not attribute to МТС ПАО.",
        description="MTS's cloud & AI brand (MWS), launched its own GPU cloud in 2025.",
        segment="IaaS / GPU (cloud GPU)",
        products="3 GPU types; Cloud GPU with up to 4 configs.",
        prodlink="https://mws.ru/services/virtual-infrastructure-gpu/",
        numprod="3 GPU products in map.",
        rev="51.37 млрд ₽", rev_year="2024", rev_conf="medium",
        rev_assump="Filed for ООО «МВС» 2024; 2025 ≈ 50.07 млрд ₽. Confirm which MWS legal entity the "
                   "figure belongs to — the brand is split across several INNs.",
        news="MTS consolidated cloud/AI assets under the MWS brand (2024–2025).", news_conf="medium",
        news_src="MTS press — confirm",
        ab=[dict(f="inn", a="7707767501 (ООО «МВС»)", b="7841468537 (МВС Облачные решения)", c="7707767501 for the MWS cloud brand entity — verify per product")],
    ),
    dict(
        sub="GPU", brand="Timeweb Cloud", legal="ТАЙМВЭБ.КЛАУД, ООО",
        inn="7810945525", website="timeweb.cloud", etype="legal_entity", ematch="high",
        disambig="Cloud arm of Timeweb; legal entity ООО «Таймвэб.Клауд».",
        description="Russian cloud-infrastructure provider; large self-service GPU catalogue.",
        segment="IaaS / GPU (cloud GPU + bare metal)",
        products="17 GPU types (incl. GTX series), Cloud + Bare Metal.",
        prodlink="https://timeweb.cloud/services/gpu",
        numprod="4 GPU products in map. Cross-list: IaaS ALL 17 (Compute 6, Storage 2, GPU 4, BM 1, Network 3), PaaS 10, Sec 3.",
        rev="1.48 млрд ₽", rev_year="2024", rev_conf="high",
        rev_assump="Filed 2024 for ООО «Таймвэб.Клауд». 2025 ≈ 2.47 млрд ₽ (map). Cloud entity only, not the whole Timeweb group.",
        news="—", news_conf="low", news_src="—",
        ab=[],
    ),
    # ──────────────────────── PaaS / DP-MDB (databases) ─────────────────────
    dict(
        sub="DPMDB", brand="Arenadata", legal="АРЕНАДАТА СОФТВЕР, ООО",
        inn="7713468845", website="arenadata.tech", etype="group", ematch="high",
        disambig="Public data-platform vendor. Holding «Группа Аренадата» (ПАО, MOEX ticker DATA) over "
                 "operating entity ООО «Аренадата Софтвер». Revenue below is the group; the DP-MDB "
                 "product line is only part of it.",
        description="Russian data-platform vendor (from the 2016 Hadoop-based distribution). Full on-prem "
                    "data stack across MDB, streaming, DWH and tooling.",
        segment="PaaS / Data Platform — Managed Databases (MDB), on-prem vendor",
        products="Arenadata DB (ADB/Greenplum), Arenadata Postgres (ADPG), Arenadata One (AD.ONE), "
                 "Arenadata Prosperity (ADP), Arenadata Streaming (ADS), Arenadata QuickMarts (ADQM).",
        prodlink="https://arenadata.tech/products/arenadata-db/",
        numprod="6 products in DP-MDB. Cross-list (Num of Prod) — the multi-segment footprint: "
                "PaaS ALL 12 = Kubernetes 1 + DP-MDB 6 + DP-tools 5, plus IaaS Compute 1.",
        rev="5.18 млрд ₽", rev_year="2024", rev_conf="high",
        rev_assump="Group filed revenue 2024; 2025 ≈ 8.8 млрд ₽. DP-MDB product-line revenue alone ≈ 2.1 млрд ₽ (2024, map) — do NOT equate the two.",
        news="IPO on MOEX (ticker DATA), October 2024.", news_conf="high",
        news_src="MOEX / Arenadata IR",
        ab=[dict(f="revenue", a="5.18 млрд ₽ (group, ГИР БО)", b="~2.1 млрд ₽ (DP-MDB product line, model)", c="5.18 млрд ₽ for the company; keep the product-line figure separate")],
    ),
    dict(
        sub="DPMDB", brand="Postgres Pro", legal="ППГ, ООО",
        inn="7729445882", website="postgrespro.ru", etype="brand", ematch="high",
        disambig="Brand 'Postgres Professional / Postgres Pro'; legal entity ООО «ППГ». Brand ≠ legal name.",
        description="Russian PostgreSQL vendor; enterprise-grade distribution and DBMS tooling for gov/enterprise.",
        segment="PaaS / Data Platform — Managed Databases (MDB), on-prem DBMS vendor",
        products="Postgres Pro Enterprise, Postgres Pro Standard, Postgres Pro Certified, Postgres Pro Shardman.",
        prodlink="https://postgrespro.ru/products/postgrespro/enterprise",
        numprod="9 products in DP-MDB. Cross-list: PaaS ALL 11 (DP-MDB 9, Analytics-platform 1, Dev tools 1), IaaS 1, Infra SW 1.",
        rev="9.29 млрд ₽", rev_year="2024", rev_conf="high",
        rev_assump="Filed 2024 for ООО «ППГ». 2025 ≈ 6.66 млрд ₽ (map) — a DECREASE vs 2024; verify against the filing before publishing.",
        news="—", news_conf="low", news_src="—",
        ab=[dict(f="legal_entity_name", a="ООО «ППГ»", b="'Postgres Professional'", c="ООО «ППГ» (brand Postgres Pro)")],
    ),
    dict(
        sub="DPMDB", brand="Tantor Labs", legal="ТАНТОР ЛАБС, ООО",
        inn="9701183207", website="tantorlabs.ru", etype="legal_entity", ematch="high",
        disambig="Subsidiary of Astra Group (ПАО «Группа Астра», MOEX ASTR). Founded 2021. "
                 "Attribute Tantor's own revenue to ООО «Тантор Лабс», not to the Astra parent.",
        description="PostgreSQL-focused vendor inside Astra Group; DBMS + database-management platform.",
        segment="PaaS / Data Platform — Managed Databases (MDB), on-prem DBMS vendor",
        products="Tantor Postgres, Tantor PipelineDB, Tantor Platform (DB management).",
        prodlink="https://tantorlabs.ru/",
        numprod="2 products in DP-MDB (on-prem).",
        rev="1.38 млрд ₽", rev_year="2024", rev_conf="high",
        rev_assump="Filed 2024 for ООО «Тантор Лабс». 2025 ≈ 1.32 млрд ₽ (map).",
        news="Parent Astra Group IPO'd on MOEX (ASTR), October 2023.", news_conf="high",
        news_src="MOEX / Astra IR",
        ab=[dict(f="legal_entity_name", a="ООО «Тантор Лабс»", b="'Группа Астра'", c="ООО «Тантор Лабс» (parent: Astra Group)")],
    ),
    dict(
        sub="DPMDB", brand="СберТех", legal="СБЕРТЕХ, АО",
        inn="7736632467", website="sbertech.ru", etype="legal_entity", ematch="high",
        disambig="AO «СберТех», Sber's technology subsidiary. Databases are ONE product line of a very "
                 "large company — the total-revenue trap below is the key risk here.",
        description="Sber's in-house technology company; ships the Platform V stack including its DBMS/data-grid.",
        segment="PaaS / Data Platform — Managed Databases (MDB), on-prem (part of a platform)",
        products="Platform V Pangolin DB, Platform V DataGrid.",
        prodlink="https://pangolin.sbertech.ru/",
        numprod="2 products in DP-MDB.",
        rev="20.56 млрд ₽", rev_year="2024", rev_conf="high",
        rev_assump="Filed 2024 = WHOLE СберТех; 2025 ≈ 28.27 млрд ₽. The Pangolin/DataGrid product line ≈ 905 млн ₽ (2024, map). Never publish the company total as the DB-product revenue.",
        news="—", news_conf="low", news_src="—",
        ab=[dict(f="revenue", a="20.56 млрд ₽ (whole СберТех, ГИР БО)", b="~0.9 млрд ₽ (Pangolin DB line, model)", c="Company 20.56 млрд ₽; DB product line ~0.9 млрд ₽ — keep separate")],
    ),
    dict(
        sub="DPMDB", brand="Diasoft", legal="ДИАСОФТ, ООО",
        inn="7715560268", website="diasoft.ru", etype="legal_entity", ematch="high",
        disambit_placeholder=None,
        disambig="Primarily a banking-software vendor; its database offering is the Digital Q line. "
                 "Segment-classification risk: don't file the whole company under DP-MDB.",
        description="Russian IT-solutions vendor (core banking software) with an infrastructure/data platform line.",
        segment="PaaS / Data Platform — Managed Databases (MDB) [adjacent to core fintech-software business]",
        products="Digital Q.DataBase, Digital Q.ClientCatalog.",
        prodlink="https://q.diasoft.ru/products/infrastrukturnye-platformy",
        numprod="2 products in DP-MDB.",
        rev="7.30 млрд ₽", rev_year="2024", rev_conf="high",
        rev_assump="Filed 2024 = whole Diasoft (mostly banking software). 2025 ≈ 8.35 млрд ₽.",
        news="IPO on MOEX (ticker DIAS), February 2024.", news_conf="high",
        news_src="MOEX / Diasoft IR",
        ab=[dict(f="segment", a="DP-MDB vendor", b="core-banking software vendor", c="Core business = banking software; DB is an adjacent line")],
    ),
    # ─────────────────── Security / Application Security ────────────────────
    dict(
        sub="AppSec", brand="Positive Technologies", legal="ПОЗИТИВ ТЕКНОЛОДЖИЗ, АО",
        inn="7718668887", website="ptsecurity.com", etype="group", ematch="high",
        disambig="Public group ПАО «Группа Позитив» (MOEX POSI) over operating АО «Позитив Текнолоджиз». "
                 "AppSec is one of many segments; flagship products are VM/SIEM (MaxPatrol), not AppSec.",
        description="Leading Russian cybersecurity product vendor; broad portfolio across VM, SIEM, NGFW, "
                    "sandbox and application security.",
        segment="Security / Application Security (WAF, SAST/DAST)",
        products="PT Application Firewall (PT AF), PT Cloud Application Firewall, PT BlackBox (DAST), "
                 "PT Application Inspector (SAST).",
        prodlink="https://ptsecurity.com/products/",
        numprod="7 AppSec products. Cross-list (Num of Prod): Security ALL 33 (Apps 7, Data 1, Network 5, "
                "Cloud Platform 1, Infra 18, Endpoint 1), PaaS 1.",
        rev="24.47 млрд ₽", rev_year="2024", rev_conf="high",
        rev_assump="Filed 2024 = WHOLE company. AppSec-segment product revenue ≈ 17.27 млрд ₽ (2024) is a MODEL "
                   "estimate, not filed — flag as low/estimate. No 2025 figure in source.",
        news="Listed on MOEX (ticker POSI) since 2021.", news_conf="high",
        news_src="MOEX / Positive IR",
        ab=[dict(f="revenue", a="24.47 млрд ₽ (whole company, ГИР БО)", b="~17.27 млрд ₽ (AppSec line, model estimate)", c="Company 24.47 млрд ₽ filed; AppSec-line figure is a model estimate, mark low")],
    ),
    dict(
        sub="AppSec", brand="Solar", legal="ООО «СОЛАР»",
        inn="9724113301", website="rt-solar.ru", etype="legal_entity", ematch="high",
        disambig="Rostelecom's cybersecurity arm («Солар», ex-Ростелеком-Солар / ex-Solar Security). "
                 "Attribute to ООО «Солар», not to Ростелеком ПАО.",
        description="Rostelecom's security group; MSSP + product portfolio incl. application-security testing.",
        segment="Security / Application Security (SAST/DAST/SCA/ASOC)",
        products="Solar appScreener (SAST/DAST/SCA/ASOC), Solar webProxy.",
        prodlink="https://rt-solar.ru/products/",
        numprod="5 AppSec products. Cross-list: Security ALL 23 (Apps 5, Data 2, User 2, Network 7, Infra 7).",
        rev="", rev_year="2024", rev_conf="low",
        rev_assump="NOT in the market-map source ($$$ list blank for Solar). Leave blank — pull the ООО «Солар» "
                   "filing from bo.nalog.ru before quoting a number. Honest-blank example.",
        news="—", news_conf="low", news_src="—",
        ab=[dict(f="revenue", a="(blank — no filing in source)", b="(press may quote Rostelecom-Solar group)", c="Get ООО «Солар» filing; don't borrow the Rostelecom group figure")],
    ),
    dict(
        sub="AppSec", brand="Вебмониторэкс", legal="ВЕБМОНИТОРЭКС, ООО",
        inn="7735194651", website="webmonitorx.ru", etype="legal_entity", ematch="high",
        disambig="Pure-play WAF/API-security vendor; single clear legal entity ООО «Вебмониторэкс».",
        description="Russian vendor of web-application and API protection products.",
        segment="Security / Application Security (WAF + API Security)",
        products="ПроWAF (WAF), ПроAPI Security (API security).",
        prodlink="https://webmonitorx.ru",
        numprod="3 AppSec products. Cross-list: Security ALL 4 (Apps 3, Infra 1).",
        rev="571.75 млн ₽", rev_year="2024", rev_conf="high",
        rev_assump="Filed 2024. 2025 ≈ 1.81 млрд ₽ (map) — ~3× jump; verify (round or a genuine scale-up).",
        news="Revenue roughly tripled 2024→2025 (571.75 млн → 1.81 млрд ₽ in source).", news_conf="medium",
        news_src="market-map $$$ list — confirm the driver",
        ab=[],
    ),
    dict(
        sub="AppSec", brand="SolidWall", legal="СОЛИДСОФТ, ООО",
        inn="7714944046", website="solidwall.ru", etype="brand", ematch="medium",
        disambig="Brand 'SolidWall'; legal entity ООО «Солидсофт». Map lists parent = Яндекс — treat as a "
                 "Yandex-affiliated entity and confirm the current ownership before publishing.",
        description="Russian vendor of an intelligent web-application firewall and DAST tooling.",
        segment="Security / Application Security (WAF + DAST)",
        products="SolidWall WAF, SolidPoint DAST.",
        prodlink="https://www.solidwall.ru",
        numprod="3 AppSec products. Cross-list: Security ALL 3 (Apps 3).",
        rev="902.6 млн ₽", rev_year="2024", rev_conf="high",
        rev_assump="Filed 2024 for ООО «Солидсофт». 2025 ≈ 860.8 млн ₽ (map).",
        news="Listed with parent = Яндекс in the market map (confirm ownership/date).", news_conf="low",
        news_src="market-map Apps list — confirm",
        ab=[dict(f="legal_entity_name", a="ООО «Солидсофт»", b="'SolidWall' / Яндекс", c="ООО «Солидсофт» (brand SolidWall; parent per map: Яндекс — verify)")],
    ),
    dict(
        sub="AppSec", brand="Servicepipe", legal="СЕРВИСПАЙП, ООО",
        inn="7708257951", website="servicepipe.ru", etype="legal_entity", ematch="high",
        disambig="Anti-DDoS / WAF vendor; single clear legal entity ООО «Сервиспайп».",
        description="Russian vendor of anti-DDoS, WAF and anti-bot protection (on-prem and cloud).",
        segment="Security / Application Security (WAF + Anti-DDoS + anti-bot)",
        products="DDoS Protection, WAF, Cybert on-prem (WAF + anti-bot), Web anti-bot.",
        prodlink="https://servicepipe.ru/",
        numprod="6 AppSec products. Cross-list: Security ALL 11 (Apps 6, Data 3, Network 2).",
        rev="973.29 млн ₽", rev_year="2024", rev_conf="high",
        rev_assump="Filed 2024. 2025 ≈ 1.43 млрд ₽ (map).",
        news="—", news_conf="low", news_src="—",
        ab=[],
    ),
]


def render(c: dict) -> str:
    ab = "\n".join(
        f"- field: {t['f']}  |  A (registry) says: {t['a']}  |  B (press) says: {t['b']}  |  correct: {t['c']}"
        for t in c["ab"]
    ) or "- (no material A/B tension expected — official and market views should agree)"
    rev_line = c["rev"] if c["rev"] else "(blank — not in source; pull from bo.nalog.ru)"
    return f"""# GOLD — {c['brand']}   [{c['sub']}]

> Perfect-example ground truth, built from the B2B market map (segment tab + `$$$`
> + `Num of Prod`). This is the accuracy yardstick the pipeline is scored against.
> One field-block = one claim row, so it lines up 1:1 with a verifier output.

**Seed name:** {c['brand']}
**Website:** {c['website']}
**INN:** {c['inn']}
**Subsector:** {c['sub']}
**Filled by:** market-map v6.5   **Date:** 2026-07-03   **Hand-time (min):** n/a (reference)

---

## Entity match  (get this right FIRST — a wrong entity poisons every field)
- brand_name: {c['brand']}
- legal_entity_name: {c['legal']}
- inn: {c['inn']}
- website: {c['website']}
- entity_type: {c['etype']}
- confidence_entity_match: {c['ematch']}
- disambiguation_note: {c['disambig']}

---

## Business fields

### description
- value: {c['description']}
- source_url: {c['prodlink']}
- source_tier: 2
- confidence: high
- snippet: (from company site / market map)

### segment
- value: {c['segment']}
- source_url: https://.../Num of Prod  (internal market map)
- source_tier: 2
- confidence: high
- snippet: {c['numprod']}

### revenue   (RU: prefer the filed bo.nalog.ru / ГИР БО figure → high)
- value: {rev_line}
- source_url: {fin(c['inn'])}
- source_tier: 1
- year: {c['rev_year']}
- confidence: {c['rev_conf']}
- snippet: (filed accounting statements)
- assumptions: {c['rev_assump']}

### headcount
- value:
- source_url:
- source_tier:
- confidence: low
- snippet:
- assumptions: not in the B2B market-map source — fill from ЕГРЮЛ/СБИС/HH if needed.

### key_products
- value: {c['products']}
- source_url: {c['prodlink']}
- source_tier: 2
- confidence: high
- snippet: {c['numprod']}

### latest_news
- value: {c['news']}
- source_url: {c['news_src']}
- source_tier: 2
- year: {c['rev_year']}
- confidence: {c['news_conf']}
- snippet:

---

## Registry provenance
- legal_entity_name / inn source: {reg(c['inn'])}  (tier 1)

## Known A/B tension  (scores the verifier's conflict detection)
{ab}
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    created = skipped = 0
    for c in COMPANIES:
        safe = re.sub(r"[\\/:*?\"<>|]", "_", c["brand"]).strip().replace(" ", "-")
        dest = HERE / f"{c['sub']}_{safe}.md"
        if dest.exists() and not args.force:
            skipped += 1
            continue
        dest.write_text(render(c), encoding="utf-8")
        created += 1
        print(f"  wrote {dest.name}")
    print(f"\nDone. {created} written, {skipped} skipped (use --force to overwrite).")


if __name__ == "__main__":
    main()
