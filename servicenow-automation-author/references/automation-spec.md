# The automation spec

You write this one JSON object; `scripts/create.py` derives the ServiceNow flow spec, the playbook draft and the
workflow from it. Never write those three yourself, and never copy the key, the variable names or the version into
several places: the script does it, so they cannot differ.

## Shape

```json
{
  "name": "okta-reset",
  "title": "Okta password reset",
  "description": "An employee locked out of Okta orders a reset; SynergyAI unlocks the account and resets the password.",
  "language": "en",
  "form": {
    "submitted_by": "An employee who is locked out of Okta.",
    "variables": [
      {"name": "user_email", "label": "Your work email", "type": "email", "mandatory": true},
      {"name": "reason", "label": "What happened?", "type": "single_line_text"}
    ]
  },
  "servicenow": {
    "before": [],
    "after": [],
    "placeholders": {}
  },
  "playbook": {
    "trigger": "An employee asks for an Okta password reset.",
    "steps": [
      {"text": "#Search Okta Users on user_email to find the account."},
      {"text": "#Unlock Okta User for that account."},
      {"text": "#Leave Internal Note with what was done and the reason given."}
    ],
    "tools_used": ["Okta"]
  },
  "outcomes": {
    "done": {"close_notes": "playbook summary"},
    "failed": {"hand_back_group": "Service Desk"}
  }
}
```

| key | required | what |
|---|---|---|
| `name` | yes | the automation key: `^[a-z0-9][a-z0-9-]{1,40}$`. It becomes the tag `synergyai:<name>`, the playbook key and the flow name (the flow-author also wants 4–50 chars, starting with a letter). The same key again regenerates the same automation. |
| `title` | yes | shown on the catalog item, the playbook and the workflow, in the user's language |
| `description` | yes | one or two sentences |
| `language` | no | the procedure's language code (default `en`) |
| `form.submitted_by` | yes | who orders the item, and when |
| `form.variables` | yes | the order form, in servicenow-flow-author's variable shape (`name`, `label`, `type`, `mandatory`, `help`, `choices`, `reference`…). They are also the playbook's inputs: the job's `answers` carry them. |
| `servicenow.before` | no | flow steps (servicenow-flow-author's step language) that run before SynergyAI gets the task — e.g. a manager approval |
| `servicenow.after` | no | flow steps that run once the task is closed, by SynergyAI (done) or later by the hand-back group |
| `servicenow.placeholders` | no | values (or extra placeholders used by `before`/`after`): `{"synergyai_group": {"value": "<sys_id>"}}` |
| `playbook.trigger` | yes | what the request is; the script appends ` (automation <name>@<version>)` |
| `playbook.steps` | yes | at least one step, in playbook-author's draft shape (`{"text", "sub"}`) |
| `playbook.tools_used` | no | the services the playbook acts on |
| `playbook.domain` | no | as in playbook-author |
| `outcomes.done.close_notes` | no | what the closed task records (kept in the automation record) |
| `outcomes.failed.hand_back_group` | no | the ServiceNow group a failed job is handed back to (default `Service Desk`) |

No `version`: it comes from `save_automation` and is passed as `--version N`.

## Rules the script checks

- `name` matches the key regex; `title`, `description`, `form.submitted_by`, `playbook.trigger` are set.
- Every variable name matches `^[a-z][a-z0-9_]{0,39}$` (no spaces, no capitals) and none is `synergyai_automation`
  (the script adds that hidden stamp variable itself).
- At least one playbook step.
- Then each part through its own skill — every problem is listed with its prefix:
  - `[flow]` servicenow-flow-author (variable types, step language, placeholders, name length…);
  - `[playbook]` playbook-author in unattended mode: no `#Trigger Form`, `#Request Approval` or
    `#Prompt for Handoff` (nobody answers), and every action must bind to an MCP tool. Data comes from the form
    variables; approvals go in `servicenow.before`.
  - `[workflow]` workflow-compile.

## What the script derives

| | flow spec | playbook draft | workflow |
|---|---|---|---|
| tag | catalog task `correlation_display: "synergyai:<name>"` | key `<name>` | Start args `tag` |
| stamp `<name>@<N>` | task `correlation_id`; hidden read-only variable `synergyai_automation` (default the stamp) | trigger ends ` (automation <name>@<N>)` | Start args `version: N` (an integer) |
| form variables | `form.variables` verbatim, plus the stamp | `inputs` (name, label, type, mandatory) | — |
| group | `assignment_group` placeholder `synergyai_group`, `ah_wait: true` | — | — |
| outcomes | `after` steps once the task closes | — | done → `ServiceNow: complete job`; failed → `ServiceNow: hand back` with `{group: hand_back_group}`; Start args `hand_back_group` |

Flow steps: `before` + the SynergyAI catalog task (id `synergyai_task`) + `after`. The catalog and category are the
placeholders `service_catalog` and `synergyai_category`.
