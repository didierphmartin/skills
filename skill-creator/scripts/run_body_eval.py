#!/usr/bin/env python3
"""Body-quality evaluator. Companion to run_eval.py."""
import argparse, asyncio, json, re, sys, uuid
from pathlib import Path
from scripts.utils import parse_skill_md

CHAT_ENDPOINT = "/gpt/backend/api/v1/chat"
AGENT_ENDPOINT = "/gpt/backend/api/v1/agent"


def _is_body_case(case):
    return bool(case.get("expectations") or case.get("must_contain") or case.get("must_not_contain"))


async def _capture_transcript(query, skill_name, skill_description, skill_scripts, model, provider, timeout):
    import pyodide.http
    from js import window
    clean_name = f"{skill_name}-bodyeval-{uuid.uuid4().hex[:8]}"
    headers = {"Content-Type": "application/json"}
    tok = getattr(getattr(window, "authManager", None), "token", None)
    if tok: headers["Authorization"] = f"Bearer {tok}"
    body = {"message": query, "provider": provider,
            "available_skills": [{"dir_name": clean_name, "description": skill_description,
                                   "scripts": skill_scripts or ["scripts/run.py"]}]}
    if model: body["model"] = model
    try:
        resp = await pyodide.http.pyfetch(CHAT_ENDPOINT, method="POST", headers=headers, body=json.dumps(body))
        data = json.loads(await resp.string())
    except Exception as e:
        return f"[transcript-capture-error] {e!r}"
    parts = []
    if data.get("pending_client_tool_call"):
        for call in (data.get("pending_tool_calls") or []):
            parts.append(f"[tool_call] name={call.get('name','?')} input={json.dumps(call.get('input') or {})}")
    for fc in (data.get("functions_called") or []):
        if isinstance(fc, str):
            parts.append(f"[tool_call] name={fc}")
        elif isinstance(fc, dict):
            inp = fc.get("input") or fc.get("arguments") or {}
            inp_str = inp if isinstance(inp, str) else json.dumps(inp)
            parts.append(f"[tool_call] name={fc.get('name','?')} input={inp_str}")
    for key in ("reply", "text", "response", "content", "message"):
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            parts.append(f"[assistant_text] {v}"); break
    if not parts:
        parts.append(f"[raw_response] {json.dumps(data)[:2000]}")
    return "\n\n".join(parts)


def _check_hard_gate(transcript, must_contain, must_not_contain):
    fails = []
    for p in (must_contain or []):
        try:
            if not re.search(p, transcript, re.IGNORECASE | re.DOTALL):
                fails.append(f"missing required: {p!r}")
        except re.error as e: fails.append(f"bad must_contain regex {p!r}: {e}")
    for p in (must_not_contain or []):
        try:
            if re.search(p, transcript, re.IGNORECASE | re.DOTALL):
                fails.append(f"forbidden pattern present: {p!r}")
        except re.error as e: fails.append(f"bad must_not_contain regex {p!r}: {e}")
    return (len(fails) == 0, fails)


async def _judge_softly(query, transcript, expectations, model, provider, timeout):
    if not expectations:
        return {"score": 5, "per_expectation": [], "reasoning": "no soft expectations"}
    system = ("You are an evaluator scoring how well an LLM's response satisfies a list of expectations. "
              "For each expectation, decide PASS or FAIL and quote evidence. Then give an overall score 1-5.\n\n"
              "Respond with ONLY this JSON shape:\n"
              '{\n  "per_expectation": [{"text": "...", "passed": true|false, "evidence": "..."}],\n'
              '  "score": <1-5>,\n  "reasoning": "<one sentence>"\n}')
    user_parts = [f"## Original query\n{query}", f"## Transcript\n{transcript}", "## Expectations"]
    for i, e in enumerate(expectations, 1): user_parts.append(f"{i}. {e}")
    user_msg = "\n\n".join(user_parts)
    import pyodide.http
    from js import window
    headers = {"Content-Type": "application/json"}
    tok = getattr(getattr(window, "authManager", None), "token", None)
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    try:
        resp = await pyodide.http.pyfetch(AGENT_ENDPOINT, method="POST",
            headers=headers,
            body=json.dumps({"prompt": user_msg, "system": system, "model": model, "provider": provider}))
        outer = json.loads(await resp.string())
    except Exception as e:
        return {"score": 0, "per_expectation": [], "reasoning": f"judge call failed: {e!r}"}
    if isinstance(outer, dict) and outer.get("success") is False:
        return {"score": 0, "per_expectation": [], "reasoning": f"judge endpoint error: {outer.get('error', 'unknown')}"}
    raw = outer.get("text", "") if isinstance(outer, dict) else ""
    for c in sorted(re.findall(r"\{[\s\S]*\}", raw), key=len, reverse=True):
        try:
            obj = json.loads(c)
            if isinstance(obj, dict) and "score" in obj: return obj
        except json.JSONDecodeError: continue
    return {"score": 0, "per_expectation": [], "reasoning": "judge returned unparseable response"}


