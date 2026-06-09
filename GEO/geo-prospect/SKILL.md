---
name: geo-prospect
description: "CRM-lite for tracking GEO agency prospects and clients through the sales pipeline — Lead -> Qualified -> Proposal -> Won -> Lost. Stores prospect records, links audit and proposal files already in outputs/, tracks notes with timestamps, deal values, and contract dates. Use for: 'prospect', 'lead', 'client', 'pipeline', 'crm', 'add prospect', 'new lead', 'mark as won', 'mark as lost', 'pipeline summary', 'committed MRR', 'follow-up list', 'show prospects', 'aggiungi cliente', 'nuovo prospect'. Pipeline: a single Python script with subcommands (new / list / show / link-audit / link-proposal / note / status / won / lost / pipeline) manipulates ~/Documents/synergyAI/outputs/prospects/prospects.json. No web fetches, no LLM judgment in the script itself — the LLM picks the right subcommand for the user's request and parses the JSON output to present pipeline summaries with revenue forecasts."
dependencies: []
fetches_urls: false
---

# geo-prospect

CRM-lite for tracking GEO agency prospects and clients. Maintains a single
`prospects.json` database at
`~/Documents/synergyAI/outputs/prospects/prospects.json` and links to
audit / proposal files already produced by the other geo-* skills.

This skill is the **business-side counterpart** to the measurement skills
(geo-audit, geo-report, geo-proposal). It stores who you're talking to,
where they are in the funnel, what you've delivered, and what the deal is
worth.

