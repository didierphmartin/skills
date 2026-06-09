# Workflow `output` Block + `report-pdf` Skill — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Let the simple workflow language express output storage + format (`output: {store, folder, format}`), compiling to the DSL's `output_storage_enabled`/`output_folder` and auto‑binding a formatter skill on the terminal agent — and add a `report-pdf` skill so `format:"pdf"` works.

**Architecture:** Two units. (1) A new folder skill `report-pdf` (fpdf2, markdown→PDF). (2) Edits to `workflow-compile`: add `output` to `references/schema.json`, and to `scripts/compile.py` add `validate_output`, a `FORMAT_SKILL` map, terminal‑agent auto‑binding, and storage‑field mapping. `validate_schema` already ignores unknown keys, so `output` is additive.

**Tech Stack:** Python 3 (stdlib for the compiler; `fpdf2` for the skill — pure‑Python, Pyodide‑safe).

**Spec:** `docs/2026-06-08-workflow-output-block-design.md`
**Repo root:** `~/Documents/synergyAI/skills`

---

## Task 1: `report-pdf` skill

**Files:** Create `report-pdf/SKILL.md`, `report-pdf/scripts/build.py`.

- [ ] **Step 1: `report-pdf/SKILL.md`:**

```markdown
---
name: report-pdf
description: "Render synthesized report content (markdown or plain text) into a styled PDF. USE scripts/build.py when a workflow's terminal agent must deliver a PDF, or when the user asks for 'a PDF report'. Input: the report text/markdown. Output: a .pdf written to /outputs/. Do NOT use to author a workflow (that's workflow-compile) or to edit Office files (docx/pptx/xlsx skills)."
license: MIT
metadata:
  version: 0.1.0
  origin: synergyAI custom
dependencies: [fpdf2]
allowed-tools: run_skill_script
---

# report-pdf

Turn a synthesized report (markdown or plain text) into a clean, styled PDF.

## Runtime contract
- Call `run_skill_script` with `scripts/build.py`; only the written `.pdf` in `/outputs/` is the deliverable.
- `argv`: `["-i", "/scratch/report.md", "-o", "/outputs/<name>.pdf"]` (or `--title "..."`).
- `read_outputs`: `["/outputs/<name>.pdf"]`.

## What it renders
Markdown headings (`#`, `##`, `###`) become titled sections; `-`/`*` lines become bullets; blank
lines separate paragraphs. Inline `**bold**`/`*italic*` are honored. Everything else is body text.
```

- [ ] **Step 2: `report-pdf/scripts/build.py`:**

```python
#!/usr/bin/env python3
"""Render markdown/plain-text report content into a styled PDF (fpdf2)."""
import argparse
import sys
from pathlib import Path

from fpdf import FPDF


def render_pdf(text: str, title: str | None) -> FPDF:
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    pdf.set_margins(18, 18, 18)

    if title:
        pdf.set_font("Helvetica", "B", 20)
        pdf.multi_cell(0, 10, title)
        pdf.ln(2)

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line:
            pdf.ln(3)
            continue
        if line.startswith("### "):
            pdf.set_font("Helvetica", "B", 12); pdf.multi_cell(0, 7, line[4:])
        elif line.startswith("## "):
            pdf.set_font("Helvetica", "B", 14); pdf.ln(1); pdf.multi_cell(0, 8, line[3:])
        elif line.startswith("# "):
            pdf.set_font("Helvetica", "B", 17); pdf.ln(1); pdf.multi_cell(0, 9, line[2:])
        elif line.lstrip().startswith(("- ", "* ")):
            pdf.set_font("Helvetica", "", 11)
            pdf.multi_cell(0, 6, "  • " + line.lstrip()[2:], markdown=True)
        else:
            pdf.set_font("Helvetica", "", 11)
            pdf.multi_cell(0, 6, line, markdown=True)
    return pdf


