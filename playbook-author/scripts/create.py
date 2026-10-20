#!/usr/bin/env python3
"""
playbook-author / create.py — render a Console-style playbook from a structured
draft, check it the way the app's PlaybookDocument + PlaybookAnalyzer do, and
write the text to /outputs/.

Input (-i): JSON written by the model:
{
  "name": "time-off-requests",            # file slug
  "title": "Time Off & Leave Requests",   # required
  "trigger": "Requester asks for ...",    # required
  "domain": "an HR desk",                 # optional
  "preamble": "...",                      # optional, kept verbatim above Title
  "steps": [                              # required, >= 1
    {"text": "#Lookup Users on the requester with includeManager."},
    {"text": "Validate the request:", "sub": ["Sufficient balance? If not, #Send Direct Message ..."]}
  ],
  "tools_used": ["Workday", "Slack"],     # optional — servers of bound actions are merged in
  "actions_used": ["#Lookup Users"],      # optional — derived from the prose when omitted
  "inputs": [{"name": "start_date", "label": "Start date", "type": "date", "mandatory": true}]
                                           # optional — fields the ServiceNow request carries in "answers"
}

Checks (mirror the PHP kernel):
  * sections present, steps numbered 1..N, sub-steps as "- " bullets
  * every #Action in the prose is listed under Actions used, and vice versa
  * native verbs recognised exactly (see NATIVE_VERBS)
  * each custom action scored against references/mcp-catalog.md with the
    analyzer's auto-binding rule (server token = 4, tool token = 2, +1 full
    cover; needs >= 4 and a unique best) → bound / unbound
--unattended: gate verbs (GATE_VERBS) and unbound actions are errors (exit 1).
Exit 1 on structural errors (fix and re-run); exit 0 with warnings for
unbound actions (legal at run time — the handoff policy applies).
"""
from __future__ import annotations
import argparse, json, os, re, sys
from pathlib import Path

NATIVE_VERBS = {
    '#send direct message', '#send channel message', '#send email', '#leave internal note',
    '#resolve request', '#escalate request', '#request approval', '#prompt for handoff', '#trigger form',
}
# Native verbs that open a human gate (wait for a person). Read from the backend:
# gpt/backend/src/Playbook/GateManager.php (KIND_MAP: trigger_form, request_approval,
# prompt_handoff, await_message) and PlaybookAnalyzer.php (GATE_BY_NATIVE, the three
# that have a native verb; await_message is only a runtime fallback, it has no verb).
GATE_VERBS = {'#trigger form', '#request approval', '#prompt for handoff'}
STOP = {'custom', 'the', 'a', 'an', 'by', 'for', 'to', 'and', 'of', 'action'}
ACTION_RE = re.compile(r"#[A-Z][\w'’]*(?: [A-Z][\w'’]*){0,5}(?: \([A-Za-z ]+\))?")
# Native verbs may carry lowercase words ("#Prompt for Handoff"), which ACTION_RE
# would cut at "#Prompt"; match them first, longest name first, case-insensitively.
NATIVE_RE = re.compile('|'.join(re.escape(v) for v in sorted(NATIVE_VERBS, key=len, reverse=True)), re.I)


def find_actions(text: str) -> list[str]:
    """All #Actions in `text`, in order of appearance, native verbs recognised whole."""
    found = [(m.start(), m.group(0)) for m in NATIVE_RE.finditer(text)]
    masked = NATIVE_RE.sub(lambda m: ' ' * len(m.group(0)), text)
    found += [(m.start(), m.group(0)) for m in ACTION_RE.finditer(masked)]
    return [a for _, a in sorted(found)]
CATALOG_CANDIDATES = [
    'references/mcp-catalog.md', '../references/mcp-catalog.md',
    '/skill/playbook-author/references/mcp-catalog.md',
]


def tok(text: str) -> list[str]:
    out = []
    for w in re.split(r'[^a-z0-9]+', text.lower()):
        if not w or w in STOP:
            continue
        w2 = w.rstrip('s') or w
        if w2 not in out:
            out.append(w2)
    return out


def slug(server_name: str) -> str:
    return re.sub(r'[^a-z0-9]+', '_', server_name, flags=re.I).lower()


def load_catalog(explicit: str | None) -> tuple[list[tuple[str, str, str]], str | None]:
    """Return ([(server_slug, server_name, tool)], path) from the generated markdown."""
    paths = [explicit] if explicit else CATALOG_CANDIDATES
    for p in paths:
        if p and Path(p).is_file():
            tools, server, name = [], '', ''
            for line in Path(p).read_text(encoding='utf-8').splitlines():
                m = re.match(r'^## (.+?)\s*$', line)
                if m:
                    name = m.group(1).strip(); server = slug(name); continue
                m = re.match(r'^- `mcp_([^`]+)`', line)
                if m and server:
                    tools.append((server, name, m.group(1)))
            return tools, p
    return [], None