> ## ✅ REQUIRED FIRST STEP — CALL THE SCRIPT WITH A SUBCOMMAND
>
> When the user asks anything about prospects / leads / pipeline / clients,
> your **first action** is `run_skill_script` with `dir_name: geo-prospect`
> and `scripts/prospect_manager.py` plus a subcommand. The script reads /
> writes the prospects database; you parse its output and present it.
>
> If the user is ambiguous about which prospect ("the Stripe one", "that
> recent one"), run **`list`** first to see what's in the database, then
> match by domain / company / ID.
>
> ## 🪙 TOOL-CALL BUDGET — PREFER THE WIZARD
>
> SynergyAI caps client-tool calls at 6 rounds per chat turn (each round can
> carry many parallel calls, but most LLMs run subcommands sequentially). To
> stay safe:
>
> - **When the user gives you everything in one ask** (domain + audit file +
>   score, or domain + proposal + score), call the **`wizard`** subcommand —
>   it does new + link-audit + link-proposal + status auto-advance in **one
>   call**. Do NOT chain `new` then `link-audit` then `status` as separate
>   calls.
> - **When you genuinely need multiple subcommands** in one ask (e.g.
>   `pipeline` after a `note`), emit them as **parallel tool_use blocks in a
>   single response** — that's one round, not two. Each block can be a
>   different subcommand on `prospect_manager.py`.
> - **Don't call `list` defensively** to "see what's in the database" before
>   acting — that burns a round. Call the action directly; if the prospect
>   doesn't exist the script will tell you, and the next round you can
>   create it (or just use `wizard` which handles missing).
>
> ## ⛔ NO FABRICATION
>
> The script prints the **prospect record(s) or pipeline summary to
> stdout** — `run_skill_script` returns it to you.
>
> - Every prospect ID, company name, status, score, deal value, audit
>   file path, and note timestamp must come from the script's output.
>   Never invent a prospect that isn't in the database.
> - If `list` returns an empty pipeline, say so — don't synthesize a fake
>   pipeline to look populated.
> - When asked to mark a prospect won / lost / status-change, **always**
>   call the matching subcommand. Don't just write a chat reply saying
>   "OK, marked as won" without actually running the script — the
>   database is the single source of truth.

## Invocation — subcommands

```json
// 🌟 WIZARD (preferred when the user gives you everything in one ask)
// Creates the prospect if missing, links audit + optional proposal, sets
// score, auto-advances status — all in ONE round.
{ "dir_name": "geo-prospect", "script": "scripts/prospect_manager.py",
  "argv": ["wizard", "electron-srl.com",
           "--audit", "GEO-CLIENT-REPORT.md", "--score", "32",
           "--monthly-value", "4500"] }
```

```json
// Wizard with proposal too — full intake in one call
{ "argv": ["wizard", "electron-srl.com",
           "--audit", "GEO-CLIENT-REPORT.md", "--score", "32",
           "--proposal", "GEO-PROPOSAL.md"] }
```

```json
// New prospect (only when the user hasn't yet run audits)
{ "argv": ["new", "electron-srl.com", "--monthly-value", "4500"] }
```

```json
// New with full details
{ "argv": ["new", "electron-srl.com",
           "--company", "Electron Srl",
           "--contact-name", "Marco Rossi",
           "--contact-email", "marco@electron-srl.com",
           "--industry", "Educational Equipment Manufacturing",
           "--country", "Italy",
           "--monthly-value", "4500"] }
```

```json
// List everything (pipeline table)
{ "argv": ["list"] }
```

```json
// List by status
{ "argv": ["list", "qualified"] }
```

```json
// Show full detail (by ID or by domain)
{ "argv": ["show", "PRO-001"] }
{ "argv": ["show", "electron-srl.com"] }
```

```json
// Link an existing audit file (in outputs/) to a prospect
{ "argv": ["link-audit", "electron-srl.com", "GEO-CLIENT-REPORT.md",
           "--score", "32"] }
```

```json
// Link an existing proposal file (in outputs/) to a prospect
{ "argv": ["link-proposal", "electron-srl.com", "GEO-PROPOSAL.md"] }
```

```json
// Add a timestamped note
{ "argv": ["note", "PRO-001", "Discovery call set for Mar 18. CTO joining."] }
```

```json
// Change pipeline status
{ "argv": ["status", "PRO-001", "proposal"] }   // values: lead/qualified/proposal/won/lost
```

```json
// Shortcut: mark as won with monthly value + contract length
{ "argv": ["won", "PRO-001", "5000", "--months", "12",
           "--start-date", "2026-04-01"] }
```

```json
// Shortcut: mark as lost with reason
{ "argv": ["lost", "PRO-002", "Budget freeze for Q2 2026"] }
```

```json
// Pipeline summary with revenue forecast
{ "argv": ["pipeline"] }
```

```json
// No-arg default: list + pipeline (quick overview)
{ "argv": [] }
```

## Pipeline stage definitions

| Status | Meaning | Typical next action |
|---|---|---|
| `lead` | Discovered, not yet contacted | Run `geo-audit` to score them, then `link-audit` |
| `qualified` | Audit done, confirmed pain points | Run `geo-proposal`, then `link-proposal` + set status `proposal` |
| `proposal` | Proposal sent, awaiting decision | Follow up; add notes after each touchpoint |
| `won` | Contract signed, active client | Run a full `geo-audit` baseline; schedule monthly `geo-compare` runs |
| `lost` | Closed lost | Note the reason for future review (price / timing / no-decision) |

## Workflow patterns the LLM should know

**New prospect → audit → proposal**
1. `new <domain>` — creates the record at `lead`
2. Run `geo-audit https://<domain>` (the orchestrator) — produces audits in outputs/
3. `link-audit <domain> GEO-CLIENT-REPORT.md --score <composite-score>` — attaches the audit + sets the score + auto-advances to `qualified` if score is known
4. Run `geo-proposal <domain>` — produces `GEO-PROPOSAL.md`
5. `link-proposal <domain> GEO-PROPOSAL.md` — attaches + auto-advances to `proposal`
6. After client signs: `won <id> <monthly-value> --months 12`

**Pipeline review**
- `pipeline` to see the committed MRR + pipeline value + suggested next actions
- For each prospect in `proposal` for ≥7 days: suggest a follow-up note

**Monthly check-ins for `won` clients**
- Run `geo-compare` between baseline and current audit
- `link-audit <domain> <new-audit>.md --score <new>` to update the recorded score
- `note <id> "Score improved from X to Y this month"`

## Database location and schema

The database lives at `~/Documents/synergyAI/outputs/prospects/prospects.json`
(`/outputs/prospects/prospects.json` in Pyodide). Schema per record:

```json
{
  "id": "PRO-001",
  "company": "Electron Srl",
  "domain": "electron-srl.com",
  "contact_email": "marco@electron-srl.com",
  "contact_name": "Marco Rossi",
  "industry": "Educational Equipment Manufacturing",
  "country": "Italy",
  "status": "qualified",
  "geo_score": 32,
  "audit_date": "2026-03-12",
  "audit_file": "GEO-CLIENT-REPORT.md",
  "proposal_file": null,
  "monthly_value": 4500,
  "contract_start": null,
  "contract_months": 0,
  "notes": [
    {"date": "2026-03-12T10:42:18", "text": "..."}
  ],
  "created_at": "2026-03-12T09:00:00",
  "updated_at": "2026-03-12T10:42:18"
}
```

IDs are auto-assigned: `PRO-001`, `PRO-002`, …, incrementing from the
highest existing ID.

## Auto-advancement rules

The script auto-advances status for convenience (the LLM should still
respect explicit `status` calls from the user):

| Action | Auto-status |
|---|---|
| `link-audit` with a score | `lead` → `qualified` (only if currently `lead`) |
| `link-proposal` | `qualified` → `proposal` (only if currently `qualified`) |
| `won` | sets status to `won` |
| `lost` | sets status to `lost` |

Every action appends a timestamped auto-note to the prospect's `notes`
array describing what changed.

## Company-name auto-detection

For `new <domain>` without `--company`, the script derives a default
company name from the domain: strip TLD, replace hyphens/underscores with
spaces, title-case (e.g. `electron-srl.com` → `Electron Srl`). The user
can override with `--company "Exact Name"`.

## Pipeline summary format

`pipeline` output:

```
GEO Agency Pipeline — <Month YYYY>

STAGE          COUNT   POTENTIAL VALUE    NOTES
Lead             N      €N,NNN/mo          new discoveries
Qualified        N      €N,NNN/mo          ready for proposal
Proposal Sent    N      €N,NNN/mo          awaiting signature
Won              N      €N,NNN/mo          active clients (MRR)
Lost             N      —                  

COMMITTED MRR:        €N,NNN
PIPELINE (qualified+): €N,NNN
TOTAL POTENTIAL:      €N,NNN/mo  →  €N,NNN/yr

Next actions:
→ PRO-XXX (domain): <suggested action based on age / status>
```

The LLM may augment with prose ("Three deals are in the proposal stage for
more than a week — recommend follow-up calls this week.") but the numbers
come from the script.

## Pyodide notes

`dependencies: []`, `fetches_urls: false` — pure filesystem + stdlib JSON.

## Limitations

- **No CRM features beyond the basics.** No email integration, no
  reminders/cron, no deal probability scoring. The skill records the
  state; the user (or a follow-up SynergyAI workflow) drives the
  follow-up actions.
- **One database per user.** The skill assumes a single agency operator;
  multi-user / role separation is out of scope.
- **No automatic audit refresh.** `link-audit` only attaches an existing
  file; it does NOT re-run `geo-audit`. The orchestration sequence
  (`geo-audit` → `link-audit`) is the LLM's job, not the script's.
- **No currency conversion.** All monetary values are unit-less and
  rendered with the symbol the LLM chooses (defaults to € in the
  template; pass an explicit currency if needed).
