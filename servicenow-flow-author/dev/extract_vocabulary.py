#!/usr/bin/env python3
"""Dev-only: extract the Fluent flow vocabulary from an installed @servicenow/sdk.

Reads (SDK 4.13.3, under --sdk <node_modules/@servicenow>):
  sdk/docs/api/flow/action-api.md                     -> actions (inputs, outputs)
  sdk/docs/api/flow/trigger-api.md                    -> triggers (inputs, outputs)
  sdk-core/dist/external/flow/flow-data-helpers.d.ts  -> pill types
  sdk-core/dist/fluent/tables/*.now.d.ts              -> table index
  sdk/docs/api/service-catalog/catalogitem-api.md     -> CatalogItem properties
  sdk/docs/api/service-catalog/cataloguipolicy-api.md -> CatalogUiPolicy properties and actions
  sdk/docs/api/service-catalog/variables/*.md         -> variable builders
Writes references/fluent-vocabulary.json and references/fluent-vocabulary.md.
Standard library only. Not shipped to the runtime path (the app never runs it).
"""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path

SDK_VERSION = '4.13.3'
ACTION_DOC = 'docs/api/flow/action-api.md'
TRIGGER_DOC = 'docs/api/flow/trigger-api.md'
LOGIC_DOC = 'docs/api/flow/wfa-flow-logic-api.md'
GUIDE_DOC = 'docs/guides/wfa-flow-guide.md'
ACTIONS_GUIDE = 'docs/guides/wfa-flow-actions-guide.md'
CAT_DOC = 'docs/api/service-catalog'
SAMPLE_CAT = 'src/fluent/flow-trigger-catalog-item.now.ts'   # the lab's official sample (SDK 4.13.3)

TRIGGER_NAMES = {
    'record.created': 'record_created', 'record.updated': 'record_updated',
    'record.createdOrUpdated': 'record_created_or_updated',
    'scheduled.daily': 'scheduled_daily', 'scheduled.weekly': 'scheduled_weekly',
    'scheduled.monthly': 'scheduled_monthly', 'scheduled.repeat': 'scheduled_repeat',
    'scheduled.runOnce': 'scheduled_run_once',
    'application.serviceCatalog': 'service_catalog', 'application.inboundEmail': 'inbound_email',
    'application.slaTask': 'sla_task', 'application.knowledgeManagement': 'knowledge_management',
    'application.remoteTableQuery': 'remote_table_query',
}
# Reference outputs whose table is fixed (trigger-api.md output tables); `current` takes the trigger's table input.
TRIGGER_OUTPUT_TABLES = {'request_item': 'sc_req_item', 'knowledge': 'kb_knowledge',
                         'inbound_email': 'sys_email', 'user': 'sys_user'}
# Action outputs that are records of the table named by one of the step's inputs (action-api.md).
OUTPUT_TABLES = {
    'createRecord': {'record': 'table_name'},        # action-api.md:73
    'updateRecord': {'record': 'table_name'},        # action-api.md:93
    'lookUpRecord': {'Record': 'table'},             # action-api.md:128
    'lookUpRecords': {'Records': 'table'},           # action-api.md:156
    'createOrUpdateRecord': {'record': 'table_name'},  # action-api.md:209
    'createTask': {'Record': 'task_table'},          # action-api.md:594
}
# The table TemplateValue keys are checked against (action-api.md parameter tables).
TEMPLATE_TABLE_INPUT = {'createRecord': 'table_name', 'updateRecord': 'table_name',
                        'updateMultipleRecords': 'table_name', 'createOrUpdateRecord': 'table_name',
                        'createTask': 'task_table'}
