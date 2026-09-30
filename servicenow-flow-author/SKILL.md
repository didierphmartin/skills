---
name: servicenow-flow-author
description: "Turn a procedure described in plain language (any language) into a ServiceNow FLOW that builds with the ServiceNow SDK and installs into the user's instance, where it opens in Workflow Studio. USE THIS whenever the user asks to 'create/build/generate a ServiceNow flow', 'automate this process in ServiceNow', 'make a Flow Designer / Workflow Studio flow', or pastes a procedure (onboarding, approvals, reminders, escalations) and wants it in ServiceNow. You write the flow as a JSON spec; scripts/create.py checks it against the SDK 4.13.3 vocabulary, writes the Fluent project (one shared app, x_companycode_synergy; a procedure that needs data from people becomes a catalog item with an order form) and lists the placeholders to fill. Do NOT use for: running, testing or debugging flows on an instance, Console-style playbooks (that is playbook-author), SynergyAI workflow graphs (workflow-compile)."
license: MIT
metadata:
  version: 0.1.0
  origin: synergyAI custom
allowed-tools: run_skill_script
context-references: references/flow-spec.md, references/fluent-vocabulary.md
---

# servicenow-flow-author

## CRITICAL — runtime contract (read first)

1. **You never write TypeScript, and you never paste the spec or the flow in chat.** You write a JSON flow spec
   (`references/flow-spec.md`); `scripts/create.py` owns every line of Fluent.
2. **Run the script, then fix everything at once.** On `INVALID` (exit 1) every problem is listed with its step;
   fix all of them in one revision and run again. **At most two revisions**; if it is still INVALID, tell the user
   which problems remain and what they need to decide. Exit 2 means the spec string is not valid JSON: resend it.
3. **Company code.** `NEED_VENDOR` (exit 1, printed only once the spec is valid) means the app's company code is
   unknown: ask the user once — *"What is your instance's company code? In ServiceNow Studio it is the prefix shown
   when you create an app, e.g. x_1234567."* — then run the same call with `"--vendor", "<code>"` added. The script
   remembers it in `/outputs/servicenow/.vendor`; never ask twice.
4. **Never invent instance data or fields.** An instance record (group, user, notification, schedule, catalog,
   category, event, any record input) is a pill or a declared placeholder — a typed name or sys_id is rejected.
   Declare it under `placeholders` (kind, description; `value` only when the user gave it). Data a person supplies
   is a catalog variable: on the order form (`"form"`) when known at the start, or in an **ask step** (a catalog
   task that person fills in mid-run) when only someone else knows it later — never a table field the SDK does
   not list.
5. **Map, don't invent.** A feature Fluent lacks is mapped to a supported construct (table below) or left out and
   named in your reply — never a script, never an action that is not in `references/fluent-vocabulary.md`.
