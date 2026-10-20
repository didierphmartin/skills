#!/usr/bin/env python3
"""
servicenow-automation-author / create.py — derive the three halves of a SynergyAI automation from ONE spec and run the
skills that own them: the ServiceNow flow + catalog form (servicenow-flow-author), the unattended playbook
(playbook-author) and the SynergyAI workflow (workflow-compile).

Usage (the argv of run_skill_script):
  create.py -i /scratch/<key>.json --version N [--vendor <company code>] [--app <app>] [--out /outputs]

--version comes from save_automation (the record is the master); the spec never carries it.

Writes, only when all three parts are VALID:
  <out>/servicenow/<scope>/flow-specs/<key>.json (+ the Fluent project, by servicenow-flow-author)
  <out>/<key>.playbook.md
  <out>/<key>.workflow.json
  <out>/<key>.automation.json   (the spec, the version and the derived values)

Exit 0  VALID: one line per part, then NEXT, then the playbook text.
Exit 1  INVALID (each sub-skill's problems prefixed [flow] / [playbook] / [workflow]) or NEED_VENDOR.
Exit 2  the spec cannot be read, or a sibling skill / the MCP catalog cannot be found.
Standard library only: it runs in the app's browser Pyodide; the other skills' scripts are imported, not spawned.
"""
from __future__ import annotations
import argparse, contextlib, copy, importlib.util, io, json, re, shutil, sys, tempfile
from pathlib import Path

SKILLS_ROOT = Path('/skills-root' if sys.platform == 'emscripten' else Path.home() / 'Documents' / 'synergyAI' / 'skills')
KEY_RE = re.compile(r'^[a-z0-9][a-z0-9-]{1,40}$')
VAR_RE = re.compile(r'^[a-z][a-z0-9_]{0,39}$')
STAMP_VAR = 'synergyai_automation'
TASK_STEP = 'synergyai_task'
START_OPTION = 'ServiceNow: get new job'
COMPLETE = 'ServiceNow: complete job'
HAND_BACK = 'ServiceNow: hand back'
DEFAULT_HAND_BACK_GROUP = 'Service Desk'
NEXT = 'NEXT: save_playbook_agent then save_workflow then save_automation'
DEFAULT_PLACEHOLDERS = {
    'service_catalog': {'kind': 'catalog', 'description': 'The catalog that lists SynergyAI automations (sc_catalog).'},
    'synergyai_category': {'kind': 'category', 'description': 'The category of SynergyAI automations (sc_category).'},
    'synergyai_group': {'kind': 'group', 'description': 'The SynergyAI group the connector polls (sys_user_group).'},
}