INPUT_RULES = {
    'template_text': {  # text with pills allowed (rendered as a template literal)
        'log.log_message': f'{GUIDE_DOC}:127', 'sendEmail.ah_subject': f'{GUIDE_DOC}:127',
        'sendEmail.ah_to': f'{ACTION_DOC}:256', 'lookUpRecord.conditions': 'src/fluent/flow-trigger-record.now.ts:44',
        'lookUpRecords.conditions': 'src/fluent/flow-trigger-record.now.ts:44',
        'updateMultipleRecords.conditions': f'{GUIDE_DOC}:99', 'waitForCondition.conditions': f'{ACTION_DOC}:405',
    },
    'wrap_pill': {  # a single pill must still be written as `${wfa.dataPill(...)}`
        'sendSms.recipients': f'{ACTION_DOC}:293', 'fireEvent.record': f'{ACTION_DOC}:390',
        'fireEvent.parm1': f'{ACTION_DOC}:390', 'fireEvent.parm2': f'{ACTION_DOC}:390',
        'waitForCondition.record': f'{ACTION_DOC}:411', 'getAttachmentsOnRecord.source_record': f'{ACTION_DOC}:774',
        'createCatalogTask.ah_requested_item': f'{SAMPLE_CAT}:227', 'getCatalogVariables.requested_item': f'{SAMPLE_CAT}:142',
    },
    'no_pills': {'sendEmail.ah_body': f'{ACTION_DOC}:256', 'sendSms.message': f'{GUIDE_DOC}:127'},
}
LOGIC = {
    'if': {'fluent': 'wfa.flowLogic.if / elseIf / else', 'doc': f'{LOGIC_DOC}:23',
           'spec': '{"logic":"if","condition":…,"steps":[…],"else_if":[{"id","label","condition","steps"}],"else":{"id","label","steps"}}'},
    'for_each': {'fluent': 'wfa.flowLogic.forEach(items, { $id }, (item) => {…})', 'doc': f'{LOGIC_DOC}:106',
                 'spec': '{"logic":"for_each","items":{"pill":…},"item":"alias","steps":[…]}'},
    'parallel': {'fluent': 'wfa.flowLogic.doInParallel({ $id }, () => {…}, () => {…})', 'doc': f'{LOGIC_DOC}:211',
                 'spec': '{"logic":"parallel","branches":[[…],[…]]}'},
    'try_catch': {'fluent': 'wfa.flowLogic.tryCatch({ $id }, { try: () => {…}, catch: () => {…} })', 'doc': f'{LOGIC_DOC}:273',
                  'spec': '{"logic":"try_catch","try":[…],"catch":[…]}'},
    'do_until': {'fluent': 'wfa.flowLogic.doTheFollowing({ $id }, () => {…; wfa.flowLogic.until(…)})', 'doc': f'{LOGIC_DOC}:346',
                 'spec': '{"logic":"do_until","steps":[…],"until":…}'},
    'wait_duration': {'fluent': 'wfa.flowLogic.waitForADuration({ $id, durationType, duration, … })', 'doc': f'{LOGIC_DOC}:459',
                      'spec': '{"logic":"wait_duration","duration":{"days":2},"relative_to":{"pill":…},"direction":"before","schedule":{"placeholder":…}}'},
    'end': {'fluent': 'wfa.flowLogic.endFlow({ $id })', 'doc': f'{LOGIC_DOC}:187', 'spec': '{"logic":"end"}'},
}
UNSUPPORTED = {
    'go_back_to': 'Workflow Studio "Go back to" is not in Fluent (SDK 4.13.3 wfa.flowLogic has no goBackTo: sdk-core/dist/external/flow/flow-logic/index.d.ts). Put the steps to repeat in a do_until block with the exit condition.',
    'make_a_decision': 'Decision tables are not in Fluent (SDK 4.13.3). Use an if step with else_if branches, one per answer.',
    'decision': 'Decision tables are not in Fluent (SDK 4.13.3). Use an if step with else_if branches, one per answer.',
    'call_workflow': 'Call a Workflow is not in Fluent (SDK 4.13.3). Leave it out and tell the user to add it in Workflow Studio.',
    'dynamic_flow': 'Dynamic Flow is not in Fluent (SDK 4.13.3). Leave it out and tell the user.',
    'get_flow_outputs': 'Get Flow Outputs is not in Fluent (SDK 4.13.3). Leave it out and tell the user.',
    'subflow': 'This skill uses core actions only (subflows are out of scope). Inline the steps.',
    'script': 'The skill never writes code. Use core actions and flow logic; for a computed date use wait_duration with a schedule placeholder.',
    'inline_script': 'The skill never writes code. Use core actions and flow logic; for a computed date use wait_duration with a schedule placeholder.',
    'set_flow_variables': 'Flow variables are not in this skill\'s vocabulary. Read the value again with a pill where it is needed.',
    'exit_loop': 'Not in this skill\'s vocabulary. Put the rest of the loop body inside an if step.',
    'skip_iteration': 'Not in this skill\'s vocabulary. Put the loop body inside an if step.',
    'wait_for_condition': 'Waiting for a record condition is the action waitForCondition (not flow logic): {"action":"waitForCondition", …}.',
    'record_deleted': 'There is no record-deleted trigger in Fluent (trigger-api.md lists created, updated, createdOrUpdated).',
    'rest_api': 'The REST API trigger is not a Fluent trigger (trigger-api.md). Use a record or scheduled trigger.',
    'metricbase': 'The MetricBase trigger is not a Fluent trigger (trigger-api.md).',
    'kafka_message': 'The Kafka Message trigger is not a Fluent trigger (trigger-api.md).',
}
COMMON_TABLES = ['task', 'incident', 'problem', 'change_request', 'sc_request', 'sc_req_item', 'sc_task',
                 'sys_user', 'sys_user_group', 'cmdb_ci', 'cmdb_model', 'kb_knowledge', 'sysapproval_approver']

