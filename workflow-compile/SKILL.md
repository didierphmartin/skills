---
name: workflow-compile
description: "Design and compile a multi-agent workflow FROM INTENT, then write the full Workflow DSL the engine loads. USE scripts/compile.py whenever the request decomposes into multiple steps or independent sources — EVEN IF the user never says 'workflow' or 'agents'. Trigger patterns: 'research X and Y (and Z) [in parallel] then combine/synthesize/compare', 'gather info from several sources and produce a report/PDF', 'do A, then B, then C', 'fan out to several researchers and merge', 'monitor/collect from N feeds and summarize', or any research→write→publish pipeline. RECOGNIZE PARALLELISM: when several things should be looked up independently and then merged, emit MULTIPLE edges from 'start' (fan-out) into a synthesizer node (fan-in) — do NOT chain them sequentially. You author a compact simplified-language JSON (start prompt + inline agents + flow edges); compile.py validates (DAG, reachability, terminus, providers, MCP tool allowlist) and writes the DSL to /outputs/ for the workflow editor. Reserved ids: 'start' and 'output'. Do NOT use for: editing a single agent's prompt (no graph), running a workflow (this only emits the spec), or non-workflow JSON."
license: MIT
metadata:
  version: 0.1.0
  origin: synergyAI custom
allowed-tools: run_skill_script
---

# workflow-compile

## CRITICAL — runtime contract (read first)

You MUST call `run_skill_script` with `scripts/compile.py` to produce the workflow file. Specifically:

- **Never claim a workflow was created unless you called `run_skill_script` and got `exit_code: 0`.** Pasting the simplified JSON in chat is not a deliverable — only the compiled DSL written to `/outputs/` is.
- **Write the simplified JSON yourself.** You compose it from the user's intent. Don't ask the user to write JSON.
- **Pass the output path in `read_outputs`** so the user sees the compiled DSL file.
- **If `compile.py` returns errors**, read them, fix the simplified JSON, and call again. Errors are aggregated — fix all in one revision, don't iterate one-at-a-time.

---

## What this skill does

You author a workflow in a **compact "simplified language"** (one JSON object with `start`, `agents`, `flow`). The script compiles it into the **full Workflow DSL** that the engine loads — positioned nodes, edges with ports, full agent configs, defaults, layout, the whole shape.

```
simplified JSON  ──▶  scripts/compile.py  ──▶  full DSL JSON  ──▶  /outputs/<name>.json
```

The user then imports the DSL into the workflow editor (or, later, the skill will POST it directly to the backend).

## Step-by-step

1. **Write the simplified JSON** to `/scratch/<name>.simple.json` based on the user's request. Use the shape in [References](#references) below.
2. **Call `run_skill_script`** with:
   - `script_path`: `scripts/compile.py`
   - `argv`: `["-i", "/scratch/<name>.simple.json", "-o", "/outputs/<name>.json"]`
   - `read_outputs`: `["/outputs/<name>.json"]`
3. **If it errors**, read the aggregated error list, rewrite the simplified JSON, call again. The error messages name the exact field and the fix.
4. **On success**, briefly tell the user the workflow has been saved and summarize the graph (1–3 bullets: number of agents, providers used, fan-out/fan-in structure). Don't paste the DSL.

## The simplified language

```jsonc
{
  "name": "...",                    // required
  "description": "...",             // optional
  "start": {
    "prompt": "...",                // required — user-facing kickoff prompt
    "documents": []                 // optional — paths or full doc objects
  },
  "agents": [                       // required, ≥1
    {
      "id": "researcher",           // required — referenced by flow edges; NOT "start" or "output"
      "name": "Research Agent",     // required — display name
      "description": "...",         // optional
      "provider": "openai",         // required — one of: openai, anthropic, kimi, grok, deepseek, gemini
                                    //   (anthropic = Claude; these are the ONLY supported providers)
      "instructions": "...",        // required — system prompt
      "skill": "...",               // optional — inline procedure / output-format guidance
      "tools": [],                  // optional — every entry must appear verbatim in references/mcp-catalog.md (strict-all)
      "skill_binding": "medium-format",  // optional — bind a folder-backed skill (string = dir_name)
      "agent_template_id": null,    // optional — int, if binding to an existing template
      "model": "",                  // optional — empty lets the engine pick the default
      "max_tokens": 4096,           // optional — default 4096
      "temperature": 0.7,           // optional — default 0.7
      "output_schema_id": null      // optional — int, if binding to a structured-output schema
    }
  ],
  "flow": [                         // required — directed edges
    ["start", "researcher"],
    ["researcher", "output"]
  ]
}
```

### Reserved ids and structural rules

- `"start"` is the implicit entry node (carries the user prompt + documents). It can only be a **source** in `flow`.
- `"output"` is the implicit terminus node. It can only be a **target** in `flow`.
- Exactly one start, exactly one output — both always emitted.
- Every workflow must be a **DAG** (no cycles).
- Every agent must be **reachable from `"start"`** AND have a **path to `"output"`** — no orphans, no dead branches.
- At least one edge must end at `"output"`.

### Fan-out and fan-in

- **Fan-out (parallel execution)**: multiple edges from the same source. Example: `["start","crypto"], ["start","pubmed"]` → both agents run in parallel after start.
- **Fan-in (merge)**: multiple edges into the same target. Example: `["crypto","publisher"], ["pubmed","publisher"]` → publisher receives both outputs (merged with `merge_strategy: "labeled"` automatically).

