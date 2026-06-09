#!/usr/bin/env python3
"""Closed-loop body optimizer. Baseline -> propose -> re-eval -> strict gate."""
import argparse, asyncio, json, sys, time
from pathlib import Path
from scripts.improve_body import improve_body
from scripts.run_body_eval import run_body_eval
from scripts.utils import parse_skill_md


def _summarize_failures_for_analyzer(results):
    lines = []
    for r in results:
        if r.get("skipped") or r.get("overall_pass"): continue
        q = r.get("query", "")[:80]
        if not r.get("hard_pass", True):
            reasons = "; ".join(r.get("hard_failures", [])) or "hard gate failed"
            lines.append(f'Case "{q}": {reasons}')
        else:
            lines.append(f'Case "{q}": soft score {r.get("soft_score","?")}/5 - {r.get("soft_reasoning","")}')
    return lines


def _detect_regressions(best_results, new_results):
    new_by_q = {r["query"]: r for r in new_results if not r.get("skipped")}
    regs = []
    for r_best in best_results:
        if r_best.get("skipped") or not r_best.get("overall_pass"): continue
        rn = new_by_q.get(r_best["query"])
        if not rn or not rn.get("overall_pass"): regs.append(r_best["query"])
    return regs


async def run_body_loop(eval_set, skill_path, num_workers, timeout, max_iterations, lr_budget,
                        soft_threshold, model, provider, verbose, strict_gate=True, log_dir=None,
                        runs_per_query=3):
    skill_name, _, _ = parse_skill_md(skill_path)
    skill_md_path = skill_path / "SKILL.md"
    original_skill_md = skill_md_path.read_text()
    (skill_path / "SKILL.md.preopt.bak").write_text(original_skill_md)
    if log_dir: log_dir.mkdir(parents=True, exist_ok=True)
    if verbose:
        print(f"\n{'='*60}\nBody loop - {skill_name}\n  max_iter={max_iterations} lr_budget={lr_budget} strict={strict_gate} soft>={soft_threshold} runs_per_query={runs_per_query}\n{'='*60}", file=sys.stderr)

    t0 = time.time()
    baseline = await run_body_eval(eval_set, skill_path, None, num_workers, timeout, model, provider, soft_threshold, runs_per_query=runs_per_query)
    baseline_elapsed = time.time() - t0
    best_skill_md = original_skill_md
    best_results = baseline["results"]
    best_score = baseline["summary"]["overall_passed"]
    total_body = baseline["summary"]["total_body_cases"]
    history = [{"iteration": 0, "phase": "baseline", "score": best_score,
                "total_body_cases": total_body, "results": best_results,
                "elapsed_s": round(baseline_elapsed, 1)}]
    if log_dir: (log_dir / "00-baseline.json").write_text(json.dumps(baseline, indent=2))
    if verbose:
        print(f"Baseline: {best_score}/{total_body} (hard {baseline['summary']['hard_passed']}, avg {baseline['summary']['avg_soft_score']}/5, {baseline_elapsed:.1f}s)", file=sys.stderr)

    rejected = []
    exit_reason = "unknown"
    if baseline["summary"]["strict_all_pass"]:
        exit_reason = "baseline_already_passes"
        return _build_return(exit_reason, baseline, history, rejected, best_score, total_body, original_skill_md, best_skill_md)

    for iteration in range(1, max_iterations + 1):
        if verbose: print(f"\n--- iteration {iteration}/{max_iterations} ---", file=sys.stderr)
        failure_patterns = _summarize_failures_for_analyzer(best_results)
        if not failure_patterns:
            exit_reason = f"no_failures_left (iter {iteration})"; break

        t0 = time.time()
        patch_result = await improve_body(skill_path=skill_path, failure_patterns=failure_patterns,
                                          model=model, provider=provider, lr_budget=lr_budget,
                                          rejected_edits=rejected)
        improve_elapsed = time.time() - t0
        if log_dir:
            (log_dir / f"{iteration:02d}-patch.json").write_text(json.dumps({
                "patch": patch_result.get("patch"), "applied": patch_result.get("applied"),
                "skipped": patch_result.get("skipped"), "reasoning": patch_result.get("reasoning")}, indent=2))

        applied = patch_result.get("applied", 0)
        if not patch_result.get("patch") or applied == 0:
            rejected.append({"iteration": iteration, "reason": "no-op / unparseable / all edits skipped",
                             "skipped": patch_result.get("skipped", []), "reasoning": patch_result.get("reasoning", "")})
            history.append({"iteration": iteration, "phase": "iteration", "accepted": False,
                            "reject_reason": "no-op patch", "patch": patch_result.get("patch"),
                            "skipped_edits": patch_result.get("skipped", []),
                            "elapsed_s": round(improve_elapsed, 1)})
            if verbose: print(f"  REJECT: no-op patch", file=sys.stderr)
            continue

        candidate = patch_result["after"]
        skill_md_path.write_text(candidate)
        t0 = time.time()
        new_eval = await run_body_eval(eval_set, skill_path, None, num_workers, timeout, model, provider, soft_threshold, runs_per_query=runs_per_query)
        eval_elapsed = time.time() - t0
        if log_dir: (log_dir / f"{iteration:02d}-eval.json").write_text(json.dumps(new_eval, indent=2))

        new_score = new_eval["summary"]["overall_passed"]
        regressions = _detect_regressions(best_results, new_eval["results"])
        improvement = new_score - best_score
        # Strict gate: monotonic non-decrease + no regression on previously
        # passing cases. Tie acceptance is intentional — SkillOpt §3.5's
        # accumulation strategy. Zero-delta edits with no losses are still
        # progress in text space (they pre-position later iterations).
        # Lenient gate: any positive net delta, regressions allowed.
        accept = (improvement >= 0 and not regressions) if strict_gate else (improvement > 0)

        if accept:
            best_skill_md = candidate
            best_results = new_eval["results"]
            best_score = new_score
            if verbose: print(f"  GATE ACCEPT: {new_score}/{total_body} (delta=+{improvement})", file=sys.stderr)
        else:
            skill_md_path.write_text(best_skill_md)
            rejected.append({"iteration": iteration, "patch": patch_result["patch"],
                             "score_delta": improvement, "regressions": regressions,
                             "candidate_score": new_score, "best_score_at_attempt": best_score})
            if verbose:
                bits = []
                if improvement <= 0: bits.append(f"score {new_score}/{total_body} <= best {best_score}")
                if regressions: bits.append(f"{len(regressions)} regression(s)")
                print(f"  GATE REJECT: {' & '.join(bits)} - rolling back", file=sys.stderr)

        history.append({"iteration": iteration, "phase": "iteration", "accepted": accept,
                        "patch": patch_result["patch"], "applied": applied,
                        "skipped_edits": patch_result.get("skipped", []), "score": new_score,
                        "total_body_cases": total_body, "regressions": regressions,
                        "results": new_eval["results"],
                        "elapsed_s": round(improve_elapsed + eval_elapsed, 1)})

        if accept and new_eval["summary"]["strict_all_pass"]:
            exit_reason = f"all_pass (iter {iteration})"; break
        if iteration == max_iterations:
            exit_reason = f"max_iterations ({max_iterations})"; break

    if exit_reason == "unknown": exit_reason = f"loop_exited (iter {max_iterations})"
    return _build_return(exit_reason, baseline, history, rejected, best_score, total_body, original_skill_md, best_skill_md)


