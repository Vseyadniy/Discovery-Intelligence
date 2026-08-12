"""Deterministic Funds validation rules.

Every rule is a plain Python callable registered by id (never LLM-authored) and
operates on a serialised `FundGraph`. They encode the domain errors that are
expensive in fund research — the ones a generic gate would not catch.

Rule ids (all `reject` unless noted):

  aum-vs-fund-size            AUM attached to a fund vehicle, or fund size to a
                              management company; or a vehicle's size equal to
                              the manager's AUM while other vehicles exist.
  manco-vs-vehicle            a node typed as a fund vehicle that is really the
                              manager (or vice versa) — name/vintage signals.
  stale-person-role           a person presented as CURRENT leadership while the
                              employment edge has an end date.
  unsupported-relationship    a deal/portfolio/manages edge with no evidence, or
                              in a non-factual state while rendered as fact.
  marketing-as-strategy       an "active strategy" claim whose only support is
                              the GP's own marketing page.
  absence-as-negative         a claim asserting a negative ("no successor fund",
                              "not investing") stated as fact while resting on
                              absence of evidence. An evidence-BACKED negative
                              (a filing that states dissolution) is legitimate.
  unsupported-claim           a factual claim with no supporting evidence —
                              distinct from the above: missing evidence means
                              UNSUPPORTED, not "false".
  evidence-integrity  (warn)  a claim citing an evidence id that does not exist,
                              or evidence with no URL.
"""
from __future__ import annotations

import re

from research_core import Issue, REJECT, WARN

from .model import (
    FundGraph, MANAGEMENT_COMPANY, FUND_VEHICLE, PERSON, DEAL, PORTFOLIO_COMPANY,
    CONTINUATION_VEHICLE, EMPLOYS, MANAGES, INVESTED_IN, DEAL_OF, DEAL_TARGET,
    CONFIRMED, PARTIALLY_CONFIRMED, INFERRED, UNRESOLVED, NON_FACTUAL_STATES,
    TIER_WEAK,
)

# Phrases that assert a negative — dangerous when they rest on absence of evidence.
_NEGATIVE_ASSERTION = re.compile(
    r"\b(no |none|never|not |ceased|стоп|нет |не )", re.IGNORECASE)
_ABSENCE_BASIS = re.compile(
    r"(no (public |published )?(evidence|record|data|filing|mention|trace)|"
    r"could not find|not found|nothing found|absen\w*|"
    r"не найдено|нет данных|нет информации|отсутству\w*)", re.IGNORECASE)

# Marketing-ish wording used to claim an active strategy.
_STRATEGY_FIELDS = ("active_strategy", "strategy", "current_strategy", "investing_status")


def _graph(payload) -> FundGraph:
    return payload if isinstance(payload, FundGraph) else FundGraph.from_dict(payload or {})


def _num(v) -> float | None:
    """Parse '$250m' / '1.2bn' / '250 000 000' → float in base units."""
    if v is None:
        return None
    s = str(v).strip().lower().replace(",", "").replace(" ", " ")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(bn|b|billion|млрд|m|mn|million|млн|k|тыс)?", s)
    if not m:
        return None
    n = float(m.group(1))
    unit = (m.group(2) or "").strip()
    mult = {"bn": 1e9, "b": 1e9, "billion": 1e9, "млрд": 1e9,
            "m": 1e6, "mn": 1e6, "million": 1e6, "млн": 1e6,
            "k": 1e3, "тыс": 1e3}.get(unit, 1.0)
    return n * mult


