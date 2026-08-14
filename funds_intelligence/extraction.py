"""Structured extraction for paid pass output — the text→JSON boundary.

The first live landscape pass died here: `json.loads(res.text)` on raw model
text, with the response discarded on failure. A real provider returns prose,
```json fences, or a trailing explanation around the object; the company
pipeline has known this since day one and solved it in
`src.model_router.extract_json`.

`extract_json` below is a faithful port of that proven primitive, NOT an
import: `funds_intelligence` may not import `src/` outside `live_pass.py`
(enforced by `test_company_stack_is_confined_to_the_adapter_module` and the
fresh-interpreter offline check), and the controller must keep running with the
provider stack absent. The two are pinned together by an equivalence contract
test (`TestExtractorMatchesCompanyPrimitive`) that feeds both the same battery
of provider-shaped inputs — so a change to either side fails the suite. If the
isolation boundary is ever relaxed, this collapses to an import.

Validation here is STRUCTURAL only — "is this the shape the controller can
consume". Semantic validation stays where it belongs: the eight deterministic
rules in `rules.py`.
"""
from __future__ import annotations

import json
import re


class MalformedPassOutput(ValueError):
    """A paid pass returned output the controller cannot consume.

    Carries the on-disk location of the raw response so a failure is always
    diagnosable, and never advances run state — the stage stays retryable.
    """

    def __init__(self, message: str, *, stage: str = "", target: str = "",
                 raw_path=None, reason: str = ""):
        super().__init__(message)
        self.stage = stage
        self.target = target
        self.raw_path = raw_path
        self.reason = reason


def extract_json(text: str) -> dict:
    """Pull the first JSON object out of a model response (handles ```json
    fences). Port of `src.model_router.extract_json` — keep the two identical."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    blob = fenced.group(1) if fenced else text
    start = blob.find("{")
    if start == -1:
        raise ValueError(f"No JSON object in model output:\n{text[:500]}")
    depth = 0
    for i in range(start, len(blob)):
        if blob[i] == "{":
            depth += 1
        elif blob[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(blob[start : i + 1])
    raise ValueError("Unbalanced JSON in model output.")


# ── structural validators (shape only — semantics belong to rules.py) ────────
def _is_list_of_dicts(v) -> bool:
    return isinstance(v, list) and all(isinstance(x, dict) for x in v)


def validate_landscape(data) -> str:
    """Return a reason string when the payload is unusable, else ""."""
    if not isinstance(data, dict):
        return f"expected a JSON object, got {type(data).__name__}"
    cands = data.get("candidates")
    if cands is None:
        return "no «candidates» key — the landscape schema was not followed"
    if not _is_list_of_dicts(cands):
        return "«candidates» must be a list of objects"
    if not cands:
        return "«candidates» is empty — the pass found nothing to review"
    unnamed = [i for i, c in enumerate(cands) if not str(c.get("name") or "").strip()]
    if unnamed:
        return f"candidate(s) at index {unnamed} have no «name»"
    return ""


def validate_research(data) -> str:
    if not isinstance(data, dict):
        return f"expected a JSON object, got {type(data).__name__}"
    for key in ("vehicles", "continuation_vehicles", "people", "portfolio"):
        if key in data and not _is_list_of_dicts(data[key]):
            return f"«{key}» must be a list of objects"
    mc = data.get("management_company")
    if mc is not None and not isinstance(mc, dict):
        return "«management_company» must be an object"
    if not mc and not data.get("vehicles"):
        return ("neither «management_company» nor «vehicles» present — nothing "
                "to build a graph from")
    return ""


def validate_deep_dive(data) -> str:
    if not isinstance(data, dict):
        return f"expected a JSON object, got {type(data).__name__}"
    for key in ("deals", "people"):
        if key in data and not _is_list_of_dicts(data[key]):
            return f"«{key}» must be a list of objects"
    if not data.get("deals") and not data.get("people"):
        return "neither «deals» nor «people» present — nothing to expand"
    return ""