def _build_return(exit_reason, baseline, history, rejected, best_score, total_body, original_skill_md, best_skill_md):
    accepted = [h for h in history if h.get("phase") == "iteration" and h.get("accepted")]
    iter_runs = sum(1 for h in history if h.get("phase") == "iteration")
    return {"exit_reason": exit_reason, "skill_name": baseline["skill_name"],
            "baseline_score": f"{baseline['summary']['overall_passed']}/{total_body}",
            "best_score": f"{best_score}/{total_body}",
            "net_improvement": best_score - baseline["summary"]["overall_passed"],
            "iterations_run": iter_runs, "iterations_accepted": len(accepted),
            "iterations_rejected": len(rejected), "rejected_body_edits": rejected,
            "preopt_backup_path": "SKILL.md.preopt.bak",
            "body_changed": best_skill_md != original_skill_md, "history": history}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--eval-set", required=True); p.add_argument("--skill-path", required=True)
    p.add_argument("--num-workers", type=int, default=5); p.add_argument("--timeout", type=int, default=60)
    p.add_argument("--max-iterations", type=int, default=3); p.add_argument("--lr-budget", type=int, default=2)
    p.add_argument("--soft-threshold", type=int, default=4); p.add_argument("--model", required=True)
    p.add_argument("--provider", default="claude"); p.add_argument("--lenient-gate", action="store_true")
    p.add_argument("--runs-per-query", type=int, default=3, help="Run each eval case N times and majority-vote, to denoise LLM variance (default 3). Lower to 1 for a fast/cheap but noisy run.")
    p.add_argument("--results-dir", default=None); p.add_argument("--verbose", action="store_true")
    args = p.parse_args()
    asyncio.run(_async_main(args))


