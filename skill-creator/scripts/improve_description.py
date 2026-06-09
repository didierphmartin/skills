#!/usr/bin/env python3
"""Improve a skill description based on eval results.

gpt-env port: the upstream calls `claude -p` as a subprocess. Pyodide has
no subprocess, so the LLM call is replaced with a fetch to the host's
single-pass agent endpoint `/api/v1/agent` (which itself wraps
LLMManager::chat with tools disabled — see backend ChatController::agent).
"""

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

from scripts.utils import parse_skill_md
# Phase 2A: apply_patch moved to scripts/patch.py so improve_body.py can
# share it. Re-export under the original name for backward compatibility
# with any external code that imports it from this module.
from scripts.patch import apply_patch, PATCH_OPS

AGENT_ENDPOINT = "/gpt/backend/api/v1/agent"


def _build_patch_prompt(
    skill_name: str,
    skill_content: str,
    current_description: str,
    scores_summary: str,
    failed_triggers: list[dict],
    false_triggers: list[dict],
    lr_budget: int,
    rejected_edits: list[dict] | None,
) -> str:
    """Patch-mode prompt: asks for a structured JSON edit list rather than a
    free-form rewrite. Mirrors SkillOpt prompts/analyst_error.md adapted to
    description-level optimization.
    """
    p = [
        f'You are optimizing the description of a skill called "{skill_name}".',
        "Propose a SMALL number of atomic edits to the description. Each edit fixes ONE specific failure mode observed below.",
        "",
        "Current description:",
        f'"""{current_description}"""',
        "",
        f"Current scores ({scores_summary}):",
    ]
    if failed_triggers:
        p.append("FAILED TO TRIGGER (should have triggered but didn't):")
        for r in failed_triggers:
            p.append(f'  - "{r["query"]}" (triggered {r["triggers"]}/{r["runs"]} times)')
    if false_triggers:
        p.append("FALSE TRIGGERS (triggered but shouldn't have):")
        for r in false_triggers:
            p.append(f'  - "{r["query"]}" (triggered {r["triggers"]}/{r["runs"]} times)')
    p.extend([
        "",
        "Skill body (context only):",
        f'"""{skill_content[:2000]}"""',
        "",
        f"Propose AT MOST {lr_budget} edits using these atomic operations:",
        "  - append:       add text at the end (provide content only)",
        "  - insert_after: insert content right after an exact target substring",
        "  - replace:      replace an exact target substring with content",
        "  - delete:       remove an exact target substring (no content)",
        "",
        "Rules:",
        "  - The `target` for replace/delete/insert_after MUST appear EXACTLY ONCE in the current description. Ambiguous targets are dropped silently.",
        "  - Be surgical. One precise edit beats three speculative ones.",
        "  - The final description must stay under 1024 characters; trim with replace/delete if needed.",
    ])
    if rejected_edits:
        p.append("")
        p.append("Recently REJECTED edits that regressed the validation score — do not propose anything similar:")
        for r in rejected_edits[-5:]:
            p.append(f'  - iteration {r.get("iteration", "?")}: produced "{r.get("description_attempted", "")[:120]}..."')
    p.extend([
        "",
        "Respond with ONLY a JSON object — no markdown fences, no commentary:",
        '{',
        '  "reasoning": "<one short paragraph: what failure modes you are addressing>",',
        '  "edits": [',
        '    {"op": "append|insert_after|replace|delete", "target": "<if op != append>", "content": "<if op != delete>"}',
        '  ]',
        '}',
        '',
        '"edits" may be an empty list if no patch is warranted.',
    ])
    return "\n".join(p)


def _parse_patch_response(text: str) -> dict | None:
    """Parse a JSON patch response. Tolerates a leading/trailing prose preamble
    (some providers wrap the JSON in chatter despite the prompt) by extracting
    the largest JSON object. Returns None if no valid object is found.
    """
    candidates = re.findall(r"\{[\s\S]*\}", text)
    for c in sorted(candidates, key=len, reverse=True):
        try:
            obj = json.loads(c)
            if isinstance(obj, dict) and "edits" in obj:
                return obj
        except json.JSONDecodeError:
            continue
    return None

# ─── end of BOUNDED-PATCH MODE additions ─────────────────────────────────────