def main() -> int:
    p = argparse.ArgumentParser(description="Render report text/markdown into a PDF.")
    p.add_argument("-i", "--input", required=True, help="path to markdown/text file")
    p.add_argument("-o", "--output", default="/outputs/report.pdf", help="output PDF path")
    p.add_argument("-t", "--title", default=None, help="optional report title")
    args = p.parse_args()

    try:
        text = Path(args.input).read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"error: input file not found: {args.input}", file=sys.stderr)
        return 2

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    render_pdf(text, args.title).output(str(out))
    print(f"wrote {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: Install fpdf2 locally + smoke-test (it runs in Pyodide at runtime; locally we need the dep):**

```bash
cd ~/Documents/synergyAI/skills
python3 -m pip install --quiet fpdf2
printf '# Market Report\n\n## Metals\nGold up.\n\n- bullet one\n- bullet two\n' > /tmp/r.md
python3 report-pdf/scripts/build.py -i /tmp/r.md -o /tmp/r.pdf -t "Market Report"
head -c 5 /tmp/r.pdf; echo   # expect: %PDF-
```
Expected: prints `wrote /tmp/r.pdf (<N> bytes)` and the file starts with `%PDF-`.

- [ ] **Step 4: Commit:**

```bash
cd ~/Documents/synergyAI/skills
git add report-pdf/SKILL.md report-pdf/scripts/build.py
git commit -m "feat(report-pdf): new skill — render markdown/text report into a styled PDF (fpdf2)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Add `output` to the simple-language schema

**Files:** Modify `workflow-compile/references/schema.json`.

- [ ] **Step 1:** In `properties` (alongside `name`, `start`, `agents`, `flow`), add the `output` block. Insert after the `flow` property's closing `}` (keep the surrounding object's `additionalProperties: false`):

```jsonc
,
    "output": {
      "type": "object",
      "additionalProperties": false,
      "description": "Optional. Where/how the final result is stored. Compiled into output_storage_enabled / output_folder, and (for non-md formats) auto-binds a formatter skill on the terminal agent.",
      "properties": {
        "store":  { "type": "boolean", "default": false, "description": "Persist the result to disk." },
        "folder": { "type": "string", "description": "Relative subfolder under the outputs root, e.g. \"reports\". No leading \"/\", no \"..\"." },
        "format": { "type": "string", "enum": ["pdf", "html", "docx", "md"], "default": "md", "description": "Output format. pdf→report-pdf, html→html, docx→docx, md→raw." }
      }
    }
```

- [ ] **Step 2: Validate the JSON parses:**

```bash
cd ~/Documents/synergyAI/skills
python3 -c "import json; json.load(open('workflow-compile/references/schema.json')); print('schema ok')"
```
Expected: `schema ok`.

- [ ] **Step 3: Commit:**

```bash
git add workflow-compile/references/schema.json
git commit -m "feat(workflow-compile): add optional output block to the simple-language schema

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `compile.py` — validate + apply the `output` block (TDD)

**Files:** Modify `workflow-compile/scripts/compile.py`; Create `workflow-compile/tests/test_compile_output.py`.

- [ ] **Step 1: Write the failing test — `workflow-compile/tests/test_compile_output.py`:**

```python
"""Standalone tests (no pytest needed): python3 tests/test_compile_output.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import compile as c  # workflow-compile/scripts/compile.py


def wf(output=None):
    w = {
        "name": "T", "start": {"prompt": "go"},
        "agents": [{"id": "a", "name": "A", "provider": "openai", "instructions": "x"}],
        "flow": [["start", "a"], ["a", "output"]],
    }
    if output is not None:
        w["output"] = output
    return w


def agent_node(dsl):
    return next(n for n in dsl["definition"]["nodes"] if n["node_type"] == "agent")


# storage + format auto-bind
d = c.compile_workflow(wf({"store": True, "folder": "reports", "format": "pdf"}))
assert d["output_storage_enabled"] == 1, d["output_storage_enabled"]
assert d["output_folder"] == "reports", d["output_folder"]
assert agent_node(d)["config"]["bound_skill"]["dir_name"] == "report-pdf"
print("ok storage+format")

# no output -> defaults unchanged
d = c.compile_workflow(wf())
assert d["output_storage_enabled"] == 0 and d["output_folder"] is None
assert agent_node(d)["config"]["bound_skill"] is None
print("ok defaults")

# explicit skill_binding is NOT overridden
w = wf({"format": "pdf"}); w["agents"][0]["skill_binding"] = "html"
assert agent_node(c.compile_workflow(w))["config"]["bound_skill"]["dir_name"] == "html"
print("ok no-override")

# bad format -> ValidationError
try:
    c.compile_workflow(wf({"format": "xlsx"})); assert False, "expected ValidationError"
except c.ValidationError as e:
    assert any("format" in x for x in e.errors), e.errors
print("ok bad-format")

# unsafe folder -> ValidationError
try:
    c.compile_workflow(wf({"folder": "../etc"})); assert False, "expected ValidationError"
except c.ValidationError as e:
    assert any("folder" in x for x in e.errors), e.errors
print("ok unsafe-folder")

print("ALL PASS")
```

