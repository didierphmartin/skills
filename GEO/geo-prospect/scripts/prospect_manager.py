#!/usr/bin/env python3
"""
prospect_manager.py — CRM-lite database for the geo-prospect skill.

Manages prospects.json with subcommands for new / list / show / link-audit /
link-proposal / note / status / won / lost / pipeline. Pure filesystem,
no network, no external deps.

Storage: ~/Documents/synergyAI/outputs/prospects/prospects.json
         (/outputs/prospects/prospects.json in Pyodide)

Usage:
    python prospect_manager.py new electron-srl.com --monthly-value 4500
    python prospect_manager.py list
    python prospect_manager.py list qualified
    python prospect_manager.py show PRO-001
    python prospect_manager.py link-audit electron-srl.com GEO-CLIENT-REPORT.md --score 32
    python prospect_manager.py link-proposal electron-srl.com GEO-PROPOSAL.md
    python prospect_manager.py note PRO-001 "Discovery call set for Mar 18"
    python prospect_manager.py status PRO-001 proposal
    python prospect_manager.py won PRO-001 5000 --months 12
    python prospect_manager.py lost PRO-002 "Budget freeze"
    python prospect_manager.py pipeline
    python prospect_manager.py            # default: list + pipeline
"""

from __future__ import annotations

import os
import argparse
import json
import sys
import time
from pathlib import Path


DEFAULT_OUTPUT_DIR = (
    Path(os.environ.get("SYNERGYAI_OUTPUT_DIR", "/outputs"))
    if sys.platform == "emscripten"
    else Path.home() / "Documents" / "synergyAI" / "outputs"
)
PROSPECTS_DIR = DEFAULT_OUTPUT_DIR / "prospects"
DB_PATH = PROSPECTS_DIR / "prospects.json"

VALID_STATUSES = ("lead", "qualified", "proposal", "won", "lost")


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

def ensure_db() -> list[dict]:
    PROSPECTS_DIR.mkdir(parents=True, exist_ok=True)
    if not DB_PATH.exists():
        DB_PATH.write_text("[]", encoding="utf-8")
        return []
    try:
        data = json.loads(DB_PATH.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        print(f"WARNING: {DB_PATH} did not contain a JSON list; resetting.",
              file=sys.stderr)
        return []
    except json.JSONDecodeError as e:
        print(f"ERROR: corrupt JSON in {DB_PATH}: {e}", file=sys.stderr)
        sys.exit(1)


def save_db(records: list[dict]) -> None:
    PROSPECTS_DIR.mkdir(parents=True, exist_ok=True)
    DB_PATH.write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n",
                       encoding="utf-8")


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def today_iso() -> str:
    return time.strftime("%Y-%m-%d")


def next_id(records: list[dict]) -> str:
    max_n = 0
    for r in records:
        rid = r.get("id", "")
        if rid.startswith("PRO-"):
            try:
                n = int(rid[4:])
                if n > max_n:
                    max_n = n
            except ValueError:
                continue
    return f"PRO-{max_n + 1:03d}"


