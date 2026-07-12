"""
API mode — drive the SAME staged state machine (discovery → per-company
research → repair) through API calls instead of paste-into-ChatGPT.

Reuses:
  * src.model_router  — GPT-5.5 (OpenAI Responses API + web_search) or Claude
    (Messages API + server-side web_search); keys from .env / the app settings.
  * prompts/*.md      — the REAL collector/verifier prompt files, so an API run
    matches an emulated run byte-for-byte.
  * src.runs          — the state machine, salvage and the ingest gate stay in
    charge; API mode only replaces WHO executes the current step.

Independence is real here: Collector A and Collector B are separate API calls
with fresh contexts — B physically cannot see A's findings.

Usage (also wired to the app's "Run next step via API ▶" button):
  python -m src.api_runner <run_id> [--batch 3] [--provider gpt|claude]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import gate
from . import model_router as mr
from . import runs
from . import web_tools
from .orchestrator import (PROMPTS, fill, format_corrections, format_fields,
                           format_sources, load_config)

_SYS = ("You are the researcher on a market-intelligence pipeline, with web "
        "search enabled. Follow the prompt exactly; open real sources; return "
        "STRICT JSON only — no prose around it.")

_SYS_QUAL = ("You are a qualitative research designer on a market-intelligence "
             "pipeline. You do NOT browse and do NOT add facts — work only from "
             "the material in the prompt. Return STRICT JSON only — no prose.")

_ACTION_LABEL = {"searching": "🔎 searching", "reading": "📄 reading",
                 "writing": "🧠 analyzing & writing", "thinking": "⏳ thinking",
                 "quota": "⚠️ Search API quota exhausted — continuing without "
                          "new search and highlighting unresolved fields"}

_QUOTA_SUMMARY = (" · ⚠️ Search API quota exhausted — continuing without new "
                  "search; unresolved fields stay blank (yellow in Excel)")


def _ev(log, prefix: str):
    """Stream-event → status-line adapter: '<agent · company> — action: detail'."""
    last = {"msg": None}

    def cb(action: str, detail: str = ""):
        d = str(detail or "")
        if len(d) > 90:
            d = d[:87] + "…"
        msg = f"{prefix} — {_ACTION_LABEL.get(action, action)}" + (f": {d}" if d else "")
        if msg != last["msg"]:
            last["msg"] = msg
            log(msg)
    return cb


def apply_env(values: dict[str, str]) -> None:
    """Apply key/model settings at runtime (the app saves .env, then calls this
    so no restart is needed)."""
    import os
    for k, v in values.items():
        if v:
            os.environ[k] = v
    mr.CHEAP_API_KEY = os.environ.get("CHEAP_API_KEY", mr.CHEAP_API_KEY)
    mr.CHEAP_MODEL = os.environ.get("CHEAP_MODEL", mr.CHEAP_MODEL)
    mr.CHEAP_BASE_URL = os.environ.get("CHEAP_BASE_URL") or mr.CHEAP_BASE_URL
    mr.CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", mr.CLAUDE_MODEL)
    mr.GROK_API_KEY = os.environ.get("GROK_API_KEY", mr.GROK_API_KEY)
    mr.GROK_MODEL = os.environ.get("GROK_MODEL", mr.GROK_MODEL)
    mr.DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", mr.DEEPSEEK_API_KEY)
    mr.DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", mr.DEEPSEEK_MODEL)
    mr.SEARCH_API_KEY = os.environ.get("SEARCH_API_KEY", mr.SEARCH_API_KEY)
    mr.SEARCH_PROVIDER = os.environ.get("SEARCH_PROVIDER") or mr.SEARCH_PROVIDER
    web_tools.reset_quota_flag()   # a new/upgraded search key gets a fresh start
    mr._cheap_client = None      # force re-construction with the new keys
    mr._claude_client = None
    mr._grok_client = None
    mr._deepseek_client = None


def _collector_prompt(letter: str, brand: str, hint: str, schema, registry, corrections) -> str:
    tpl = (PROMPTS / f"collector_{letter.lower()}.md").read_text(encoding="utf-8")
    return fill(tpl, {
        "seed_name": brand, "inn": "", "website": "", "hint": hint,
        "geo": schema["geo"], "industry": schema["industry"],
        "output_language": schema.get("output_language", "English"),
        f"sources_{letter.lower()}": format_sources(
            registry, schema["geo"], schema["industry"], schema["mode"], letter.upper()),
        "schema_fields": format_fields(schema),
        "corrections": format_corrections(corrections),
    })


def _verifier_prompt(a: dict, b: dict, schema, corrections) -> str:
    tpl = (PROMPTS / "verifier.md").read_text(encoding="utf-8")
    return fill(tpl, {
        "collector_a_json": json.dumps(a, ensure_ascii=False, indent=2),
        "collector_b_json": json.dumps(b, ensure_ascii=False, indent=2),
        "corrections": format_corrections(corrections),
        "output_language": schema.get("output_language", "English"),
    })


def _save(path: Path, obj: dict) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


# Collector-B gate codes are derived from the _A/_B files, which a record
# repair never touches — routing them into the record-repair prompt loops
# forever. They get a fresh Collector B pass + verifier re-merge instead.
_B_CODES = gate.B_CODES
_B_RERUN_LIMIT = 2   # same code still failing after this many fresh B passes → manual review

_REPAIR_LIMIT = 3    # record-level repair attempts per company before giving up
# Codes whose only fix is NEW evidence — when repairs are exhausted (or search
# is unavailable) these fields are blanked + flagged instead of looping forever
_EVIDENCE_CODES = {"unsourced", "bad-source", "search-url",
                   "insignificant-news", "history-missing"}


def _repair_sigs(run_dir: Path, brand: str) -> list[str]:
    """Failure signatures of this brand's past record repairs (events.jsonl)."""
    out = []
    try:
        for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines():
            ev = json.loads(line)
            if ev.get("event") == "api_repair" and ev.get("brand") == brand:
                out.append(str(ev.get("sig", "")))
    except Exception:
        pass
    return out


