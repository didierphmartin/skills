#!/usr/bin/env python3
"""Improve the BODY of a SKILL.md based on observed failure patterns."""
from __future__ import annotations
import argparse, asyncio, json, re, sys
from pathlib import Path
from scripts.patch import apply_patch_to_body, apply_patches_to_corpus


def _read_corpus(skill_path):
    """The skill's editable markdown: SKILL.md + every references/*.md,
    keyed by relative path. The proposer sees and may edit all of them."""
    corpus = {}
    sk = skill_path / "SKILL.md"
    if sk.is_file():
        corpus["SKILL.md"] = sk.read_text()
    refs = skill_path / "references"
    if refs.is_dir():
        for f in sorted(refs.glob("*.md")):
            corpus[f"references/{f.name}"] = f.read_text()
    return corpus

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
    corpus = _read_corpus(skill_path)
    skill_creator_dir = Path(__file__).resolve().parent.parent
    system_prompt = _read_body_analyzer_prompt(skill_creator_dir)

    parts = ["## Skill documents",
             'You may edit ANY of these files — set `"file"` on each edit (default "SKILL.md"). '
             'The problem may live in a reference doc, not SKILL.md.', ""]
    for relpath, content in corpus.items():
        parts.extend([f"### FILE: {relpath}", "", content, ""])
    parts.extend([f"## Observed failure patterns ({len(failure_patterns)} items)", ""])
    for i, fp in enumerate(failure_patterns, 1): parts.append(f"{i}. {fp.strip()}")
    if success_patterns:
        parts.extend(["", f"## Observed success patterns ({len(success_patterns)} items)", ""])
        for i, sp in enumerate(success_patterns, 1): parts.append(f"{i}. {sp.strip()}")
    if rejected_edits:
        parts.extend(["", "## Rejected edits from prior iterations (do not repeat)", ""])
        for r in rejected_edits[-5:]: parts.append(json.dumps(r, indent=2))
    parts.extend(["", "## Constraints",
                  f"- lr_budget: {lr_budget} (propose AT MOST this many edits TOTAL across all files)",
                  "- In SKILL.md, edits to frontmatter or the SLOW_UPDATE block are silently rejected",
                  '- Set `file` to the exact path shown above (e.g. "references/vocabulary.md")',
                  "", "Respond with ONLY the JSON object specified in your system prompt."])
    user_msg = "\n".join(parts)

    response_text = await _call_optimizer(prompt=user_msg, model=model, provider=provider, system=system_prompt)
    parsed = _parse_patch_response(response_text)
    if not parsed:
        return {"patch": None, "applied": 0, "skipped": [], "before_corpus": corpus, "after_corpus": corpus,
                "changed_files": {}, "reasoning": "Optimizer returned no parseable JSON; no edits applied.",
                "raw_response": response_text}

    edits = parsed.get("edits", [])
    new_corpus, skipped, applied = apply_patches_to_corpus(corpus, edits, lr_budget=lr_budget)
    changed_files = {f: new_corpus[f] for f in new_corpus if new_corpus[f] != corpus.get(f)}
    return {"patch": parsed, "applied": applied, "skipped": skipped,
            "before_corpus": corpus, "after_corpus": new_corpus, "changed_files": changed_files,
            "reasoning": parsed.get("reasoning", ""), "raw_response": response_text}


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
        changed = result.get("changed_files", {})
        if not changed:
            print("[improve_body] No changes (no-op).", file=sys.stderr)
        else:
            for relpath, content in changed.items():
                fpath = skill_path / relpath
                (fpath.parent / (fpath.name + ".bak")).write_text(result["before_corpus"][relpath])
                fpath.write_text(content)
            print(f"[improve_body] APPLIED to {len(changed)} file(s); .bak backups written.", file=sys.stderr)
    else:
        print("[improve_body] DRY RUN (use --apply to write).", file=sys.stderr)


if __name__ == "__main__":
    main()