6. **Reply in 3–5 lines**, in the user's language (every label in the spec is in it too): the flow title; the
   folder `~/Documents/synergyAI/outputs/servicenow/x_<code>_synergy/` (the report's `/outputs/…` path), where the
   user builds and installs every time — the zip beside it only moves the project; the placeholders to fill (all
   listed in its `FLOWS.md`); then `npx now-sdk build` and `npx now-sdk install` (first time in that folder:
   `npm install` and `npx now-sdk auth --add <instance> --type basic`). Details on demand: `references/deploy.md`.

## Steps

1. Read the procedure. Does it need data from people (a new hire's start date, an equipment list, a reason)?
   Then it is a **catalog item**: trigger `service_catalog`, a `"form"` with the variables known at the start, its
   start condition ("when HR creates a user…") in `form.submitted_by`, catalog and category placeholders, and an
   ask step for each question a person answers later. Read `references/example-onboarding.md` first. Otherwise
   pick a record trigger ("when X is created/updated", only fields the vocabulary lists) or a scheduled one.
2. Write the steps in order. Each has an `id` (snake_case, unique), a `label` in the user's language, and an
   `action` with `inputs`, an `ask`, or a `logic` block. Read values with pills: `{"pill": "trigger.current.manager"}`,
   `{"pill": "form.start_date"}`, `{"pill": "<step id>.<output>"}`, `{"pill": "<loop item>.<field>"}`.
3. Tasks: in a catalog flow every task is `createCatalogTask` with `"ah_requested_item": {"pill": "trigger.request_item"}`
   and `"ah_wait": false` (work the flow does not wait for) — never `createTask`. In a record flow a `createTask`
   must point back at the trigger record (`{"pill": "trigger.current.sys_id"}` in a reference field such as
   `parent`) — the script names the fields you can use.
4. Call `run_skill_script` with EXACTLY this shape (the spec travels as ONE JSON string):
   ```json
   {
     "script": "scripts/create.py",
     "argv": ["-i", "/scratch/<name>.json"],
     "input_files": { "/scratch/<name>.json": "{ \"name\": \"<name>\", \"title\": \"…\", … }" }
   }
   ```
   Add `"--vendor", "<code>"` to `argv` only after the user gave the company code.
5. Read the report: `VALID` (files written, placeholders listed, `NEXT:` line), `INVALID` (fix, rerun) or
   `NEED_VENDOR` (rule 3).
6. Reply (rule 6). When the user later gives placeholder values, put them in `placeholders.<name>.value` and run
   the same spec again (same `name`): the flow is rewritten with the values and `FLOWS.md` marks them filled.

## Procedure features Fluent does not have

| The procedure says | Write |
|---|---|
| "X approves …; if not, stop" | `askForApproval`, then `if` `[{"pill": "<approval>.approval_state"}, "!=approved"]` → notify + `end`: any outcome other than approved (rejected, cancelled…) takes the stop path |
| "N working days before/after <date>" | `wait_duration` with `relative_to` the date pill, `direction`, and a `schedule` placeholder (kind `schedule`). A schedule "day" is 24h of schedule time: the placeholder's description asks for a Mon–Fri all-day (24h) schedule with holidays, or give the offset in schedule hours (16h = 2 days of an 8h schedule) |
| "go back to step N", "repeat until" | `do_until` around the steps to repeat, with the exit condition in `until` |
| "decide", "depending on the answer" | `if` with one `else_if` per answer and an `else` |
| "wait until the task/record is done" | the action `waitForCondition` on that record |
| "ask X for …", "X confirms …" (data mid-run) | an ask step assigned to X; branch on `<ask>.catalog_task.state` (3 = closed complete) when the procedure says what happens otherwise |
| a list of items ("laptop, monitor, phone") | a `list_collector` variable on the item table (e.g. `cmdb_model`); `lookUpRecords` with `["sys_idIN", {"pill": "form.<variable>"}]`, then `for_each` over its `Records` |
| an email that must show a variable in its body | `sendNotification` with a notification placeholder on `sc_req_item` (an email body takes no pills; pills go in `ah_subject`) |
| "raise the event …" | `fireEvent` with `event_name` a placeholder (kind `text`, the registered event) |
| call a workflow, subflow, dynamic flow, script or spoke | leave it out and say so in the reply |

## What the script checks (so you know what "INVALID" means)

- The spec shape, unique ids, labels, and that every placeholder is declared and used.
- Every trigger, action and input is in the SDK 4.13.3 vocabulary, with required inputs present and values of the right kind; instance records are pills or placeholders.
- Every pill resolves: to the trigger, to an order-form variable (`form.<name>`), to an earlier step's output or ask answer in a block you can see, or to a loop item — and every field exists on its table.
- Variables: types from the vocabulary, unique names, what the builder needs (`reference` table, `choices`); an ask step's pills read only its own variables; asks are not inside `for_each`.
- Catalog flows use `createCatalogTask` on the request item with an explicit `ah_wait`; catalog actions and ask steps are refused elsewhere.
- Text mixed with pills only where ServiceNow interpolates it (log messages, email subject and To, conditions); email bodies and SMS messages are static.
- Created tasks are linked to the trigger record (record flows); parallel blocks are not nested.