def auto_bind(action: str, tools: list[tuple[str, str, str]]) -> tuple[str | None, int, bool]:
    at = tok(action)
    if not at:
        return None, 0, False
    best, best_score, tie = None, 0, False
    for server, _name, tool in tools:
        score = sum(4 for st in tok(server) if st in at)
        tt = tok(tool)
        hits = sum(1 for t in tt if t in at)
        score += 2 * hits
        if tt and hits == len(tt):
            score += 1
        if score > best_score:
            best, best_score, tie = f'{server}.{tool}', score, False
        elif score == best_score and score > 0:
            tie = True
    return (best if best_score >= 4 and not tie else None), best_score, tie


def render(d: dict) -> str:
    lines = []
    if d.get('preamble'):
        lines += [d['preamble'].strip(), '']
    lines += [f"Title: {d['title'].strip()}", '']
    if d.get('domain'):
        lines += [f"Domain: {d['domain'].strip()}", '']
    lines += [f"Trigger: {d['trigger'].strip()}", '', 'Instructions:']
    if d.get('inputs'):
        carried = ', '.join(f"{i['name']} ({i['type']}, {'required' if i.get('mandatory') else 'optional'})"
                            for i in d['inputs'])
        lines.append(f'The request is a ServiceNow job: its "answers" carry {carried}.')
    for i, step in enumerate(d['steps'], 1):
        lines.append(f"{i}. {step['text'].strip()}")
        for sub in step.get('sub') or []:
            lines.append(f"- {sub.strip()}")
    lines += ['', f"Tools used: {'; '.join(d['tools_used'])}", '', f"Actions used: {'; '.join(d['actions_used'])}"]
    return '\n'.join(lines) + '\n'


def check(d: dict, tools: list[tuple[str, str, str]]) -> tuple[list[str], list[str], list[str], dict]:
    errors, warnings, notices = [], [], []
    for k in ('title', 'trigger'):
        if not str(d.get(k, '')).strip():
            errors.append(f'missing "{k}"')
    for n, i in enumerate(d.get('inputs') or [], 1):
        if not (isinstance(i, dict) and str(i.get('name', '')).strip() and str(i.get('type', '')).strip()):
            errors.append(f'inputs[{n}] needs a "name" and a "type" ({{name, label, type, mandatory}})')
    steps = d.get('steps') or []
    if not steps or not all(isinstance(s, dict) and str(s.get('text', '')).strip() for s in steps):
        errors.append('"steps" must be a non-empty list of {"text": ..., "sub": [...]}')
    # actions in prose, with their step numbers
    where: dict[str, str] = {}
    for i, s in enumerate(steps, 1):
        if not isinstance(s, dict):
            continue
        for j, text in enumerate([s.get('text', '')] + list(s.get('sub') or [])):
            for a in find_actions(str(text)):
                where.setdefault(a.lower(), f'step {i}' + (f', bullet {j}' if j else '') + f': "{str(text).strip()[:80]}"')
    listed = [a.strip() for a in (d.get('actions_used') or []) if str(a).strip()]
    if not listed:
        # derive from prose, first-seen order, canonical spelling from the prose
        seen = {}
        for i, s in enumerate(steps, 1):
            for text in [s.get('text', '')] + list(s.get('sub') or []):
                for a in find_actions(str(text)):
                    seen.setdefault(a.lower(), a)
        listed = list(seen.values())
        notices.append(f'Actions used derived from the prose ({len(listed)} actions)')
    listed_l = {a.lower() for a in listed}
    for a_l, loc in where.items():
        if a_l not in listed_l:
            errors.append(f'{a_l} is used in the prose but not listed under Actions used — {loc}')
    for a in listed:
        if a.lower() not in where:
            errors.append(f'{a} is listed under Actions used but never appears in the Instructions')
        if not a.startswith('#'):
            errors.append(f'{a}: actions must start with "#"')
        if re.match(r'#\s*custom\b', a, re.I):
            # Console writes integration actions as the function name
            # (#Archive Channel, #Activate Okta User); "Custom" is not part
            # of the name. Suggest the plain name: drop "Custom" and, when
            # the next words are a catalog server, drop those too.
            rest = re.sub(r'^#\s*custom\s*', '', a, flags=re.I).strip()
            for _sid, display, _tool in tools:
                if rest.lower().startswith(display.lower() + ' '):
                    rest = rest[len(display):].strip()
                    break
            errors.append(f'{a}: drop the "Custom" prefix — write it as #{rest} (the function name; '
                          f'add the server name only when two servers have the same function) — {where.get(a.lower(), "?")}')
    # bindings
    bound, unbound, servers = {}, [], set()
    for a in listed:
        if a.lower() in NATIVE_VERBS:
            bound[a] = 'native'; continue
        target, score, tie = auto_bind(a, tools)
        if target:
            bound[a] = target; servers.add(target.split('.')[0])
        else:
            unbound.append(a)
            hint = ' (ambiguous — two tools score the same; add the server name to the action)' if tie else (
                ' name it after the MCP function, e.g. "#Get PTO Balance"; add the server name if two servers have it)' if score < 4 else '')
            warnings.append(f'{a} is unbound{hint} — {where.get(a.lower(), "?")}')
    # tools used: merge servers of bound actions (display names)
    names = {s: n for s, n, _ in tools}
    tools_used = [t.strip() for t in (d.get('tools_used') or []) if str(t).strip()]
    for s in sorted(servers):
        n = names.get(s, s)
        if n.lower() not in {t.lower() for t in tools_used}:
            tools_used.append(n)
    d['actions_used'] = listed
    d['tools_used'] = tools_used
    return errors, warnings, notices, {'bound': bound, 'unbound': unbound}


