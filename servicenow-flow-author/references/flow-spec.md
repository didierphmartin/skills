# JSON flow spec

You write this; `scripts/create.py` checks it and writes the Fluent. Names of triggers, actions, inputs and
outputs come from `fluent-vocabulary.md` (ServiceNow SDK 4.13.3).

## Top level

| key | required | what |
|---|---|---|
| `name` | yes | kebab-case slug, 4–50 chars; the file is `src/fluent/flows/<name>.now.ts`. The same name again replaces that flow. |
| `title` | yes | flow name shown in Workflow Studio, in the user's language |
| `description` | yes | one or two sentences, in the user's language |
| `language` | yes | the procedure's language code (`en`, `fr`, …); every label stays in it |
| `trigger` | yes | `{"type": …, …inputs}` — see Triggers |
| `form` | with `service_catalog` | the catalog item's order form — see Form |
| `steps` | yes | ordered list of steps — see Steps |
| `placeholders` | no | `{"<name>": {"kind", "description", "value"?}}` — instance data the text cannot know |

## Triggers

`type` is one of the keys in `fluent-vocabulary.md` → Triggers; the other keys are that trigger's inputs.

- Record: `{"type": "record_created", "table": "sys_user", "condition": "active=true"}` (also `record_updated`
  with `"trigger_strategy": "unique_changes"`, and `record_created_or_updated`). The table must be one the SDK
  types, and so must every field in the condition (an encoded query: `^` AND, `^OR` OR, `ISEMPTY`, `ISNOTEMPTY`,
  `LIKE` for "contains" — there is no `CONTAINS`; same operators in every `if`/`until` condition).
  `run_flow_in` defaults to `background`.
- Scheduled: `{"type": "scheduled_daily", "time": {"hours": 9, "minutes": 0, "seconds": 0, "timezone": "UTC"}}`;
  `scheduled_weekly` adds `"day_of_week": 1` (1 = Monday); `scheduled_monthly` adds `"day_of_month": 1`;
  `scheduled_repeat` takes `"repeat": {"hours": 1}`; `scheduled_run_once` takes `"run_in": "2026-10-01 09:00:00"`.
- Catalog: `{"type": "service_catalog"}` with a `form` — the flow runs when someone orders the item; its output
  `trigger.request_item` is the requested item (`sc_req_item`).
- Application: `inbound_email`, `sla_task`, `knowledge_management`.

## Form

A procedure that needs data from people is a catalog item. `form` describes its order form:

```json
"form": {
  "submitted_by": "HR submits this form when it creates a new user record for an employee who hasn't started yet.",
  "catalog": {"placeholder": "service_catalog"},
  "category": {"placeholder": "hr_category"},
  "variables": [
    {"name": "new_hire", "label": "New hire", "type": "reference", "reference": "sys_user", "mandatory": true},
    {"name": "start_date", "label": "Start date", "type": "date", "mandatory": true, "help": "First working day."},
    {"name": "equipment", "label": "Equipment", "type": "list_collector", "reference": "cmdb_model"},
    {"name": "size", "label": "Desk size", "type": "select_box", "choices": {"small": "Small", "large": "Large"}}
  ]
}
```

- `submitted_by`: the procedure's start condition — who submits the form and when (the item's description).
- `catalog`, `category`: always `{"placeholder": "<name>"}`, and both placeholders must be declared under the top-level
  `placeholders` with kind `catalog` / `category` (every item needs both; `FLOWS.md` names them so the user knows
  where the item is listed).
- `variables`: `name` (snake_case, unique on the item), `label` (the question), `type` from
  `fluent-vocabulary.md` → Catalog, `mandatory`, `help`; `reference` and `list_collector` need `"reference":
  "<table>"`, `select_box` and `multiple_choice` need `"choices"`. A checkbox cannot be mandatory (use `yes_no`).
  Optional `default` (text; Fluent `defaultValue`), `read_only` (`readOnly`) and `hidden` (`hidden`), each true/false
  except `default`; a variable cannot be both hidden and mandatory. The SynergyAI stamp is
  `{"name": "synergyai_automation", "label": "SynergyAI automation", "type": "single_line_text", "default": "<key>@<N>", "read_only": true, "hidden": true}`.
- `select_box` (dropdown) and `multiple_choice` (radio buttons) take **one** answer. When the person may pick
  **several** of a fixed list ("VPN, Git, Jira…"), write one `checkbox` variable per option (`vpn`, `git`, …) and
  branch on each: `[{"pill": "form.vpn"}, "=true"]`. Several records from a table → `list_collector`.
