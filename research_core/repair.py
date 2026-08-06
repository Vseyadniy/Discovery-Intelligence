"""Scoped, non-destructive repair merge.

Generalises the company's proven `gate.merge_repair` policy (which fixed real
repair-loop data-loss incidents) into a reusable, pack-agnostic function:

  * start from the SAVED record;
  * take a field from the repair patch only if it is (a) inside the declared
    repair scope, or (b) a gap-fill (was empty before, has content now);
  * fields the patch dropped or restructured are preserved untouched — a bad
    repair can damage only what it was explicitly asked to fix;
  * flags are unioned (old markers survive; new ones append).

`is_empty` and the container/flag keys are injectable so a pack can reuse the
policy over its own record shape without the core knowing that shape.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Callable

from ._util import is_empty as _default_is_empty


@dataclass
class RepairPatch:
    """A scoped patch. `scope` is the set of fields the repair is allowed to
    overwrite; `fields` are the proposed new field values; `flags` append to
    the record's review flags."""
    scope: set[str] = field(default_factory=set)
    fields: dict = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)


def apply_patch(base: dict, patch: RepairPatch, *,
                container: str = "fields", flags_key: str = "review_flags",
                is_empty: Callable[[object], bool] = _default_is_empty) -> dict:
    """Return a new record = base with the patch safely merged in. Pure; does
    not mutate `base`. If `base` is not a dict, an empty record is used."""
    merged = copy.deepcopy(base) if isinstance(base, dict) else {}
    mf = merged.get(container)
    if not isinstance(mf, dict):
        mf = {}
        merged[container] = mf

    for name, value in (patch.fields or {}).items():
        if name in patch.scope:
            mf[name] = value                                   # in-scope: overwrite
        elif is_empty(mf.get(name)) and not is_empty(value):
            mf[name] = value                                   # gap-fill: never lose data
        # else: out-of-scope, already present → preserved

    old_flags = [str(x) for x in (merged.get(flags_key) or [])]
    add = [str(x) for x in (patch.flags or []) if str(x) not in old_flags]
    if old_flags or add:
        merged[flags_key] = old_flags + add
    return merged