- [ ] **Step 2: Run it — expect FAIL** (no `output` handling yet → storage stays 0, no bind):

```bash
cd ~/Documents/synergyAI/skills
python3 workflow-compile/tests/test_compile_output.py
```
Expected: `AssertionError` on the first assert (`output_storage_enabled == 1`).

- [ ] **Step 3: Add the `FORMAT_SKILL` map + `validate_output`.** In `scripts/compile.py`, after the existing module constants (e.g. after `ALLOWED_PROVIDERS`), add:

```python
FORMAT_SKILL = {"pdf": "report-pdf", "html": "html", "docx": "docx", "md": None}
ALLOWED_FORMATS = set(FORMAT_SKILL)


def validate_output(simple: dict) -> list[str]:
    """Validate the optional top-level `output` block. Never raises — errors aggregated."""
    out = simple.get("output")
    if out is None:
        return []
    if not isinstance(out, dict):
        return ['root: "output" must be an object']
    errors: list[str] = []
    fmt = out.get("format", "md")
    if fmt not in ALLOWED_FORMATS:
        errors.append(
            f"output.format {fmt!r} is not allowed; use one of: "
            + ", ".join(sorted(ALLOWED_FORMATS))
        )
    folder = out.get("folder")
    if folder is not None:
        if not isinstance(folder, str) or not folder.strip():
            errors.append('output.folder must be a non-empty string')
        elif folder.startswith("/") or ".." in folder.split("/"):
            errors.append(
                f'output.folder {folder!r} must be a safe relative path (no leading "/", no "..")'
            )
    if not isinstance(out.get("store", False), bool):
        errors.append('output.store must be a boolean')
    return errors
```

- [ ] **Step 4: Aggregate output errors.** In `compile_workflow`, find:

```python
    all_errors = schema_errors + graph_errors + mcp_errors
```
Replace with:
```python
    output_errors = validate_output(simple)
    all_errors = schema_errors + graph_errors + mcp_errors + output_errors
```

- [ ] **Step 5: Auto-bind the formatter on terminal agents.** In `compile_workflow`, immediately **before** the line `nodes = [build_start_node(dsl_id["start"], simple, positions["start"])]`, insert:

```python
    # Optional output block: auto-bind a formatter skill on terminal agents (edge -> "output").
    out_cfg = simple.get("output") or {}
    formatter = FORMAT_SKILL.get(out_cfg.get("format", "md"))
    if formatter:
        terminal_ids = {f for f, t in flow if t == "output"}
        for a in agents:
            if isinstance(a, dict) and a.get("id") in terminal_ids and not a.get("skill_binding"):
                a["skill_binding"] = formatter
```

- [ ] **Step 6: Map the storage fields.** In `compile_workflow`'s `return { ... }`, replace:

```python
        "output_storage_enabled": 0,
        "output_folder": None,
```
with:
```python
        "output_storage_enabled": 1 if out_cfg.get("store", False) else 0,
        "output_folder": (out_cfg.get("folder") or None),
```

- [ ] **Step 7: Run the test — expect PASS:**

```bash
cd ~/Documents/synergyAI/skills
python3 workflow-compile/tests/test_compile_output.py
```
Expected: five `ok ...` lines then `ALL PASS` (exit 0).

- [ ] **Step 8: Regression — existing examples still compile:**

```bash
cd ~/Documents/synergyAI/skills/workflow-compile
python3 scripts/compile.py -i references/example-newspaper.simple.json -o /tmp/np.json && echo "newspaper ok"
python3 scripts/compile.py -i references/example-sequential.simple.json -o /tmp/seq.json && echo "sequential ok"
```
Expected: both print `wrote …` + `ok` (no output block → `output_storage_enabled:0`, unchanged).

- [ ] **Step 9: Commit:**

