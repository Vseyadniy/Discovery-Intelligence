"""
Render the *real* filled agent prompts for a seed company, so a subscription
researcher (GPT-5.5 in Codex) can run the two-collector + verifier architecture
by hand — with zero drift from what `src.orchestrator` sends the API.

It reuses the orchestrator's own prompt-assembly (`format_sources`,
`format_fields`, `format_corrections`, `fill`) and the same `prompts/*.md` files,
so an emulated run and a real pipeline run see byte-identical instructions.

Emulation loop, per company:
  1) python -m src.render_prompts collectors --seed "K2 Cloud"
        → prints the filled Collector A prompt and the filled Collector B prompt.
     Run A as one independent pass (registry/financial sources only) → save A JSON.
     Run B as a separate, independent pass (news/market only, blind to A) → B JSON.
  2) python -m src.render_prompts verifier --a A.json --b B.json
        → prints the filled Verifier prompt. Run it over A+B → the merged record JSON.

Save each JSON under outputs/agent_runs/ (see --emit), then build the table:
  python -m src.export_excel --from-records outputs/agent_runs --out docs/test_gpu/GPU_IaaS_RU.xlsx
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from .orchestrator import (
    ROOT, PROMPTS, load_config,
    format_sources, format_fields, format_corrections, fill,
)

RUNS = ROOT / "outputs" / "agent_runs"


def _find_row(seed: str) -> dict:
    path = ROOT / "inputs" / "input_entities.csv"
    want = seed.strip().lower()
    for r in csv.DictReader(path.open(encoding="utf-8")):
        if (r.get("seed_name") or "").strip().lower() == want:
            return r
    raise SystemExit(f"seed '{seed}' not found in {path} (match on the seed_name column).")


def _collector_prompt(letter: str, row: dict, schema, registry, corrections) -> str:
    prompt = (PROMPTS / f"collector_{letter.lower()}.md").read_text(encoding="utf-8")
    return fill(prompt, {
        "seed_name": row["seed_name"],
        "inn": row.get("inn", ""),
        "website": row.get("website", ""),
        "hint": row.get("hint", ""),
        "geo": schema["geo"],
        "industry": schema["industry"],
        "output_language": schema.get("output_language", "English"),
        f"sources_{letter.lower()}": format_sources(
            registry, schema["geo"], schema["industry"], schema["mode"], letter.upper()),
        "schema_fields": format_fields(schema),
        "corrections": format_corrections(corrections),
    })


def _verifier_prompt(a: dict, b: dict, corrections, schema=None) -> str:
    prompt = (PROMPTS / "verifier.md").read_text(encoding="utf-8")
    return fill(prompt, {
        "collector_a_json": json.dumps(a, ensure_ascii=False, indent=2),
        "collector_b_json": json.dumps(b, ensure_ascii=False, indent=2),
        "output_language": (schema or {}).get("output_language", "English"),
        "corrections": format_corrections(corrections),
    })


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in name).strip("-")


def cmd_collectors(args) -> None:
    schema, registry, corrections = load_config()
    row = _find_row(args.seed)
    a = _collector_prompt("A", row, schema, registry, corrections)
    b = _collector_prompt("B", row, schema, registry, corrections)

    if args.emit:
        RUNS.mkdir(parents=True, exist_ok=True)
        slug = _slug(args.seed)
        (RUNS / f"{slug}_promptA.md").write_text(a, encoding="utf-8")
        (RUNS / f"{slug}_promptB.md").write_text(b, encoding="utf-8")
        print(f"[emit] wrote {RUNS}/{slug}_promptA.md and _promptB.md")
        print(f"       run each as an INDEPENDENT pass; save results as "
              f"{slug}_A.json and {slug}_B.json in {RUNS}/")
        return

    print("=" * 78)
    print(f"COLLECTOR A — {args.seed}  (registry/financial sources ONLY)")
    print("=" * 78)
    print(a)
    print("\n" + "=" * 78)
    print(f"COLLECTOR B — {args.seed}  (news/market sources ONLY; DO NOT look at A)")
    print("=" * 78)
    print(b)


def cmd_verifier(args) -> None:
    schema, _, corrections = load_config()
    a = json.loads(Path(args.a).read_text(encoding="utf-8"))
    b = json.loads(Path(args.b).read_text(encoding="utf-8"))
    v = _verifier_prompt(a, b, corrections, schema)
    if args.emit:
        RUNS.mkdir(parents=True, exist_ok=True)
        out = RUNS / (Path(args.a).stem.replace("_A", "") + "_promptV.md")
        out.write_text(v, encoding="utf-8")
        print(f"[emit] wrote {out}  → save the merged result as *_record.json in {RUNS}/")
        return
    print("=" * 78)
    print("VERIFIER — merge A + B, detect conflicts, assign confidence")
    print("=" * 78)
    print(v)


def main() -> None:
    ap = argparse.ArgumentParser(description="Render filled agent prompts for hand-run (emulated) research.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("collectors", help="render Collector A + B prompts for a seed")
    c.add_argument("--seed", required=True, help="seed_name from inputs/input_entities.csv, e.g. 'K2 Cloud'")
    c.add_argument("--emit", action="store_true", help="write prompts to outputs/agent_runs/ instead of stdout")
    c.set_defaults(func=cmd_collectors)

    v = sub.add_parser("verifier", help="render the Verifier prompt from two collector JSON files")
    v.add_argument("--a", required=True, help="Collector A output JSON")
    v.add_argument("--b", required=True, help="Collector B output JSON")
    v.add_argument("--emit", action="store_true", help="write the prompt to outputs/agent_runs/ instead of stdout")
    v.set_defaults(func=cmd_verifier)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