# ------------------------------------------------------------------ sibling skills
def load_script(skill: str, script: str):
    """Import <skills root>/<skill>/scripts/<script> as a module (each run gets a fresh module object)."""
    path = SKILLS_ROOT / skill / 'scripts' / script
    if not path.is_file():
        raise FileNotFoundError(f'{skill}/scripts/{script} not found under {SKILLS_ROOT}')
    spec = importlib.util.spec_from_file_location(f'_automation_{skill.replace("-", "_")}', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def call(mod, argv: list[str]) -> tuple[int, str, str]:
    """Run mod.main(argv) in-process; return (exit code, stdout, stderr). argparse exits become codes."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = mod.main(argv)
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 2
        except Exception as e:  # noqa: BLE001 — a sub-skill crash is reported as INVALID under its prefix
            return 1, '', f'- {type(e).__name__}: {e}'
    return int(code or 0), out.getvalue(), err.getvalue()


def problems(text: str) -> list[str]:
    """The '- …' problem lines of a sub-skill's INVALID report (or every non-empty line when it has none)."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    listed = [l[2:] for l in lines if l.startswith('- ')]
    return listed or [l for l in lines if not l.startswith('NEXT:')]


# ------------------------------------------------------------------ validation
def check(spec) -> list[str]:
    """Problems with the automation spec itself, before any sub-skill sees it."""
    if not isinstance(spec, dict):
        return ['the spec must be a JSON object']
    errors = []
    key = spec.get('name')
    if not (isinstance(key, str) and KEY_RE.match(key)):
        errors.append(f'name {key!r} must match {KEY_RE.pattern} (lowercase letters, digits and hyphens): '
                      f'it becomes the tag synergyai:<name> and the playbook key')
    for k in ('title', 'description'):
        if not (isinstance(spec.get(k), str) and spec[k].strip()):
            errors.append(f'missing "{k}" (non-empty text)')
    form = spec.get('form')
    if not isinstance(form, dict):
        errors.append('missing "form" ({"submitted_by", "variables"})')
        form = {}
    if not (isinstance(form.get('submitted_by'), str) and form['submitted_by'].strip()):
        errors.append('form.submitted_by: who submits the order form, and when (non-empty text)')
    variables = form.get('variables', [])
    if not isinstance(variables, list):
        errors.append('form.variables must be a list'); variables = []
    for n, v in enumerate(variables, 1):
        name = v.get('name') if isinstance(v, dict) else None
        if not (isinstance(name, str) and VAR_RE.match(name)):
            errors.append(f'form.variables[{n}] name {name!r} must match {VAR_RE.pattern} (snake_case): '
                          f'it is both the catalog variable and the playbook input')
        elif name == STAMP_VAR:
            errors.append(f'form.variables[{n}] "{STAMP_VAR}" is reserved: the script adds it as the version stamp')
    sn = spec.get('servicenow', {})
    if not isinstance(sn, dict):
        errors.append('"servicenow" must be an object ({"before", "after", "placeholders"})'); sn = {}
    for k in ('before', 'after'):
        if not isinstance(sn.get(k, []), list):
            errors.append(f'servicenow.{k} must be a list of flow steps')
    phs = sn.get('placeholders', {})
    if not (isinstance(phs, dict) and all(isinstance(v, dict) for v in phs.values())):
        errors.append('servicenow.placeholders must be an object of {"<name>": {"kind", "description", "value"}}')
    pb = spec.get('playbook')
    if not isinstance(pb, dict):
        errors.append('missing "playbook" ({"trigger", "steps", "tools_used"})'); pb = {}
    if not (isinstance(pb.get('trigger'), str) and pb['trigger'].strip()):
        errors.append('playbook.trigger: what the request is (non-empty text)')
    if not (isinstance(pb.get('steps'), list) and pb['steps']):
        errors.append('playbook.steps: at least one playbook step')
    else:
        for n, st in enumerate(pb['steps'], 1):
            sub = st.get('sub', []) if isinstance(st, dict) else None
            if not (isinstance(st, dict) and isinstance(st.get('text'), str) and st['text'].strip()):
                errors.append(f'playbook.steps[{n}] must be {{"text": "…", "sub": ["…"]}} with a non-empty text, '
                              f'got {json.dumps(st, ensure_ascii=False)[:80]}')
            elif not (isinstance(sub, list) and all(isinstance(x, str) for x in sub)):
                errors.append(f'playbook.steps[{n}].sub must be a list of strings')
    outcomes = spec.get('outcomes', {})
    failed = outcomes.get('failed', {}) if isinstance(outcomes, dict) else None
    if not isinstance(failed, dict):
        errors.append('"outcomes" must be {"done": {…}, "failed": {"hand_back_group": "<group>"}}')
    elif not (isinstance(failed.get('hand_back_group', DEFAULT_HAND_BACK_GROUP), str)
              and failed.get('hand_back_group', DEFAULT_HAND_BACK_GROUP).strip()):
        errors.append('outcomes.failed.hand_back_group must be a group name')
    return errors


# ------------------------------------------------------------------ derivation
def hand_back_group(spec: dict) -> str:
    return (((spec.get('outcomes') or {}).get('failed') or {}).get('hand_back_group') or DEFAULT_HAND_BACK_GROUP).strip()


def flow_spec(spec: dict, version: int) -> dict:
    """servicenow-flow-author's spec: the form + the stamp variable, then before + the SynergyAI task + after."""
    key, sn = spec['name'], spec.get('servicenow') or {}
    stamp = f'{key}@{version}'
    placeholders = copy.deepcopy(DEFAULT_PLACEHOLDERS)
    for name, p in (sn.get('placeholders') or {}).items():
        placeholders[name] = {**placeholders.get(name, {}), **p}
    task = {
        'id': TASK_STEP,
        'label': f'SynergyAI fulfils the request (playbook {key})',
        'action': 'createCatalogTask',
        'inputs': {
            'ah_requested_item': {'pill': 'trigger.request_item'},
            'ah_short_description': f'SynergyAI: {spec["title"].strip()}',
            'ah_fields': {
                'assignment_group': {'placeholder': 'synergyai_group'},
                'correlation_display': f'synergyai:{key}',
                'correlation_id': stamp,
            },
            'ah_wait': True,
        },
    }
    return {
        'name': key,
        'title': spec['title'],
        'description': spec['description'],
        'language': spec.get('language') or 'en',
        'trigger': {'type': 'service_catalog'},
        'form': {
            'submitted_by': spec['form']['submitted_by'],
            'catalog': {'placeholder': 'service_catalog'},
            'category': {'placeholder': 'synergyai_category'},
            'variables': copy.deepcopy(spec['form'].get('variables') or []) + [{
                'name': STAMP_VAR, 'label': 'SynergyAI automation', 'type': 'single_line_text',
                'default': stamp, 'read_only': True, 'hidden': True}],
        },
        'placeholders': placeholders,
        'steps': copy.deepcopy(sn.get('before') or []) + [task] + copy.deepcopy(sn.get('after') or []),
    }


def playbook_draft(spec: dict, version: int) -> dict:
    """playbook-author's draft: key = automation key, trigger stamped, inputs = the form variables."""
    key, pb = spec['name'], spec['playbook']
    draft = {
        'name': key,
        'title': spec['title'],
        'trigger': f'{pb["trigger"].strip()} (automation {key}@{version})',
        'steps': copy.deepcopy(pb['steps']),
        'tools_used': list(pb.get('tools_used') or []),
        'inputs': [{'name': v['name'], 'label': v.get('label', v['name']), 'type': v.get('type', 'single_line_text'),
                    'mandatory': bool(v.get('mandatory'))} for v in spec['form'].get('variables') or []],
    }
    for k in ('domain', 'actions_used'):
        if pb.get(k):
            draft[k] = pb[k]
    return draft


def workflow(spec: dict, version: int) -> dict:
    """workflow-compile's simplified JSON: Start [get new job] → playbook → complete job / hand back → output."""
    key, group = spec['name'], hand_back_group(spec)
    return {
        'name': spec['title'],
        'description': spec['description'],
        'start': {'option': START_OPTION, 'args': {'tag': key, 'version': version, 'hand_back_group': group}},
        'agents': [
            {'id': 'pb', 'kind': 'playbook', 'playbook': key, 'name': spec['title']},
            {'id': 'close', 'kind': 'component', 'component': COMPLETE},
            {'id': 'back', 'kind': 'component', 'component': HAND_BACK, 'args': {'group': group}},
        ],
        'flow': [['start', 'pb'], ['pb.done', 'close'], ['pb.failed', 'back'], ['close', 'output'], ['back', 'output']],
    }


# ------------------------------------------------------------------ CLI
def resolve_catalog(explicit: str | None) -> list[str]:
    if explicit:
        return [explicit]
    return [str(SKILLS_ROOT / 'playbook-author' / 'references' / 'mcp-catalog.md'),
            '/skill/playbook-author/references/mcp-catalog.md']


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='Derive and write the flow, playbook and workflow of one automation.')
    ap.add_argument('-i', '--input', required=True, help='the automation spec (references/automation-spec.md)')
    ap.add_argument('--version', required=True, type=int, help='the version save_automation returned')
    ap.add_argument('--vendor', help="the instance's company code (passed to servicenow-flow-author)")
    ap.add_argument('--app', help='app name in the scope x_<code>_<app> (passed to servicenow-flow-author)')
    ap.add_argument('--out', default='/outputs', help='output root (default /outputs)')
    ap.add_argument('--catalog', help="MCP catalog (default: playbook-author's references/mcp-catalog.md)")
    a = ap.parse_args(argv)
    try:
        spec = json.loads(Path(a.input).read_text(encoding='utf-8'))
    except Exception as e:  # noqa: BLE001 — any unreadable input is reported the same way
        print(f'error: cannot read the automation spec JSON: {e}', file=sys.stderr)
        return 2
    errors = check(spec)
    if a.version < 1:
        errors.append(f'--version {a.version} must be a positive integer (from save_automation)')
    if errors:
        print(f'INVALID — {len(errors)} problem(s):\n  - ' + '\n  - '.join(f'[automation] {e}' for e in errors),
              file=sys.stderr)
        return 1
    try:
        flow_mod = load_script('servicenow-flow-author', 'create.py')
        pb_mod = load_script('playbook-author', 'create.py')
        wf_mod = load_script('workflow-compile', 'compile.py')
    except Exception as e:  # noqa: BLE001
        print(f'error: cannot load a sibling skill: {e}', file=sys.stderr)
        return 2
    catalog, cat_path = [], None
    for p in resolve_catalog(a.catalog):
        catalog, cat_path = pb_mod.load_catalog(p)
        if catalog:
            break
    if not catalog:
        print('error: no MCP catalog could be loaded (tried ' + ', '.join(resolve_catalog(a.catalog)) +
              '); the unattended check needs it to find unbound actions', file=sys.stderr)
        return 2

    key, n = spec['name'], a.version
    out = Path(a.out)
    work = Path(tempfile.mkdtemp(prefix=f'automation-{key}-'))
    try:
        return run(spec, n, a, out, work, flow_mod, pb_mod, wf_mod, catalog, cat_path)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def run(spec, n, a, out, work, flow_mod, pb_mod, wf_mod, catalog, cat_path) -> int:
    key = spec['name']
    fails: list[str] = []
    # 1. playbook and workflow into the scratch folder first: nothing reaches <out> unless every part is VALID.
    draft_path, pb_tmp = work / f'{key}.draft.json', work / f'{key}.playbook.md'
    draft_path.write_text(json.dumps(playbook_draft(spec, n), indent=2), encoding='utf-8')
    pcode, pout, perr = call(pb_mod, ['-i', str(draft_path), '-o', str(pb_tmp), '--catalog', cat_path, '--unattended'])
    pb_text = pb_tmp.read_text(encoding='utf-8') if pcode == 0 and pb_tmp.is_file() else ''
    if pcode != 0:
        fails += [f'[playbook] {p}' for p in problems(perr or pout)]
    else:   # the design's re-check of the final text, with the catalog always loaded
        fails += [f'[playbook] {p}' for p in pb_mod.run_unattended_check(pb_text, catalog)]
    simple_path, wf_tmp = work / f'{key}.simple.json', work / f'{key}.workflow.json'
    simple_path.write_text(json.dumps(workflow(spec, n), indent=2), encoding='utf-8')
    wcode, wout, werr = call(wf_mod, ['-i', str(simple_path), '-o', str(wf_tmp)])
    if wcode != 0:
        fails += [f'[workflow] {p}' for p in problems(werr or wout)]
    # 2. the flow: a dry run in scratch when another part already failed (to list every problem at once),
    #    the real run into <out>/servicenow otherwise.
    fspec_path = work / f'{key}.flow.json'
    fspec_path.write_text(json.dumps(flow_spec(spec, n), indent=2), encoding='utf-8')
    sn_out = out / 'servicenow'
    flow_argv = ['-i', str(fspec_path)]
    if a.app:
        flow_argv += ['--app', a.app]
    if fails:
        vendor = a.vendor or ((sn_out / '.vendor').read_text(encoding='utf-8').strip()
                              if (sn_out / '.vendor').is_file() else '0000000')
        fcode, fout, ferr = call(flow_mod, flow_argv + ['--out', str(work / 'servicenow'), '--vendor', vendor])
    else:
        fcode, fout, ferr = call(flow_mod, flow_argv + ['--out', str(sn_out)] + (['--vendor', a.vendor] if a.vendor else []))
    if fcode != 0 and 'NEED_VENDOR' in fout:
        print(fout, end='')
        return 1
    if fcode != 0:
        fails = [f'[flow] {p}' for p in problems(ferr or fout)] + fails
    if fails:
        print(f'INVALID — {len(fails)} problem(s):\n  - ' + '\n  - '.join(fails), file=sys.stderr)
        print('NEXT: fix every problem in one revision of the automation spec and run again (at most two revisions).',
              file=sys.stderr)
        return 1

    # 3. every part VALID: move the playbook and workflow into place, then write the record.
    out.mkdir(parents=True, exist_ok=True)
    pb_path, wf_path, rec_path = out / f'{key}.playbook.md', out / f'{key}.workflow.json', out / f'{key}.automation.json'
    pb_path.write_text(pb_text, encoding='utf-8')
    wf_path.write_text(wf_tmp.read_text(encoding='utf-8'), encoding='utf-8')
    m = re.search(r'^project: (.+?) \((?:created|existing); (\d+ flows?)\)', fout, re.M)
    proj = Path(m.group(1)) if m else sn_out
    flows = m.group(2) if m else '? flows'
    flow_spec_path = proj / 'flow-specs' / f'{key}.json'
    m = re.search(r'^preview: (wrote \S+)', fout, re.M)
    preview = m.group(1) if m else ''
    m = re.search(r'placeholders to fill: (.+?)\.$', fout, re.M)
    to_fill = m.group(1) if m else 'none'
    bound = re.search(r'^VALID — (.+)$', pout, re.M)
    group = hand_back_group(spec)
    record = {
        'key': key,
        'version': n,
        'spec': spec,
        'derived': {
            'tag': key,
            'stamp': f'{key}@{n}',
            'correlation_display': f'synergyai:{key}',
            'correlation_id': f'{key}@{n}',
            'stamp_variable': STAMP_VAR,
            'scope': proj.name,
            'inputs': [v['name'] for v in spec['form'].get('variables') or []],
            'playbook_key': key,
            'start': {'option': START_OPTION, 'args': {'tag': key, 'version': n, 'hand_back_group': group}},
            'hand_back': {'component': HAND_BACK, 'args': {'group': group}},
            'files': {'flow_spec': str(flow_spec_path), 'playbook': str(pb_path), 'workflow': str(wf_path)},
        },
    }
    rec_path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')

    print(f'VALID — automation "{spec["title"]}" v{n}')
    print(f'flow: {flow_spec_path} — app {proj.name} ({flows} in the app); tag synergyai:{key}, stamp {key}@{n}; '
          f'placeholders to fill: {to_fill}' + (f'; preview: {preview}' if preview else ''))
    print(f'playbook: {pb_path} — key {key}; {bound.group(1) if bound else "VALID"}; '
          f'inputs {", ".join(record["derived"]["inputs"]) or "none"}')
    print(f'workflow: {wf_path} — Start "{START_OPTION}" (tag {key}, version {n}); '
          f'done → {COMPLETE}, failed → {HAND_BACK} to {group}')
    print(f'automation: {rec_path}')
    print(NEXT)
    print('\n----- playbook -----\n' + pb_text, end='')
    return 0


if __name__ == '__main__':
    sys.exit(main())