# ---- Service Catalog (decision 6: the procedure's data comes from a catalog item's variables)
# Spec variable type (snake_case of the builder name without "Variable") -> pill type of its value in the flow.
# Only these builders are offered: they collect one value a flow can read. The others are layout/display or
# need instance records (UI macros, pages, lookup tables) the skill cannot know.
VARIABLE_PILL = {
    'single_line_text': 'string', 'wide_single_line_text': 'string', 'multi_line_text': 'string',
    'email': 'email', 'url': 'string', 'ip_address': 'string', 'date': 'glide_date', 'date_time': 'glide_date_time',
    'yes_no': 'string', 'checkbox': 'boolean', 'select_box': 'string', 'multiple_choice': 'string',
    'reference': 'reference', 'requested_for': 'reference', 'list_collector': 'string',
}
# Builder property a type cannot do without -> the spec key that fills it.
VARIABLE_EXTRA = {'reference': ('referenceTable', 'reference'), 'list_collector': ('listTable', 'reference'),
                  'select_box': ('choices', 'choices'), 'multiple_choice': ('choices', 'choices')}
VARIABLE_TABLE = {'requested_for': 'sys_user'}   # requestedforvariable-api.md:6 "(typically references a user)"
CATALOG_ONLY = {   # "Use only in flows triggered by trigger.application.serviceCatalog."
    'createCatalogTask': f'{ACTIONS_GUIDE}:1080', 'getCatalogVariables': f'{ACTIONS_GUIDE}:1041',
    'submitCatalogItemRequest': f'{ACTIONS_GUIDE}:995',
}
CATALOG_NOTES = {
    'item_flow': f'{CAT_DOC}/catalogitem-api.md:228 (flow: Now.ref(...) to a project flow; {SAMPLE_CAT}:52)',
    'item_catalogs': f'{CAT_DOC}/catalogitem-api.md:280 (ALWAYS assign items to at least one catalog and category)',
    'ah_wait': f'{ACTIONS_GUIDE}:1092 (set ah_wait explicitly; default true blocks the flow until the task closes)',
    'catalog_task_output': f'{ACTIONS_GUIDE}:1096 says "Catalog Task" in brackets; the official sample reads '
                           f'createCatalogTaskAction.catalog_task.state ({SAMPLE_CAT}:243) and that builds',
    'variable_outputs': f'{ACTION_DOC}:679 and {ACTIONS_GUIDE}:1055 say "no outputs"; the typings return '
                        'OutputsWithDotwalk<Record<string, FlowValueType>> (sdk-core/dist/external/flow/built-ins/'
                        f'actions/core/getCatalogVariables.now.d.ts) and the sample reads answers as pills ({SAMPLE_CAT}:158) — trust those',
    'fulfilment': f'{ACTIONS_GUIDE}:948 (fulfilment work assigned to a person/group is an sc_task off the catalog request)',
    'for_each_list': 'wfa-flow-logic-api.md:134-135 iterates arrays only (array.object / array.string); nothing in the docs or '
                     'types makes a list collector answer an array — it is a comma-separated sys_id string, so a flow looks the '
                     'records up with lookUpRecords "sys_idIN<answer>" and iterates Records (wfa-flow-guide.md:185)',
    'ui_policy': f'{CAT_DOC}/cataloguipolicy-api.md:35-41 (appliesOnCatalogItemView / appliesOnCatalogTasks) and :99 (mandatory)',
}

