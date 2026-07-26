"""Search-only comparison harness — Brave vs Tavily, no model involved.

Runs a fixed fixture of queries through the PRODUCTION search adapters
(`web_tools.search_with_telemetry`) and writes machine- and human-readable
outputs for offline comparison. It never invokes a model, never creates or
touches a research run, never falls back between providers, and writes only to
its own output directory.

    python -m src.search_harness FIXTURE --provider both --out harness_out/run1
    python -m src.search_harness FIXTURE --provider brave --dry-run
    python -m src.search_harness --analyze harness_out/run1     # offline re-metric

Outputs (per run dir): results.json (per-query-per-provider raw + telemetry),
metrics.json (aggregate + paired), report.md (human-readable). Saved results
can be re-analysed offline (`--analyze DIR`) with no new API calls.

Retrieval quality (domain hits) is kept separate from page fetchability;
`--fetch-check` (off by default) fetches the top result per query via the
existing `fetch_url` to record whether it is openable — it never affects the
retrieval metrics.

Secrets are never read or written here: telemetry is counts/categories only,
saved results hold title/url/snippet only.
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from . import web_tools

SCHEMA_VERSION = 1
PROVIDERS = ("brave", "tavily")


class SearchHarnessError(ValueError):
    """Fixture or invocation problem — reported clearly, no traceback needed."""


# ── fixture ───────────────────────────────────────────────────────────────────
@dataclass
class Query:
    id: str
    query: str
    category: str = ""
    expected_domains: list[str] = field(default_factory=list)
    preferred_domain: str = ""
    notes: str = ""


def _norm_domain(d: str) -> str:
    d = str(d or "").strip().lower()
    if "://" in d:
        d = urlparse(d).netloc or d
    d = d.split("/")[0]
    return d[4:] if d.startswith("www.") else d


def _host(url: str) -> str:
    try:
        h = (urlparse(str(url)).netloc or "").lower()
        return h[4:] if h.startswith("www.") else h
    except Exception:
        return ""


def _domain_matches(host: str, expected: str) -> bool:
    """A result host matches an expected domain if equal or a subdomain of it."""
    if not host or not expected:
        return False
    return host == expected or host.endswith("." + expected)


def load_fixture(path: str | Path) -> list[Query]:
    p = Path(path)
    if not p.exists():
        raise SearchHarnessError(f"fixture not found: {p}")
    raw = p.read_text(encoding="utf-8")
    try:
        if p.suffix.lower() in (".yaml", ".yml"):
            import yaml
            data = yaml.safe_load(raw)
        else:
            data = json.loads(raw)
    except Exception as ex:
        raise SearchHarnessError(f"fixture is not valid {p.suffix or 'JSON'}: {ex}")
    return validate_fixture(data)


def validate_fixture(data) -> list[Query]:
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict) and isinstance(data.get("queries"), list):
        items = data["queries"]
    else:
        raise SearchHarnessError(
            "fixture must be a list of queries or an object with a `queries` list")
    if not items:
        raise SearchHarnessError("fixture has no queries")
    out: list[Query] = []
    seen: set[str] = set()
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            raise SearchHarnessError(f"query #{i + 1} is not a mapping")
        qid = str(it.get("id") or "").strip()
        text = str(it.get("query") or "").strip()
        if not qid:
            raise SearchHarnessError(f"query #{i + 1} is missing `id`")
        if not text:
            raise SearchHarnessError(f"query «{qid}» is missing `query` text")
        if qid in seen:
            raise SearchHarnessError(f"duplicate query id «{qid}»")
        seen.add(qid)
        exp = it.get("expected_domains")
        if exp is None:
            exp = []
        if not isinstance(exp, list):
            raise SearchHarnessError(
                f"query «{qid}»: expected_domains must be a list of domains")
        out.append(Query(
            id=qid, query=text, category=str(it.get("category") or "").strip(),
            expected_domains=[_norm_domain(d) for d in exp if str(d).strip()],
            preferred_domain=_norm_domain(it.get("preferred_domain") or ""),
            notes=str(it.get("notes") or "").strip()))
    return out


# ── one query · one provider ──────────────────────────────────────────────────
def _hits_at(results: list[dict], expected: list[str], k: int) -> bool:
    for r in results[:k]:
        host = _host(r.get("url", ""))
        if any(_domain_matches(host, e) for e in expected):
            return True
    return False


def _pref_hits_at(results: list[dict], pref: str, k: int) -> bool:
    if not pref:
        return False
    return any(_domain_matches(_host(r.get("url", "")), pref) for r in results[:k])


def probe(q: Query, provider: str, count: int,
          fetch_check: bool = False) -> dict:
    """Run ONE query on ONE provider through the production adapter (never
    raises), and compute per-query retrieval metrics. Optionally record whether
    the top result is fetchable (kept separate from retrieval quality)."""
    results, tel = web_tools.search_with_telemetry(q.query, count, provider=provider)
    urls = [r.get("url", "") for r in results if r.get("url")]
    domains = [_host(u) for u in urls if _host(u)]
    rec = {
        "id": q.id, "category": q.category, "provider": provider,
        "telemetry": tel.public_dict(),
        "n_results": len(results),
        "hit1": _hits_at(results, q.expected_domains, 1),
        "hit3": _hits_at(results, q.expected_domains, 3),
        "hit5": _hits_at(results, q.expected_domains, 5),
        "pref_hit1": _pref_hits_at(results, q.preferred_domain, 1),
        "pref_hit3": _pref_hits_at(results, q.preferred_domain, 3),
        "pref_hit5": _pref_hits_at(results, q.preferred_domain, 5),
        "unique_domains": sorted(set(domains)),
        "duplicate_urls": len(urls) - len(set(urls)),
        "duplicate_domains": len(domains) - len(set(domains)),
        # title/url/snippet only — compact, no secrets; enough for offline metrics
        "results": [{"title": (r.get("title") or "")[:200],
                     "url": r.get("url", ""),
                     "snippet": (r.get("snippet") or "")[:200]} for r in results],
    }
    if fetch_check:
        rec["fetch"] = _fetch_top(results)
    return rec


def _fetch_top(results: list[dict]) -> dict:
    """Fetchability of the TOP result only (bounded) via the existing fetch_url —
    disabled by default; never influences retrieval metrics."""
    if not results:
        return {"checked": False}
    url = results[0].get("url", "")
    if not url:
        return {"checked": False}
    res = web_tools.fetch_url(url)
    ok = "error" not in res and bool(res.get("text"))
    return {"checked": True, "ok": ok,
            "chars": len(res.get("text", "")) if ok else 0,
            "error": web_tools.redact(res.get("error", "")) if not ok else ""}


# ── run ───────────────────────────────────────────────────────────────────────
def _providers_arg(provider: str) -> list[str]:
    p = (provider or "brave").strip().lower()
    if p == "both":
        return list(PROVIDERS)
    if p not in PROVIDERS:
        raise SearchHarnessError(f"unknown provider «{p}» — brave | tavily | both")
    return [p]


def run_harness(fixture_path: str | Path, provider: str = "brave",
                out_dir: str | Path = "harness_out", count: int = 8,
                max_queries: int = 0, dry_run: bool = False,
                fetch_check: bool = False, log=print) -> dict:
    """Execute the harness. Returns a summary dict; writes results.json,
    metrics.json and report.md into out_dir (a fresh directory of its own)."""
    queries = load_fixture(fixture_path)
    providers = _providers_arg(provider)
    if max_queries and max_queries > 0:
        queries = queries[:max_queries]
    plan = {"fixture": str(fixture_path), "providers": providers,
            "n_queries": len(queries), "count": count,
            "fetch_check": bool(fetch_check),
            "api_calls_upper_bound": len(queries) * len(providers)
            + (len(queries) * len(providers) if fetch_check else 0)}

    if dry_run:
        log(f"[harness] DRY RUN — validated {len(queries)} query(ies); would run "
            f"{providers} → ≤ {plan['api_calls_upper_bound']} search call(s); "
            f"no API calls made, no files written.")
        return {"dry_run": True, "plan": plan}

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    for n, q in enumerate(queries, 1):
        for prov in providers:                       # never falls back
            rec = probe(q, prov, count, fetch_check=fetch_check)
            records.append(rec)
            t = rec["telemetry"]
            log(f"[harness] {n}/{len(queries)} {q.id} · {prov}: "
                f"{t['outcome']}{'/' + t['error_category'] if t['error_category'] else ''}"
                f" · {rec['n_results']} results · {t['latency_ms']}ms")

    meta = {"schema_version": SCHEMA_VERSION,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "providers": providers, "n_queries": len(queries), "count": count,
            "fetch_check": bool(fetch_check)}
    results_doc = {"meta": meta, "records": records}
    (out / "results.json").write_text(
        json.dumps(results_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    metrics = analyze_records(records, meta)
    (out / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    report = render_report(meta, metrics)
    (out / "report.md").write_text(report, encoding="utf-8")
    log(f"[harness] wrote {out/'results.json'}, {out/'metrics.json'}, "
        f"{out/'report.md'}")
    return {"dry_run": False, "out_dir": str(out), "n_records": len(records),
            "metrics": metrics}


# ── metrics (pure; offline) ───────────────────────────────────────────────────
def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return float(s[0])
    rank = pct / 100 * (len(s) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(s) - 1)
    frac = rank - lo
    return round(s[lo] + (s[hi] - s[lo]) * frac, 1)


def _rate(num: int, den: int) -> float:
    return round(num / den, 4) if den else 0.0


def _provider_metrics(recs: list[dict]) -> dict:
    n = len(recs)
    ok = [r for r in recs if r["telemetry"]["outcome"] == "ok"]
    empty = [r for r in recs if r["telemetry"]["outcome"] == "empty"]
    err = [r for r in recs if r["telemetry"]["outcome"] == "error"]
    cats: dict[str, int] = {}
    for r in err:
        c = r["telemetry"]["error_category"] or "other"
        cats[c] = cats.get(c, 0) + 1
    lat = [r["telemetry"]["latency_ms"] for r in recs]
    # domain metrics count only queries that actually returned results
    answered = ok + empty
    doms: list[str] = []
    dup_urls = dup_doms = 0
    for r in recs:
        doms.extend(r.get("unique_domains", []))
        dup_urls += r.get("duplicate_urls", 0)
        dup_doms += r.get("duplicate_domains", 0)
    return {
        "n": n, "success": len(ok), "empty": len(empty), "error": len(err),
        "success_rate": _rate(len(ok), n), "empty_rate": _rate(len(empty), n),
        "error_rate": _rate(len(err), n), "error_categories": cats,
        "latency_p50_ms": _percentile(lat, 50),
        "latency_p95_ms": _percentile(lat, 95),
        "hit1_rate": _rate(sum(r["hit1"] for r in recs), n),
        "hit3_rate": _rate(sum(r["hit3"] for r in recs), n),
        "hit5_rate": _rate(sum(r["hit5"] for r in recs), n),
        "pref_hit1_rate": _rate(sum(r["pref_hit1"] for r in recs), n),
        "pref_hit3_rate": _rate(sum(r["pref_hit3"] for r in recs), n),
        "pref_hit5_rate": _rate(sum(r["pref_hit5"] for r in recs), n),
        "unique_domains": len(set(doms)),
        "duplicate_urls": dup_urls, "duplicate_domains": dup_doms,
        "answered": len(answered),
    }


def analyze_records(records: list[dict], meta: dict | None = None) -> dict:
    by_provider: dict[str, dict] = {}
    for prov in PROVIDERS:
        recs = [r for r in records if r["provider"] == prov]
        if recs:
            by_provider[prov] = _provider_metrics(recs)

    # paired: query ids present for BOTH providers
    paired = []
    by_id: dict[str, dict] = {}
    for r in records:
        by_id.setdefault(r["id"], {})[r["provider"]] = r
    for qid, provs in by_id.items():
        if "brave" in provs and "tavily" in provs:
            b, t = provs["brave"], provs["tavily"]
            paired.append({
                "id": qid, "category": b.get("category", ""),
                "brave": {"outcome": b["telemetry"]["outcome"],
                          "n": b["n_results"], "hit1": b["hit1"],
                          "hit3": b["hit3"], "hit5": b["hit5"]},
                "tavily": {"outcome": t["telemetry"]["outcome"],
                           "n": t["n_results"], "hit1": t["hit1"],
                           "hit3": t["hit3"], "hit5": t["hit5"]}})

    paired_summary = {}
    if paired:
        np = len(paired)
        paired_summary = {
            "n_paired": np,
            "brave_hit3_rate": _rate(sum(p["brave"]["hit3"] for p in paired), np),
            "tavily_hit3_rate": _rate(sum(p["tavily"]["hit3"] for p in paired), np),
            "both_hit3": sum(1 for p in paired
                             if p["brave"]["hit3"] and p["tavily"]["hit3"]),
            "neither_hit3": sum(1 for p in paired
                                if not p["brave"]["hit3"] and not p["tavily"]["hit3"]),
            "brave_only_hit3": sum(1 for p in paired
                                   if p["brave"]["hit3"] and not p["tavily"]["hit3"]),
            "tavily_only_hit3": sum(1 for p in paired
                                    if p["tavily"]["hit3"] and not p["brave"]["hit3"]),
        }
    return {"schema_version": SCHEMA_VERSION, "meta": meta or {},
            "by_provider": by_provider, "paired": paired,
            "paired_summary": paired_summary}


def render_report(meta: dict, metrics: dict) -> str:
    L = ["# Search comparison report",
         f"generated: {meta.get('generated_at', '?')} · "
         f"providers: {', '.join(meta.get('providers', []))} · "
         f"queries: {meta.get('n_queries', '?')} · count: {meta.get('count', '?')}"
         + (" · fetch-check ON" if meta.get("fetch_check") else ""),
         ""]
    L.append("## Per provider")
    L.append("| provider | n | success | empty | error | hit@1 | hit@3 | hit@5 "
             "| p50 ms | p95 ms | uniq dom | dup url |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for prov, m in metrics.get("by_provider", {}).items():
        L.append(f"| {prov} | {m['n']} | {m['success_rate']:.0%} | "
                 f"{m['empty_rate']:.0%} | {m['error_rate']:.0%} | "
                 f"{m['hit1_rate']:.0%} | {m['hit3_rate']:.0%} | "
                 f"{m['hit5_rate']:.0%} | {m['latency_p50_ms']:.0f} | "
                 f"{m['latency_p95_ms']:.0f} | {m['unique_domains']} | "
                 f"{m['duplicate_urls']} |")
    for prov, m in metrics.get("by_provider", {}).items():
        if m["error_categories"]:
            L.append(f"\n**{prov} errors:** " + ", ".join(
                f"{k}={v}" for k, v in sorted(m["error_categories"].items())))
    ps = metrics.get("paired_summary") or {}
    if ps:
        L += ["", "## Paired (queries run on both)",
              f"- paired queries: {ps['n_paired']}",
              f"- hit@3 — brave {ps['brave_hit3_rate']:.0%} · "
              f"tavily {ps['tavily_hit3_rate']:.0%}",
              f"- both hit@3: {ps['both_hit3']} · neither: {ps['neither_hit3']} · "
              f"brave-only: {ps['brave_only_hit3']} · "
              f"tavily-only: {ps['tavily_only_hit3']}"]
    return "\n".join(L) + "\n"


def analyze_saved(run_dir: str | Path, log=print) -> dict:
    """Re-compute metrics.json + report.md from a saved results.json — offline,
    no API calls (for re-analysing an earlier harness run)."""
    d = Path(run_dir)
    rj = d / "results.json"
    if not rj.exists():
        raise SearchHarnessError(f"no results.json in {d}")
    doc = json.loads(rj.read_text(encoding="utf-8"))
    meta = doc.get("meta", {})
    metrics = analyze_records(doc.get("records", []), meta)
    (d / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    (d / "report.md").write_text(render_report(meta, metrics), encoding="utf-8")
    log(f"[harness] re-analysed {rj} → metrics.json, report.md (offline)")
    return metrics


# ── CLI ───────────────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(
        description="Search-only Brave/Tavily comparison harness (no model, "
                    "no research run, no fallback).")
    ap.add_argument("fixture", nargs="?", help="JSON/YAML fixture of queries")
    ap.add_argument("--provider", default="brave",
                    choices=["brave", "tavily", "both"])
    ap.add_argument("--out", default="harness_out/run",
                    help="output directory (its own; never a research run)")
    ap.add_argument("--count", type=int, default=8, help="results per query")
    ap.add_argument("--max-queries", type=int, default=0,
                    help="hard cap on how many fixture queries to run (0 = all)")
    ap.add_argument("--dry-run", action="store_true",
                    help="validate the fixture and show the plan; no API calls")
    ap.add_argument("--fetch-check", action="store_true",
                    help="also test top-result fetchability (off by default)")
    ap.add_argument("--analyze", metavar="DIR",
                    help="re-compute metrics from a saved run dir (offline)")
    args = ap.parse_args()
    try:
        if args.analyze:
            analyze_saved(args.analyze)
            return
        if not args.fixture:
            ap.error("give a fixture path (or --analyze DIR)")
        summary = run_harness(
            args.fixture, provider=args.provider, out_dir=args.out,
            count=args.count, max_queries=args.max_queries,
            dry_run=args.dry_run, fetch_check=args.fetch_check)
    except SearchHarnessError as ex:
        raise SystemExit(f"harness error: {ex}")
    if not summary.get("dry_run"):
        print(f"done → {summary['out_dir']} ({summary['n_records']} records)")


if __name__ == "__main__":
    main()
