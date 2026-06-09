#!/usr/bin/env python3
"""Run trigger evaluation for a skill description.

gpt-env port: the upstream creates a `.claude/commands/<id>.md` stub and
runs `claude -p` to see whether the LLM picks up the skill. Pyodide has no
filesystem-as-skill-registry and no subprocess, so we instead send
`/api/v1/chat` with `available_skills: [{the one skill under test}]` and
detect the trigger by looking for a `run_skill_script` tool-call with our
synthetic dir_name in the response.
"""

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

from scripts.utils import parse_skill_md

CHAT_ENDPOINT = "/gpt/backend/api/v1/chat"


async def run_single_query(
    query: str,
    skill_name: str,
    skill_description: str,
    timeout: int,
    model: str | None = None,
    provider: str = "claude",
) -> bool:
    """Test whether the description causes the LLM to trigger this skill.

    Posts the query to /api/v1/chat with a single-entry `available_skills`
    list containing a unique synthetic dir_name (to avoid colliding with
    the user's installed skills). Triggered := the response indicates the
    LLM called `run_skill_script` with our dir_name. We never let the
    script actually run — the first response is enough.
    """
    import pyodide.http  # type: ignore[import-not-found]
    from js import window  # type: ignore[import-not-found]

    clean_name = f"{skill_name}-skill-{uuid.uuid4().hex[:8]}"

    headers = {"Content-Type": "application/json"}
    tok = getattr(getattr(window, "authManager", None), "token", None)
    if tok:
        headers["Authorization"] = f"Bearer {tok}"

    body = {
        "message": query,
        "provider": provider,
        "available_skills": [{
            "dir_name": clean_name,
            "description": skill_description,
            # Dummy script path — required by the backend validator but
            # never executed; we read only the first response.
            "scripts": ["scripts/audit.py"],
        }],
        # Restrict the LLM to the skill-caller tool so unrelated tools
        # can't muddy the trigger signal.
        "tools": ["run_skill_script"],
    }
    if model:
        body["model"] = model

    try:
        resp = await pyodide.http.pyfetch(
            CHAT_ENDPOINT,
            method="POST",
            headers=headers,
            body=json.dumps(body),
        )
        text = await resp.string()
        data = json.loads(text)
    except Exception:
        return False

    # Path 1: backend short-circuited the recursive tool loop and
    # returned the pending client-side tool call without executing it.
    if data.get("pending_client_tool_call"):
        for call in (data.get("pending_tool_calls") or []):
            if call.get("name") != "run_skill_script":
                continue
            inp = call.get("input") or {}
            if inp.get("dir_name") == clean_name:
                return True

    # Path 2: the backend included a `functions_called` list (some
    # providers report tool invocations there even when not
    # short-circuited).
    for fc in (data.get("functions_called") or []):
        name = fc if isinstance(fc, str) else (fc.get("name") if isinstance(fc, dict) else None)
        if name == "run_skill_script":
            inp = (fc.get("input") if isinstance(fc, dict) else None) or {}
            if inp.get("dir_name") == clean_name:
                return True

    return False


async def run_eval(
    eval_set: list[dict],
    skill_name: str,
    description: str,
    num_workers: int,
    timeout: int,
    runs_per_query: int = 1,
    trigger_threshold: float = 0.5,
    model: str | None = None,
    provider: str = "claude",
) -> dict:
    """Run the full eval set and return results.

    Concurrency is bounded by an asyncio.Semaphore set to num_workers.
    The upstream used ProcessPoolExecutor, which isn't available in
    Pyodide; asyncio.gather + semaphore gives equivalent throughput for
    I/O-bound work (every call is a fetch).
    """
    sem = asyncio.Semaphore(max(1, num_workers))

    async def _one(query: str) -> bool:
        async with sem:
            try:
                return await run_single_query(
                    query, skill_name, description, timeout, model, provider
                )
            except Exception as e:
                print(f"Warning: query failed: {e}", file=sys.stderr)
                return False

    task_items: list[dict] = []
    tasks: list = []
    for item in eval_set:
        for _ in range(runs_per_query):
            task_items.append(item)
            tasks.append(_one(item["query"]))

    flat = await asyncio.gather(*tasks)

    query_triggers: dict[str, list[bool]] = {}
    query_items: dict[str, dict] = {}
    for item, triggered in zip(task_items, flat):
        query = item["query"]
        query_items[query] = item
        query_triggers.setdefault(query, []).append(bool(triggered))

    results = []
    for query, triggers in query_triggers.items():
        item = query_items[query]
        trigger_rate = sum(triggers) / len(triggers)
        should_trigger = item["should_trigger"]
        if should_trigger:
            did_pass = trigger_rate >= trigger_threshold
        else:
            did_pass = trigger_rate < trigger_threshold
        results.append({
            "query": query,
            "should_trigger": should_trigger,
            "trigger_rate": trigger_rate,
            "triggers": sum(triggers),
            "runs": len(triggers),
            "pass": did_pass,
        })

    passed = sum(1 for r in results if r["pass"])
    total = len(results)

    return {
        "skill_name": skill_name,
        "description": description,
        "results": results,
        "summary": {
            "total": total,
            "passed": passed,
            "failed": total - passed,
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Run trigger evaluation for a skill description")
    parser.add_argument("--eval-set", required=True, help="Path to eval set JSON file")
    parser.add_argument("--skill-path", required=True, help="Path to skill directory")
    parser.add_argument("--description", default=None, help="Override description to test")
    parser.add_argument("--num-workers", type=int, default=10, help="Number of parallel workers")
    parser.add_argument("--timeout", type=int, default=30, help="Timeout per query in seconds")
    parser.add_argument("--runs-per-query", type=int, default=3, help="Number of runs per query")
    parser.add_argument("--trigger-threshold", type=float, default=0.5, help="Trigger rate threshold")
    parser.add_argument("--model", default=None, help="Model to use (default: provider's configured default)")
    parser.add_argument("--provider", default="claude", help="LLM provider (claude, openai, gemini, grok, deepseek, kimi)")
    parser.add_argument("--verbose", action="store_true", help="Print progress to stderr")
    args = parser.parse_args()

    eval_set = json.loads(Path(args.eval_set).read_text())
    skill_path = Path(args.skill_path)

    if not (skill_path / "SKILL.md").exists():
        print(f"Error: No SKILL.md found at {skill_path}", file=sys.stderr)
        sys.exit(1)

    name, original_description, content = parse_skill_md(skill_path)
    description = args.description or original_description

    if args.verbose:
        print(f"Evaluating: {description}", file=sys.stderr)

    output = asyncio.run(run_eval(
        eval_set=eval_set,
        skill_name=name,
        description=description,
        num_workers=args.num_workers,
        timeout=args.timeout,
        runs_per_query=args.runs_per_query,
        trigger_threshold=args.trigger_threshold,
        model=args.model,
        provider=args.provider,
    ))

    if args.verbose:
        summary = output["summary"]
        print(f"Results: {summary['passed']}/{summary['total']} passed", file=sys.stderr)
        for r in output["results"]:
            status = "PASS" if r["pass"] else "FAIL"
            rate_str = f"{r['triggers']}/{r['runs']}"
            print(f"  [{status}] rate={rate_str} expected={r['should_trigger']}: {r['query'][:70]}", file=sys.stderr)

    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