PILL_OF_DOC_TYPE = {'reference': 'reference', 'string': 'string', 'integer': 'integer', 'boolean': 'boolean',
                    'choice': 'choice', 'records': 'records', 'array': 'array.object', 'object': 'object',
                    'glide_date_time': 'glide_date_time', 'datetime': 'glide_date_time', 'duration': 'glide_duration',
                    'Duration': 'glide_duration', 'string_full_utf8': 'string_full_utf8', 'html': 'html'}


def cells(line: str) -> list[str]:
    """Split a markdown table row on unescaped pipes; strip cells."""
    parts = re.split(r'(?<!\\)\|', line.strip())
    return [c.strip().replace('\\|', '|') for c in parts[1:-1]]


def unquote(cell: str) -> str:
    return cell.strip('`').strip('"')


def norm_type(cell: str) -> tuple[str, list[str]]:
    t = cell.strip('`').strip()
    if "'" in t:
        return 'enum', re.findall(r"'([^']*)'", t)
    return t, []


def parse_tables(lines: list[str], start: int, end: int) -> dict[str, list[dict]]:
    """Tables under **Parameters:** / **Outputs:** / #### Configuration Parameters / #### Output Fields."""
    found, current, header = {}, None, None
    for i in range(start, end):
        line = lines[i]
        m = re.match(r'^\*\*(Parameters|Outputs):\*\*(.*)$', line) or re.match(r'^#### (Configuration Parameters|Output Fields)', line)
        if m:
            current = 'in' if m.group(1) in ('Parameters', 'Configuration Parameters') else 'out'
            found.setdefault(current, [])
            header = None
            if m.lastindex and m.lastindex > 1 and m.group(2).strip():   # "**Outputs:** None ..." opens no table
                current = None
            continue
        if current and line.startswith('|'):
            row = cells(line)
            if header is None:
                header = [h.lower() for h in row]
                if header[0] not in ('parameter', 'field'):
                    current, header = None, None
                continue
            if set(''.join(row)) <= set('-: '):
                continue
            rec = dict(zip(header, row))
            rec['_line'] = i + 1
            found[current].append(rec)
        elif current and header is not None and not line.startswith('|'):
            current, header = None, None
    return found


def sections(lines: list[str], pattern: str) -> list[tuple[str, int, int]]:
    heads = [(m.group(1), i) for i, l in enumerate(lines) for m in [re.match(pattern, l)] if m]
    out = []
    for k, (name, i) in enumerate(heads):
        j = heads[k + 1][1] if k + 1 < len(heads) else len(lines)
        # stop at the next heading of the same or higher level
        for n in range(i + 1, j):
            if re.match(r'^#{1,3} ', lines[n]):
                j = n
                break
        out.append((name, i, j))
    return out


