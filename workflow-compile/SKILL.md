---
name: workflow-compile
description: "Compile a multi-agent workflow FROM INTENT into the Workflow DSL the engine runs. USE scripts/compile.py whenever a request decomposes into multiple steps or independent sources — EVEN IF the user never says 'workflow' or 'agents'. Triggers: 'research X and Y [in parallel] then combine/synthesize', 'gather from several sources and produce a report/PDF', 'do A then B then C', 'fan out to researchers and merge', any research→write→publish pipeline. RECOGNIZE PARALLELISM: when things are looked up independently then merged, emit MULTIPLE edges from 'start' (fan-out) into a synthesizer (fan-in) — don't chain them. You author compact JSON (start prompt + inline agents + flow edges); compile.py validates (DAG, reachability, providers, MCP allowlist) and writes the DSL to /outputs/. Reserved ids 'start'/'output'. Do NOT use for: editing one agent's prompt, running a workflow (emits the spec only), or non-workflow JSON."
license: MIT
metadata:
  version: 0.1.0
  origin: synergyAI custom
allowed-tools: run_skill_script
context-references: references/mcp-catalog.md
inject-default-provider: true
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
2. **Assign MCP tools per agent.** For each agent, consult `references/mcp-catalog.md` and populate its `tools` array with every function whose description matches the agent's task (see [Tools](#tools--match-each-agent-to-the-mcp-functions-it-needs)). This is where a workflow gets its live-data capability — don't skip it.
3. **Call `run_skill_script`** with:
   - `script_path`: `scripts/compile.py`
   - `argv`: `["-i", "/scratch/<name>.simple.json", "-o", "/outputs/<name>.json"]`
   - `read_outputs`: `["/outputs/<name>.json"]`
4. **If it errors**, read the aggregated error list, rewrite the simplified JSON, call again. The error messages name the exact field and the fix.
5. **On success**, briefly tell the user the workflow has been saved and summarize the graph (1–3 bullets: number of agents, providers used, fan-out/fan-in structure). Don't paste the DSL.

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
      "provider": "deepseek",       // required — use the "Default provider" from the directive at the END of this skill unless the user names one
                                    //   allowed: openai, anthropic (=Claude), kimi, grok, deepseek, gemini — the ONLY supported providers
      "instructions": "...",        // required — system prompt
      "skill": "...",               // optional — inline procedure / output-format guidance
      "tools": [],                  // attach MCP functions matching this agent's task (see Tools); each entry must appear verbatim in references/mcp-catalog.md
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

### Tools — match each agent to the MCP functions it needs

**This is a first-class authoring step, not an afterthought.** An agent without the right tools can only guess from training data; an agent with them pulls live data. For **every** agent, do this before compiling:

1. **Read `references/mcp-catalog.md`.** It groups every function this user can call under its server, each with a description and the exact `mcp_`-prefixed identifier (e.g. `mcp_battery_news_get_all`, `mcp_pubmed_search`, `mcp_get_crypto_news`).
2. **Match the agent's job to the tool descriptions and attach what fits.** A battery agent gets the battery functions; a biomedical/PubMed agent gets the pubmed functions; a metals agent gets the metals functions. Attach *every* function whose description fits the agent's task — copy the identifier **verbatim**, `mcp_` prefix included.
3. **Leave `tools: []` empty ONLY when the catalog genuinely has nothing relevant** to that agent (a pure writing/formatting agent, or a topic no server covers). Empty is valid, but it must be a deliberate "nothing fits" — never the lazy default.

Example — a battery-research agent and a biomedical-research agent:
```jsonc
{ "id": "battery_researcher", "name": "Battery Researcher", "provider": "deepseek",
  "instructions": "Research the latest battery and EV developments…",
  "tools": ["mcp_battery_news_get_all", "mcp_battery_news_search"] },
{ "id": "biomed_researcher", "name": "Biomedical Researcher", "provider": "anthropic",
  "instructions": "Find peer-reviewed research on the topic…",
  "tools": ["mcp_pubmed_search", "mcp_pubmed_build_query"] }
```

**Hard rules (the compiler enforces these):**
- Every entry in `tools` must appear **verbatim** in `references/mcp-catalog.md` — `mcp_` prefix and all.
- **Inventing names is forbidden — with or without the `mcp_` prefix.** Generic guesses like `mcp_search`, `web_search`, `api_call`, `data_retrieval`, `mcp_lookup` are all rejected. There is no escape hatch via dropping the prefix — the catalog is the only authority, and there is no non-MCP built-in tool set.
- On a mismatch the compiler names the closest real entries — pick one, or leave the array empty:
```
agents[i] 'x': tool 'mcp_search' is not in the MCP catalog. Did you mean: mcp_search_arxiv, mcp_pubmed_search, mcp_search_metals_news?
```

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
