# servicenow-automation-author

Makes a SynergyAI **automation** — something a person orders in the ServiceNow catalog and SynergyAI fulfils — from
one spec, so the two halves are generated together and stay in sync:

- **[ServiceNow]** a catalog item (the order form) and a flow that gives SynergyAI a task, through
  `servicenow-flow-author`;
- **[SynergyAI]** an unattended playbook (`playbook-author`) inside a workflow (`workflow-compile`):
  `Start [ServiceNow: get new job] → playbook → complete job (done) / hand back (failed) → output`.

## Automations vs skills

A **skill** is a tool the model uses; an **automation** is a finished product that one of those skills builds and
that then runs without anyone watching.

| | skill | automation |
|---|---|---|
| what it is | a folder: `SKILL.md`, scripts, references | a record in the app (`save_automation`, Automations view) linking a ServiceNow flow, a playbook and a workflow |
| who uses it | the model, in a chat or as a workflow step | employees, by ordering an item in the ServiceNow catalog |
| trigger | a request in chat, or a node that calls the skill | a ServiceNow order: the flow creates a task tagged `synergyai:<key>` and the workflow picks it up |
| where it runs | in the browser's Pyodide (`run_skill_script`) | across two systems: the flow in ServiceNow, the playbook and workflow in SynergyAI |
| person involved | yes: someone is chatting; results go to the chat or `/outputs/` | no: the playbook can't ask questions, every action maps to an MCP tool, approvals happen in ServiceNow first |
| produces | files or an answer | real actions (Okta, GitHub, Slack…), then the task is closed or handed back |
| versioning | the skill version in its frontmatter; updated by editing the folder | each regeneration bumps N; the stamp `<key>@<N>` keeps both sides in step |
| deployment | the skills folder or the skill store | a ServiceNow instance (`install_automation` or `build-install.sh`), with the workflow left switched on |

This skill runs once, at design time, to create or regenerate an automation (using `servicenow-flow-author`,
`playbook-author` and `workflow-compile` for its parts). After that the automation runs on its own for every order,
and no skill is involved. In short, the skill is the factory and the automation is what it makes.

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
