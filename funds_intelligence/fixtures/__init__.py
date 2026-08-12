"""Offline Funds fixtures — a few deliberately tricky mandates, no live calls.

Each fixture is a scripted `ResearchPass` script: a list of
`(reply_json_text, {url: page_text})` steps. The reply is what a researcher pass
would return; the pages are what it "fetched", so grounding is real (claims are
checked against actual fixture source text) while staying entirely offline.

The four funds are chosen to break naive implementations:

  ALPHA   clean-ish baseline, but the landscape reply confuses **AUM with fund
          size** on Fund III and lists the *manager* as a vehicle.
  BOREAS  a **former partner** presented as current leadership + a portfolio
          relationship with **no evidence**.
  CASPIAN **marketing page** used as proof of an "active" strategy + a negative
          ("no successor fund") asserted from absence of evidence.
  DELTA   a **continuation vehicle** that must not be flattened into its
          predecessor fund; deep dive expands deals/people correctly.
"""
from __future__ import annotations

import json

# ── shared source pages (what a pass would have fetched) ─────────────────────
PAGES = {
    "https://reg.example/alpha": (
        "Alpha Capital Partners LLP is the manager of the Alpha Capital funds. "
        "Firm-wide assets under management: $780m as of 2025. "
        "Alpha Capital Fund III (2021) closed at $250m in committed capital. "
        "Alpha Capital Fund II (2017) closed at $180m."),
    "https://press.example/alpha-fund3": (
        "Alpha Capital Fund III held a final close at $250m, vintage 2021, "
        "targeting Series A software in the Baltics."),
    "https://reg.example/boreas": (
        "Boreas Ventures Management is the general partner of Boreas Ventures "
        "Fund II (vintage 2019, EUR 120m). Ivan Petrov served as Managing "
        "Partner from 2016 until his departure in 2022. Current Managing "
        "Partner is Maria Lind."),
    "https://gp.example/caspian-strategy": (
        "Caspian Growth actively deploys capital into regional B2B software "
        "champions and is always open to new opportunities."),
    "https://reg.example/caspian": (
        "Caspian Growth Advisors manages Caspian Growth Fund I (vintage 2016, "
        "$95m). No filings after 2021 were located in this register."),
    "https://press.example/delta-cv": (
        "Delta Capital completed a GP-led secondary in 2024, moving three assets "
        "from Delta Fund I (vintage 2015) into Delta Continuation Vehicle I, "
        "sized at $140m. Delta Fund I remains a separate 2015 vehicle."),
    "https://press.example/delta-deal": (
        "Delta Fund I led a $12m Series B in Nordwind Robotics in 2019. "
        "Delta partner Anna Sorokin joined the Nordwind board."),
}


def _step(payload: dict, urls: list[str]):
    return (json.dumps(payload, ensure_ascii=False),
            {u: PAGES[u] for u in urls})


# ── landscape fixtures (one step per mandate) ────────────────────────────────
def landscape_script(mandate: str = "ru_baltics_vc"):
    """A landscape pass proposing candidate managers/vehicles. Deliberately
    contains the AUM/fund-size trap and a manager listed as a vehicle."""
    payload = {
        "candidates": [
            {"name": "Alpha Capital Partners LLP", "kind": "management_company",
             "aum": "$780m", "source": "https://reg.example/alpha"},
            {"name": "Alpha Capital Fund III", "kind": "fund_vehicle",
             "vintage": 2021, "fund_size": "$250m",
             "source": "https://press.example/alpha-fund3"},
            {"name": "Boreas Ventures Management", "kind": "management_company",
             "aum": "EUR 120m", "source": "https://reg.example/boreas"},
            {"name": "Caspian Growth Advisors", "kind": "management_company",
             "aum": "$95m", "source": "https://reg.example/caspian"},
            {"name": "Delta Capital", "kind": "management_company",
             "aum": "$400m", "source": "https://press.example/delta-cv"},
        ]
    }
    return [_step(payload, ["https://reg.example/alpha",
                            "https://press.example/alpha-fund3",
                            "https://reg.example/boreas",
                            "https://reg.example/caspian",
                            "https://press.example/delta-cv"])]


# ── per-target research fixtures (the tricky ones) ───────────────────────────
def target_script(name: str):
    """Return the scripted research pass for one landscape target."""
    return _TARGETS[name]()


