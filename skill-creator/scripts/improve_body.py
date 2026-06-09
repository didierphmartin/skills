#!/usr/bin/env python3
"""Improve the BODY of a SKILL.md based on observed failure patterns."""
from __future__ import annotations
import argparse, asyncio, json, re, sys
from pathlib import Path
from scripts.patch import apply_patch_to_body

AGENT_ENDPOINT = "/gpt/backend/api/v1/agent"


async def _call_optimizer(prompt, model, provider, system, timeout=300):
    """POST to /api/v1/agent. Uses pyodide.http in the browser (resolves
    relative URLs against the current origin); falls back to urllib for
    native-CPython CLI use (where AGENT_ENDPOINT would need to be absolute).
    """
    payload = {"prompt": prompt, "model": model, "provider": provider, "system": system}
    body_bytes = json.dumps(payload).encode("utf-8")
    if sys.platform == "emscripten":
        import pyodide.http  # type: ignore[import-not-found]
        from js import window  # type: ignore[import-not-found]
        headers = {"Content-Type": "application/json"}
        tok = getattr(getattr(window, "authManager", None), "token", None)
        if tok:
            headers["Authorization"] = f"Bearer {tok}"
        resp = await pyodide.http.pyfetch(
            AGENT_ENDPOINT,
            method="POST",
            headers=headers,
            body=body_bytes,
        )
        text = await resp.string()
    else:
        import urllib.request
        req = urllib.request.Request(
            AGENT_ENDPOINT, data=body_bytes,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = r.read().decode("utf-8")
    data = json.loads(text)
    if not data.get("success"):
        raise RuntimeError(f"/api/v1/agent failed: {data.get('error', 'unknown')}")
    return data.get("text", "")


def _parse_patch_response(text):
    for c in sorted(re.findall(r"\{[\s\S]*\}", text), key=len, reverse=True):
        try:
            obj = json.loads(c)
            if isinstance(obj, dict) and "edits" in obj: return obj
        except json.JSONDecodeError:
            continue
    return None


def _read_body_analyzer_prompt(skill_creator_dir):
    p = skill_creator_dir / "agents" / "body_analyzer.md"
    if not p.is_file():
        raise FileNotFoundError(f"Body analyzer prompt not found at {p}")
    return p.read_text()


async def improve_body(skill_path, failure_patterns, model, provider="claude", lr_budget=3, rejected_edits=None, success_patterns=None):
    skill_md_path = skill_path / "SKILL.md"
    if not skill_md_path.is_file():
        raise FileNotFoundError(f"SKILL.md not found at {skill_md_path}")
    skill_md = skill_md_path.read_text()
    skill_creator_dir = Path(__file__).resolve().parent.parent
    system_prompt = _read_body_analyzer_prompt(skill_creator_dir)

    parts = [f"## Current SKILL.md (path: {skill_md_path})", "", skill_md, "",
             f"## Observed failure patterns ({len(failure_patterns)} items)", ""]
    for i, fp in enumerate(failure_patterns, 1): parts.append(f"{i}. {fp.strip()}")
    if success_patterns:
        parts.extend(["", f"## Observed success patterns ({len(success_patterns)} items)", ""])
        for i, sp in enumerate(success_patterns, 1): parts.append(f"{i}. {sp.strip()}")
    if rejected_edits:
        parts.extend(["", "## Rejected edits from prior iterations (do not repeat)", ""])
        for r in rejected_edits[-5:]: parts.append(json.dumps(r, indent=2))
    parts.extend(["", "## Constraints",
                  f"- lr_budget: {lr_budget} (propose AT MOST this many edits)",
                  "- Edits to frontmatter or the SLOW_UPDATE block will be silently rejected",
                  "", "Respond with ONLY the JSON object specified in your system prompt."])
    user_msg = "\n".join(parts)

    response_text = await _call_optimizer(prompt=user_msg, model=model, provider=provider, system=system_prompt)
    parsed = _parse_patch_response(response_text)
    if not parsed:
        return {"patch": None, "applied": 0, "skipped": [], "before": skill_md, "after": skill_md,
                "reasoning": "Optimizer returned no parseable JSON; no edits applied.", "raw_response": response_text}

    edits = parsed.get("edits", [])
    new_skill_md, skipped = apply_patch_to_body(skill_md, edits, lr_budget=lr_budget)
    return {"patch": parsed, "applied": len(edits) - len(skipped), "skipped": skipped,
            "before": skill_md, "after": new_skill_md, "reasoning": parsed.get("reasoning", ""),
            "raw_response": response_text}


def main():
    parser = argparse.ArgumentParser(description="Propose+apply 4-atom body patches to a SKILL.md")
    parser.add_argument("--skill-path", required=True)
    parser.add_argument("--failure-patterns", required=True)
    parser.add_argument("--success-patterns", default=None)
    parser.add_argument("--rejected-edits", default=None)
    parser.add_argument("--model", required=True)
    parser.add_argument("--provider", default="claude")
    parser.add_argument("--lr-budget", type=int, default=3)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    skill_path = Path(args.skill_path)
    if not skill_path.is_dir():
        print(f"skill-path not a directory: {skill_path}", file=sys.stderr); sys.exit(2)
    failure_patterns = [l for l in Path(args.failure_patterns).read_text().splitlines() if l.strip() and not l.strip().startswith("#")]
    if not failure_patterns:
        print(f"no failure patterns in {args.failure_patterns}", file=sys.stderr); sys.exit(2)
    success_patterns = None
    if args.success_patterns:
        success_patterns = [l for l in Path(args.success_patterns).read_text().splitlines() if l.strip() and not l.strip().startswith("#")]
    rejected_edits = json.loads(Path(args.rejected_edits).read_text()) if args.rejected_edits else None

    result = asyncio.run(improve_body(skill_path=skill_path, failure_patterns=failure_patterns,
                                       model=args.model, provider=args.provider, lr_budget=args.lr_budget,
                                       rejected_edits=rejected_edits, success_patterns=success_patterns))
    print(json.dumps({"applied": result["applied"], "skipped_count": len(result["skipped"]),
                      "skipped": result["skipped"], "reasoning": result["reasoning"],
                      "patch": result["patch"]}, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=2))
    if args.apply:
        if result["after"] == result["before"]:
            print("[improve_body] No changes (no-op).", file=sys.stderr)
        else:
            (skill_path / "SKILL.md.bak").write_text(result["before"])
            (skill_path / "SKILL.md").write_text(result["after"])
            print("[improve_body] APPLIED. Backup at SKILL.md.bak", file=sys.stderr)
    else:
        print("[improve_body] DRY RUN (use --apply to write).", file=sys.stderr)


if __name__ == "__main__":
    main()
