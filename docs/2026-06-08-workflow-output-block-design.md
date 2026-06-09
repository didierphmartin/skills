# Workflow `output` block — Design Spec

- **Date:** 2026-06-08
- **Repo:** `skills` (synergyAI)
- **Affects:** `workflow-compile` (`references/schema.json`, `scripts/compile.py`) + a new `report-pdf` skill
- **Status:** Approved design → ready for implementation planning

## 1. Goal

Let the **simple workflow language** express *where the result is stored* and *in what format*, so a
prompt like *"…synthesize into a coherent PDF report stored in reports"* compiles end‑to‑end. Today the
simple schema is `{name, description, start, agents, flow}` with `additionalProperties: false` — it
cannot express storage location or output format, so those requirements silently fall on the floor (the
compiler hard‑codes `output_storage_enabled: 0`, `output_folder: null`).

## 2. Decisions (locked in brainstorming)

- Add an **optional top‑level `output: { store, folder, format }`** to the simple language.
- `store` / `folder` → the DSL's `output_storage_enabled` / `output_folder`.
- **`format` auto‑binds a formatter skill** (chosen): `compile.py` maps the format to a skill and sets
  it as `bound_skill` on the **terminal agent(s)** — `pdf → report-pdf`, `html → html`, `docx → docx`,
  `md → none` (raw markdown).
- New **`report-pdf` skill** using **`fpdf2`** (pure‑Python, Pyodide‑safe — the same engine
  `linkedin-carousel` already uses).

## 3. Non‑Goals

- Other formats now (`xlsx`, `pptx`) — the map is extensible later.
- Multiple distinct outputs per workflow (single `output` terminus).
- A reverse transpile (graphical → simple).
- Changing how the runner *executes* a bound skill — we rely on the existing `config.bound_skill`
  mechanism that `compile.py` already emits.

## 4. Architecture

### 4.1 `references/schema.json`
Add an optional `output` property (the rest of the object stays `additionalProperties: false`, which
already disallows anything not listed — adding `output` to `properties` is sufficient):

```jsonc
"output": {
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "store":  { "type": "boolean", "default": false },
    "folder": { "type": "string",  "description": "Relative subfolder under the outputs root, e.g. \"reports\". No absolute paths, no \"..\"." },
    "format": { "type": "string", "enum": ["pdf", "html", "docx", "md"], "default": "md" }
  }
}
```

### 4.2 `scripts/compile.py`
- Parse `output` (absent → today's behavior: `output_storage_enabled:0`, `output_folder:null`, no auto‑bind).
- **Storage:** `output_storage_enabled = 1 if output.store else 0`; `output_folder = output.folder or null`.
- **Format auto‑bind:** compute the **terminal agents** (any agent with an edge `[agent, "output"]`).
  For each, if `FORMAT_SKILL[format]` is non‑null **and the agent has no explicit `skill_binding`
  already**, set its `bound_skill` to that skill. Map:
  `{"pdf": "report-pdf", "html": "html", "docx": "docx", "md": None}`.
- **Validation** (aggregated, like the rest of the compiler):
  - `format` ∉ enum → error.
  - `folder` absolute or containing `..` → error.
  - `format` maps to a skill that isn't in `references/skills-catalog.md` → error with a hint.
  - An agent that already has an explicit `skill_binding` is **not overridden** — emit a note, keep theirs.

### 4.3 New `report-pdf` skill (`skills/report-pdf/`)
- `SKILL.md` — when to use (turn synthesized content into a PDF), runtime contract (call
  `scripts/build.py`, read the output), and the strict "only deliver via the script" rule.
- `scripts/build.py` — reads the agent's synthesized **markdown/HTML** content from an input file,
  renders a **styled PDF via `fpdf2`**, writes to `/outputs/<name>.pdf`. Pure‑Python; mirrors
  `linkedin-carousel`'s structure.
- Bundled by the format auto‑bind (`format:"pdf"` → this skill on the terminal agent).

## 5. Data flow

`prompt → model authors simple JSON (with output block) → compile.py: graph→DSL + storage fields +
auto‑bind report-pdf on terminal agent → DSL → editor/runner → terminal agent runs report-pdf → PDF
written to <outputs>/<folder>`.

## 6. Error / Edge handling

| Case | Behavior |
|---|---|
| No `output` block | Unchanged: `output_storage_enabled:0`, `output_folder:null`, no auto‑bind |
| `format` not in enum | Validation error |
| `folder` absolute / has `..` | Validation error |
| `format:"pdf"` but `report-pdf` not installed | Error with install/create hint |
| Terminal agent already has `skill_binding` | Keep the explicit one; skip auto‑bind; note it |
| Multiple terminal agents | Bind the formatter to each (single terminus is typical) |

## 7. Testing

- **compile.py:** a `.simple.json` with `output:{store:true, folder:"reports", format:"pdf"}` →
  DSL has `output_storage_enabled:1`, `output_folder:"reports"`, and the terminal agent's
  `config.bound_skill.dir_name == "report-pdf"`. Reject bad `format`; reject unsafe `folder`;
  no‑`output` → defaults unchanged; explicit `skill_binding` not overridden.
- **report-pdf:** `build.py` renders a non‑empty PDF from sample markdown (smoke; check magic bytes `%PDF`).
- **Golden pair:** `references/example-market-report.simple.json` + `.dsl.json` (the metals/cryptos/
  stocks/finance → PDF‑in‑`reports` workflow) as a reference.

## 8. Decomposition (for the plan)

1. **`report-pdf` skill** — `SKILL.md` + `scripts/build.py` (fpdf2) + smoke test.
2. **`schema.json`** — add the `output` block.
3. **`compile.py`** — parse `output` → storage fields + format auto‑bind + validation (TDD).
4. **Example pair** (`example-market-report.*`) + docs update (`workflow-compile/README.md`,
   `providers-and-defaults.md`).

## 9. Future

`xlsx`/`pptx` formats (extend the map + skills); per‑branch outputs; output filename templating.
