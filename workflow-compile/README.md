# workflow-compile

Author a multi‑agent workflow in a compact **simple language**, and compile it into the **graphical
DSL** that the visual workflow editor draws and the engine runs.

There are two representations of the same workflow, and this skill is the bridge between them:

| | Simple language | Graphical DSL |
|---|---|---|
| **Form** | compact JSON: `start` + inline `agents` + `flow` edges | positioned nodes + ports + numeric ids |
| **Author** | an LLM (from a prompt) or a human, by hand | the visual editor, by dragging nodes |
| **Position‑free?** | yes — no coordinates, no ports | no — `pos_x/pos_y`, `from_port`, ids |
| **Role** | ergonomic authoring surface | what `GraphWorkflowRunner` executes |

```
prompt ──▶ (LLM authors) simple JSON ──▶ scripts/compile.py ──▶ graphical DSL ──▶ editor / runner
```

This is the capability behind *"ask in chat to build a workflow and it appears in the editor."*

---

## 1. The simple language

A single JSON object. Full schema: [`references/schema.json`](references/schema.json).

```jsonc
{
  "name": "...",                 // required
  "description": "...",          // optional
  "start": { "prompt": "...", "documents": [] },   // required prompt; optional docs
  "agents": [                    // required, ≥1 — agents are defined INLINE
    {
      "id": "researcher",        // required — referenced by flow; NOT "start"/"output"
      "name": "Research Agent",  // required
      "provider": "openai",      // required — openai | anthropic | kimi | grok | deepseek | gemini
      "instructions": "...",     // required — system prompt
      "tools": [],               // optional — must match references/mcp-catalog.md verbatim
      "skill": "...",            // optional — inline procedure text
      "skill_binding": "html",   // optional — bind a folder-backed skill by dir_name
      "model": "", "max_tokens": 4096, "temperature": 0.7,
      "agent_template_id": null, "output_schema_id": null
    }
  ],
  "flow": [ ["start", "researcher"], ["researcher", "output"] ]   // directed edges
}
```

**Reserved ids:** `start` (entry; source‑only) and `output` (terminus; target‑only) — exactly one
each, always emitted. The graph must be a **DAG**; every agent must be reachable from `start` and have
a path to `output`.

**Parallelism is implicit in the edges — there is no node to declare:**
- **Fan‑out** = several edges from one source → those targets run in parallel.
- **Fan‑in** = several edges into one target → it receives all inputs (compiler sets
  `merge_strategy: "labeled"`).

See [`references/example-newspaper.simple.json`](references/example-newspaper.simple.json) (fan‑out +
fan‑in) and [`references/example-sequential.simple.json`](references/example-sequential.simple.json)
(linear).

---

## 2. The graphical DSL

The compiled output — and what the visual editor produces/imports. Shape (per node / per edge):

```jsonc
// node
{ "id": "2", "node_type": "agent",  // start | output | agent | parallel
  "agent_id": 17, "agent_name": "...", "config": { ... },
  "pos_x": 380, "pos_y": 120 }
// edge
{ "from": "1", "to": "2", "from_port": "output_1", "to_port": "input_1", "condition": null }
// wrapped as
{ "definition": { "runtime_mode": "batch", "nodes": [ ... ], "edges": [ ... ] } }
```

The editor is the **source of truth** for a graphical workflow; positions come from where nodes are
dropped on the canvas. The simple language exists so a workflow can be authored *without* a canvas —
the compiler supplies the geometry.

---

## 3. The compilation (simple → graphical)

`scripts/compile.py -i <name>.simple.json -o <name>.json` performs:

1. **Schema validation** — required fields, types, `provider` enum, duplicate‑id detection.
2. **Graph validation** — DAG (no cycles), reachable‑from‑`start`, path‑to‑`output`, ≥1 edge into `output`.
3. **MCP‑tool validation** — every `mcp_*` in any `tools` is checked verbatim against
   `references/mcp-catalog.md` (strict allowlist; unknown names rejected with closest‑match hints).
4. **Layout** — topological columns, left‑to‑right: `start` at `x=61`, columns **330px** apart,
   siblings stacked **175px** apart around `y=250`.
5. **Emit** the graphical DSL; **write** to the output path. Errors are aggregated on stderr for
   one‑shot self‑correction (exit `0` ok, `1` validation fail, `2` bad/missing input).

### Field mapping

| Simple | Graphical DSL |
|---|---|
| `start.prompt` (+ `documents`) | one `start` node (`agent_id:null`, prompt in config) |
| each `agents[i]` | one `agent` node (inline config → provider, instructions, `skill_content`, tools, model, max_tokens, temperature; `agent_id` = `agent_template_id` or null) |
| implicit terminus | one `output` node |
| node ids | numeric: `start`→`"1"`, agents→`"2".."n+1"`, `output`→`"n+2"` |
| `flow` `[from,to]` | `{ "from": id[from], "to": id[to], "from_port": "output_1" }` |
| fan‑in (≥2 edges → node) | `merge_strategy: "labeled"` (auto) |
| — | defaults: `runtime_mode:"batch"`, layout positions |

> The compiled DSL expresses parallelism by **graph shape + `merge_strategy`** — it does not emit a
> separate `parallel` node (that node type exists in the editor; the compiler doesn't use it). Compare
> a `*.simple.json` with its `*.dsl.json` in [`references/`](references/) to see the exact expansion.

### Reverse direction (graphical → simple)

Not implemented, and largely unnecessary: the editor already *is* the graphical authoring surface, and
the simple language is the compact one. A back‑transpile would just strip geometry/ports/ids and
recover names — straightforward, but only useful for round‑tripping an editor‑built graph back into
text. The forward path (simple → graphical) is the one that matters for LLM authoring.

---

## 4. The chat → editor flow

1. User: *"build a workflow that researches crypto and PubMed in parallel and publishes a newspaper."*
2. The model **authors** the simple JSON (it composes it from intent — it never asks the user to write JSON).
3. The model calls `run_skill_script` → `compile.py`; on error it reads the aggregated list, fixes
   **all** issues, recompiles.
4. The user **imports** the resulting DSL into the workflow editor; it runs on `GraphWorkflowRunner`.

Any of the six providers can author — the only requirement is schema‑conformant structured output,
with the compiler's validation as the guardrail.

---

## Files

| Path | Purpose |
|---|---|
| `SKILL.md` | Skill contract (when to use, runtime rules, validation guide) |
| `scripts/compile.py` | The compiler (simple → graphical DSL) |
| `references/schema.json` | JSON Schema for the simple language |
| `references/example-*.simple.json` | Example workflows (simple) |
| `references/example-*.dsl.json` | The compiled output for each (study to learn the expansion) |
| `references/providers-and-defaults.md` | Allowed providers + hard‑coded DSL defaults |
| `references/mcp-catalog.md`, `skills-catalog.md` | Auto‑generated at app startup (gitignored) |
