# Providers & defaults

The simplified language enforces the `provider` field per agent. The compiler rejects values outside this list.

## Allowed providers

These are the **only** supported providers. Anything else is rejected by the compiler.

| Value | Notes |
|---|---|
| `openai` | GPT-family |
| `anthropic` | Claude-family (use `"anthropic"`, not `"claude"`) |
| `kimi` | Moonshot Kimi |
| `grok` | xAI Grok |
| `deepseek` | DeepSeek-family |
| `gemini` | Google Gemini |

Do not invent providers. `mistral`, `groq`, `ollama`, etc. are **not** supported and will be rejected.

If a new provider is ever added platform-side, it must be registered in both `ALLOWED_PROVIDERS` in `scripts/compile.py` and the `enum` in `references/schema.json`.

## Per-agent defaults

| Field | Default | Notes |
|---|---|---|
| `tools` | `[]` | List of MCP tool names. Pass-through — the compiler doesn't validate against your live MCP registry. |
| `skill` | `""` | Optional. Maps to `config.skill_content` in the DSL. |
| `model` | `""` | Empty string lets the engine pick the provider's default model. |
| `max_tokens` | `4096` | |
| `temperature` | `0.7` | |
| `agent_template_id` | `null` | Set to an integer to bind the node to an existing agent template. |
| `output_schema_id` | `null` | Set to an integer to bind a structured-output schema. |
| `skill_binding` | `null` | Optional. String (skill dir_name like `"medium-format"`) or full object. Compiler expands strings to `{id: "local:<name>", name, source: "local", dir_name}`. Distinct from `skill` (inline procedure text). |

## Hard-coded DSL fields

The compiler always emits these on agent nodes — they are not exposed in the simplified language:

| DSL field | Value | Why |
|---|---|---|
| `config.agent_type` | `"worker"` | Workflow engine expects worker-typed agents in the middle of the graph. |
| `config.merge_strategy` | `"labeled"` | Fan-in nodes (multiple incoming edges) need a merge strategy; labeled is the safe default. |
| `config.isTemplate` | `false` | Templates are bound by setting `agent_template_id`, not by flipping this flag. |
| `config.bound_skill` | `null` | Reserved for future per-node skill binding. |
| `config.output_schema` | `null` | The inline schema field; bind by id (`output_schema_id`) instead. |

## Top-level workflow defaults

| DSL field | Value |
|---|---|
| `definition.runtime_mode` | `"batch"` |
| `triggers.schedule.enabled` | `false` |
| `output_storage_enabled` | `0` (or `1` when the simple `output.store` is true) |
| `output_folder` | `null` (or the simple `output.folder`) |

The user can flip these in the editor after import.
