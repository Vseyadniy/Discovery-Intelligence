"""Deterministic validation infrastructure.

Reliability-critical validation is CODE, never LLM-authored. A research plan may
*select* which registered rules apply (by id), but the rules themselves are
Python callables registered in code. This module provides the framework — the
result vocabulary, a rule registry, and a runner — that a pack's own gate rules
plug into. It intentionally contains no company- or funds-specific checks.

The `Issue` shape (`field`, `severity`, `code`, `reason`) is deliberately
identical to the company gate's dicts, so existing gate rules can be adopted as
registered rules later without reshaping their output.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

REJECT = "reject"
WARN = "warn"
_SEVERITIES = (REJECT, WARN)


@dataclass(frozen=True)
class Issue:
    field: str
    severity: str      # "reject" | "warn"
    code: str
    reason: str = ""

    def __post_init__(self):
        if self.severity not in _SEVERITIES:
            raise ValueError(f"severity must be one of {_SEVERITIES}, got {self.severity!r}")

    def as_dict(self) -> dict:
        return {"field": self.field, "severity": self.severity,
                "code": self.code, "reason": self.reason}


@dataclass
class ValidationResult:
    issues: list[Issue] = field(default_factory=list)

    @property
    def rejects(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == REJECT]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == WARN]

    @property
    def accepted(self) -> bool:
        """Verdict rule mirrors the company gate: rejected iff any reject."""
        return not any(i.severity == REJECT for i in self.issues)

    @property
    def verdict(self) -> str:
        return "accepted" if self.accepted else "rejected"

    def codes(self) -> set[str]:
        return {i.code for i in self.issues}

    def as_dicts(self) -> list[dict]:
        return [i.as_dict() for i in self.issues]


# A rule inspects a payload and returns the issues it found (empty = clean).
# Rules are pure/deterministic by contract — no I/O, no model calls.
Rule = Callable[[dict], "list[Issue] | list[dict]"]


class RuleRegistry:
    """Registry of deterministic validation rules, keyed by id. A spec selects
    rule ids; only registered *callables* can ever run — an LLM cannot inject
    executable validation because there is no code path that compiles a rule
    from data."""

    def __init__(self):
        self._rules: dict[str, Rule] = {}

    def register(self, rule_id: str, fn: Rule) -> None:
        if not callable(fn):
            raise TypeError(f"rule {rule_id!r} must be a callable, got {type(fn).__name__}")
        if rule_id in self._rules:
            raise ValueError(f"rule id already registered: {rule_id!r}")
        self._rules[rule_id] = fn

    def rule(self, rule_id: str) -> Callable[[Rule], Rule]:
        """Decorator form: @registry.rule("my-code")."""
        def deco(fn: Rule) -> Rule:
            self.register(rule_id, fn)
            return fn
        return deco

    def ids(self) -> list[str]:
        return sorted(self._rules)

    def has(self, rule_id: str) -> bool:
        return rule_id in self._rules

    def run(self, payload: dict, rule_ids: "list[str] | None" = None) -> ValidationResult:
        """Run selected rules (default: all registered) over one payload.
        Unknown ids raise — a spec cannot silently skip a required rule."""
        ids = self.ids() if rule_ids is None else list(rule_ids)
        issues: list[Issue] = []
        for rid in ids:
            fn = self._rules.get(rid)
            if fn is None:
                raise KeyError(f"no registered rule with id {rid!r}")
            for item in (fn(payload) or []):
                issues.append(item if isinstance(item, Issue) else Issue(**item))
        return ValidationResult(issues)