def _blank_unresolved(rec: dict, issues: list[dict], reason: str) -> list[str]:
    """Blank non-required evidence fields that repairs could not source, with an
    `unresolved:` review_flags note per field (the Excel export paints those
    cells yellow; the cells themselves stay EMPTY — never marker text)."""
    fields = rec.get("fields") or {}
    blanked = []

    def blank(name):
        f = fields.get(name)
        if name in gate.REQUIRED_FIELDS or not isinstance(f, dict):
            return
        if f.get("value") in (None, ""):
            return
        f["value"] = ""
        f["source"] = ""
        rec.setdefault("review_flags", []).append(f"unresolved: {name} — {reason}")
        blanked.append(name)

    for i in issues:
        if i["code"] in _EVIDENCE_CODES:
            blank(i["field"])
        elif i["code"] == "product-source-missing":
            # figures whose method could not be stated are unverifiable
            # estimates — blank the remaining product-revenue values themselves
            for name in gate._PRODUCT_REV_FIELDS:
                blank(name)
    return blanked


def _b_reruns(run_dir: Path, brand: str, codes: set[str]) -> int:
    """How many fresh Collector B passes this brand already got for any of
    the currently-failing codes (from events.jsonl)."""
    n = 0
    try:
        for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines():
            ev = json.loads(line)
            if (ev.get("event") == "api_collector_b_rerun" and ev.get("brand") == brand
                    and codes & set(str(ev.get("codes", "")).split(","))):
                n += 1
    except Exception:
        pass
    return n