def find_prospect(records: list[dict], key: str) -> dict | None:
    """Find by ID (PRO-NNN) or by domain (case-insensitive)."""
    if not key:
        return None
    key_l = key.lower().strip()
    # Try ID first
    for r in records:
        if r.get("id", "").lower() == key_l:
            return r
    # Then exact domain match
    for r in records:
        if r.get("domain", "").lower() == key_l:
            return r
    # Then domain substring (e.g. "electron" matches "electron-srl.com")
    matches = [r for r in records if key_l in r.get("domain", "").lower()
               or key_l in r.get("company", "").lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        print(f"ERROR: {key!r} matches multiple prospects: "
              f"{[m.get('domain') for m in matches]}. Use the ID instead.",
              file=sys.stderr)
        sys.exit(1)
    return None


def add_auto_note(prospect: dict, text: str) -> None:
    prospect.setdefault("notes", []).append({
        "date": now_iso(),
        "text": text,
    })
    prospect["updated_at"] = now_iso()


def domain_to_company(domain: str) -> str:
    """electron-srl.com -> Electron Srl."""
    name = domain
    if "." in name:
        name = name.rsplit(".", 1)[0]
    name = name.replace("-", " ").replace("_", " ").strip()
    return name.title() or domain


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_new(args) -> int:
    records = ensure_db()
    domain = args.domain.lower().strip()

    # Reject duplicates
    if find_prospect(records, domain):
        existing = find_prospect(records, domain)
        print(f"ERROR: a prospect with domain {domain!r} already exists "
              f"(ID {existing['id']}, status {existing['status']}). Use "
              f"`show {existing['id']}` to see it.", file=sys.stderr)
        return 1

    rec = {
        "id": next_id(records),
        "company": args.company or domain_to_company(domain),
        "domain": domain,
        "contact_email": args.contact_email or "",
        "contact_name": args.contact_name or "",
        "industry": args.industry or "",
        "country": args.country or "",
        "status": "lead",
        "geo_score": None,
        "audit_date": None,
        "audit_file": None,
        "proposal_file": None,
        "monthly_value": float(args.monthly_value) if args.monthly_value else 0.0,
        "contract_start": None,
        "contract_months": 0,
        "notes": [],
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    add_auto_note(rec, "Prospect created.")
    records.append(rec)
    save_db(records)

    print(f"# Prospect created: {rec['id']} — {rec['company']} ({rec['domain']})")
    print()
    print(_format_record(rec))
    print()
    print(f"**Next step:** run `geo-audit https://{rec['domain']}` to score this "
          f"prospect, then `link-audit {rec['domain']} GEO-CLIENT-REPORT.md "
          f"--score <N>` to attach the audit.")
    return 0


def cmd_list(args) -> int:
    records = ensure_db()
    status_filter = args.status
    if status_filter and status_filter not in VALID_STATUSES:
        print(f"ERROR: invalid status {status_filter!r}. Valid: "
              f"{', '.join(VALID_STATUSES)}", file=sys.stderr)
        return 2

    rows = records
    if status_filter:
        rows = [r for r in records if r.get("status") == status_filter]

    title = (f"# GEO Prospect Pipeline — {time.strftime('%B %Y')}"
             + (f" — status={status_filter}" if status_filter else ""))
    print(title)
    print()
    if not rows:
        print("(no prospects)")
        return 0
    print("| ID | Domain | Company | Status | Score | Value/mo |")
    print("|---|---|---|---|---|---|")
    for r in rows:
        score = r.get("geo_score")
        score_s = f"{score}/100" if score is not None else "—"
        value = r.get("monthly_value") or 0
        value_s = f"€{value:,.0f}" if value else "—"
        print(f"| {r['id']} | {r['domain']} | {r['company']} | "
              f"{r['status'].title()} | {score_s} | {value_s} |")
    print()
    _print_pipeline_summary(records)
    return 0


def cmd_show(args) -> int:
    records = ensure_db()
    r = find_prospect(records, args.id_or_domain)
    if not r:
        print(f"ERROR: prospect {args.id_or_domain!r} not found.",
              file=sys.stderr)
        return 1
    print(f"# Prospect detail — {r['id']} — {r['company']}")
    print()
    print(_format_record(r))
    print()
    notes = r.get("notes") or []
    print(f"## Notes ({len(notes)})")
    print()
    if not notes:
        print("(none)")
    for n in notes[-30:]:
        print(f"- **{n['date']}** — {n['text']}")
    if len(notes) > 30:
        print(f"- … {len(notes) - 30} older notes")
    return 0


def cmd_link_audit(args) -> int:
    records = ensure_db()
    r = find_prospect(records, args.id_or_domain)
    if not r:
        print(f"ERROR: prospect {args.id_or_domain!r} not found.", file=sys.stderr)
        return 1
    audit_path = _resolve_outputs_relative(args.audit_file)
    if not audit_path.is_file():
        print(f"ERROR: audit file not found: {audit_path} "
              f"(also tried {DEFAULT_OUTPUT_DIR / args.audit_file})",
              file=sys.stderr)
        return 1
    rel = audit_path.name if audit_path.parent == DEFAULT_OUTPUT_DIR else str(audit_path)
    r["audit_file"] = rel
    r["audit_date"] = today_iso()
    note_parts = [f"Linked audit `{rel}`"]
    if args.score is not None:
        r["geo_score"] = int(args.score)
        note_parts.append(f"(score: {args.score}/100)")
    add_auto_note(r, " ".join(note_parts) + ".")

    auto_advance = False
    if r["status"] == "lead" and args.score is not None:
        r["status"] = "qualified"
        auto_advance = True
        add_auto_note(r, "Status auto-advanced: lead -> qualified (audit + score linked).")

    save_db(records)
    print(f"# Audit linked to {r['id']} — {r['company']}")
    print()
    print(_format_record(r))
    if auto_advance:
        print()
        print(f"**Auto-advanced** status from `lead` to `qualified`. "
              f"Consider running `geo-proposal {r['domain']}` next.")
    return 0


def cmd_link_proposal(args) -> int:
    records = ensure_db()
    r = find_prospect(records, args.id_or_domain)
    if not r:
        print(f"ERROR: prospect {args.id_or_domain!r} not found.", file=sys.stderr)
        return 1
    p_path = _resolve_outputs_relative(args.proposal_file)
    if not p_path.is_file():
        print(f"ERROR: proposal file not found: {p_path}", file=sys.stderr)
        return 1
    rel = p_path.name if p_path.parent == DEFAULT_OUTPUT_DIR else str(p_path)
    r["proposal_file"] = rel
    add_auto_note(r, f"Linked proposal `{rel}`.")

    auto_advance = False
    if r["status"] == "qualified":
        r["status"] = "proposal"
        auto_advance = True
        add_auto_note(r, "Status auto-advanced: qualified -> proposal.")

    save_db(records)
    print(f"# Proposal linked to {r['id']} — {r['company']}")
    print()
    print(_format_record(r))
    if auto_advance:
        print()
        print(f"**Auto-advanced** status from `qualified` to `proposal`. "
              f"Follow up in 5-7 days.")
    return 0


def cmd_note(args) -> int:
    records = ensure_db()
    r = find_prospect(records, args.id_or_domain)
    if not r:
        print(f"ERROR: prospect {args.id_or_domain!r} not found.", file=sys.stderr)
        return 1
    r.setdefault("notes", []).append({"date": now_iso(), "text": args.text})
    r["updated_at"] = now_iso()
    save_db(records)
    print(f"# Note added to {r['id']} — {r['company']}")
    print()
    print(f"> {args.text}")
    print()
    print(f"_({now_iso()})_")
    return 0


def cmd_status(args) -> int:
    records = ensure_db()
    r = find_prospect(records, args.id_or_domain)
    if not r:
        print(f"ERROR: prospect {args.id_or_domain!r} not found.", file=sys.stderr)
        return 1
    if args.new_status not in VALID_STATUSES:
        print(f"ERROR: invalid status. Valid: {', '.join(VALID_STATUSES)}",
              file=sys.stderr)
        return 2
    old = r["status"]
    r["status"] = args.new_status
    add_auto_note(r, f"Status changed: {old} -> {args.new_status}.")
    save_db(records)
    print(f"# Status updated: {r['id']} — {r['company']}")
    print()
    print(f"`{old}` -> `{args.new_status}`")
    return 0


def cmd_won(args) -> int:
    records = ensure_db()
    r = find_prospect(records, args.id_or_domain)
    if not r:
        print(f"ERROR: prospect {args.id_or_domain!r} not found.", file=sys.stderr)
        return 1
    old = r["status"]
    r["status"] = "won"
    r["monthly_value"] = float(args.monthly_value)
    r["contract_months"] = int(args.months)
    r["contract_start"] = args.start_date or today_iso()
    add_auto_note(r,
        f"Status: {old} -> won. Monthly: €{r['monthly_value']:.0f}, "
        f"contract: {r['contract_months']} months from {r['contract_start']}.")
    save_db(records)
    print(f"# 🎉 Won: {r['id']} — {r['company']}")
    print()
    print(_format_record(r))
    print()
    print(f"**Total contract value:** "
          f"€{r['monthly_value'] * r['contract_months']:,.0f} "
          f"({r['contract_months']} months × €{r['monthly_value']:,.0f}/mo)")
    return 0


def cmd_lost(args) -> int:
    records = ensure_db()
    r = find_prospect(records, args.id_or_domain)
    if not r:
        print(f"ERROR: prospect {args.id_or_domain!r} not found.", file=sys.stderr)
        return 1
    old = r["status"]
    r["status"] = "lost"
    add_auto_note(r, f"Status: {old} -> lost. Reason: {args.reason}")
    save_db(records)
    print(f"# Marked lost: {r['id']} — {r['company']}")
    print()
    print(f"Reason: {args.reason}")
    return 0


def cmd_pipeline(args) -> int:
    records = ensure_db()
    _print_pipeline_summary(records, full=True)
    return 0


def cmd_wizard(args) -> int:
    """One-shot: create prospect if missing, link audit + optional proposal,
    set score, auto-advance status. Designed for SynergyAI's 6-round budget —
    a full intake completes in a single client-tool call."""
    records = ensure_db()
    domain = args.domain.lower().strip()
    actions: list[str] = []

    existing = find_prospect(records, domain)
    if existing:
        rec = existing
        actions.append(f"Found existing prospect {rec['id']} — updating.")
    else:
        rec = {
            "id": next_id(records),
            "company": args.company or domain_to_company(domain),
            "domain": domain,
            "contact_email": args.contact_email or "",
            "contact_name": args.contact_name or "",
            "industry": args.industry or "",
            "country": args.country or "",
            "status": "lead",
            "geo_score": None,
            "audit_date": None,
            "audit_file": None,
            "proposal_file": None,
            "monthly_value": float(args.monthly_value) if args.monthly_value else 0.0,
            "contract_start": None,
            "contract_months": 0,
            "notes": [],
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        add_auto_note(rec, "Prospect created via wizard.")
        records.append(rec)
        actions.append(f"Created {rec['id']} — {rec['company']}.")

    # Apply optional fields (only if the wizard supplied them and they're absent)
    for field, val in (("contact_email", args.contact_email),
                       ("contact_name", args.contact_name),
                       ("industry", args.industry),
                       ("country", args.country)):
        if val and not rec.get(field):
            rec[field] = val
            actions.append(f"Set {field} = {val!r}.")

    if args.monthly_value:
        rec["monthly_value"] = float(args.monthly_value)
        actions.append(f"Set monthly_value = €{rec['monthly_value']:,.0f}.")

    # Link audit
    if args.audit:
        audit_path = _resolve_outputs_relative(args.audit)
        if not audit_path.is_file():
            print(f"ERROR: audit file not found: {audit_path}", file=sys.stderr)
            return 1
        rel = audit_path.name if audit_path.parent == DEFAULT_OUTPUT_DIR else str(audit_path)
        rec["audit_file"] = rel
        rec["audit_date"] = today_iso()
        actions.append(f"Linked audit `{rel}`.")
        if args.score is not None:
            rec["geo_score"] = int(args.score)
            actions.append(f"Set score = {args.score}/100.")

    # Link proposal
    if args.proposal:
        p_path = _resolve_outputs_relative(args.proposal)
        if not p_path.is_file():
            print(f"ERROR: proposal file not found: {p_path}", file=sys.stderr)
            return 1
        rel = p_path.name if p_path.parent == DEFAULT_OUTPUT_DIR else str(p_path)
        rec["proposal_file"] = rel
        actions.append(f"Linked proposal `{rel}`.")

    # Status resolution: explicit override > auto-advance from links > leave alone
    if args.status:
        if args.status not in VALID_STATUSES:
            print(f"ERROR: invalid status {args.status!r}. Valid: "
                  f"{', '.join(VALID_STATUSES)}", file=sys.stderr)
            return 2
        if rec["status"] != args.status:
            old = rec["status"]
            rec["status"] = args.status
            actions.append(f"Status: {old} -> {args.status} (explicit).")
    else:
        old = rec["status"]
        if args.proposal and old in ("lead", "qualified"):
            rec["status"] = "proposal"
            actions.append(f"Status auto-advanced: {old} -> proposal "
                            "(proposal linked).")
        elif args.audit and args.score is not None and old == "lead":
            rec["status"] = "qualified"
            actions.append(f"Status auto-advanced: lead -> qualified "
                            "(audit + score linked).")

    add_auto_note(rec, "wizard: " + " ".join(actions))
    save_db(records)

    print(f"# Wizard complete: {rec['id']} — {rec['company']}")
    print()
    for a in actions:
        print(f"- {a}")
    print()
    print(_format_record(rec))
    return 0


# ---------------------------------------------------------------------------
# Pipeline summary
# ---------------------------------------------------------------------------

def _print_pipeline_summary(records: list[dict], full: bool = False) -> None:
    counts = {s: 0 for s in VALID_STATUSES}
    values = {s: 0.0 for s in VALID_STATUSES}
    for r in records:
        s = r.get("status", "lead")
        if s in counts:
            counts[s] += 1
            values[s] += float(r.get("monthly_value") or 0)

    committed_mrr = values["won"]
    pipeline_value = (values["qualified"] + values["proposal"])
    total_potential = committed_mrr + pipeline_value + values["lead"]

    title = f"# GEO Agency Pipeline — {time.strftime('%B %Y')}" if full else "## Pipeline summary"
    print(title)
    print()
    print("| Stage | Count | Potential value | Notes |")
    print("|---|---|---|---|")
    notes_per_stage = {
        "lead": "new discoveries",
        "qualified": "ready for proposal",
        "proposal": "awaiting signature",
        "won": "active clients (MRR)",
        "lost": "—",
    }
    for s in VALID_STATUSES:
        v = values[s]
        v_s = f"€{v:,.0f}/mo" if v else "—"
        print(f"| {s.title()} | {counts[s]} | {v_s} | {notes_per_stage[s]} |")
    print()
    print(f"- **Committed MRR:** €{committed_mrr:,.0f}")
    print(f"- **Pipeline (qualified + proposal):** €{pipeline_value:,.0f}")
    print(f"- **Total potential:** €{total_potential:,.0f}/mo  →  "
          f"€{total_potential * 12:,.0f}/yr")

    if full:
        # Suggested next actions: oldest proposals + leads with scores ≤55
        suggestions: list[str] = []
        for r in records:
            if r.get("status") == "proposal" and r.get("updated_at"):
                # Days since last update
                try:
                    last = time.mktime(time.strptime(r["updated_at"][:10],
                                                      "%Y-%m-%d"))
                    days = int((time.time() - last) / 86400)
                    if days >= 5:
                        suggestions.append(
                            f"→ {r['id']} ({r['domain']}): follow up — "
                            f"proposal sent {days}d ago")
                except Exception:
                    pass
            if r.get("status") == "lead" and r.get("geo_score") is None:
                suggestions.append(
                    f"→ {r['id']} ({r['domain']}): run `geo-audit` to score "
                    "(currently un-scored lead)")
            if (r.get("status") == "qualified"
                and r.get("geo_score") is not None
                and r["geo_score"] <= 55
                and not r.get("proposal_file")):
                suggestions.append(
                    f"→ {r['id']} ({r['domain']}): send proposal — "
                    f"score {r['geo_score']}/100 (strong case)")
        if suggestions:
            print()
            print("**Suggested next actions:**")
            for s in suggestions[:8]:
                print(s)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_record(r: dict) -> str:
    score = r.get("geo_score")
    score_s = f"{score}/100" if score is not None else "—"
    value = r.get("monthly_value") or 0
    value_s = f"€{value:,.0f}/mo" if value else "—"
    lines = [
        f"- **Status:** `{r['status']}`",
        f"- **Domain:** {r['domain']}",
        f"- **Contact:** {r.get('contact_name') or '—'}"
        f"{' · ' + r['contact_email'] if r.get('contact_email') else ''}",
        f"- **Industry / Country:** {r.get('industry') or '—'} "
        f"/ {r.get('country') or '—'}",
        f"- **GEO Score:** {score_s} (audited {r.get('audit_date') or '—'})",
        f"- **Audit file:** {r.get('audit_file') or '(not linked)'}",
        f"- **Proposal file:** {r.get('proposal_file') or '(not linked)'}",
        f"- **Monthly value:** {value_s}",
    ]
    if r.get("status") == "won":
        lines.append(f"- **Contract:** {r.get('contract_months')} months "
                     f"from {r.get('contract_start')}")
    lines.append(f"- **Created / Updated:** {r['created_at']} / {r['updated_at']}")
    return "\n".join(lines)


def _resolve_outputs_relative(path_str: str) -> Path:
    p = Path(path_str).expanduser()
    if p.is_absolute() or p.exists():
        return p
    return DEFAULT_OUTPUT_DIR / path_str


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="geo-prospect CRM-lite.")
    sub = parser.add_subparsers(dest="cmd")

    p_new = sub.add_parser("new")
    p_new.add_argument("domain")
    p_new.add_argument("--company", default=None)
    p_new.add_argument("--contact-name", default=None)
    p_new.add_argument("--contact-email", default=None)
    p_new.add_argument("--industry", default=None)
    p_new.add_argument("--country", default=None)
    p_new.add_argument("--monthly-value", default=None)

    p_list = sub.add_parser("list")
    p_list.add_argument("status", nargs="?", default=None)

    p_show = sub.add_parser("show")
    p_show.add_argument("id_or_domain")

    p_la = sub.add_parser("link-audit")
    p_la.add_argument("id_or_domain")
    p_la.add_argument("audit_file")
    p_la.add_argument("--score", type=int, default=None)

    p_lp = sub.add_parser("link-proposal")
    p_lp.add_argument("id_or_domain")
    p_lp.add_argument("proposal_file")

    p_note = sub.add_parser("note")
    p_note.add_argument("id_or_domain")
    p_note.add_argument("text")

    p_status = sub.add_parser("status")
    p_status.add_argument("id_or_domain")
    p_status.add_argument("new_status")

    p_won = sub.add_parser("won")
    p_won.add_argument("id_or_domain")
    p_won.add_argument("monthly_value")
    p_won.add_argument("--months", type=int, default=6)
    p_won.add_argument("--start-date", default=None)

    p_lost = sub.add_parser("lost")
    p_lost.add_argument("id_or_domain")
    p_lost.add_argument("reason")

    sub.add_parser("pipeline")

    p_wiz = sub.add_parser("wizard",
        help="One-shot create-or-update + link audit/proposal + set status.")
    p_wiz.add_argument("domain")
    p_wiz.add_argument("--audit", default=None,
        help="Filename (in outputs/) or absolute path of the audit Markdown.")
    p_wiz.add_argument("--proposal", default=None,
        help="Filename (in outputs/) or absolute path of the proposal Markdown.")
    p_wiz.add_argument("--score", type=int, default=None,
        help="GEO score from the audit (0-100).")
    p_wiz.add_argument("--status", default=None,
        help=f"Explicit status (overrides auto-advance). "
             f"Values: {', '.join(VALID_STATUSES)}.")
    p_wiz.add_argument("--company", default=None)
    p_wiz.add_argument("--contact-name", default=None)
    p_wiz.add_argument("--contact-email", default=None)
    p_wiz.add_argument("--industry", default=None)
    p_wiz.add_argument("--country", default=None)
    p_wiz.add_argument("--monthly-value", default=None)

    # Accept --source / -o flags from the runner even though we don't fetch.
    parser.add_argument("--source", default=None,
                        help="Runner pre-fetch source label (ignored).")
    parser.add_argument("-o", "--output", default=None,
                        help="Output dir (ignored — DB is at a fixed path).")

    args = parser.parse_args(argv)

    if args.cmd is None:
        # Default: list + pipeline
        list_args = argparse.Namespace(status=None)
        cmd_list(list_args)
        return 0

    dispatch = {
        "new": cmd_new,
        "list": cmd_list,
        "show": cmd_show,
        "link-audit": cmd_link_audit,
        "link-proposal": cmd_link_proposal,
        "note": cmd_note,
        "status": cmd_status,
        "won": cmd_won,
        "lost": cmd_lost,
        "pipeline": cmd_pipeline,
        "wizard": cmd_wizard,
    }
    handler = dispatch.get(args.cmd)
    if not handler:
        print(f"ERROR: unknown subcommand {args.cmd!r}", file=sys.stderr)
        return 2
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