async def run_body_eval(eval_set, skill_path, skill_description, num_workers, timeout, model, provider, soft_threshold=4, runs_per_query=1):
    skill_name, original_description, _ = parse_skill_md(skill_path)
    description = skill_description or original_description
    script_paths = []
    scripts_dir = skill_path / "scripts"
    if scripts_dir.is_dir():
        for f in sorted(scripts_dir.glob("*.py")): script_paths.append(f"scripts/{f.name}")
    if not script_paths: script_paths = ["scripts/run.py"]
    sem = asyncio.Semaphore(max(1, num_workers))
    n_runs = max(1, runs_per_query)

    async def _one(case):
        if not _is_body_case(case):
            return {"query": case.get("query", ""), "skipped": True,
                    "skip_reason": "no expectations / must_contain / must_not_contain"}
        async with sem:
            # Run the case n_runs times to denoise the LLM judge + chat
            # response variance. A case PASSES only if a strict majority of
            # its runs pass — a single noisy response can't flip it. This is
            # the body-side analogue of run_eval.py's trigger_threshold vote.
            runs = []
            for _ in range(n_runs):
                tr = await _capture_transcript(case["query"], skill_name, description, script_paths, model, provider, timeout)
                hp, hf = _check_hard_gate(tr, case.get("must_contain", []), case.get("must_not_contain", []))
                judge = await _judge_softly(case["query"], tr, case.get("expectations", []), model, provider, timeout)
                runs.append({"hard_pass": hp, "hard_failures": hf,
                             "soft_score": judge.get("score", 0),
                             "soft_reasoning": judge.get("reasoning", ""), "transcript": tr})
            hard_votes = sum(1 for r in runs if r["hard_pass"])
            hard_pass = hard_votes > n_runs / 2 if n_runs > 1 else runs[0]["hard_pass"]
            mean_soft = sum(r["soft_score"] for r in runs) / n_runs
            overall = hard_pass and mean_soft >= soft_threshold
            # Representative run for diagnosis: prefer a failing run so the
            # transcript/reasoning shown explains why the case isn't passing.
            rep = next((r for r in runs if not (r["hard_pass"] and r["soft_score"] >= soft_threshold)), runs[0])
            return {"query": case["query"], "skipped": False, "hard_pass": hard_pass, "hard_failures": rep["hard_failures"],
                    "soft_score": round(mean_soft, 2), "soft_reasoning": rep["soft_reasoning"],
                    "overall_pass": overall, "transcript": rep["transcript"],
                    "runs": n_runs, "hard_votes": hard_votes,
                    "soft_scores_all": [r["soft_score"] for r in runs]}

    results = await asyncio.gather(*[_one(c) for c in eval_set])
    body_results = [r for r in results if not r.get("skipped")]
    total = len(body_results)
    op = sum(1 for r in body_results if r["overall_pass"])
    hp = sum(1 for r in body_results if r["hard_pass"])
    avg = (sum(r["soft_score"] for r in body_results) / total) if total else 0
    return {"skill_name": skill_name, "description": description, "soft_threshold": soft_threshold,
            "results": results,
            "summary": {"total_body_cases": total, "skipped_cases": len(results) - total,
                        "overall_passed": op, "hard_passed": hp, "avg_soft_score": round(avg, 2),
                        "strict_all_pass": op == total and total > 0}}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--eval-set", required=True); p.add_argument("--skill-path", required=True)
    p.add_argument("--description", default=None); p.add_argument("--num-workers", type=int, default=5)
    p.add_argument("--timeout", type=int, default=60); p.add_argument("--model", required=True)
    p.add_argument("--provider", default="claude"); p.add_argument("--soft-threshold", type=int, default=4)
    p.add_argument("--runs-per-query", type=int, default=1, help="Run each case N times and majority-vote to denoise (default 1)")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()
    eval_set = json.loads(Path(args.eval_set).read_text())
    skill_path = Path(args.skill_path)
    if not (skill_path / "SKILL.md").exists():
        print(f"No SKILL.md at {skill_path}", file=sys.stderr); sys.exit(1)
    out = asyncio.run(run_body_eval(eval_set, skill_path, args.description, args.num_workers,
                                     args.timeout, args.model, args.provider, args.soft_threshold,
                                     runs_per_query=args.runs_per_query))
    if args.verbose:
        s = out["summary"]
        print(f"Body eval: {s['overall_passed']}/{s['total_body_cases']} pass (hard {s['hard_passed']}, avg {s['avg_soft_score']}/5, {s['skipped_cases']} skipped)", file=sys.stderr)
        for r in out["results"]:
            if r.get("skipped"): continue
            print(f"  [{'PASS' if r['overall_pass'] else 'FAIL'}] hard={r['hard_pass']} soft={r['soft_score']}/5 :: {r['query'][:70]}", file=sys.stderr)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
