#!/usr/bin/env python3
"""
Fetch the daily WA commercial construction recruitment brief from Grok (xAI),
parse the JSON response, and write it to data/latest.json + data/history/<date>.json.

Two Grok calls per run:

  1. Daily brief (prompts/daily_brief.md) — broad web + recent X posts. Produces
     top_3_today, hot_projects, hiring_signals, watchlist_status. Uses
     unrestricted web_search and an x_search restricted to the last 14 days for
     freshness.

  2. Pipeline (prompts/pipeline.md) — pre-hiring signals (DA approvals, ASX
     announcements, tender awards, anchor leases). Uses web_search restricted
     to a curated set of high-signal Australian property/business domains so
     the model stops chasing low-quality blog reposts. Produces the `pipeline`
     array.

The two responses are merged into a single brief JSON.

Uses the official xai-sdk package (gRPC). The SDK insulates us from xAI's
frequent REST schema churn.

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
PIPELINE_PROMPT_PATH = REPO_ROOT / "prompts" / "pipeline.md"
WATCHLIST_PATH = DATA_DIR / "watchlist.json"

XAI_API_KEY = os.environ.get("XAI_API_KEY")
XAI_MODEL = os.environ.get("XAI_MODEL", "grok-4-latest")

AWST = timezone(timedelta(hours=8))

# Curated high-signal Australian property/business sources for the pipeline call.
# Limit is small on purpose — Grok's web_search ranks better when the search
# space is curated. Edit this list to taste.
PIPELINE_DOMAINS = [
    "businessnews.com.au",      # WA business news, often first-print on local deals
    "theurbandeveloper.com",    # Australia-wide property dev news with WA section
    "afr.com",                  # AFR commercial property
    "asx.com.au",               # ASX listed-builder/developer announcements
    "wa.gov.au",                # WA Government project announcements (planning, JTSI)
]


def _render_prompt(template_path: Path) -> str:
    template = template_path.read_text()
    watchlist = json.loads(WATCHLIST_PATH.read_text())["builders"]
    rendered = "\n".join(f"  - {b}" for b in watchlist)
    today = datetime.now(AWST).strftime("%Y-%m-%d")
    return template.replace("{{WATCHLIST}}", rendered).replace("YYYY-MM-DD", today)


def _extract_telemetry(response) -> tuple[list, dict]:
    """Pull citations + tool-usage counters off a Response in a way that doesn't
    break if the SDK's proto shape changes.

    Returns: (citations_list, tool_usage_dict)
    """
    citations = list(response.citations or [])

    tool_usage = {}
    sstu = response.server_side_tool_usage
    if sstu is not None:
        # `server_side_tool_usage` exposes per-tool counters as nested submessages
        # like web_search { num_calls: 8 }. Walk fields generically.
        try:
            for field in sstu.DESCRIPTOR.fields:
                val = getattr(sstu, field.name, None)
                if val is None:
                    continue
                # Scalar counter
                if isinstance(val, (int, float)) and val:
                    tool_usage[field.name] = val
                # Nested message with a counter inside
                elif hasattr(val, "DESCRIPTOR"):
                    nested = {}
                    for sub in val.DESCRIPTOR.fields:
                        sv = getattr(val, sub.name, 0)
                        if isinstance(sv, (int, float)) and sv:
                            nested[sub.name] = sv
                    if nested:
                        tool_usage[field.name] = nested
        except Exception:
            # Best-effort — never fail the run because telemetry shape changed.
            pass

    return citations, tool_usage


def _parse_json_content(content: str) -> dict:
    content = (content or "").strip()
    # Models occasionally wrap JSON in ```json fences despite response_format='json_object'.
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.MULTILINE)
    return json.loads(content)


def call_daily_brief(client: Client, prompt: str) -> tuple[dict, list, dict]:
    """The main daily brief — broad web + recent X posts."""
    fourteen_days_ago = datetime.now(AWST) - timedelta(days=14)

    chat = client.chat.create(
        model=XAI_MODEL,
        tools=[
            web_search(),                              # general web + news pages
            x_search(from_date=fourteen_days_ago),     # X posts, last 14 days only
        ],
        response_format="json_object",
        temperature=0.2,
    )
    chat.append(system(
        "You are a precise data-returning assistant. Always return valid JSON exactly "
        "matching the requested schema. No prose. You MUST use web_search and x_search "
        "for every fact — never invent companies, projects, contacts, or URLs. If you "
        "cannot find a fact via search, omit that item rather than guessing. Strictly "
        "enforce the recency cutoffs described in the user prompt."
    ))
    chat.append(user(prompt))

    response = chat.sample()
    parsed = _parse_json_content(response.content)
    citations, tool_usage = _extract_telemetry(response)
    return parsed, citations, tool_usage


def call_pipeline(client: Client, prompt: str) -> tuple[dict, list, dict]:
    """The pipeline call — pre-hiring signals from curated high-signal domains."""
    chat = client.chat.create(
        model=XAI_MODEL,
        tools=[
            web_search(allowed_domains=PIPELINE_DOMAINS),
        ],
        response_format="json_object",
        temperature=0.2,
    )
    chat.append(system(
        "You are a precise data-returning assistant. Always return valid JSON exactly "
        "matching the requested schema. No prose. You MUST use web_search for every "
        "fact — never invent. Only return items where you can cite a source from the "
        "allowed domains, dated within the last 90 days. An empty pipeline array is "
        "preferred over padded/stale items."
    ))
    chat.append(user(prompt))

    response = chat.sample()
    parsed = _parse_json_content(response.content)
    citations, tool_usage = _extract_telemetry(response)
    return parsed, citations, tool_usage


def call_grok() -> dict:
    if not XAI_API_KEY:
        raise SystemExit("XAI_API_KEY env var is not set. Add it as a GitHub Actions secret.")

    client = Client(api_key=XAI_API_KEY, timeout=180)

    print("  → Daily brief call...", flush=True)
    daily_prompt = _render_prompt(PROMPT_PATH)
    daily, daily_cites, daily_usage = call_daily_brief(client, daily_prompt)
    print(f"    citations={len(daily_cites)} tool_usage={daily_usage}", flush=True)

    print("  → Pipeline call (high-signal domains)...", flush=True)
    pipeline_prompt = _render_prompt(PIPELINE_PROMPT_PATH)
    try:
        pipeline_resp, pipe_cites, pipe_usage = call_pipeline(client, pipeline_prompt)
        pipeline_items = pipeline_resp.get("pipeline", []) or []
        print(f"    citations={len(pipe_cites)} tool_usage={pipe_usage} items={len(pipeline_items)}", flush=True)
    except Exception as e:
        # Don't fail the whole run if the pipeline call has issues — the main
        # brief is still valuable.
        print(f"    Pipeline call failed: {e}", flush=True)
        pipeline_items, pipe_cites, pipe_usage = [], [], {}

    # Merge: take the daily brief as the base, attach the pipeline array.
    merged = dict(daily)
    merged["pipeline"] = pipeline_items

    # Combined grounding telemetry — both calls' citations, both calls' tool usage.
    all_citations = daily_cites + pipe_cites
    grounded = bool(all_citations) or bool(daily_usage) or bool(pipe_usage)

    merged["_grok_search_mode"] = "agent_tools_sdk" if grounded else "no_live_search"
    merged["_grok_grounded"] = grounded
    merged["_grok_citations"] = all_citations
    merged["_grok_tool_usage"] = {
        "daily_brief": daily_usage,
        "pipeline": pipe_usage,
    }
    return merged


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
    print(f"Calling Grok ({XAI_MODEL}) via xai-sdk...", flush=True)
    brief = call_grok()

    print(f"Combined: mode={brief['_grok_search_mode']} "
          f"total_citations={len(brief['_grok_citations'])} "
          f"pipeline_items={len(brief.get('pipeline', []))}", flush=True)

    print("Saving brief...", flush=True)
    today = save(brief)
    print(f"Done. Brief for {today} written.")


if __name__ == "__main__":
    main()