async def _call_claude(prompt: str, model: str | None, timeout: int = 300, provider: str = "claude") -> str:
    """Single-pass LLM call via the gpt backend's /api/v1/agent endpoint.

    Returns the raw text response. The caller parses any structured
    content (e.g. <new_description> tags) downstream.
    """
    import pyodide.http  # type: ignore[import-not-found]
    from js import window  # type: ignore[import-not-found]

    headers = {"Content-Type": "application/json"}
    tok = getattr(getattr(window, "authManager", None), "token", None)
    if tok:
        headers["Authorization"] = f"Bearer {tok}"

    body = {"prompt": prompt, "provider": provider}
    if model:
        body["model"] = model

    resp = await pyodide.http.pyfetch(
        AGENT_ENDPOINT,
        method="POST",
        headers=headers,
        body=json.dumps(body),
    )
    text = await resp.string()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"agent endpoint returned non-JSON: {text[:200]}") from e
    if not data.get("success"):
        raise RuntimeError(f"agent call failed: {data.get('error', 'unknown error')}")
    return data.get("text", "")


async def improve_description(
    skill_name: str,
    skill_content: str,
    current_description: str,
    eval_results: dict,
    history: list[dict],
    model: str,
    test_results: dict | None = None,
    log_dir: Path | None = None,
    iteration: int | None = None,
    provider: str = "claude",
    rejected_edits: list[dict] | None = None,
    patch_mode: bool = False,
    lr_budget: int = 3,
) -> str:
    """Call Claude to improve the description based on eval results.

    Two modes:
    - Default (patch_mode=False): full-rewrite path. The optimizer returns a
      complete new description string. Original behavior. Stable.
    - Opt-in (patch_mode=True): bounded 4-atom patch path. The optimizer
      returns a JSON edit list capped at lr_budget operations. If the patch
      can't be applied cleanly (target ambiguous / not found, all edits
      skipped, etc.), falls back to the full-rewrite path.
    """
    failed_triggers = [
        r for r in eval_results["results"]
        if r["should_trigger"] and not r["pass"]
    ]
    false_triggers = [
        r for r in eval_results["results"]
        if not r["should_trigger"] and not r["pass"]
    ]

    # Build scores summary
    train_score = f"{eval_results['summary']['passed']}/{eval_results['summary']['total']}"
    if test_results:
        test_score = f"{test_results['summary']['passed']}/{test_results['summary']['total']}"
        scores_summary = f"Train: {train_score}, Test: {test_score}"
    else:
        scores_summary = f"Train: {train_score}"

    # ─── PATCH MODE BRANCH ────────────────────────────────────────────────
    if patch_mode:
        patch_prompt = _build_patch_prompt(
            skill_name=skill_name,
            skill_content=skill_content,
            current_description=current_description,
            scores_summary=scores_summary,
            failed_triggers=failed_triggers,
            false_triggers=false_triggers,
            lr_budget=lr_budget,
            rejected_edits=rejected_edits,
        )
        patch_text = await _call_claude(patch_prompt, model, provider=provider)
        parsed = _parse_patch_response(patch_text)
        if parsed and parsed.get("edits"):
            new_desc, skipped = apply_patch(current_description, parsed["edits"], lr_budget=lr_budget)
            # If we got a non-empty result AND at least one edit applied, return it.
            # Otherwise fall through to the full-rewrite path as a safety net.
            applied_count = len(parsed["edits"]) - len(skipped)
            if applied_count > 0 and new_desc and new_desc != current_description:
                if log_dir:
                    log_dir.mkdir(parents=True, exist_ok=True)
                    (log_dir / f"patch_{iteration or 0}.json").write_text(
                        json.dumps({
                            "iteration": iteration,
                            "prompt": patch_prompt,
                            "response": patch_text,
                            "parsed_edits": parsed["edits"],
                            "applied_count": applied_count,
                            "skipped": skipped,
                            "before": current_description,
                            "after": new_desc,
                            "reasoning": parsed.get("reasoning", ""),
                        }, indent=2)
                    )
                return new_desc
            # Patch path failed — fall through to full rewrite.
            print(
                f"[improve_description] patch_mode produced no usable edits "
                f"(skipped={len(skipped)}, applied={applied_count}); "
                f"falling back to full rewrite",
                file=sys.stderr,
            )
    # ─── END PATCH MODE BRANCH ────────────────────────────────────────────

    prompt = f"""You are optimizing a skill description for a Claude Code skill called "{skill_name}". A "skill" is sort of like a prompt, but with progressive disclosure -- there's a title and description that Claude sees when deciding whether to use the skill, and then if it does use the skill, it reads the .md file which has lots more details and potentially links to other resources in the skill folder like helper files and scripts and additional documentation or examples.

The description appears in Claude's "available_skills" list. When a user sends a query, Claude decides whether to invoke the skill based solely on the title and on this description. Your goal is to write a description that triggers for relevant queries, and doesn't trigger for irrelevant ones.

Here's the current description:
<current_description>
"{current_description}"
</current_description>

Current scores ({scores_summary}):
<scores_summary>
"""
    if failed_triggers:
        prompt += "FAILED TO TRIGGER (should have triggered but didn't):\n"
        for r in failed_triggers:
            prompt += f'  - "{r["query"]}" (triggered {r["triggers"]}/{r["runs"]} times)\n'
        prompt += "\n"

    if false_triggers:
        prompt += "FALSE TRIGGERS (triggered but shouldn't have):\n"
        for r in false_triggers:
            prompt += f'  - "{r["query"]}" (triggered {r["triggers"]}/{r["runs"]} times)\n'
        prompt += "\n"

    if history:
        prompt += "PREVIOUS ATTEMPTS (do NOT repeat these — try something structurally different):\n\n"
        for h in history:
            train_s = f"{h.get('train_passed', h.get('passed', 0))}/{h.get('train_total', h.get('total', 0))}"
            test_s = f"{h.get('test_passed', '?')}/{h.get('test_total', '?')}" if h.get('test_passed') is not None else None
            score_str = f"train={train_s}" + (f", test={test_s}" if test_s else "")
            prompt += f'<attempt {score_str}>\n'
            prompt += f'Description: "{h["description"]}"\n'
            if "results" in h:
                prompt += "Train results:\n"
                for r in h["results"]:
                    status = "PASS" if r["pass"] else "FAIL"
                    prompt += f'  [{status}] "{r["query"][:80]}" (triggered {r["triggers"]}/{r["runs"]})\n'
            if h.get("note"):
                prompt += f'Note: {h["note"]}\n'
            prompt += "</attempt>\n\n"

    prompt += f"""</scores_summary>

Skill content (for context on what the skill does):
<skill_content>
{skill_content}
</skill_content>

Based on the failures, write a new and improved description that is more likely to trigger correctly. When I say "based on the failures", it's a bit of a tricky line to walk because we don't want to overfit to the specific cases you're seeing. So what I DON'T want you to do is produce an ever-expanding list of specific queries that this skill should or shouldn't trigger for. Instead, try to generalize from the failures to broader categories of user intent and situations where this skill would be useful or not useful. The reason for this is twofold:

1. Avoid overfitting
2. The list might get loooong and it's injected into ALL queries and there might be a lot of skills, so we don't want to blow too much space on any given description.

Concretely, your description should not be more than about 100-200 words, even if that comes at the cost of accuracy. There is a hard limit of 1024 characters — descriptions over that will be truncated, so stay comfortably under it.

Here are some tips that we've found to work well in writing these descriptions:
- The skill should be phrased in the imperative -- "Use this skill for" rather than "this skill does"
- The skill description should focus on the user's intent, what they are trying to achieve, vs. the implementation details of how the skill works.
- The description competes with other skills for Claude's attention — make it distinctive and immediately recognizable.
- If you're getting lots of failures after repeated attempts, change things up. Try different sentence structures or wordings.

I'd encourage you to be creative and mix up the style in different iterations since you'll have multiple opportunities to try different approaches and we'll just grab the highest-scoring one at the end.

Please respond with only the new description text in <new_description> tags, nothing else."""

    # Rejected-edit buffer (Wave 1, step C). Previously-proposed descriptions
    # that regressed on the validation gate — these are now negative training
    # signal. The optimizer model should NOT propose anything substantially
    # similar to these unless it has a strong reason to believe the previous
    # rejection was a fluke. Modeled on SkillOpt's rejected-step buffer (§3.5).
    if rejected_edits:
        rejected_block = "\n\n<rejected_descriptions>\nThe following descriptions were tried in previous iterations and REGRESSED on the held-out validation set. Do not repeat them or near-duplicates; learn from the regression direction.\n"
        for r in rejected_edits[-5:]:  # cap at last 5 to keep the prompt bounded
            rejected_block += (
                f'- iteration {r.get("iteration", "?")}'
                f' (gate score {r.get("gate_score", "?")}/{r.get("previous_best_score", "?")}+):'
                f' "{r.get("description_attempted", "")}"\n'
            )
        rejected_block += "</rejected_descriptions>"
        prompt += rejected_block

    text = await _call_claude(prompt, model, provider=provider)

    match = re.search(r"<new_description>(.*?)</new_description>", text, re.DOTALL)
    description = match.group(1).strip().strip('"') if match else text.strip().strip('"')

    transcript: dict = {
        "iteration": iteration,
        "prompt": prompt,
        "response": text,
        "parsed_description": description,
        "char_count": len(description),
        "over_limit": len(description) > 1024,
    }

    # Safety net: the prompt already states the 1024-char hard limit, but if
    # the model blew past it anyway, make one fresh single-turn call that
    # quotes the too-long version and asks for a shorter rewrite. (The old
    # SDK path did this as a true multi-turn; `claude -p` is one-shot, so we
    # inline the prior output into the new prompt instead.)
    if len(description) > 1024:
        shorten_prompt = (
            f"{prompt}\n\n"
            f"---\n\n"
            f"A previous attempt produced this description, which at "
            f"{len(description)} characters is over the 1024-character hard limit:\n\n"
            f'"{description}"\n\n'
            f"Rewrite it to be under 1024 characters while keeping the most "
            f"important trigger words and intent coverage. Respond with only "
            f"the new description in <new_description> tags."
        )
        shorten_text = await _call_claude(shorten_prompt, model, provider=provider)
        match = re.search(r"<new_description>(.*?)</new_description>", shorten_text, re.DOTALL)
        shortened = match.group(1).strip().strip('"') if match else shorten_text.strip().strip('"')

        transcript["rewrite_prompt"] = shorten_prompt
        transcript["rewrite_response"] = shorten_text
        transcript["rewrite_description"] = shortened
        transcript["rewrite_char_count"] = len(shortened)
        description = shortened

    transcript["final_description"] = description

    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"improve_iter_{iteration or 'unknown'}.json"
        log_file.write_text(json.dumps(transcript, indent=2))

    return description