def extract_actions(doc: Path) -> dict:
    lines = doc.read_text(encoding='utf-8').splitlines()
    keys = {m.group(1): i + 1 for i, l in enumerate(lines) for m in [re.match(r'^\| `(\w+)` \|', l)] if m and i < 60}
    actions = {k: {'fluent': f'action.core.{k}', 'doc': f'{ACTION_DOC}:{line}', 'documented': False,
                   'inputs': {}, 'outputs': {}} for k, line in keys.items()}
    for name, i, j in sections(lines, r'^#### action\.core\.(\w+)'):
        t = parse_tables(lines, i, j)
        a = actions.setdefault(name, {'fluent': f'action.core.{name}', 'inputs': {}, 'outputs': {}})
        a.update(doc=f'{ACTION_DOC}:{i + 1}', documented=True)
        for r in t.get('in', []):
            typ, values = norm_type(r.get('type', ''))
            a['inputs'][unquote(r['parameter'])] = {'type': typ, 'values': values,
                                                  'required': r.get('required', '').lower() == 'yes',
                                                  'doc': f'{ACTION_DOC}:{r["_line"]}'}
        for r in t.get('out', []):
            out = unquote(r['field'])
            typ, _ = norm_type(r.get('type', ''))
            pill = 'records' if out == 'Records' or typ == 'records' else PILL_OF_DOC_TYPE.get(typ, 'string')
            a['outputs'][out] = {'pill': pill, 'doc': f'{ACTION_DOC}:{r["_line"]}'}
        for out, inp in OUTPUT_TABLES.get(name, {}).items():
            a['outputs'][out]['table_from'] = inp
        if name in TEMPLATE_TABLE_INPUT:
            a['template_table_input'] = TEMPLATE_TABLE_INPUT[name]
    return actions


def extract_triggers(doc: Path) -> dict:
    lines = doc.read_text(encoding='utf-8').splitlines()
    triggers = {}
    for fluent, i, j in sections(lines, r'^### trigger\.(\w+\.\w+)'):
        t = parse_tables(lines, i, j)
        spec_name = TRIGGER_NAMES[fluent]
        entry = {'fluent': f'trigger.{fluent}', 'doc': f'{TRIGGER_DOC}:{i + 1}', 'inputs': {}, 'outputs': {}}
        for r in t.get('in', []):
            typ, values = norm_type(r.get('type', ''))
            entry['inputs'][unquote(r['parameter'])] = {'type': typ, 'values': values,
                                                      'required': r.get('required', '').lower() == 'yes',
                                                      'doc': f'{TRIGGER_DOC}:{r["_line"]}'}
        for r in t.get('out', []):
            out = unquote(r['field'])
            typ, _ = norm_type(r.get('type', ''))
            o = {'pill': PILL_OF_DOC_TYPE.get(typ, 'string'), 'doc': f'{TRIGGER_DOC}:{r["_line"]}'}
            if out in TRIGGER_OUTPUT_TABLES:
                o['table'] = TRIGGER_OUTPUT_TABLES[out]
            entry['outputs'][out] = o
        if fluent.startswith('record.'):
            entry['outputs']['current']['table_from'] = 'table'
        triggers[spec_name] = entry
    return triggers


def field_kind(typed: str) -> tuple[str, str]:
    ref = re.search(r'Record<"([a-z0-9_]+)">', typed)
    if ref and 'Record<"' + ref.group(1) + '">[]' in typed:
        return 'list', ref.group(1)
    if ref:
        return 'reference', ref.group(1)
    if typed.startswith('`${number}-${number}-${number} ${number}'):
        return 'datetime', ''
    if typed.startswith('`${number}-${number}-${number}`'):
        return 'date', ''
    if typed.startswith('boolean'):
        return 'boolean', ''
    if typed.startswith('number'):
        return 'integer', ''
    return 'string', ''