def _alpha():
    # TRAP: AUM attached to the *vehicle*, and the manager given a fund_size.
    return [_step({
        "management_company": {"name": "Alpha Capital Partners LLP",
                               "aum": {"value": "$780m", "state": "confirmed",
                                       "source": "https://reg.example/alpha"}},
        "vehicles": [
            {"name": "Alpha Capital Fund III", "vintage": 2021,
             "aum": {"value": "$780m", "state": "confirmed",           # ← wrong field
                     "source": "https://reg.example/alpha"},
             "fund_size": {"value": "$250m", "state": "confirmed",
                           "source": "https://press.example/alpha-fund3"}},
        ],
    }, ["https://reg.example/alpha", "https://press.example/alpha-fund3"])]


def _boreas():
    # TRAP: former partner as current + portfolio edge with no evidence.
    return [_step({
        "management_company": {"name": "Boreas Ventures Management",
                               "aum": {"value": "EUR 120m", "state": "confirmed",
                                       "source": "https://reg.example/boreas"}},
        "vehicles": [{"name": "Boreas Ventures Fund II", "vintage": 2019,
                      "fund_size": {"value": "EUR 120m", "state": "confirmed",
                                    "source": "https://reg.example/boreas"}}],
        "people": [{"name": "Ivan Petrov", "role": "Managing Partner",
                    "role_status": "current",                       # ← left in 2022
                    "from": "2016", "to": "2022",
                    "source": "https://reg.example/boreas"}],
        "portfolio": [{"company": "Helios Analytics", "state": "confirmed",
                       "evidence": []}],                            # ← unsupported
    }, ["https://reg.example/boreas"])]


def _caspian():
    # TRAP (deliberately HARD): both bad claims are quoted VERBATIM from their
    # sources, so grounding accepts them and only the semantic rules can catch
    # them. This is the realistic failure — the GP really did write the
    # marketing sentence, and the register really did say it found nothing.
    return [_step({
        "management_company": {
            "name": "Caspian Growth Advisors",
            "aum": {"value": "$95m", "state": "confirmed",
                    "source": "https://reg.example/caspian"},
            # verbatim marketing copy, presented as established active strategy
            "active_strategy": {
                "value": "actively deploys capital into regional B2B software champions",
                "state": "confirmed",
                "source": "https://gp.example/caspian-strategy",
                "marketing": True},
            # the register's own "we found nothing", presented as a negative FACT
            "successor_fund": {
                "value": "No filings after 2021 were located in this register",
                "state": "confirmed",
                "note": "treated as proof that no successor fund exists",
                "source": "https://reg.example/caspian"},
        },
        "vehicles": [{"name": "Caspian Growth Fund I", "vintage": 2016,
                      "fund_size": {"value": "$95m", "state": "confirmed",
                                    "source": "https://reg.example/caspian"}}],
    }, ["https://reg.example/caspian", "https://gp.example/caspian-strategy"])]


def _delta():
    # Clean-but-structurally-hard: continuation vehicle must stay distinct.
    return [_step({
        "management_company": {"name": "Delta Capital",
                               "aum": {"value": "$400m", "state": "confirmed",
                                       "source": "https://press.example/delta-cv"}},
        "vehicles": [
            {"name": "Delta Fund I", "vintage": 2015,
             "fund_size": {"value": "$210m", "state": "partially_confirmed",
                           "source": "https://press.example/delta-cv"}},
        ],
        "continuation_vehicles": [
            {"name": "Delta Continuation Vehicle I", "vintage": 2024,
             "fund_size": {"value": "$140m", "state": "confirmed",
                           "source": "https://press.example/delta-cv"},
             "continues": "Delta Fund I"},
        ],
    }, ["https://press.example/delta-cv"])]


_TARGETS = {
    "Alpha Capital Partners LLP": _alpha,
    "Boreas Ventures Management": _boreas,
    "Caspian Growth Advisors": _caspian,
    "Delta Capital": _delta,
}


# ── deep-dive fixture (child run over one accepted parent target) ────────────
def deep_dive_script(name: str = "Delta Capital"):
    """Expands portfolio companies / deals / people for an accepted parent."""
    return [_step({
        "deals": [{"name": "Nordwind Series B", "amount": "$12m", "year": 2019,
                   "vehicle": "Delta Fund I", "target": "Nordwind Robotics",
                   "state": "confirmed", "source": "https://press.example/delta-deal"}],
        "people": [{"name": "Anna Sorokin", "role": "Partner", "from": "2018",
                    "state": "confirmed", "source": "https://press.example/delta-deal"}],
    }, ["https://press.example/delta-deal"])]


LANDSCAPE_TARGETS = ["Alpha Capital Partners LLP", "Boreas Ventures Management",
                     "Caspian Growth Advisors", "Delta Capital"]