def main():
    parser = argparse.ArgumentParser(description="Improve a skill description based on eval results")
    parser.add_argument("--eval-results", required=True, help="Path to eval results JSON (from run_eval.py)")
    parser.add_argument("--skill-path", required=True, help="Path to skill directory")
    parser.add_argument("--history", default=None, help="Path to history JSON (previous attempts)")
    parser.add_argument("--model", required=True, help="Model for improvement")
    parser.add_argument("--provider", default="claude", help="LLM provider (claude, openai, gemini, grok, deepseek, kimi)")
    parser.add_argument("--verbose", action="store_true", help="Print thinking to stderr")
    args = parser.parse_args()

    skill_path = Path(args.skill_path)
    if not (skill_path / "SKILL.md").exists():
        print(f"Error: No SKILL.md found at {skill_path}", file=sys.stderr)
        sys.exit(1)

    eval_results = json.loads(Path(args.eval_results).read_text())
    history = []
    if args.history:
        history = json.loads(Path(args.history).read_text())

    name, _, content = parse_skill_md(skill_path)
    current_description = eval_results["description"]

    if args.verbose:
        print(f"Current: {current_description}", file=sys.stderr)
        print(f"Score: {eval_results['summary']['passed']}/{eval_results['summary']['total']}", file=sys.stderr)

    new_description = asyncio.run(improve_description(
        skill_name=name,
        skill_content=content,
        current_description=current_description,
        eval_results=eval_results,
        history=history,
        model=args.model,
        provider=args.provider,
    ))

    if args.verbose:
        print(f"Improved: {new_description}", file=sys.stderr)

    # Output as JSON with both the new description and updated history
    output = {
        "description": new_description,
        "history": history + [{
            "description": current_description,
            "passed": eval_results["summary"]["passed"],
            "failed": eval_results["summary"]["failed"],
            "total": eval_results["summary"]["total"],
            "results": eval_results["results"],
        }],
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