def _ground(obj: dict, log, label: str, only_fields=None) -> dict:
    """DeepSeek-only grounding audit: every `source` the model cited must be a
    URL its web tools actually saw this pass; ungrounded sources are stripped so
    the EXISTING gate rejects the field as 'unsourced' → existing repair loop.
    No-op (empty dict) for the other providers, whose server-side search makes
    fabricated URLs impossible by construction.
    v1: reads the module-global mr.LAST_SOURCE_LOG — which is why deepseek
    collectors run sequentially (see below). TODO: pass the SourceLog explicitly
    per collector when parallelizing. Returns event kwargs (tool-call count +
    stripped/flagged tally); full URLs go to log()/events only, never into
    review_flags (year strings in URLs would interact with the gate's
    history-missing suppression keywords)."""
    if mr.MODE != "deepseek" or mr.LAST_SOURCE_LOG is None:
        return {}
    slog = mr.LAST_SOURCE_LOG
    details = slog.check_grounding(obj, only_fields=only_fields)
    if details:
        log(f"[grounding] {label}: {len(details)} source(s) stripped/flagged")
        for d in details:
            log(f"[grounding]   {d}")
    return {"tool_calls": slog.tool_calls, "grounding_affected": len(details),
            # per-pass telemetry (counts only) → flows into events.jsonl
            **{k: v for k, v in slog.stats.items() if v}}


