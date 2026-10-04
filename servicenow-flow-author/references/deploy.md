# Build and install the flows

What the user does on their own machine once `create.py` has written the project. Tell them only the part
they need (usually the folder, the placeholders and the two commands).

## Where the project is

SynergyAI writes it to `~/Documents/synergyAI/outputs/servicenow/x_<code>_synergy/` (the app's
`/outputs/servicenow/…`). It is built and installed from `~/Documents/synergyAI/ServiceNow/x_<code>_synergy/`
by `build-install.sh`: `npm install` puts ~380 MB of SDK in `node_modules`, and the app loads the whole outputs
folder into the browser on every skill run, so the SDK must never land there. The script copies the project
across (deleting nothing there), builds and installs, then copies `keys.ts` back. The zip
`x_<code>_synergy.zip` only moves the project to another machine: it holds the whole project,
`src/fluent/generated/keys.ts` included (not `node_modules`, `dist`, `target`, `.now`), as of the last SynergyAI
run — unzip it under `outputs/servicenow/` there and run `build-install.sh`. A test flow can go to a separate app
with `--app <name>` (scope `x_<code>_<name>`, at most 18 characters), so installing or removing it never touches
the shared app. One app ("SynergyAI flows") holds every flow; `FLOWS.md` in the folder lists them:
per flow its file, trigger, who submits the order form and when, the catalog and category it is listed in, the
variables, and each placeholder (filled or to fill).

The company code in the folder name is asked once and remembered in `/outputs/servicenow/.vendor`.

## Prerequisites (once)

- Node.js 20.18 or later and npm (the SDK's `engines` field).
- Admin (or developer) rights on the instance. The SDK's Fluent features need a Yokohama-family instance or later,
  with the ServiceNow IDE store app 4.1.1 or later — 4.4.1 or later for flows to be activated on install
  (ServiceNow staff statements; there is no official matrix).
- `rsync` (built into macOS and most Linux).
- The first run, in a terminal in the outputs project folder:

      sh build-install.sh https://<instance>.service-now.com

  It runs `npm install` in the ServiceNow folder and
  `npx now-sdk auth --add https://<instance>.service-now.com --type basic`, which asks for your login and stores
  it in the system keychain (`--type oauth` is the alternative, run by hand in the ServiceNow folder).

## Fill the placeholders

Each `PLACEHOLDER:<name>` in a flow is something only the instance knows. `FLOWS.md` lists them with what to look up:

| kind | where to find it |
|---|---|
| group | User Administration → Groups → open the group → ⋯ → Copy sys_id |
| user | User Administration → Users → open the user → Copy sys_id |
| notification | System Notification → Email → Notifications: the notification's sys_id (create it first if needed, on the table the step names) |
| schedule | System Scheduler → Schedules (cmn_schedule) → Copy sys_id. A schedule "day" counts as 24h of schedule time: for "N working days" use a Mon–Fri all-day (24h) schedule with holidays (or ask SynergyAI to give the offset in schedule hours) |
| catalog | Service Catalog → Catalogs (sc_catalog), e.g. "Service Catalog" → Copy sys_id |
| category | Service Catalog → Categories (sc_category) in that catalog → Copy sys_id |
| text (e.g. an event name), choice, email | as the description says (events: System Policy → Events → Registry) |

Easiest: give the values to SynergyAI ("the IT Service Desk group is 8a5…"); it re-runs the flow with them and
`FLOWS.md` marks them filled. Editing `PLACEHOLDER:<name>` by hand in `src/fluent/flows/<name>.now.ts` is undone the
next time SynergyAI writes that flow, and edits in the ServiceNow folder are overwritten by `build-install.sh`.

## Build and install (every time)

    sh build-install.sh

It runs `npx now-sdk build` then `npx now-sdk install` in the ServiceNow folder. `build` compiles offline and stops on any error — send the log to SynergyAI and it fixes the spec.
`install` sends the whole app; unchanged flows stay as they are, and flows are activated on install
(SDK 4.5.0 and later).

## See it in Workflow Studio

Workflow Studio → Flows → filter by application **SynergyAI flows** → open the flow. The trigger, every step
label, the approval, parallel branches, loops, waits and notifications appear as written in the spec.
A flow with an order form is started by ordering its catalog item (Service Catalog → the catalog and category
from `FLOWS.md`; Maintain Items shows its variables and the Process Engine tab names the flow). `FLOWS.md` says
who submits the form and when — tell those people where the item is.

## Keep, don't touch

- `src/fluent/generated/keys.ts` is created by the first build (in the ServiceNow folder) and maps every step to
  its record on the instance. Keep it (commit it if the folder is in git). Without it the next install creates
  duplicates. `build-install.sh` never overwrites it from the outputs copy.
- A flow renamed or dropped from the spec stays in the app: delete its `.now.ts` and `flow-specs/` file in both
  folders by hand.
- `flow-specs/` holds the spec of every flow; `FLOWS.md` is regenerated from it on each run.
