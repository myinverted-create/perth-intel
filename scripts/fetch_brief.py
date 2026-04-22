#!/usr/bin/env python3
"""
Fetch the daily WA commercial construction recruitment brief from Grok (xAI),
parse the JSON response, and write it to data/latest.json + data/history/<date>.json.

Required env vars:
  XAI_API_KEY   — your xAI API key (set as a GitHub Actions secret)

Optional env vars:
  XAI_MODEL     — defaults to "grok-4-latest". Override if xAI renames models.
"""

import os
import sys
import json
import glob
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
HISTORY_DIR = DATA_DIR / "history"
PROMPT_PATH = REPO_ROOT / "prompts" / "daily_brief.md"
WATCHLIST_PATH = DATA_DIR / "watchlist.json"

XAI_API_KEY = os.environ.get("XAI_API_KEY")
XAI_MODEL = os.environ.get("XAI_MODEL", "grok-4-latest")
XAI_ENDPOINT = "https://api.x.ai/v1/chat/completions"

AWST = timezone(timedelta(hours=8))


def load_prompt() -> str:
    template = PROMPT_PATH.read_text()
    watchlist = json.loads(WATCHLIST_PATH.read_text())["builders"]
    rendered = "\n".join(f"  - {b}" for b in watchlist)
    today = datetime.now(AWST).strftime("%Y-%m-%d")
    return template.replace("{{WATCHLIST}}", rendered).replace("YYYY-MM-DD", today)


def _post(payload: dict) -> dict:
    resp = requests.post(
        XAI_ENDPOINT,
        headers={
            "Authorization": f"Bearer {XAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=180,
    )
    if resp.status_code >= 400:
        # surface the body so we can read what xAI is complaining about
        raise requests.HTTPError(f"{resp.status_code} {resp.reason}: {resp.text}", response=resp)
    return resp.json()


def call_grok(prompt: str) -> dict:
    if not XAI_API_KEY:
        raise SystemExit("XAI_API_KEY env var is not set. Add it as a GitHub Actions secret.")

    base_payload = {
        "model": XAI_MODEL,
        "messages": [
            {"role": "system", "content": "You are a precise data-returning assistant. Always return valid JSON exactly matching the requested schema. No prose. You MUST use live web/news/x search to ground every fact — never invent companies, projects, contacts, or URLs. If you cannot find a fact via live search, omit that item rather than guessing."},
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
    }

    # xAI's Live Search is the documented way to give Grok web/news/x access.
    # It is enabled via a top-level `search_parameters` block, NOT via the
    # `tools` field. The previous tools=[{"type":"web_search"},...] form was
    # rejected by the API ("expected `function` or `live_search`").
    #
    # Strategy:
    #   1. Preferred: search_parameters with web + news + x sources, mode=on.
    #   2. Fallback: tools=[{"type":"live_search"}] in case the account/model
    #      uses the agent-tools form instead.
    #   3. Last resort: no live search (will be flagged in the brief so the
    #      dashboard can warn the user the data isn't grounded).
    #
    # Override either via env vars if xAI changes the schema again.
    sp_override = os.environ.get("XAI_SEARCH_PARAMETERS_JSON")
    if sp_override:
        search_parameters = json.loads(sp_override)
    else:
        search_parameters = {
            "mode": "on",
            "return_citations": True,
            "max_search_results": 20,
            "sources": [
                {"type": "web"},
                {"type": "news"},
                {"type": "x"},
            ],
        }

    tools_override = os.environ.get("XAI_TOOLS_JSON")
    if tools_override:
        live_tools = json.loads(tools_override)
    else:
        live_tools = [{"type": "live_search"}]

    attempts = [
        ("search_parameters", search_parameters, None),
        ("live_search_tool", None, live_tools),
        ("no_live_search", None, None),
    ]

    body = None
    last_err = None
    used_label = None
    for label, sp, tools in attempts:
        payload = dict(base_payload)
        if sp is not None:
            payload["search_parameters"] = sp
        if tools is not None:
            payload["tools"] = tools
        try:
            body = _post(payload)
            used_label = label
            print(f"OK with mode='{label}'", flush=True)
            break
        except requests.HTTPError as e:
            last_err = e
            print(f"Attempt failed (mode='{label}'): {e}", flush=True)
            continue

    if body is None:
        raise last_err or RuntimeError("All Grok attempts failed.")

    msg = body["choices"][0]["message"]
    content = msg["content"]
    # Some models occasionally wrap JSON in ```json fences despite response_format.
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.MULTILINE)
    parsed = json.loads(content)

    # Surface citations + which mode actually worked. The dashboard uses these
    # to show "live search ON" badges and a list of sources Grok consulted.
    citations = msg.get("citations") or body.get("citations") or []
    parsed["_grok_search_mode"] = used_label
    parsed["_grok_citations"] = citations
    parsed["_grok_grounded"] = used_label != "no_live_search"
    return parsed


def save(brief: dict) -> str:
    today_dt = datetime.now(AWST)
    today = today_dt.strftime("%Y-%m-%d")
    brief["date"] = today
    brief["generated_at"] = today_dt.isoformat()

    HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    (DATA_DIR / "latest.json").write_text(json.dumps(brief, indent=2, ensure_ascii=False))
    (HISTORY_DIR / f"{today}.json").write_text(json.dumps(brief, indent=2, ensure_ascii=False))

    # Rebuild history index
    dates = sorted(
        [Path(f).stem for f in glob.glob(str(HISTORY_DIR / "*.json"))],
        reverse=True,
    )
    (DATA_DIR / "history-index.json").write_text(
        json.dumps({"dates": dates}, indent=2)
    )

    return today


def main() -> None:
    print("Loading prompt + watchlist...", flush=True)
    prompt = load_prompt()

    print(f"Calling Grok ({XAI_MODEL})...", flush=True)
    try:
        brief = call_grok(prompt)
    except requests.HTTPError as e:
        print(f"xAI HTTP error: {e.response.status_code}\n{e.response.text}", file=sys.stderr)
        raise

    print("Saving brief...", flush=True)
    today = save(brief)
    print(f"Done. Brief for {today} written.")


if __name__ == "__main__":
    main()