def run_next_step(run_dir: Path, batch: int = 3, provider: str | None = None,
                  log=print) -> str:
    """Execute the run's current step via API. Returns a one-line summary."""
    if provider:
        mr.set_mode(provider)
    meta = runs._load_meta(run_dir)
    brands, _note, segments = runs.manifest(run_dir)

    # step 1 — discovery
    if not brands:
        _kind, text = runs.next_prompt(run_dir, batch)
        prompt = (text + "\n\n## API MODE OVERRIDE\nYou have no repo access. Do the "
                  "discovery via web search NOW and return ONLY the JSON object that "
                  "belongs in companies.json — no prose, no file operations.")
        log(f"[api] discovery via {mr.banner()}")
        import time as _t
        t0 = _t.time()
        raw, engine = mr.collect(_SYS, prompt, on_event=_ev(log, "🗺 Discovery"))
        data = mr.extract_json(raw)
        grd = _ground(data, log, "Discovery")   # no-op shape for discovery output
        if not (data.get("companies") and data.get("segments")):
            raise SystemExit("discovery response lacked companies/segments — retry or "
                             "fall back to the paste flow")
        (run_dir / "companies.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        runs._event(run_dir, "api_discovery", engine=engine,
                    companies=len(data["companies"]),
                    seconds=int(_t.time() - t0), **grd)
        return f"discovery ({engine}): {len(data['companies'])} companies, " \
               f"{len(data['segments'])} segments"

    schema = runs.load_schema()
    _s, registry, corrections = load_config()
    ar = run_dir / "agent_runs"

    # step 2 — per-company research. A and B run as PARALLEL separate calls:
    # genuinely independent contexts, and roughly half the wall-clock time.
    # Research passes take minutes each — files save incrementally, and one
    # failed company never loses the others' work.
    pending = runs._pending_brands(run_dir, brands)
    if pending:
        import time
        from concurrent.futures import ThreadPoolExecutor
        done, failed = [], []
        seg_by_brand = {}
        try:
            data = json.loads((run_dir / "companies.json").read_text(encoding="utf-8"))
            comps = data.get("companies") if isinstance(data, dict) else data
            seg_by_brand = {c.get("brand"): c.get("segment") or c.get("reason", "")
                            for c in comps or [] if isinstance(c, dict)}
        except Exception:
            pass
        todo = pending[:batch]
        for n, brand in enumerate(todo, 1):
            stem = runs._slug(brand)
            hint = str(seg_by_brand.get(brand, ""))[:200]
            t0 = time.time()
            try:
                # resume: a collector file saved by a previous (partly failed)
                # attempt is reused as-is — pressing ⚡ again redoes only the
                # missing passes, never finished work
                a = gate._load(ar / f"{stem}_A.json")
                b = gate._load(ar / f"{stem}_B.json")
                engine, grd = "resumed", {}
                ga = gb = {}
                have = ""
                if a is not None or b is not None:
                    have = " + ".join(x for x, v in (("A", a), ("B", b)) if v is not None)
                    log(f"[api] {brand} ({n}/{len(todo)}): resuming — collector "
                        f"{have} already saved, redoing only the rest")
                if mr.MODE == "deepseek":
                    # v1 safety measure: DeepSeek research uses the app's web
                    # tools, whose SourceLog travels through the module-global
                    # mr.LAST_SOURCE_LOG — running A and B in parallel would
                    # race on it and ground each record against the other's
                    # browsing. So deepseek runs A then B SEQUENTIALLY, each
                    # grounded right after its own pass. TODO: pass a SourceLog
                    # explicitly per collector, then re-join the parallel path.
                    if a is None and b is None:
                        log(f"[api] {brand} ({n}/{len(todo)}): collectors A, then B "
                            f"(sequential on DeepSeek) — a research pass takes minutes…")
                    if a is None:
                        a_raw, engine = mr.collect(
                            _SYS, _collector_prompt("a", brand, hint, schema, registry, corrections),
                            16000, _ev(log, f"🟦 Collector A · {brand} ({n}/{len(todo)})"))
                        a = mr.extract_json(a_raw)
                        ga = _ground(a, log, f"Collector A · {brand}")
                        a.update(entity=brand, collector="A")
                        _save(ar / f"{stem}_A.json", a)  # save NOW: a B failure must not cost A's pass
                    if b is None:
                        b_raw, engine = mr.collect(
                            _SYS, _collector_prompt("b", brand, hint, schema, registry, corrections),
                            16000, _ev(log, f"🟩 Collector B · {brand} ({n}/{len(todo)})"))
                        b = mr.extract_json(b_raw)
                        gb = _ground(b, log, f"Collector B · {brand}")
                        b.update(entity=brand, collector="B")
                        _save(ar / f"{stem}_B.json", b)
                    grd = {k: ga.get(k, 0) + gb.get(k, 0) for k in {*ga, *gb}}
                else:
                    if a is None and b is None:
                        log(f"[api] {brand} ({n}/{len(todo)}): collectors A+B in parallel — "
                            f"a research pass takes several minutes…")
                    with ThreadPoolExecutor(max_workers=2) as ex:
                        fa = ex.submit(mr.collect, _SYS,
                                       _collector_prompt("a", brand, hint, schema, registry, corrections),
                                       16000, _ev(log, f"🟦 Collector A · {brand} ({n}/{len(todo)})")
                                       ) if a is None else None
                        fb = ex.submit(mr.collect, _SYS,
                                       _collector_prompt("b", brand, hint, schema, registry, corrections),
                                       16000, _ev(log, f"🟩 Collector B · {brand} ({n}/{len(todo)})")
                                       ) if b is None else None
                        # harvest BOTH futures before re-raising: one failed
                        # collector must not throw away the other's finished pass
                        err = None
                        if fa is not None:
                            try:
                                a_raw, engine = fa.result()
                                a = mr.extract_json(a_raw)
                                a.update(entity=brand, collector="A")
                                _save(ar / f"{stem}_A.json", a)
                            except Exception as e:
                                err = e
                        if fb is not None:
                            try:
                                b_raw, engine = fb.result()
                                b = mr.extract_json(b_raw)
                                b.update(entity=brand, collector="B")
                                _save(ar / f"{stem}_B.json", b)
                            except Exception as e:
                                err = err or e
                        if err is not None:
                            raise err
                log(f"[api] {brand}: collectors done in {int(time.time() - t0)}s; verifier…")
                v_raw, _ = mr.verify(_SYS, _verifier_prompt(a, b, schema, corrections),
                                     escalate=False,
                                     on_event=_ev(log, f"🟨 Verifier · {brand} — merging A+B"))
                rec = mr.extract_json(v_raw)
                rec["entity"] = brand
                _save(ar / f"{stem}_record.json", rec)
                runs._event(run_dir, "api_company", brand=brand, engine=engine,
                            seconds=int(time.time() - t0),
                            **({"resumed": have} if have else {}), **grd)
                done.append(brand)
            except Exception as ex_err:                      # keep the batch alive
                failed.append(f"{brand} ({type(ex_err).__name__}: {str(ex_err)[:120]})")
                runs._event(run_dir, "api_company_failed", brand=brand,
                            error=str(ex_err)[:300])
                log(f"[api] {brand} FAILED after {int(time.time() - t0)}s — continuing")
        summary = f"researched {len(done)}/{len(todo)}: {', '.join(done) or '—'}"
        if failed:
            summary += f" · FAILED: {'; '.join(failed)} (press ⚡ again to retry)"
        if web_tools.QUOTA_EXHAUSTED:
            summary += _QUOTA_SUMMARY
        return summary

    # step 3 — repair rejected records (after deterministic web-free healing)
    runs.salvage_records(run_dir)
    fixes = runs.autofix_records(run_dir)
    if fixes:
        log(f"[autofix] {len(fixes)} record(s) healed without web research: "
            + "; ".join(f"{k} ({len(v)})" for k, v in fixes.items()))
    g = runs.run_gate(run_dir)
    from collections import Counter
    code_hist = Counter(i["code"] for e in g["rejected"]
                        for i in e["issues"] if i["severity"] == "reject")
    runs._event(run_dir, "gate", accepted=len(g["accepted"]),
                rejected=len(g["rejected"]),
                **({"codes": dict(code_hist)} if code_hist else {}))
    if g["rejected"]:
        fixed, manual, failed = [], [], []
        for e in g["rejected"][:batch]:
            issues = [i for i in e["issues"] if i["severity"] == "reject"]
            a = gate._load(ar / f"{e['stem']}_A.json") or {}
            b = gate._load(ar / f"{e['stem']}_B.json") or {}

            # livelock guard: stop when attempts run out OR the last two
            # repairs left the exact same failures — then blank the evidence
            # fields we cannot source and flag them `unresolved:` instead of
            # burning more tool calls on the same wall
            sig = ",".join(sorted(f"{i['field']}:{i['code']}" for i in issues))
            sigs = _repair_sigs(run_dir, e["entity"])
            if len(sigs) >= _REPAIR_LIMIT or sigs[-2:] == [sig, sig]:
                blanked = _blank_unresolved(
                    e["record"], issues,
                    "не подтверждено после повторных repair-проходов"
                    + (" (поисковая квота исчерпана)" if web_tools.QUOTA_EXHAUSTED else ""))
                if blanked:
                    _save(e["path"], e["record"])
                    log(f"[api] {e['entity']}: {len(sigs)} repairs made no "
                        f"progress — blanked {len(blanked)} unsourced field(s) "
                        f"({', '.join(blanked)}), flagged for review")
                    fixed.append(f"{e['entity']} (unresolved fields blanked)")
                else:
                    msg = (f"{e['entity']}: {len(sigs)} repairs made no progress "
                           f"on [{sig}] — manual review needed")
                    log(f"[api] STOP · {msg}")
                    manual.append(msg)
                continue

            # Collector-B failures → fresh B pass + verifier re-merge (a record
            # repair cannot clear them: the gate re-reads the _B.json file)
            b_fail = {i["code"] for i in issues} & _B_CODES
            if b_fail:
                brand = e["entity"]
                if _b_reruns(run_dir, brand, b_fail) >= _B_RERUN_LIMIT:
                    msg = (f"{brand}: {'/'.join(sorted(b_fail))} survived "
                           f"{_B_RERUN_LIMIT} fresh Collector B reruns — manual "
                           f"review needed (inspect {e['stem']}_A/_B.json; no "
                           f"more API calls will be spent on this check)")
                    log(f"[api] STOP · {msg}")
                    manual.append(msg)
                    continue
                log(f"[api] {brand}: {'/'.join(sorted(b_fail))} — rerunning "
                    f"Collector B from scratch, then re-merging…")
                b_raw, engine = mr.collect(
                    _SYS, _collector_prompt("b", brand, "", schema, registry, corrections),
                    16000, _ev(log, f"🟩 Collector B (rerun) · {brand}"))
                b = mr.extract_json(b_raw)
                grd = _ground(b, log, f"Collector B rerun · {brand}")
                b.update(entity=brand, collector="B")
                _save(ar / f"{e['stem']}_B.json", b)
                v_raw, _ = mr.verify(_SYS, _verifier_prompt(a, b, schema, corrections),
                                     escalate=False,
                                     on_event=_ev(log, f"🟨 Verifier · {brand} — re-merging A+B"))
                rec = mr.extract_json(v_raw)
                rec["entity"] = brand
                _save(e["path"], rec)
                runs._event(run_dir, "api_collector_b_rerun", brand=brand,
                            codes=",".join(sorted(b_fail)), engine=engine, **grd)
                fixed.append(f"{brand} (B rerun)")
                continue

            prompt = (
                f"Repair ONE record for «{e['entity']}» (market: {meta['market']}).\n"
                f"Failed checks:\n"
                + "\n".join(f"- {i['field']} [{i['code']}]: {i['reason']}" for i in issues)
                + f"\n\nHints per code:\n"
                + "\n".join(f"- {c}: {gate._HINTS.get(c, '')}"
                            for c in sorted({i['code'] for i in issues}))
                + "\n\nCurrent record:\n```json\n"
                + json.dumps(e["record"], ensure_ascii=False) + "\n```\n"
                + "Collector A:\n```json\n" + json.dumps(a, ensure_ascii=False) + "\n```\n"
                + "Collector B:\n```json\n" + json.dumps(b, ensure_ascii=False) + "\n```\n\n"
                + "Fix ONLY the failed fields (re-research via web search where needed); "
                + "keep every other field exactly as-is. The record must remain the full "
                + "A+B merge. Return ONLY the corrected record JSON."
                + f" Prose in {meta['output_language']}; money as «N млн ₽»."
                + (f" Allowed segments: {', '.join(segments)}." if segments else ""))
            log(f"[api] repair {e['entity']} ({len(issues)} issues)…")
            import time as _t
            t0 = _t.time()
            try:                                 # one hung/failed repair must
                raw, engine = mr.collect(        # not block the rest of the batch
                    _SYS, prompt, on_event=_ev(log, f"🔧 Repair · {e['entity']}"))
                rec = mr.extract_json(raw)
                # repair audits ONLY the fields the model was asked to fix — the
                # rest keep sources grounded in their original research pass
                grd = _ground(rec, log, f"Repair · {e['entity']}",
                              only_fields={i["field"] for i in issues})
                rec["entity"] = e["entity"]
                _save(e["path"], rec)
                runs._event(run_dir, "api_repair", brand=e["entity"],
                            engine=engine, sig=sig,
                            seconds=int(_t.time() - t0), **grd)
                fixed.append(e["entity"])
            except Exception as ex_err:
                failed.append(f"{e['entity']} ({type(ex_err).__name__}: "
                              f"{str(ex_err)[:120]})")
                runs._event(run_dir, "api_repair_failed", brand=e["entity"],
                            error=str(ex_err)[:300])
                log(f"[api] repair {e['entity']} FAILED — continuing with the "
                    f"next company")
        summary = f"repaired {len(fixed)}: {', '.join(fixed) or '—'} — re-gate via Next prompt"
        if failed:
            summary += f" · FAILED: {'; '.join(failed)} (press ⚡ again to retry)"
        if manual:
            summary += f" · MANUAL REVIEW: {'; '.join(manual)}"
        if web_tools.QUOTA_EXHAUSTED:
            summary += _QUOTA_SUMMARY
        return summary

    return f"all {len(g['accepted'])} records accepted — build the Excel"