def extract_tables(tables_dir: Path) -> dict:
    index = {}
    for f in sorted(tables_dir.glob('*.now.d.ts')):
        s = f.read_text(encoding='utf-8')
        name = re.search(r'^    readonly name: "([a-z0-9_]+)";', s, re.M)
        if not name:
            continue
        ext = re.search(r'^    readonly extends: "([a-z0-9_]+)";', s, re.M)
        start = s.find('\n    readonly schema: {')
        fields = {}
        if start >= 0:
            for m in re.finditer(r'^        readonly ([a-z0-9_]+): import\("\.\./\.\./db"\)\.Typed<(.*)$', s[start:], re.M):
                kind, ref = field_kind(m.group(2))
                fields[m.group(1)] = {'kind': kind, **({'ref': ref} if ref else {})}
        index[name.group(1)] = {'extends': ext.group(1) if ext else '', 'fields': fields,
                                'doc': f'sdk-core/dist/fluent/tables/{f.name}'}
    return index


def extract_pill_types(dts: Path) -> list[str]:
    s = dts.read_text(encoding='utf-8')
    arr = re.search(r'ARRAY_FLOW_DATA_TYPES: readonly \[([^\]]*)\]', s).group(1)
    union = re.search(r'export type FlowDataType = ArrayFlowDataType \| (.*);', s).group(1)
    return sorted(set(re.findall(r'"([^"]+)"', arr) + re.findall(r"'([^']+)'", union)))


def patch_catalog_actions(actions: dict) -> None:
    """Where the action-api.md table disagrees with the typings and the official sample, the build agrees with those."""
    cct = actions['createCatalogTask']
    out = cct['outputs'].pop('Catalog Task')
    cct['outputs']['catalog_task'] = dict(out, table='sc_task', doc=CATALOG_NOTES['catalog_task_output'])
    actions['getCatalogVariables']['dynamic_outputs'] = CATALOG_NOTES['variable_outputs']
    for name, src in CATALOG_ONLY.items():
        actions[name]['catalog_only'] = src


def properties(path: Path, rel: str) -> dict:
    """`- **name** (required|optional): `type`` lines of a service-catalog API page, with their line numbers."""
    props = {}
    for i, line in enumerate(path.read_text(encoding='utf-8').splitlines()):
        m = re.match(r'^- \*\*(\w+)\*\* \((required|optional)\): `(.*)`', line)
        if m:
            props[m.group(1)] = {'required': m.group(2) == 'required', 'type': m.group(3), 'doc': f'{rel}:{i + 1}'}
    return props


def snake(builder: str) -> str:
    return re.sub(r'(?<!^)(?=[A-Z])', '_', builder[:-len('Variable')]).lower()