def run_unattended_check(text: str, catalog: list[tuple[str, str, str]]) -> list[str]:
    """Errors that make a rendered playbook unfit for an unattended run: any gate
    verb (it would wait for a person) and any action that does not bind. Each names
    the step. `catalog` is load_catalog()'s tool list; empty means binding is skipped."""
    errors, step, seen = [], 0, set()
    in_steps = False
    for line in text.splitlines():
        if line.startswith('Instructions:'):
            in_steps = True; continue
        if not in_steps:
            continue
        if re.match(r'(Tools|Actions) used:', line):
            break
        m = re.match(r'(\d+)\.\s', line)
        if m:
            step = int(m.group(1))
        elif not line.startswith('- '):
            continue
        for a in find_actions(line):
            key = (step, a.lower())
            if key in seen:
                continue
            seen.add(key)
            if a.lower() in GATE_VERBS:
                errors.append(f'{a} waits for a person, which an unattended run cannot do — step {step}: "{line.strip()[:80]}"')
            elif a.lower() not in NATIVE_VERBS and catalog and not auto_bind(a, catalog)[0]:
                errors.append(f'{a} is unbound, and an unattended run has no human to take over — step {step}: "{line.strip()[:80]}"')
    return errors


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description='Render + check a Console-style playbook.')
    p.add_argument('-i', '--input', required=True, help='draft JSON (see module docstring)')
    p.add_argument('-o', '--output', required=True, help='where to write the playbook text (.md)')
    p.add_argument('--catalog', default=None, help='mcp-catalog.md path (auto-detected)')
    p.add_argument('--unattended', action='store_true',
                   help='no human will answer: gate verbs and unbound actions are errors')
    a = p.parse_args(argv)
    try:
        d = json.loads(Path(a.input).read_text(encoding='utf-8'))
    except Exception as e:  # noqa: BLE001
        print(f'error: cannot read draft JSON: {e}', file=sys.stderr); return 2
    tools, cat_path = load_catalog(a.catalog)
    errors, warnings, notices, binds = check(d, tools)
    if errors:
        print(f'INVALID — {len(errors)} problem(s):\n  - ' + '\n  - '.join(errors), file=sys.stderr)
        return 1
    text = render(d)
    if a.unattended:
        problems = run_unattended_check(text, tools)
        if problems:
            print(f'INVALID — {len(problems)} problem(s) for an unattended run:\n  - ' + '\n  - '.join(problems),
                  file=sys.stderr)
            return 1
    out = Path(a.output); out.parent.mkdir(parents=True, exist_ok=True); out.write_text(text, encoding='utf-8')
    print(f'wrote {out}')
    print(f'catalog: {cat_path or "NOT FOUND — binding check skipped"}')
    for act, t in binds['bound'].items():
        print(f'  bound    {act} → {t}')
    for w in warnings:
        print(f'  UNBOUND  {w}')
    for n in notices:
        print(f'  note     {n}')
    print(f'VALID — {len(binds["bound"])} bound, {len(binds["unbound"])} unbound')
    if binds['unbound']:
        print('NEXT: rename the UNBOUND actions and run again (fix them all in one revision); '
              'after two revisions save anyway with save_playbook_agent — unbound actions hand off to a human at run time.')
    else:
        print('NEXT: call save_playbook_agent now (name = Title, description = Trigger, playbook = the text below).')
    print('\n----- playbook -----\n' + text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