def run_next_qual_step(run_dir: Path, batch: int = 2, provider: str | None = None,
                       log=print) -> str:
    """Execute the qual track's current step via API (design work — no browsing).
    Mirrors run_next_step: pending one-pagers → repairs → final report."""
    import time
    from . import onepager
    if provider:
        mr.set_mode(provider)
    meta_run = runs._load_meta(run_dir)
    qmeta = onepager.load_meta(run_dir)
    if not qmeta.get("research_goal") or not qmeta.get("companies"):
        raise SystemExit("Qual track not set up — enter the research goal and "
                         "select companies first.")
    q = onepager.gate_qual(run_dir)
    g = q["records_gate"]
    rec_by_entity = {e["entity"]: e for e in g["accepted"]}
    qd = onepager.qual_dir(run_dir)

    if q["pending"]:
        todo = [b for b in q["pending"] if b in rec_by_entity][:batch]
        if not todo:
            raise SystemExit("Selected companies have no gate-accepted records — "
                             "finish the quantitative repair loop first.")
        done, failed = [], []
        for n, brand in enumerate(todo, 1):
            e = rec_by_entity[brand]
            t0 = time.time()
            try:
                log(f"[qual-api] {brand} ({n}/{len(todo)}): designing the one-pager…")
                prompt = onepager.build_qual_prompt(run_dir, [e], meta_run, qmeta, g)
                prompt += (f"\n\n## API MODE OVERRIDE\nYou have no repo access. Return "
                           f"ONLY the one-pager JSON object for «{brand}» — no prose, "
                           f"no file operations.")
                raw, engine = mr.verify(_SYS_QUAL, prompt, escalate=False, max_tokens=16000,
                                        on_event=_ev(log, f"📝 One-pager · {brand} ({n}/{len(todo)})"))
                op = mr.extract_json(raw)
                op["entity"] = brand
                _save(qd / f"{e['stem']}_onepager.json", op)
                runs._event(run_dir, "qual_api_onepager", brand=brand, engine=engine,
                            seconds=int(time.time() - t0))
                done.append(brand)
            except Exception as ex_err:
                failed.append(f"{brand} ({type(ex_err).__name__}: {str(ex_err)[:120]})")
                log(f"[qual-api] {brand} FAILED after {int(time.time() - t0)}s — continuing")
        summary = f"designed {len(done)}/{len(todo)}: {', '.join(done) or '—'}"
        if failed:
            summary += f" · FAILED: {'; '.join(failed)} (press ⚡ again to retry)"
        return summary

    if q["rejected"]:
        fixed = []
        for e in q["rejected"][:batch]:
            issues = [i for i in e["issues"] if i["severity"] == "reject"]
            log(f"[qual-api] repair {e['entity']} ({len(issues)} issues)…")
            prompt = (
                f"Repair ONE qualitative one-pager for «{e['entity']}» "
                f"(market: {meta_run['market']}; research goal: {qmeta['research_goal']}).\n"
                f"Failed checks:\n"
                + "\n".join(f"- {i['field']} [{i['code']}]: {i['reason']}" for i in issues)
                + "\n\nCurrent one-pager:\n```json\n"
                + json.dumps(e["op"], ensure_ascii=False) + "\n```\n"
                + "Company record — the ONLY permitted fact base:\n```json\n"
                + json.dumps(e["record"], ensure_ascii=False) + "\n```\n\n"
                + "Fix ONLY the failed parts; keep everything else. Facts cite "
                + "source_fields from the record; hypotheses carry validated_if; "
                + f"counts/enums per the checks above. Prose in "
                + f"{meta_run['output_language']}. Return ONLY the corrected JSON.")
            raw, engine = mr.verify(_SYS_QUAL, prompt, escalate=False, max_tokens=16000,
                                    on_event=_ev(log, f"🔧 Qual repair · {e['entity']}"))
            op = mr.extract_json(raw)
            op["entity"] = e["entity"]
            _save(e["path"], op)
            fixed.append(e["entity"])
        return f"repaired {len(fixed)}: {', '.join(fixed)} — re-gate via Next/⚡"

    if onepager.report_is_stale(run_dir):
        onepager.build_report(run_dir)
    return (f"all {len(q['accepted'])} one-pagers accepted — report ready: "
            f"{onepager.report_path(run_dir).name}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the next research step via API.")
    ap.add_argument("run_id")
    ap.add_argument("--batch", type=int, default=3)
    ap.add_argument("--provider", choices=("gpt", "claude", "grok", "deepseek"), default=None)
    ap.add_argument("--qual", action="store_true", help="drive the qualitative track")
    args = ap.parse_args()
    fn = run_next_qual_step if args.qual else run_next_step
    print(fn(runs.run_dir_for(args.run_id), args.batch, args.provider))


if __name__ == "__main__":
    main()