def extract_catalog(sdk: Path) -> dict:
    item = sdk / CAT_DOC / 'catalogitem-api.md'
    policy = sdk / CAT_DOC / 'cataloguipolicy-api.md'
    lines = policy.read_text(encoding='utf-8').splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith('## Action Properties'))
    actions = {}
    for i in range(start, len(lines)):
        if lines[i].startswith('| ') and not lines[i].startswith('| Property') and not lines[i].startswith('| ---'):
            c = cells(lines[i])
            actions[c[0]] = {'type': c[1].strip('`'), 'doc': f'{CAT_DOC}/cataloguipolicy-api.md:{i + 1}'}
        elif actions and not lines[i].startswith('|'):
            break
    variables, unsupported = {}, {}
    for f in sorted((sdk / CAT_DOC / 'variables').glob('*-api.md')):
        rel = f'{CAT_DOC}/variables/{f.name}'
        text = f.read_text(encoding='utf-8')
        builder = re.search(r'^# (\w+Variable)$', text, re.M).group(1)
        kind = snake(builder)
        if kind not in VARIABLE_PILL:
            unsupported[builder] = rel
            continue
        props = properties(f, rel)
        v = {'builder': builder, 'doc': rel, 'pill': VARIABLE_PILL[kind], 'props': sorted(props)}
        if kind in VARIABLE_EXTRA:
            prop, key = VARIABLE_EXTRA[kind]
            v['extra'] = {'prop': prop, 'spec_key': key, 'doc': props[prop]['doc']}
        if kind in VARIABLE_TABLE:
            v['table'] = VARIABLE_TABLE[kind]
        variables[kind] = v
    return {'item': {'fluent': 'CatalogItem', 'import': '@servicenow/sdk/core', 'doc': f'{CAT_DOC}/catalogitem-api.md:11',
                     'properties': properties(item, f'{CAT_DOC}/catalogitem-api.md')},
            'ui_policy': {'fluent': 'CatalogUiPolicy', 'import': '@servicenow/sdk/core', 'doc': f'{CAT_DOC}/cataloguipolicy-api.md:11',
                          'properties': properties(policy, f'{CAT_DOC}/cataloguipolicy-api.md'), 'actions': actions},
            'variables': variables, 'unsupported_variables': unsupported, 'notes': CATALOG_NOTES}


