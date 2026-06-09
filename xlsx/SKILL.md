---
name: xlsx
description: "Create or edit Excel (.xlsx, .xlsm) files. Use this skill whenever the user wants to GENERATE a new spreadsheet (report, model, table, etc.) or modify cells in an existing one (find/replace, set cells, append/delete rows). Pyodide-native — no shell, no LibreOffice. READING / EXTRACTION is NOT this skill's job: when the user attaches an .xlsx, the platform-level attachment converter has already turned it into Markdown that's in your context. Cannot recalculate formulas at write time (Excel/LibreOffice does that on first open) or render thumbnails/PDFs."
license: MIT
dependencies: openpyxl
metadata:
  version: 2.0.0
  origin: synergyAI custom (writer-only — reads handled by platform attachment converter)
allowed-tools: run_skill_script
---

# XLSX skill (Pyodide-native, writer-only)

## CRITICAL — runtime contract (read first, no exceptions)

**You MUST call `run_skill_script` for every user request that involves CREATING or EDITING a spreadsheet.** This skill is non-functional without a tool call. Specifically:

- **Never claim a spreadsheet was created or edited unless you have actually called `run_skill_script` and received a tool_result with `exit_code: 0`.** Hallucinating "I've created the report at ..." when you haven't called the tool wastes the user's time and leaves them looking for a file that doesn't exist.
- **Don't paraphrase the script's output as if you ran it.** If you didn't call the tool, you didn't run it.
- **One user intent → one or more script calls.** "Build me a sales report" → call `create.py`. "Find and replace in this workbook" → call `edit.py`.
- **Do NOT try to "extract" an xlsx with this skill.** When the user attaches a spreadsheet, the frontend attachment converter has already converted it to Markdown and it's already in your message context. Read it directly. There is no `extract.py` here.
- **Don't ask clarifying questions you can answer yourself.** "Make a Q3 sales report" → make reasonable choices (one sheet, sensible headers, totals row, basic formatting) and produce the file. Iterate after.
- **The output path you pass via argv MUST appear in `read_outputs`** so the file is surfaced back. Forgetting this means the user sees no result even though the file is on disk.

If you cannot satisfy a request via these scripts (PDF export, charts beyond bar/line/pie, macro execution), say so plainly — don't fake it.

---

Write spreadsheets in pure Python via `openpyxl`. Two scripts cover the workflow:

| Goal | Script | Inputs | Outputs |
|------|--------|--------|---------|
| Create a new xlsx from a structured spec | `scripts/create.py` | `.json` spec | `.xlsx` |
| Edit an existing xlsx (find/replace, cell ops) | `scripts/edit.py` | `.xlsx` + ops | `.xlsx` |

## When to use which script

- User asks for a new spreadsheet (report, model, plan, etc.) → **`create.py`** with a JSON spec describing sheets and rows.
- User attaches a spreadsheet and wants targeted changes → **`edit.py`** with find/replace and/or cell ops. The original file is at `/scratch/<filename>.xlsx`.
- User attaches a spreadsheet and asks for ANALYSIS or SUMMARY → don't call any script. The attachment is already in your context as Markdown; reason over that and respond in chat.

## What this skill CANNOT do

Be honest with the user — don't promise and fail:

- **Recalculate formulas at write time**. `openpyxl` writes the formula text but doesn't compute the cached value. Excel and LibreOffice recalculate on first open, so the user will see correct values when they open the file. If the user needs the cached value computed without opening, they need LibreOffice.
- **Render thumbnails / preview images** (needs LibreOffice/Excel).
- **Export to PDF** (needs LibreOffice/Excel).
- **Run or modify VBA macros** (read-only via openpyxl `keep_vba`; can't execute).
- **Pivot tables** — openpyxl preserves them on round-trip but can't author from scratch easily.
- **Conditional formatting beyond simple cell-level styles** — basic support only.
- **Complex charts** (combo, scatter w/ multiple series, etc.) — bar/line/pie/area only.
- **Read .xlsx files** — that's the platform converter's job, not yours.

## scripts/create.py — build a new xlsx from a spec

```
python scripts/create.py -i /scratch/<spec>.json -o /outputs/<name>.xlsx
```

### Spec format

```json
{
  "metadata": {"title": "Q3 Sales", "author": "Synergy", "subject": "...", "keywords": "..."},
  "sheets": [
    {
      "name": "Q3 Sales",
      "header": ["Region", "Q1", "Q2", "Q3", "YoY"],
      "rows": [
        ["NA",   12450, 13200, 15300, "=E2/B2-1"],
        ["EU",    8720,  9100, 10540, "=E3/B3-1"],
        ["APAC",  6890,  7400,  8690, "=E4/B4-1"],
        ["Total", "=SUM(B2:B4)", "=SUM(C2:C4)", "=SUM(D2:D4)", "=E5/B5-1"]
      ],
      "column_widths": [12, 12, 12, 12, 12],
      "freeze": "A2",
      "header_style": {"bold": true, "fill_rgb": "1F2937", "color_rgb": "FFFFFF", "alignment": "center"},
      "number_formats": {"B:D": "#,##0", "E": "0.0%"},
      "merges": ["A1:E1"],
      "charts": [
        {
          "type": "bar",
          "title": "Sales by Region",
          "categories_range": "A2:A4",
          "values_range": "D2:D4",
          "anchor": "G2",
          "width_in": 5,
          "height_in": 3
        }
      ]
    }
  ]
}
```

### Per-sheet fields

- `name` — sheet tab name (defaults to "Sheet1")
- `header` — first row, bolded with optional `header_style`
- `rows` — array of row arrays. Cell values can be strings, numbers, booleans, or formula strings starting with `=`
- `column_widths` — array of widths in Excel character units (matched positionally to columns)
- `freeze` — cell at which to freeze panes (e.g. "A2" freezes the top row)
- `header_style` — `{bold, italic, font_size_pt, color_rgb, fill_rgb, alignment, font_name}`
- `number_formats` — map of column letters or ranges to Excel format strings (`"#,##0"`, `"0.00%"`, `"yyyy-mm-dd"`, `"$#,##0.00"`, etc.). Range syntax: `"B"`, `"B:D"`, `"A1:E10"`
- `merges` — array of cell ranges to merge (e.g. `["A1:E1"]`)
- `charts` — array of `{type, title, categories_range, values_range, anchor, width_in?, height_in?}`. `type` ∈ {`bar`, `line`, `pie`, `area`}.

### Cell value formats supported

- Number: `12450`, `0.23`
- String: `"NA"`
- Boolean: `true`, `false`
- Date as ISO string: `"2026-05-07"` → automatically parsed
- Formula: `"=SUM(B2:B4)"` (any string starting with `=`)
- `null` → empty cell

## scripts/edit.py — modify an existing xlsx

```
python scripts/edit.py -i /scratch/<input>.xlsx -o /outputs/<output>.xlsx \
       --ops /scratch/<ops>.json
```

### Ops file format

```json
{
  "find_replace": [
    {"find": "{{client}}", "replace": "Acme Corp", "case_sensitive": true},
    {"find": "OLD COMPANY", "replace": "NewCo"}
  ],
  "set_cells": [
    {"sheet": "Q3 Sales", "cell": "F1", "value": "Reviewed"},
    {"sheet": "Q3 Sales", "cell": "B5", "value": "=SUM(B2:B4)"}
  ],
  "append_rows": [
    {"sheet": "Q3 Sales", "row": ["LATAM", 3200, 3500, 4100, "=E6/B6-1"]}
  ],
  "delete_rows": [
    {"sheet": "Q3 Sales", "from": 10, "count": 2}
  ]
}
```

Operations apply in declared order. Find/replace runs on string cell values across all sheets (formulas excluded; use `set_cells` for those).

## Output paths

Every script writes to absolute paths under `/outputs/`. ALWAYS include the output path in `read_outputs` when calling `run_skill_script`.

## Examples

**Create a Q3 sales report:**
```json
{
  "script": "scripts/create.py",
  "argv": ["-i", "/scratch/spec.json", "-o", "/outputs/q3-sales.xlsx"],
  "input_files": {
    "/scratch/spec.json": "{\"sheets\":[{\"name\":\"Q3\",\"header\":[\"Region\",\"Revenue\"],\"rows\":[[\"NA\",15300],[\"EU\",10540]]}]}"
  },
  "read_outputs": ["/outputs/q3-sales.xlsx"]
}
```

**Apply a find/replace across a template** (the user's template is at `/scratch/<filename>.xlsx` from the attachment pre-write):
```json
{
  "script": "scripts/edit.py",
  "argv": ["-i", "/scratch/template.xlsx", "-o", "/outputs/filled.xlsx", "--ops", "/scratch/ops.json"],
  "input_files": {
    "/scratch/ops.json": "{\"find_replace\":[{\"find\":\"{{client}}\",\"replace\":\"Acme Corp\"}]}"
  },
  "read_outputs": ["/outputs/filled.xlsx"]
}
```