You don't need to declare these — just write the edges; the compiler infers parallelism and merging from the graph shape.

### Function names in `tools` — STRICT allowlist

The `tools` array on each agent may contain function names. **The compiler enforces a strict allowlist** against `references/mcp-catalog.md` (auto-refreshed at app startup). The catalog is the **sole source of truth** for what's valid.

- **Read `references/mcp-catalog.md` first.** It lists every function this user can call, with the mandatory `mcp_` prefix already applied (e.g. `mcp_search_arxiv`, `mcp_pubmed_search`, `mcp_get_crypto_news`).
- **Every entry in `tools` must appear verbatim in the catalog.** Copy the names exactly — prefix included.
- **If no listed function fits the agent's job, leave `tools: []` empty.** A clean empty array is always valid and is the right answer when no catalog function applies.
- **Inventing names is forbidden — with or without the `mcp_` prefix.** Generic guesses like `mcp_search`, `web_search`, `api_call`, `data_retrieval`, `mcp_lookup` are all rejected by the compiler. There is no escape hatch via dropping the prefix — the catalog is the only authority.
- The platform does **not** have built-in non-MCP tools that are universally available across providers. If you want a function, it must come from the catalog.

The compiler error you'll see on a mismatch:
```
agents[i] 'x': tool 'mcp_search' is not in the MCP catalog. Did you mean: mcp_search_arxiv, mcp_pubmed_search, mcp_search_metals_news?
```
The suggestions are real catalog entries — pick one (or none) and recompile.

### `skill` vs `skill_binding` — important distinction

- **`skill`** — *inline* procedure text the agent prepends to its system prompt. Use this for ad-hoc procedure / output-format guidance that lives inside the workflow definition.
- **`skill_binding`** — a *reference* to an external folder-backed skill (under `~/Documents/synergyAI/skills/`). Use this when the agent should call a Python script from a skill (e.g., `medium-format`, `html`, `docx`). String form is the skill's dir_name; the compiler expands it. Distinct from inline `skill`.

Both can coexist on one agent.

## Validation errors — how to read them

If `compile.py` exits non-zero, stderr contains a numbered list of all problems found. Common patterns:

| Error | What it means | Fix |
|---|---|---|
| `agents[i] 'x': provider 'foo' is not in allowed set (...)` | Unknown provider | Use one from the list shown |
| `flow[i]: unknown source id 'foo' — known ids: ...` | Typo in an edge endpoint | Use an id from the known list |
| `graph: cycle detected involving nodes: a, b` | Loop in the graph | Remove the back-edge — workflows are DAGs |
| `graph: agent 'x' has no path to "output"` | Dead branch | Add an edge from `x` (directly or via others) to `"output"` |
| `graph: agent 'x' is not reachable from "start"` | Orphan | Add an edge from `"start"` (directly or via others) to `x` |
| `graph: no edge ends at "output"` | Forgot the terminus | Add `["last_agent", "output"]` |
| `agents[i] 'x': tool 'foo' is not in the MCP catalog. Did you mean: ...` | Hallucinated name (with or without `mcp_` prefix) | Use a name verbatim from `references/mcp-catalog.md`, or leave `tools: []` empty |
| `agents[i] 'x': tool 'foo' is not a valid function — no MCP servers are configured...` | No functions available for this user | Leave `tools: []` empty |
| `catalog: MCP catalog not found at ...` | Catalog file missing | The app should auto-generate it at startup; ask the user to reload |

All errors come at once — fix them all, then recompile. Do not iterate one-at-a-time.

## References

Read these directly when authoring a workflow:

| File | Purpose |
|---|---|
| `references/schema.json` | Machine-readable JSON Schema for the simplified language |
| `references/example-newspaper.simple.json` | Branching example (fan-out + fan-in): start → [crypto ∥ pubmed] → publisher → output |
| `references/example-newspaper.dsl.json` | The full DSL output for the above (study to learn what the compiler produces) |
| `references/example-sequential.simple.json` | Minimal linear example: start → researcher → writer → output |
| `references/example-medium-format.simple.json` | Single agent with a skill_binding + start document object |
| `references/example-medium-format.dsl.json` | The full DSL output for the above |
| `references/providers-and-defaults.md` | Allowed providers, default settings, hard-coded DSL fields |
| `references/mcp-catalog.md` | **MCP functions — strict allowlist for `tools: ["mcp_*"]`.** Auto-refreshed at app startup. Every name must be copied verbatim. |
| `references/skills-catalog.md` | Installed skills (dir_name + description) — for `skill_binding` choices. Auto-refreshed at app startup. |

## scripts/compile.py — the compiler

```bash
python scripts/compile.py -i /scratch/<name>.simple.json -o /outputs/<name>.json
```

What it does:
1. **Schema validation** — required fields, types, provider enum, duplicate-id detection.
2. **Graph validation** — DAG check, reachability from start, path to output, terminus existence.
3. **MCP catalog validation** — every `mcp_*` entry in any agent's `tools` array is checked against `references/mcp-catalog.md`. Unknown names are rejected with closest-match suggestions.
4. **Layout** — topological columns, left-to-right (start at x=61, columns 330px apart, siblings stacked 175px apart around y=250).
5. **Emit** — full DSL with numeric ids, `output_1` ports, `merge_strategy: "labeled"`, runtime-mode batch, defaults.
6. **Write** to the output path. Errors aggregated on stderr if any.

Exit codes: `0` success, `1` validation failure, `2` input file missing or not JSON.
