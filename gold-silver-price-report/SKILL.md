---
name: gold-silver-price-report
description: Multi-agent workflow to research and compile a comprehensive report on gold and silver prices using market news and financial data
fetches_urls: false
execution: compiled-pyodide
---

# gold-silver-price-report

Multi-agent workflow to research and compile a comprehensive report on gold and silver prices using market news and financial data

## How to run

This skill runs a COMPILED Pyodide snapshot of workflow #37 — the graph was frozen into `scripts/run.py` at promotion time and executes fully in the browser (agent turns via the backend, skills via the worker pool). Later edits to the workflow do NOT affect this skill; re-promote to refresh. No external runner is required.

On every invocation you MUST:
1. Compose a single plain-language instruction for the workflow from the user's request, explicitly filling in the parameters documented above (use defaults when the user did not specify one).
2. Call `run_skill_script` with script `scripts/run.py` and argv `["--prompt", "<your composed instruction>"]`.
3. The script runs the whole workflow (a multi-agent run can take several minutes; that is normal), writes the final document to /outputs/ and announces it with a "wrote <path>" line — the platform renders that document (HTML or Markdown) for the user automatically.
4. After it returns: give the user a SHORT 2-3 sentence summary and point to the rendered document. Do NOT re-paste the document body and do NOT invent results before the script returns.

Do NOT attempt to perform the workflow's steps yourself.

## Provenance

Created by skill genesis (L1 compiled Pyodide snapshot) from workflow:37 on 2026-07-20.

## Eval queries

```json
[]
```