async def _ensure_mounted_for_pyodide(skill_path_str, eval_set_str):
    """In pyodide, skills are mounted on demand at /skill/<dir-name>. A
    sibling skill that hasn't been triggered yet won't have its files
    visible. Auto-mount it via window.pyodideRunner.ensureMounted and
    rewrite host-style paths to the pyodide mount form.

    Accepts host-style ("synergyAI/skills/.../geo-citability/") or
    pyodide-style ("/skill/geo-citability/") on input.

    Returns (skill_path_str, eval_set_str) — rewritten if applicable.
    Native CPython: no-op.
    """
    if sys.platform != "emscripten":
        return skill_path_str, eval_set_str
    from js import window  # type: ignore[import-not-found]

    sp = skill_path_str.rstrip("/")
    if sp.startswith("/skill/"):
        # Already pyodide form — preserve any category subfolder (e.g. GEO/foo)
        dir_name = sp[len("/skill/"):]
        new_sp = sp
    elif "/skills/" in sp:
        # Host form — take everything AFTER "skills/" as the dir_name.
        # This preserves category prefixes like "GEO/geo-citability" so the
        # nested skill resolves correctly via skills-fs.resolvePath.
        dir_name = sp.split("/skills/", 1)[1]
        new_sp = f"/skill/{dir_name}"
    else:
        # Fallback: assume the leaf is the dir_name (top-level skill).
        dir_name = sp.split("/")[-1]
        new_sp = f"/skill/{dir_name}"

    await window.pyodideRunner.ensureMounted(dir_name)

    new_es = eval_set_str
    if not eval_set_str.startswith("/skill/"):
        host_prefix = sp + "/"
        if eval_set_str.startswith(host_prefix):
            new_es = new_sp + "/" + eval_set_str[len(host_prefix):]
        elif eval_set_str.startswith(sp):
            new_es = new_sp + eval_set_str[len(sp):]
    return new_sp, new_es


async def _async_main(args):
    skill_path_str, eval_set_str = await _ensure_mounted_for_pyodide(
        args.skill_path, args.eval_set
    )
    eval_set = json.loads(Path(eval_set_str).read_text())
    skill_path = Path(skill_path_str)
    if not (skill_path / "SKILL.md").exists():
        print(f"No SKILL.md at {skill_path}", file=sys.stderr); sys.exit(1)

    results_dir = log_dir = None
    if args.results_dir:
        ts = time.strftime("%Y-%m-%d_%H%M%S")
        results_dir = Path(args.results_dir) / f"body-loop_{skill_path.name}_{ts}"
        results_dir.mkdir(parents=True, exist_ok=True)
        log_dir = results_dir / "logs"

    out = await run_body_loop(eval_set, skill_path, args.num_workers, args.timeout,
                              args.max_iterations, args.lr_budget, args.soft_threshold,
                              args.model, args.provider, args.verbose,
                              strict_gate=not args.lenient_gate, log_dir=log_dir,
                              runs_per_query=args.runs_per_query)
    print(json.dumps(out, indent=2))
    if results_dir:
        (results_dir / "results.json").write_text(json.dumps(out, indent=2))
        print(f"\nResults: {results_dir}", file=sys.stderr)
    if args.verbose:
        print(f"\nExit: {out['exit_reason']}", file=sys.stderr)
        print(f"Score: {out['baseline_score']} -> {out['best_score']} (net {out['net_improvement']:+d})", file=sys.stderr)


if __name__ == "__main__":
    main()
