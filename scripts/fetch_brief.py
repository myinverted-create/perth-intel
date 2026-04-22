#!/usr/bin/env python3
"""
Fetch the daily WA commercial construction recruitment brief from Grok (xAI),
parse the JSON response, and write it to data/latest.json + data/history/<date>.json.

Uses the official xai-sdk package (gRPC). The SDK insulates us from xAI's
frequent REST schema churn: when they renamed/retired Live Search and moved
search to the new Agent Tools API, the SDK just kept working.

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

from xai_sdk import Client
from xai_sdk.chat import system, user
from xai_sdk.tools import web_search, x_search

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
HISTORY_DIR = DATA_DIR / "history"
PROMPT_PATH = REPO_ROOT / "prompts" / "daily_brief.md"
WATCHLIST_PATH = DATA_DIR / "watchlist.json"

XAI_API_KEY = os.environ.get("XAI_API_KEY")
XAI_MODEL = os.environ.get("XAI_MODEL", "grok-4-latest")

AWST = timezone(timedelta(hours=8))


def load_prompt() -> str:
    template = PROMPT_PATH.read_text()
    watchlist = json.loads(WATCHLIST_PATH.read_text())["builders"]
    rendered = "\n".join(f"  - {b}" for b in watchlist)
    today = datetime.now(AWST).strftime("%Y-%m-%d")
    return template.replace("{{WATCHLIST}}", rendered).replace("YYYY-MM-DD", today)


def call_grok(prompt: str) -> dict:
    if not XAI_API_KEY:
        raise SystemExit("XAI_API_KEY env var is not set. Add it as a GitHub Actions secret.")

    client = Client(api_key=XAI_API_KEY, timeout=180)

    # Built-in Agent Tools — Grok runs these server-side and returns the
    # results inline. Both are optional from the model's POV; the system prompt
    # below makes their use mandatory for any fact in the brief.
    chat = client.chat.create(
        model=XAI_MODEL,
        tools=[
            web_search(),  # general web + news pages
            x_search(),    # X/Twitter posts (good for early hiring/handshake signals)
        ],
        response_format="json_object",
        temperature=0.2,
    )

    chat.append(system(
        "You are a precise data-returning assistant. Always return valid JSON exactly "
        "matching the requested schema. No prose. You MUST use the web_search and x_search "
        "tools to ground every fact — never invent companies, projects, contacts, or URLs. "
        "If you cannot find a fact via search, omit that item rather than guessing."
    ))
    chat.append(user(prompt))

    response = chat.sample()

    # The model is supposed to return JSON because of response_format='json_object',
    # but occasionally wraps it in ```json fences. Strip defensively.
    content = (response.content or "").strip()
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.MULTILINE)
    parsed = json.loads(content)

    # Citations come back as a list of URL strings (or {url, title} dicts depending
    # on the model). Normalise to whatever the dashboard already handles — it accepts
    # both shapes.
    citations = list(response.citations or [])

    # `server_side_tool_usage` tells us how many times each Agent Tool fired.
    # If the model decided to answer purely from training data, both counters
    # will be zero and we should flag the brief as ungrounded so the dashboard
    # shows the red "no live search" badge.
    tool_usage = {}
    sstu = response.server_side_tool_usage
    if sstu is not None:
        # The proto exposes counters per tool. Convert to a simple dict.
        try:
            for field in sstu.DESCRIPTOR.fields:
                val = getattr(sstu, field.name, 0)
                if isinstance(val, (int, float)) and val:
                    tool_usage[field.name] = val
        except Exception:
            # Best-effort — don't fail the whole run because telemetry shape changed.
            pass

    grounded = bool(citations) or bool(tool_usage)

    parsed["_grok_search_mode"] = "agent_tools_sdk" if grounded else "no_live_search"
    parsed["_grok_grounded"] = grounded
    parsed["_grok_citations"] = citations
    parsed["_grok_tool_usage"] = tool_usage
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

    print(f"Calling Grok ({XAI_MODEL}) via xai-sdk...", flush=True)
    brief = call_grok(prompt)

    mode = brief.get("_grok_search_mode")
    cites = len(brief.get("_grok_citations", []))
    usage = brief.get("_grok_tool_usage", {})
    print(f"Grounding: mode={mode} citations={cites} tool_usage={usage}", flush=True)

    print("Saving brief...", flush=True)
    today = save(brief)
    print(f"Done. Brief for {today} written.")


if __name__ == "__main__":
    main()
