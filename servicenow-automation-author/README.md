# servicenow-automation-author

Makes a SynergyAI **automation** — something a person orders in the ServiceNow catalog and SynergyAI fulfils — from
one spec, so the two halves are generated together and stay in sync:

- **[ServiceNow]** a catalog item (the order form) and a flow that gives SynergyAI a task, through
  `servicenow-flow-author`;
- **[SynergyAI]** an unattended playbook (`playbook-author`) inside a workflow (`workflow-compile`):
  `Start [ServiceNow: get new job] → playbook → complete job (done) / hand back (failed) → output`.

The model writes one automation spec (`references/automation-spec.md`). `scripts/create.py` derives the three
sub-specs from it and runs the three skills' scripts in-process (imported from the skills root, no subprocess), so
the values that link the halves are copied by code, never retyped:

| linked value | ServiceNow flow | playbook | workflow |
|---|---|---|---|
| tag `<key>` | task `correlation_display = synergyai:<key>` | key `<key>` | Start args `tag` |
| stamp `<key>@<N>` | task `correlation_id`; hidden variable `synergyai_automation` | trigger line | Start args `version` (int) |
| form variables | the order form | `inputs` (the job's `answers`) | — |
| hand-back group | — | — | Start args `hand_back_group`; hand back `{group}` |

```
create.py -i <spec.json> --version N [--vendor <code>] [--app <app>] [--out /outputs] [--catalog <mcp-catalog.md>]
```

`--version` comes from the automation record (`save_automation`). Nothing is written unless all three parts are
VALID; then it writes `<out>/servicenow/<scope>/flow-specs/<key>.json` (and the Fluent project),
`<out>/<key>.playbook.md`, `<out>/<key>.workflow.json` and `<out>/<key>.automation.json` (the spec, the version and
the derived values). Problems are prefixed `[automation]`, `[flow]`, `[playbook]` or `[workflow]`; `NEED_VENDOR`
is passed through from the flow-author.

Tests: `cd servicenow-automation-author && python3 -m unittest tests/test_create.py` (each run writes to a temp dir).