```bash
cd ~/Documents/synergyAI/skills
git add workflow-compile/scripts/compile.py workflow-compile/tests/test_compile_output.py
git commit -m "feat(workflow-compile): compile the output block (storage + format auto-bind) with validation

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Golden example + docs

**Files:** Create `workflow-compile/references/example-market-report.simple.json`, `…/example-market-report.dsl.json`; Modify `workflow-compile/README.md`, `workflow-compile/references/providers-and-defaults.md`.

- [ ] **Step 1: `references/example-market-report.simple.json`:**

```json
{
  "name": "Market Report",
  "description": "Parallel research on metals, cryptos, stocks, finance → a PDF report stored in reports.",
  "start": { "prompt": "Produce a coherent market report: latest on metals, cryptos, stocks, and finance/macro." },
  "output": { "store": true, "folder": "reports", "format": "pdf" },
  "agents": [
    { "id": "metals",  "name": "Metals Researcher",  "provider": "grok",     "tools": [], "instructions": "Research metals; facts + sources." },
    { "id": "cryptos", "name": "Crypto Researcher",  "provider": "kimi",     "tools": [], "instructions": "Research crypto; confirmed news + sources." },
    { "id": "stocks",  "name": "Stocks Researcher",  "provider": "openai",   "tools": [], "instructions": "Research equities; key moves + sources." },
    { "id": "finance", "name": "Finance Researcher", "provider": "gemini",   "tools": [], "instructions": "Research macro/finance; key indicators + sources." },
    { "id": "report",  "name": "Report Publisher",   "provider": "deepseek", "tools": [], "instructions": "Synthesize the four briefs into ONE coherent report (markdown): a section per topic + an executive summary." }
  ],
  "flow": [
    ["start","metals"], ["start","cryptos"], ["start","stocks"], ["start","finance"],
    ["metals","report"], ["cryptos","report"], ["stocks","report"], ["finance","report"],
    ["report","output"]
  ]
}
```

- [ ] **Step 2: Generate the `.dsl.json` from it (this IS the reference output):**

```bash
cd ~/Documents/synergyAI/skills/workflow-compile
python3 scripts/compile.py -i references/example-market-report.simple.json -o references/example-market-report.dsl.json
python3 -c "import json;d=json.load(open('references/example-market-report.dsl.json'));print('store',d['output_storage_enabled'],'folder',d['output_folder']);print('bound',[n['config']['bound_skill'] for n in d['definition']['nodes'] if n['node_type']=='agent' and n['config']['bound_skill']])"
```
Expected: `store 1 folder reports` and the `report` agent's `bound_skill.dir_name == "report-pdf"`.

- [ ] **Step 3: Document the output block in `workflow-compile/README.md`.** After the simple-language section (the `## 1. The simple language` block), add:

```markdown
### Output: storage + format

An optional top-level `output` block controls where the result is stored and in what format:

\`\`\`jsonc
"output": { "store": true, "folder": "reports", "format": "pdf" }   // pdf | html | docx | md
\`\`\`

- `store`/`folder` → the DSL's `output_storage_enabled` / `output_folder` (folder is a subfolder under the outputs root).
- `format` (non-`md`) **auto-binds a formatter skill** on the terminal agent(s): `pdf → report-pdf`, `html → html`, `docx → docx`. An agent with its own `skill_binding` is left untouched.

See `references/example-market-report.simple.json` / `.dsl.json`.
```

- [ ] **Step 4: Note the now-settable fields in `references/providers-and-defaults.md`.** Find the rows:

```
| `output_storage_enabled` | `0` |
| `output_folder` | `null` |
```
Replace with:
```
| `output_storage_enabled` | `0` (or `1` when the simple `output.store` is true) |
| `output_folder` | `null` (or the simple `output.folder`) |
```

- [ ] **Step 5: Commit:**

```bash
cd ~/Documents/synergyAI/skills
git add workflow-compile/references/example-market-report.simple.json workflow-compile/references/example-market-report.dsl.json workflow-compile/README.md workflow-compile/references/providers-and-defaults.md
git commit -m "docs(workflow-compile): output-block docs + market-report golden example pair

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Done — outcome

A prompt like *"research metals/cryptos/stocks/finance in parallel, synthesize into a PDF report stored in reports"* now compiles end‑to‑end: the model authors the simple JSON with an `output` block → `compile.py` sets `output_storage_enabled:1` / `output_folder:"reports"` and binds `report-pdf` on the terminal agent → the workflow runs and writes a PDF to `<outputs>/reports`.