- Read an answer anywhere with `{"pill": "form.<name>"}` (a reference can be dot-walked: `form.new_hire.email`).
  A `list_collector` answer is a comma-separated list of sys_ids: look the records up with
  `["sys_idIN", {"pill": "form.equipment"}]`, then `for_each` over `Records`.

## Steps

Every step has `id` (snake_case, unique in the flow, not a JavaScript word) and `label` (≤ 200 chars, user's
language — it becomes the step annotation in Workflow Studio). Then either an action or a logic block:

| kind | shape |
|---|---|
| action | `{"id", "label", "action": "<core action>", "inputs": {…}}` |
| if | `{"id", "label", "logic": "if", "condition": …, "steps": […], "else_if": [{"id", "label", "condition", "steps"}], "else": {"id", "label", "steps"}}` |
| for_each | `{"id", "label", "logic": "for_each", "items": {"pill": …}, "item": "<alias>", "steps": […]}` — items is a lookUpRecords `Records` output or a `list` field |
| parallel | `{"id", "label", "logic": "parallel", "branches": [[…], […]]}` — at least two branches; no parallel inside parallel |
| try_catch | `{"id", "label", "logic": "try_catch", "try": […], "catch": […]}` |
| do_until | `{"id", "label", "logic": "do_until", "steps": […], "until": …}` — runs at least once; `until` may read the body's steps |
| wait_duration | `{"id", "label", "logic": "wait_duration", "duration": {"days": 2}, "relative_to": {"pill": …}, "direction": "before", "schedule": {"placeholder": …}}` — without `relative_to` it waits the duration from now; `schedule` is optional (see Waits) |
| end | `{"id", "label", "logic": "end"}` |
| ask | `{"id", "label", "ask": {"short_description", "assignment_group" and/or "assigned_to", "variables": […]}}` — see Ask |

## Waits

With a `schedule`, each "day" of the duration is 24 hours of schedule time (wfa-flow-logic-api.md:493): `{"days": 2}`
on an 8-hour weekday schedule is about six working days. For "N working days", keep `{"days": N}` and say in the
schedule placeholder's `description` that it must be a Monday–Friday all-day (24h) schedule with holidays; with an
office-hours schedule, give the offset in hours instead (`{"hours": 16}` = 2 days of an 8h schedule). A date pill
(`form.start_date`) is midnight (instance time zone): `{"hours": 9}` `after` it is 9:00 that morning. `FLOWS.md`
repeats the note under every schedule placeholder.

## Approval

`askForApproval` returns `approval_state`. Only `approved` is the approved path; every other outcome (rejected,
cancelled, …) takes the stop path — write the stop branch as
`"condition": [{"pill": "<approval id>.approval_state"}, "!=approved"]` (then notify and `end`), or put the
approved work under `=approved` and the stop in `else`.

## Ask

A question a person answers during the run: a catalog task of the request, assigned to them (`assignment_group`
and/or `assigned_to`, each a pill such as `form.manager` or a placeholder), showing the
step's own `variables` (same shape as the form's); the flow waits until the task is closed, then reads the
answers. `{"pill": "<ask id>.<variable>"}` reads an answer after the step; `{"pill": "<ask id>.catalog_task.state"}`
is the task's state (3 = closed complete; branch on it when the procedure says what happens otherwise). The
script hides these variables on the order form and shows them (mandatory when you say so) on the task.
Not inside `for_each` (one value per variable on the request).

In a catalog flow, the procedure's work items are `createCatalogTask` steps:
`{"ah_requested_item": {"pill": "trigger.request_item"}, "ah_short_description": "…", "ah_fields": {"assignment_group": {"placeholder": …}}, "ah_wait": false}`.
`ah_fields` takes any `sc_task` field, e.g. `correlation_display` and `correlation_id` (the SynergyAI stamp `<key>@<N>`).
`ah_wait` is `false` unless the task is assigned to the SynergyAI group: that one uses `true`, because the flow
resumes only when SynergyAI closes it.

## Values

| form | example | where |
|---|---|---|
| literal | `"sys_user"`, `100`, `true` | any input of that type |
| pill | `{"pill": "trigger.current.manager"}`, `{"pill": "form.start_date"}` | any input except email bodies and SMS messages |
| placeholder | `{"placeholder": "hr_group"}` | anywhere a literal fits |
| instance record | a pill or a placeholder — never a typed name or sys_id | every record input (`record`, `notification`, `event_name`, `schedule`, …), `assignment_group`, `assigned_to`, approvers, reference fields in TemplateValues, ask assignees |
| text with pills | `["Welcome, ", {"pill": "trigger.current.first_name"}]` | only `log_message`, `ah_subject`, `ah_to`, conditions and `until` |
| fields | `{"short_description": "…", "parent": {"pill": "trigger.current.sys_id"}}` | TemplateValue inputs: `values`, `field_values`, `fields` |
| duration | `{"duration": {"days": 1}}` | Duration inputs such as `timeout_duration` |
| approvers | `{"rule": "Any", "users": [pills], "groups": [placeholders]}` | `approval_conditions` of `askForApproval` |

