# Smoke check — DeepSeek quantitative research (app web tools)

Prerequisites: `DEEPSEEK_API_KEY` and `SEARCH_API_KEY` in `.env`
(Search key: free tier at https://api-dashboard.search.brave.com), and
`pip install -r requirements.txt` (adds `requests`, `beautifulsoup4`).

## 0 · Unit tests (no network, no keys)

    python -m unittest tests.test_web_tools -v

## 1 · Discovery + one company on DeepSeek

    # create a small run in the app (or reuse an existing run id from logs/)
    python -m src.api_runner <run_id> --batch 1 --provider deepseek

Expect in the console:
- banner `mode=deepseek  research=deepseek-chat (app web tools: search+fetch) …`
- live status lines `🔎 searching: …` / `📄 reading: …` from the app's own tools;
- `[grounding] …` lines only if the model cited a URL its tools never saw
  (each stripped source is listed with its full URL).

Press ⚡ again (or re-run the command) for the research step of the first
company — Collector A runs, then Collector B (sequential on DeepSeek by design).

## 2 · Gate — no new issue codes

In the app, «Next prompt ▶» runs the gate; or check the gate report for the run.
Any grounding-stripped field must appear under the EXISTING `unsourced` code
(reject → normal repair loop). There are no new gate codes.

Also check `logs/<run_id>/events.jsonl`: the `api_company` event now carries
`tool_calls` and `grounding_affected` for DeepSeek runs.

## 3 · Control — other providers unchanged

    python -m src.api_runner <run_id2> --batch 1 --provider gpt

Expect identical behavior to before this change: parallel Collectors A+B,
no `[grounding]` lines, no `tool_calls` in events.
