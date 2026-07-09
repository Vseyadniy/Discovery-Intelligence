"""
Stamp one blank gold record per company from _TEMPLATE.md, seeded with the
input_entities.csv hints. Existing files are left untouched (won't clobber
hand-filled gold) unless you pass --force.

  python docs/gold/generate_gold.py            # create missing gold files
  python docs/gold/generate_gold.py --force    # overwrite (discards edits!)
"""
from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
TEMPLATE = HERE / "_TEMPLATE.md"
CSV = ROOT / "inputs" / "input_entities.csv"


def safe_name(seed: str) -> str:
    """Match review-card naming so gold ↔ output line up; strip filesystem-hostile chars."""
    return re.sub(r"[\\/:*?\"<>|]", "_", seed).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="overwrite existing gold files")
    args = ap.parse_args()

    tmpl = TEMPLATE.read_text(encoding="utf-8")
    created, skipped = 0, 0
    for row in csv.DictReader(CSV.open(encoding="utf-8")):
        seed = row["seed_name"].strip()
        dest = HERE / f"{safe_name(seed)}.md"
        if dest.exists() and not args.force:
            skipped += 1
            continue
        content = (tmpl
                   .replace("{{SEED}}", seed)
                   .replace("{{WEBSITE}}", row.get("website", "").strip() or "—")
                   .replace("{{INN}}", row.get("inn", "").strip() or "—")
                   .replace("{{HINT}}", row.get("hint", "").strip() or "—"))
        dest.write_text(content, encoding="utf-8")
        created += 1
        print(f"  created {dest.name}")
    print(f"\nDone. created {created}, skipped {skipped} existing "
          f"(use --force to overwrite).")


if __name__ == "__main__":
    main()