Pills: `trigger.<output>[.field…]` (`trigger.current` is the record that started the flow; `trigger.request_item`
in a catalog flow), `form.<variable>[.field…]` (the order form), `<ask id>.<variable>`,
`<step id>.<output>[.field…]` (an earlier step you can see — steps inside an if/for_each/parallel/try_catch/do_until
are only visible inside it), `<loop item>[.field…]`. Output names are case-sensitive (`Record`, `Records`, `Count`
on look-ups). A pill's type is worked out for you; add `"type"` only to override it.

## Placeholders

`{"kind": "group" | "user" | "notification" | "schedule" | "catalog" | "category" | "choice" | "email" | "text",
"description": "what the user must look up", "value": "optional — the real value once the user gives it"}`.
Without a value the flow carries `PLACEHOLDER:<name>`; `FLOWS.md` lists what to fill (per flow: file, trigger, who
submits the form, the catalog and category, the variables, each placeholder filled or to fill). Declare only what
you use; a declared placeholder that no step uses is an error.

Every instance record is a pill or a placeholder: a plain string where a group, user, notification, schedule,
catalog, category or any record input goes is rejected (it would be an invented name or sys_id). When the user
gives a real value, put it in the placeholder's `value`. `fireEvent`'s `event_name` is always a placeholder (kind
`text`: the event registered under System Policy → Events → Registry).

## Links

A task created by a record-triggered flow must set a reference field to the trigger record
(`{"pill": "trigger.current.sys_id"}`): `parent` when the trigger table is a task table, otherwise a field the
script suggests. If the table has none, make the procedure a catalog item: its tasks are catalog tasks of the
request.

## Complete example

```json
{
  "name": "p1-incident-escalation",
  "title": "P1 incident escalation",
  "description": "When a P1 incident is created, the group's manager approves a war room; a linked incident task tracks it.",
  "language": "en",
  "trigger": {
    "type": "record_created",
    "table": "incident",
    "condition": "priority=1^active=true"
  },
  "placeholders": {
    "war_room_group": {
      "kind": "group",
      "description": "Group that runs P1 war rooms (sys_user_group sys_id)."
    }
  },
  "steps": [
    {
      "id": "group_info",
      "label": "Find the assignment group",
      "action": "lookUpRecord",
      "inputs": {
        "table": "sys_user_group",
        "conditions": [
          "sys_id=",
          {
            "pill": "trigger.current.assignment_group"
          }
        ]
      }
    },
    {
      "id": "approve_war_room",
      "label": "Group manager approves a war room",
      "action": "askForApproval",
      "inputs": {
        "record": {
          "pill": "trigger.current.sys_id"
        },
        "table": "incident",
        "approval_conditions": {
          "rule": "Any",
          "users": [
            {
              "pill": "group_info.Record.manager"
            }
          ]
        }
      }
    },
    {
      "id": "approved",
      "label": "Approved?",
      "logic": "if",
      "condition": [
        {
          "pill": "approve_war_room.approval_state"
        },
        "=approved"
      ],
      "steps": [
        {
          "id": "war_room_task",
          "label": "Open the war-room task",
          "action": "createTask",
          "inputs": {
            "task_table": "incident_task",
            "field_values": {
              "short_description": "P1 war room",
              "parent": {
                "pill": "trigger.current.sys_id"
              },
              "assignment_group": {
                "placeholder": "war_room_group"
              }
            }
          }
        }
      ],
      "else": {
        "id": "declined",
        "label": "Declined",
        "steps": [
          {
            "id": "note_declined",
            "label": "Work note",
            "action": "updateRecord",
            "inputs": {
              "table_name": "incident",
              "record": {
                "pill": "trigger.current.sys_id"
              },
              "values": {
                "work_notes": "War room declined by the group manager."
              }
            }
          }
        ]
      }
    },
    {
      "id": "log_p1",
      "label": "Log",
      "action": "log",
      "inputs": {
        "log_level": "warn",
        "log_message": [
          "P1 ",
          {
            "pill": "trigger.current.number"
          },
          ": ",
          {
            "pill": "approve_war_room.approval_state"
          }
        ]
      }
    }
  ]
}
```