def render_md(v: dict) -> str:
    L = [f'# Fluent flow vocabulary (ServiceNow SDK {v["sdk"]})', '',
         'Generated by `scripts/dev/extract_vocabulary.py` from the SDK docs and type definitions. '
         'Every entry cites its source under `node_modules/@servicenow/sdk/` (docs) or `node_modules/@servicenow/sdk-core/` (types). '
         'The script checks your spec against `fluent-vocabulary.json`; this page is the readable copy.', '',
         '## Triggers (`trigger.type`)', '', '| type | Fluent | inputs | outputs (pills) | source |', '|---|---|---|---|---|']
    for k, t in v['triggers'].items():
        ins = ', '.join(f'{n}{"*" if i["required"] else ""}' for n, i in t['inputs'].items()) or '—'
        outs = ', '.join(t['outputs']) or '—'
        L.append(f'| `{k}` | `{t["fluent"]}` | {ins} | {outs} | {t["doc"]} |')
    L += ['', '`*` = required. Record triggers always get `run_flow_in: \'background\'` (wfa-flow-guide.md:97).', '',
          '## Actions (`"action": "<name>"`)', '', '| action | inputs | outputs (pills) | source |', '|---|---|---|---|']
    for k, a in v['actions'].items():
        if not a['documented']:
            L.append(f'| `{k}` | not documented in action-api.md — not usable | — | {a["doc"]} |')
            continue
        ins = ', '.join(f'{n}{"*" if i["required"] else ""} ({i["type"]}{": " + "/".join(i["values"]) if i["values"] else ""})'
                        for n, i in a['inputs'].items()) or '—'
        outs = ', '.join(f'{n} ({o["pill"]})' for n, o in a['outputs'].items()) or '—'
        L.append(f'| `{k}` | {ins} | {outs} | {a["doc"]} |')
    L += ['', '## Input rules', '']
    for rule, text in (('template_text', 'Text with pills allowed (write a JSON array of strings and pills)'),
                       ('wrap_pill', 'A pill here is wrapped as a template literal by the script (nothing to do)'),
                       ('no_pills', 'Static text only — no pills')):
        L.append(f'- **{text}:** ' + '; '.join(f'`{k}` ({src})' for k, src in v['input_rules'][rule].items()))
    L += ['- **Everything else:** a literal, one pill, or a placeholder — never text mixed with pills '
          '(wfa-flow-guide.md:125-142: template literals do not interpolate in other fields or inside TemplateValue).', '',
          '## Flow logic (`"logic": "<kind>"`)', '', '| logic | Fluent | spec shape | source |', '|---|---|---|---|']
    for k, g in v['logic'].items():
        L.append(f'| `{k}` | `{g["fluent"]}` | `{g["spec"]}` | {g["doc"]} |')
    L += ['', '## Not available — and what to write instead', '']
    for k, msg in v['unsupported'].items():
        L.append(f'- `{k}` — {msg}')
    c = v['catalog']
    L += ['', '## Catalog item, variables and questions (`form`, `ask`)', '',
          f'A flow that needs data from people is a catalog item: `{c["item"]["fluent"]}` ({c["item"]["doc"]}) with variables, '
          f'the trigger `service_catalog`, and a `{c["ui_policy"]["fluent"]}` ({c["ui_policy"]["doc"]}) that hides the '
          'variables asked later on the order form and shows them on catalog tasks. The script writes all three.', '',
          '| variable `type` | builder | needs | answer pill | source |', '|---|---|---|---|---|']
    for k, var in c['variables'].items():
        need = f'`{var["extra"]["spec_key"]}` ({var["extra"]["prop"]})' if 'extra' in var else '—'
        tail = f' → {var["table"]}' if 'table' in var else ''
        L.append(f'| `{k}` | `{var["builder"]}` | {need} | {var["pill"]}{tail} | {var["doc"]} |')
    L += ['', 'Not offered (layout, display-only, or needs instance records): '
          + ', '.join(f'`{b}`' for b in c['unsupported_variables']) + '.', '']
    L += [f'- **{k}:** {n}' for k, n in c['notes'].items()]
    L += ['', 'Catalog-only actions: ' + '; '.join(f'`{a}` ({s})' for a, s in CATALOG_ONLY.items()) + '.']
    L += ['', '## Tables', '',
          f'The SDK types {len(v["tables"])} platform tables. A pill or field must exist on its table (or a table it extends). '
          'Fields of the common tables:', '']
    for t in COMMON_TABLES:
        if t in v['tables']:
            e = v['tables'][t]
            ext = f' (extends {e["extends"]})' if e['extends'] else ''
            L.append(f'- **{t}**{ext}: ' + ', '.join(
                f + (f'→{d["ref"]}' if d.get('ref') else '') for f, d in sorted(e['fields'].items())))
    L += ['', 'All typed tables: ' + ', '.join(sorted(v['tables'])), '']
    return '\n'.join(L)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--sdk', required=True, help='path to node_modules/@servicenow')
    p.add_argument('--out', default=str(Path(__file__).resolve().parents[2] / 'references'))
    a = p.parse_args()
    root = Path(a.sdk)
    version = json.loads((root / 'sdk' / 'package.json').read_text())['version']
    if version != SDK_VERSION:
        print(f'error: expected @servicenow/sdk {SDK_VERSION}, found {version}', file=sys.stderr)
        return 1
    v = {'sdk': version,
         'triggers': extract_triggers(root / 'sdk' / TRIGGER_DOC),
         'actions': extract_actions(root / 'sdk' / ACTION_DOC),
         'logic': LOGIC, 'unsupported': UNSUPPORTED, 'input_rules': INPUT_RULES,
         'pill_types': extract_pill_types(root / 'sdk-core/dist/external/flow/flow-data-helpers.d.ts'),
         'tables': extract_tables(root / 'sdk-core/dist/fluent/tables'),
         'catalog': extract_catalog(root / 'sdk')}
    patch_catalog_actions(v['actions'])
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'fluent-vocabulary.json').write_text(json.dumps(v, indent=1, sort_keys=False) + '\n', encoding='utf-8')
    (out / 'fluent-vocabulary.md').write_text(render_md(v), encoding='utf-8')
    print(f'wrote {out}/fluent-vocabulary.json and .md: {len(v["triggers"])} triggers, '
          f'{sum(a["documented"] for a in v["actions"].values())}/{len(v["actions"])} documented actions, '
          f'{len(v["tables"])} tables, {len(v["catalog"]["variables"])} variable types')
    return 0


if __name__ == '__main__':
    sys.exit(main())
