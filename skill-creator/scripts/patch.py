#!/usr/bin/env python3
"""Shared patch primitives for skill-creator. 4-atom edit language with
lr_budget cap + protected SLOW_UPDATE region (SkillOpt §3.4, §3.6)."""
from __future__ import annotations
import re
from typing import Tuple

PATCH_OPS = ("append", "insert_after", "replace", "delete")
SLOW_UPDATE_OPEN  = "<!-- SLOW_UPDATE_START -->"
SLOW_UPDATE_CLOSE = "<!-- SLOW_UPDATE_END -->"


def apply_patch(text, edits, lr_budget=3):
    skipped, out, applied = [], text, 0
    for edit in edits:
        if applied >= lr_budget:
            skipped.append({"edit": edit, "reason": f"over lr_budget ({lr_budget})"}); continue
        op = edit.get("op")
        if op not in PATCH_OPS:
            skipped.append({"edit": edit, "reason": f"unknown op {op!r}"}); continue
        if op == "append":
            content = edit.get("content", "")
            if not isinstance(content, str) or not content.strip():
                skipped.append({"edit": edit, "reason": "append needs non-empty content"}); continue
            if "\n" in out:
                sep = "\n\n" if not out.endswith("\n\n") else ""
            else:
                sep = " " if out and not out.endswith(" ") else ""
            out = (out.rstrip("\n") if "\n" in out else out) + sep + content.strip()
            applied += 1; continue
        target = edit.get("target", "")
        if not isinstance(target, str) or not target:
            skipped.append({"edit": edit, "reason": f"{op} needs non-empty target"}); continue
        n = out.count(target)
        if n == 0:
            skipped.append({"edit": edit, "reason": f"{op}: target not found"}); continue
        if n > 1:
            skipped.append({"edit": edit, "reason": f"{op}: target ambiguous ({n} matches)"}); continue
        content = edit.get("content", "") if op != "delete" else ""
        if op == "insert_after": out = out.replace(target, target + content, 1)
        elif op == "replace":    out = out.replace(target, content, 1)
        elif op == "delete":     out = out.replace(target, "", 1)
        applied += 1
    return out, skipped


def split_skill_md(skill_md):
    fm = re.match(r"^---\n.*?\n---\n", skill_md, re.DOTALL)
    if fm:
        frontmatter, rest = fm.group(0), skill_md[fm.end():]
    else:
        frontmatter, rest = "", skill_md
    su = re.search(re.escape(SLOW_UPDATE_OPEN) + r"[\s\S]*?" + re.escape(SLOW_UPDATE_CLOSE), rest)
    if su:
        slow_block = su.group(0)
        body = (rest[:su.start()] + rest[su.end():]).strip()
    else:
        slow_block, body = "", rest.strip()
    return frontmatter, slow_block, body


def join_skill_md(frontmatter, slow_block, body):
    parts = []
    if frontmatter: parts.append(frontmatter.rstrip())
    if slow_block:  parts.append(slow_block.strip())
    if body:        parts.append(body.strip())
    return "\n\n".join(parts) + "\n"


def apply_patch_to_body(skill_md, edits, lr_budget=3):
    fm, slow, body = split_skill_md(skill_md)
    blocked, runnable = [], []
    protected = fm + slow
    for edit in edits:
        if edit.get("op") in ("insert_after", "replace", "delete"):
            t = edit.get("target", "")
            if t and t in protected and t not in body:
                blocked.append({"edit": edit, "reason": "target outside body (frontmatter or SLOW_UPDATE)"}); continue
        runnable.append(edit)
    new_body, body_skipped = apply_patch(body, runnable, lr_budget=lr_budget)
    return join_skill_md(fm, slow, new_body), blocked + body_skipped


def apply_patches_to_corpus(corpus, edits, lr_budget=3):
    """Apply per-file edits across a skill's markdown corpus.

    corpus: {relpath: content}, e.g. {"SKILL.md": ..., "references/x.md": ...}.
    Each edit may carry a "file" key (default "SKILL.md"). SKILL.md edits are
    body-protected (frontmatter + SLOW_UPDATE untouched); reference files are
    plain markdown. `lr_budget` is GLOBAL across all files.

    Returns (new_corpus, skipped, applied_count).
    """
    by_file = {}
    skipped = []
    for e in edits:
        f = e.get("file") or "SKILL.md"
        if f not in corpus:
            skipped.append({"edit": e, "reason": f"unknown file {f!r}"}); continue
        by_file.setdefault(f, []).append(e)
    new_corpus = dict(corpus)
    remaining = lr_budget
    applied_total = 0
    for f, fedits in by_file.items():
        if remaining <= 0:
            for e in fedits:
                skipped.append({"edit": e, "reason": f"over lr_budget ({lr_budget})"})
            continue
        if f == "SKILL.md":
            new_text, sk = apply_patch_to_body(corpus[f], fedits, lr_budget=remaining)
        else:
            new_text, sk = apply_patch(corpus[f], fedits, lr_budget=remaining)
        new_corpus[f] = new_text
        skipped.extend(sk)
        applied_this = len(fedits) - len(sk)
        applied_total += applied_this
        remaining -= applied_this
    return new_corpus, skipped, applied_total


if __name__ == "__main__":
    S = """---
name: demo
---

<!-- SLOW_UPDATE_START -->
PROTECTED
<!-- SLOW_UPDATE_END -->

# Demo

Body text.
"""
    fm, su, body = split_skill_md(S)
    assert "name: demo" in join_skill_md(fm, su, body)
    print("PASS: split/join round-trip")
    new_md, _ = apply_patch_to_body(S, [{"op": "replace", "target": "Body text.", "content": "Updated."}])
    assert "Updated." in new_md and "PROTECTED" in new_md
    print("PASS: body patch")
    new_md, skipped = apply_patch_to_body(S, [{"op": "replace", "target": "PROTECTED", "content": "HACKED"}])
    assert "HACKED" not in new_md and len(skipped) == 1
    print("PASS: SLOW_UPDATE protected")
    new_md, _ = apply_patch_to_body(S, [{"op": "replace", "target": "name: demo", "content": "name: pwned"}])
    assert "name: pwned" not in new_md
    print("PASS: frontmatter protected")
    _, skipped = apply_patch_to_body(S, [{"op":"append","content":"X"},{"op":"append","content":"Y"},{"op":"append","content":"Z"}], lr_budget=2)
    assert len([s for s in skipped if "lr_budget" in s.get("reason","")]) == 1
    print("PASS: lr_budget clip")
    # corpus: edit a reference file + SKILL.md body in one call
    corpus = {"SKILL.md": S, "references/vocab.md": "# Vocab\n\nbad term here.\n"}
    nc, sk, ap = apply_patches_to_corpus(corpus, [
        {"file": "references/vocab.md", "op": "replace", "target": "bad term here.", "content": "good term."},
        {"op": "replace", "target": "Body text.", "content": "Body 2."},
    ], lr_budget=3)
    assert "good term." in nc["references/vocab.md"]
    assert "Body 2." in nc["SKILL.md"] and "PROTECTED" in nc["SKILL.md"] and ap == 2
    print("PASS: corpus multi-file patch")
    nc2, sk2, _ = apply_patches_to_corpus(corpus, [{"file": "references/nope.md", "op": "append", "content": "x"}])
    assert any("unknown file" in s.get("reason", "") for s in sk2)
    print("PASS: corpus unknown-file guard")
    print("\nAll patch.py self-tests pass.")