def register(registry) -> None:
    """Register every Funds rule on a core RuleRegistry."""

    # 1 ── AUM confused with fund size ────────────────────────────────────────
    @registry.rule("aum-vs-fund-size")
    def aum_vs_fund_size(payload):
        g = _graph(payload)
        issues = []
        for n in g.by_kind(FUND_VEHICLE):
            if "aum" in n.claims and n.claim("aum").value not in (None, ""):
                issues.append(Issue(
                    f"{n.name}.aum", REJECT, "aum-vs-fund-size",
                    "AUM belongs to the management company; a fund vehicle has a "
                    "fund size (committed capital). Re-attach as fund_size or to the manager."))
        for n in g.by_kind(MANAGEMENT_COMPANY):
            if "fund_size" in n.claims and n.claim("fund_size").value not in (None, ""):
                issues.append(Issue(
                    f"{n.name}.fund_size", REJECT, "aum-vs-fund-size",
                    "fund_size belongs to a specific vehicle; a management company has AUM."))
        # a vehicle whose size equals the manager's AUM while ≥2 vehicles exist
        vehicles = g.by_kind(FUND_VEHICLE)
        if len(vehicles) >= 2:
            for mc in g.by_kind(MANAGEMENT_COMPANY):
                aum = _num(mc.claim("aum").value)
                if not aum:
                    continue
                for v in vehicles:
                    size = _num(v.claim("fund_size").value)
                    if size and abs(size - aum) < 1e-6:
                        issues.append(Issue(
                            f"{v.name}.fund_size", REJECT, "aum-vs-fund-size",
                            f"fund_size equals {mc.name}'s AUM while {len(vehicles)} vehicles "
                            f"exist — AUM aggregates all vehicles and cannot equal one fund's size."))
        return issues

    # 2 ── management company confused with a fund vehicle ────────────────────
    @registry.rule("manco-vs-vehicle")
    def manco_vs_vehicle(payload):
        g = _graph(payload)
        issues = []
        manager_words = re.compile(
            r"\b(management|managers?|advisors?|partners llp|gp|ук|управляющая)\b", re.I)
        vehicle_words = re.compile(
            r"(\bfund\b|\bl\.?p\.?\b|\bfcp\b|\bscsp\b|фонд)", re.I)
        for n in g.by_kind(FUND_VEHICLE):
            has_vintage = n.claim("vintage").value not in (None, "")
            if manager_words.search(n.name) and not vehicle_words.search(n.name) and not has_vintage:
                issues.append(Issue(
                    f"{n.name}", REJECT, "manco-vs-vehicle",
                    "typed as fund_vehicle but the name reads as a manager and it has no "
                    "vintage — a vehicle needs a vintage; re-type as management_company."))
            if not has_vintage and n.claim("fund_size").value not in (None, ""):
                issues.append(Issue(
                    f"{n.name}.vintage", REJECT, "manco-vs-vehicle",
                    "a fund vehicle with a size must carry a vintage year (which fund is this?)."))
        for n in g.by_kind(MANAGEMENT_COMPANY):
            if vehicle_words.search(n.name) and n.claim("vintage").value not in (None, ""):
                issues.append(Issue(
                    f"{n.name}", REJECT, "manco-vs-vehicle",
                    "typed as management_company but named/dated like a fund vehicle."))
        return issues

    # 3 ── historical person role presented as current ────────────────────────
    @registry.rule("stale-person-role")
    def stale_person_role(payload):
        g = _graph(payload)
        issues = []
        for e in g.edges_of(EMPLOYS):
            person = g.nodes.get(e.dst)
            if person is None or person.kind != PERSON:
                continue
            ended = str(e.props.get("to") or "").strip()
            claims_current = bool(e.props.get("current")) or \
                str(person.claim("role_status").value or "").lower() == "current"
            if ended and claims_current:
                issues.append(Issue(
                    f"{person.name}.role", REJECT, "stale-person-role",
                    f"presented as current but the role ended {ended} — a former partner is "
                    f"not current leadership."))
            role = str(person.claim("role").value or "")
            if ended and role and "former" not in role.lower() and not e.props.get("historical"):
                issues.append(Issue(
                    f"{person.name}.role", REJECT, "stale-person-role",
                    f"role «{role}» ended {ended}; label it former/historical explicitly."))
        return issues

    # 4 ── unsupported deal / portfolio relationship ──────────────────────────
    @registry.rule("unsupported-relationship")
    def unsupported_relationship(payload):
        g = _graph(payload)
        issues = []
        needs_evidence = (INVESTED_IN, DEAL_OF, DEAL_TARGET, MANAGES)
        for e in g.edges_of():
            if e.kind not in needs_evidence:
                continue
            known = [i for i in e.evidence_ids if i in g.evidence]
            label = f"{e.kind}:{g.nodes.get(e.src, e.src)}"
            src_name = g.nodes[e.src].name if e.src in g.nodes else e.src
            dst_name = g.nodes[e.dst].name if e.dst in g.nodes else e.dst
            label = f"{e.kind} {src_name}→{dst_name}"
            if not known:
                issues.append(Issue(
                    label, REJECT, "unsupported-relationship",
                    "relationship has no evidence — a portfolio/deal link is a claim and "
                    "needs its own source."))
            elif e.state in (CONFIRMED, PARTIALLY_CONFIRMED) and \
                    all(g.evidence[i].tier >= TIER_WEAK for i in known):
                issues.append(Issue(
                    label, REJECT, "unsupported-relationship",
                    f"state «{e.state}» rests only on tier-3 evidence — downgrade to inferred "
                    f"or find a primary/reputable source."))
        # a deal node must connect to both a vehicle and a target
        for d in g.by_kind(DEAL):
            if not g.edges_of(DEAL_OF, src=d.id):
                issues.append(Issue(d.name, REJECT, "unsupported-relationship",
                                    "deal is not linked to a fund vehicle (deal_of edge missing)."))
            if not g.edges_of(DEAL_TARGET, src=d.id):
                issues.append(Issue(d.name, REJECT, "unsupported-relationship",
                                    "deal is not linked to a portfolio company (deal_target missing)."))
        return issues

    # 5 ── marketing language treated as proven active strategy ───────────────
    @registry.rule("marketing-as-strategy")
    def marketing_as_strategy(payload):
        g = _graph(payload)
        issues = []
        for n in g.nodes.values():
            for fname in _STRATEGY_FIELDS:
                c = n.claims.get(fname)
                if c is None or c.value in (None, "") or not c.is_factual:
                    continue
                evs = g.evidence_for(c)
                if evs and all(e.is_marketing or e.tier >= TIER_WEAK for e in evs):
                    issues.append(Issue(
                        f"{n.name}.{fname}", REJECT, "marketing-as-strategy",
                        "an active strategy stated as fact rests only on the GP's own "
                        "marketing/self-reported copy — mark inferred or support with a "
                        "filing/deal record."))
        return issues

    # 6 ── absence of evidence treated as a NEGATIVE fact ─────────────────────
    # Distinct from an merely unsupported claim (rule 6b): here the claim asserts
    # that something does NOT exist, and its stated basis is that nothing was
    # found. "We found no evidence" is not "it does not exist".
    # An evidence-BACKED negative ("the fund was dissolved", per a filing) is a
    # legitimate factual conclusion and must still pass.
    @registry.rule("absence-as-negative")
    def absence_as_negative(payload):
        g = _graph(payload)
        issues = []
        for n in g.nodes.values():
            for fname, c in n.claims.items():
                if not c.is_factual:
                    continue          # inferred/unresolved negatives are honest
                if not _NEGATIVE_ASSERTION.search(str(c.value or "")):
                    continue
                basis_text = f"{c.value} {c.note}"
                rests_on_absence = bool(_ABSENCE_BASIS.search(basis_text))
                has_evidence = any(i in g.evidence for i in c.evidence_ids)
                if rests_on_absence or not has_evidence:
                    issues.append(Issue(
                        f"{n.name}.{fname}", REJECT, "absence-as-negative",
                        "a negative asserted as fact on the basis of absent evidence — "
                        "not finding a successor fund is not proof there is none; use "
                        "unresolved (or cite a source that states the negative)."))
        return issues

    # 6b ── a factual claim with no supporting evidence ───────────────────────
    # Missing evidence normally means UNSUPPORTED, not "false" — this rule says
    # so explicitly and separately, so the operator can tell the two apart.
    @registry.rule("unsupported-claim")
    def unsupported_claim(payload):
        g = _graph(payload)
        issues = []
        for n in g.nodes.values():
            for fname, c in n.claims.items():
                if not c.is_factual:
                    continue
                if not any(i in g.evidence for i in c.evidence_ids):
                    issues.append(Issue(
                        f"{n.name}.{fname}", REJECT, "unsupported-claim",
                        f"state «{c.state}» with no supporting evidence — an unsupported "
                        f"claim must be recorded as unresolved (or inferred, with its "
                        f"grounds), never as established fact."))
        return issues

    # 7 ── evidence integrity (warn) ──────────────────────────────────────────
    @registry.rule("evidence-integrity")
    def evidence_integrity(payload):
        g = _graph(payload)
        issues = []
        for n in g.nodes.values():
            for fname, c in n.claims.items():
                for eid in c.evidence_ids:
                    if eid not in g.evidence:
                        issues.append(Issue(
                            f"{n.name}.{fname}", WARN, "evidence-integrity",
                            f"cites unknown evidence id «{eid}»"))
        for eid, ev in g.evidence.items():
            if not str(ev.url or "").strip():
                issues.append(Issue(f"evidence.{eid}", WARN, "evidence-integrity",
                                    "evidence has no URL"))
        return issues


ALL_RULE_IDS = [
    "aum-vs-fund-size", "manco-vs-vehicle", "stale-person-role",
    "unsupported-relationship", "marketing-as-strategy", "absence-as-negative",
    "unsupported-claim", "evidence-integrity",
]
