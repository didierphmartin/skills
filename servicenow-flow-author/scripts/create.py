#!/usr/bin/env python3
"""
servicenow-flow-author / create.py — check a JSON flow spec, render it as a
ServiceNow SDK (Fluent) flow, and add it to the shared app project.

Usage (the argv of run_skill_script):
  create.py -i /scratch/<name>.json [--vendor <company code>] [--out /outputs/servicenow]

Exit 0  VALID: files written, report + placeholders + NEXT on stdout.
Exit 1  INVALID (every problem listed) or NEED_VENDOR — nothing written.
Exit 2  the spec file cannot be read as JSON.
Standard library only: it runs in the app's browser Pyodide.
"""
from __future__ import annotations
import argparse, datetime, json, re, sys, uuid, zipfile
from pathlib import Path

SDK_VERSION = '4.13.3'
VOCAB_CANDIDATES = [
    Path(__file__).resolve().parent.parent / 'references' / 'fluent-vocabulary.json',
    Path('/skill/servicenow-flow-author/references/fluent-vocabulary.json'),
]
NAME_RE = re.compile(r'^[a-z][a-z0-9-]{2,48}[a-z0-9]$')
ID_RE = re.compile(r'^(?=.{1,40}$)[a-z][a-z0-9]*(?:_[a-z0-9]+)*$')   # snake_case; "__" is kept for generated names
VAR_RE = re.compile(r'^(?=.{2,30}$)[a-z][a-z0-9]*(?:_[a-z0-9]+)*$')
PH_RE = re.compile(r'^[a-z][a-z0-9_]{1,40}$')
VENDOR_RE = re.compile(r'^[a-z0-9]{1,8}$')
PILL_PATH_RE = re.compile(r'^[a-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$')
RESERVED = {
    'params', 'wfa', 'action', 'trigger', 'Flow', 'TemplateValue', 'Duration', 'Time', 'Now',
    'await', 'break', 'case', 'catch', 'class', 'const', 'continue', 'debugger', 'default', 'delete', 'do',
    'else', 'enum', 'export', 'extends', 'false', 'finally', 'for', 'function', 'if', 'implements', 'import',
    'in', 'instanceof', 'interface', 'let', 'new', 'null', 'package', 'private', 'protected', 'public',
    'return', 'static', 'super', 'switch', 'this', 'throw', 'true', 'try', 'typeof', 'var', 'void', 'while',
    'with', 'yield', 'form',
}
LOGIC_KINDS = ('if', 'for_each', 'parallel', 'try_catch', 'do_until', 'wait_duration', 'end')
STEP_KEYS = {'id', 'label', 'action', 'inputs', 'ask', 'logic', 'condition', 'steps', 'else_if', 'else', 'items',
             'item', 'branches', 'try', 'catch', 'until', 'duration', 'relative_to', 'direction', 'schedule'}
DURATION_KEYS = ('days', 'hours', 'minutes', 'seconds')
PH_KINDS = ('group', 'user', 'notification', 'schedule', 'catalog', 'category', 'choice', 'email', 'text')
FORM_KEYS = {'submitted_by', 'catalog', 'category', 'variables'}
ASK_KEYS = {'short_description', 'assignment_group', 'assigned_to', 'variables'}
PILL_OF_KIND = {'string': 'string', 'reference': 'reference', 'list': 'array.string', 'date': 'glide_date',
                'datetime': 'glide_date_time', 'boolean': 'boolean', 'integer': 'integer'}
QUERY_SEP_RE = re.compile(r'\^(?:OR|NQ|AND)?')
QUERY_FIELD_RE = re.compile(r'^([a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*)(?=!=|<=|>=|=|<|>|ISEMPTY|ISNOTEMPTY|IN|NOT IN|STARTSWITH|ENDSWITH|LIKE|NOT LIKE)')


# ------------------------------------------------------------------ vocabulary
def load_vocabulary(explicit: str | None = None) -> dict:
    paths = [Path(explicit)] if explicit else VOCAB_CANDIDATES
    for p in paths:
        if p.is_file():
            return json.loads(p.read_text(encoding='utf-8'))
    raise FileNotFoundError('fluent-vocabulary.json not found in: ' + ', '.join(map(str, paths)))


# ------------------------------------------------------------------ validation → IR
# IR value nodes (tuples): ('str', s) ('num', n) ('bool', b) ('pill', ts_expr, pill_type, table|None|'*')
# ('tpl', [('text', s) | pill | ph]) ('ph', name, value|None) ('tv', [(column, node)]) ('dur', [(unit, n)])
# ('approval', rule, users, groups) ('time', h, m, s, tz).  Steps are dicts with a 'kind'.
class Ctx:
    """Validation state. Every check appends to .errors; the IR is built as it goes."""

    def __init__(self, spec: dict, vocab: dict):
        self.spec, self.vocab = spec, vocab
        self.tables = vocab['tables']
        self.errors: list[str] = []
        self.notes: list[str] = []
        self.variables: dict[str, dict] = {}      # catalog item variables by name (order form + ask steps)
        self.catalog = False                      # trigger service_catalog: the flow runs on a requested item
        self.in_for_each = 0
        self.placeholders: dict[str, dict] = {}
        self.used_placeholders: set[str] = set()
        self.all_ids: set[str] = set()
        self.frames: list[dict] = [{}]
        self.referenced: set[str] = set()
        self.trigger: dict = {}
        self.trigger_table: str | None = None
        self.uses_params = False
        self.uses_action = False
        self.in_parallel = 0
        self.key_prefix = ''

    def err(self, where: str, msg: str) -> None:
        self.errors.append(f'{where}: {msg}')

    def visible(self, alias: str) -> dict | None:
        for frame in reversed(self.frames):
            if alias in frame:
                return frame[alias]
        return None

    def table_chain(self, table: str) -> list[str]:
        chain: list[str] = []
        while table and table in self.tables and table not in chain:
            chain.append(table)
            table = self.tables[table].get('extends') or ''
        return chain

    def field_info(self, table: str, name: str) -> tuple[str, str | None] | None:
        """(kind, referenced table) of a field on table or an ancestor, else None."""
        if name == 'sys_id':
            return 'reference', table
        for t in self.table_chain(table):
            f = self.tables[t]['fields'].get(name)
            if f is not None:
                return f['kind'], f.get('ref') or None
        return None


def known_table(ctx: Ctx, where: str, table, what: str) -> bool:
    """Report (and return False) unless table is in the SDK table index."""
    if not isinstance(table, str) or table not in ctx.tables:
        close = [t for t in ctx.tables if isinstance(table, str) and (table in t or t in table)][:5]
        ctx.err(where, f'{what} "{table}" is not in the SDK {SDK_VERSION} table index ({len(ctx.tables)} tables)'
                       + (f' — did you mean {", ".join(close)}?' if close else '.')
                       + ' Use a table listed in fluent-vocabulary.md → Tables.')
        return False
    return True


def walk_fields(ctx: Ctx, expr: list[str], ptype: str, table: str | None, segs: list[str], where: str, path: str):
    """Follow dot-walked segs from a pill through the table index → (expr, type, table) or None."""
    for seg in segs:
        if table == '*':   # a record of a table the SDK does not type: fields unchecked
            expr, ptype = expr + [seg], 'string'
            continue
        if not table:
            ctx.err(where, f'pill "{path}": "{".".join(expr)}" is not a record, so it has no field "{seg}"')
            return None
        info = ctx.field_info(table, seg)
        if info is None:
            ctx.err(where, f'pill "{path}": table {table} has no field "{seg}" (SDK {SDK_VERSION} table index) '
                           f'— use a field it has; a value a person supplies is a variable of the "form" or of an ask step')
            return None
        kind, ref = info
        expr = expr + [seg]
        ptype = PILL_OF_KIND[kind]
        table = ref if kind == 'reference' else None
    return '.'.join(expr), ptype, table


def resolve_pill(ctx: Ctx, path, where: str):
    """(ts expression, pill type, record table or None) — or None after recording an error."""
    if not isinstance(path, str) or not PILL_PATH_RE.match(path):
        ctx.err(where, f'pill {json.dumps(path)} must be a dotted path such as "trigger.current.manager" or "step_id.output"')
        return None
    head, *rest = path.split('.')
    match head:
        case 'trigger':
            ctx.uses_params = True
            outs = ctx.trigger.get('outputs', {})
            if not rest or rest[0] not in outs:
                ctx.err(where, f'pill "{path}": the trigger outputs are {", ".join(outs) or "none"}')
                return None
            out = outs[rest[0]]
            if out.get('table_from') and ctx.trigger_table is None:   # trigger table already reported
                return '.'.join(['params', 'trigger'] + rest), '?', None
            table = ctx.trigger_table if out.get('table_from') else out.get('table')
            return walk_fields(ctx, ['params', 'trigger', rest[0]], out['pill'], table, rest[1:], where, path)
        case 'form':
            return form_pill(ctx, rest, where, path)
    b = ctx.visible(head)
    if b is None:
        if head in ctx.all_ids:
            ctx.err(where, f'pill "{path}": step "{head}" is not available here — it comes later, or it sits inside '
                           f'an if/for_each/parallel/try_catch/do_until block whose outputs stay inside it '
                           f'(wfa-flow-logic-api.md:260)')
        else:
            ctx.err(where, f'pill "{path}": no step or loop item is called "{head}"')
        return None
    if b['kind'] == 'item':
        ctx.referenced.add(head)
        if b['table'] is None:
            if rest:
                ctx.err(where, f'pill "{path}": loop item "{head}" is a plain value; it has no field "{rest[0]}"')
                return None
            return head, 'string', None
        return walk_fields(ctx, [head], 'reference', b['table'], rest, where, path)
    if not rest or rest[0] not in b['outputs']:
        more = ''
        if b['kind'] == 'ask' and rest and rest[0] in ctx.variables:
            v = ctx.variables[rest[0]]
            more = (f' — "{rest[0]}" is on the order form: {{"pill": "form.{rest[0]}"}}' if v['owner'] == 'form' else
                    f' — "{rest[0]}" is asked by step "{v["owner"]}": {{"pill": "{v["owner"]}.{rest[0]}"}}')
        ctx.err(where, f'pill "{path}": step "{head}" ({b["action"]}) has outputs: {", ".join(b["outputs"]) or "none"}{more}')
        return None
    out = rest[0]
    const = b.get('const', {}).get(out, head)
    ctx.referenced.add(const)
    return walk_fields(ctx, [const, out], b['outputs'][out]['pill'], b['tables'].get(out), rest[1:], where, path)


def form_pill(ctx: Ctx, rest: list[str], where: str, path: str):
    """form.<variable>[.field…] — an answer on the order form, read once at the start of the flow."""
    names = [n for n, v in ctx.variables.items() if v['owner'] == 'form']
    if not ctx.catalog or not names:
        ctx.err(where, f'pill "{path}": the flow has no order form — declare "form" and the trigger service_catalog')
        return None
    v = ctx.variables.get(rest[0]) if rest else None
    if v is None:
        ctx.err(where, f'pill "{path}": the order form has no variable "{rest[0] if rest else ""}" — its variables are {", ".join(names)}')
        return None
    if v['owner'] != 'form':
        ctx.err(where, f'pill "{path}": "{rest[0]}" is asked by step "{v["owner"]}", not on the order form — '
                       f'read it as {{"pill": "{v["owner"]}.{rest[0]}"}} after that step')
        return None
    ctx.referenced.add('form')
    return walk_fields(ctx, ['form', rest[0]], v['pill'], v['table'], rest[1:], where, path)


def pill_node(ctx: Ctx, value: dict, where: str):
    """A {"pill": …} value → ('pill', ts_expr, pill_type, table)."""
    r = resolve_pill(ctx, value.get('pill'), where)
    if r is None:
        return None
    expr, ptype, table = r
    if 'type' in value:
        if value['type'] not in ctx.vocab['pill_types']:
            ctx.err(where, f'pill type "{value["type"]}" is not a FlowDataType (flow-data-helpers.d.ts:13)')
            return None
        ptype = value['type']
    return ('pill', expr, ptype, table)


def placeholder_node(ctx: Ctx, value: dict, where: str):
    name = value.get('placeholder')
    if name not in ctx.placeholders:
        ctx.err(where, f'placeholder "{name}" is not declared under "placeholders"')
        return None
    ctx.used_placeholders.add(name)
    return ('ph', name, ctx.placeholders[name].get('value'))


REF_KIND_HINT = {'assignment_group': 'group', 'groups': 'group', 'users': 'user', 'assigned_to': 'user',
                 'notification': 'notification', 'schedule': 'schedule'}


def record_ref_node(ctx: Ctx, value, where: str, field: str):
    """An instance record (group, user, notification, schedule, any reference): a pill or a declared placeholder only —
    a plain string would be an invented name or sys_id (the placeholder's "value" carries a known one)."""
    if isinstance(value, dict) and ('pill' in value or 'placeholder' in value):
        return scalar_node(ctx, value, where, False)
    kind = REF_KIND_HINT.get(field, 'a matching kind')
    ctx.err(where, f'"{field}" is a record that exists on the instance, so it is a pill or a placeholder, never a typed name or sys_id — '
                   f'declare {{"placeholder": "<name>"}} under "placeholders" (kind {kind}; put a known sys_id in its "value")')
    return None


def scalar_node(ctx: Ctx, value, where: str, allow_text: bool, allow_pill: bool = True):
    """A literal, {"pill"}, {"placeholder"}, or (allow_text) a list mixing text, pills and placeholders."""
    match value:
        case bool():
            return ('bool', value)
        case int() | float():
            return ('num', value)
        case str():
            return ('str', value)
        case {'pill': _} if not allow_pill:
            ctx.err(where, 'takes static text only — no pills here')
            return None
        case {'pill': _}:
            return pill_node(ctx, value, where)
        case {'placeholder': _}:
            return placeholder_node(ctx, value, where)
        case list() if not allow_pill:
            ctx.err(where, 'takes static text only — no pills here')
            return None
        case list() if not allow_text:
            ctx.err(where, 'text mixed with pills is not interpolated in this input (wfa-flow-guide.md:125-142) — '
                           'give one pill, one placeholder, or static text')
            return None
        case list():
            parts = []
            for k, part in enumerate(value):
                match part:
                    case str():
                        n = ('text', part)
                    case {'pill': _}:
                        n = pill_node(ctx, part, where)
                    case {'placeholder': _}:
                        n = placeholder_node(ctx, part, where)
                    case _:
                        ctx.err(where, f'part {k} must be a string, a {{"pill"}} or a {{"placeholder"}}')
                        return None
                if n is None:
                    return None
                parts.append(n)
            return ('tpl', parts)
    ctx.err(where, f'unsupported value {json.dumps(value)[:60]}')
    return None


def check_query(ctx: Ctx, node, table: str | None, where: str):
    """Check the field names at the start of each encoded-query term (string parts only)."""
    if node is None or table is None:
        return node

    def fix(text: str, at_term_start: bool) -> str:
        pieces = QUERY_SEP_RE.split(text)
        seps = QUERY_SEP_RE.findall(text)
        out = []
        for k, piece in enumerate(pieces):
            if k > 0 or at_term_start:
                m = QUERY_FIELD_RE.match(piece)
                if m:
                    first = m.group(1).split('.')[0]
                    if ctx.field_info(table, first) is None:
                        ctx.err(where, f'condition field "{first}" is not on table {table} (SDK {SDK_VERSION} table index) '
                                       f'— use a field it has')
            out.append(piece)
            if k < len(seps):
                out.append(seps[k])
        return ''.join(out)

    match node[0]:
        case 'str':
            return ('str', fix(node[1], True))
        case 'tpl':
            parts, start = [], True
            for p in node[1]:
                parts.append(('text', fix(p[1], start)) if p[0] == 'text' else p)
                start = False
            return ('tpl', parts)
    return node


def duration_node(ctx: Ctx, value, where: str):
    d = value.get('duration') if isinstance(value, dict) and 'duration' in value else value
    if not isinstance(d, dict) or not d or set(d) - set(DURATION_KEYS) or not all(
            isinstance(v, int) and v >= 0 for v in d.values()) or not any(d.values()):
        ctx.err(where, 'a duration is {"days": n, "hours": n, "minutes": n, "seconds": n} with at least one positive whole number (trigger-api.md:268)')
        return None
    return ('dur', [(k, d[k]) for k in DURATION_KEYS if d.get(k)])


def approval_node(ctx: Ctx, value, where: str):
    if not isinstance(value, dict) or value.get('rule') not in ('Any', 'All') or set(value) - {'rule', 'users', 'groups'}:
        ctx.err(where, 'approval_conditions is {"rule": "Any"|"All", "users": [pills/placeholders], "groups": [pills/placeholders]} (action-api.md:516)')
        return None
    users = [record_ref_node(ctx, u, where + '.users', 'users') for u in value.get('users', [])]
    groups = [record_ref_node(ctx, g, where + '.groups', 'groups') for g in value.get('groups', [])]
    if None in users or None in groups:
        return None
    if not users and not groups:
        ctx.err(where, 'an approval needs at least one approver in "users" or "groups"')
        return None
    return ('approval', value['rule'], users, groups)


def template_value_node(ctx: Ctx, value, table: str | None, where: str):
    if not isinstance(value, dict) or not value:
        ctx.err(where, 'must be an object {"field": value, …} (a TemplateValue, action-api.md:82)')
        return None
    items = []
    for field, v in value.items():
        if table and ctx.field_info(table, field) is None:
            ctx.err(where, f'field "{field}" is not on table {table} (SDK {SDK_VERSION} table index) — use a field it has')
            continue
        info = ctx.field_info(table, field) if table else None
        if field in REF_KIND_HINT or (info and info[0] == 'reference'):
            n = record_ref_node(ctx, v, f'{where}.{field}', field)
        else:
            n = scalar_node(ctx, v, f'{where}.{field}', False)
        if n is not None:
            items.append((field, n))
    return ('tv', items)


def input_node(ctx: Ctx, action: str, spec_in: dict, name: str, value, step_inputs: dict, where: str):
    """Validate one action input against its vocabulary type and rules → a value node or None."""
    key = f'{action}.{name}'
    rules = ctx.vocab['input_rules']
    typ = spec_in['type']
    if action == 'askForApproval' and name == 'approval_conditions':
        return approval_node(ctx, value, where)
    match typ:
        case 'TemplateValue':
            tin = ctx.vocab['actions'][action].get('template_table_input')
            table = step_inputs.get(tin) if tin else ('sc_task' if action == 'createCatalogTask' else None)
            return template_value_node(ctx, value, table if table in ctx.tables else None, where)
        case 'Duration':
            return duration_node(ctx, value, where)
        case 'object' | 'slushbucket' | 'duration' | 'datetime':
            ctx.err(where, f'input "{name}" ({typ}) is not supported by this skill — leave it out')
            return None
        case 'enum' if value not in spec_in['values']:
            ctx.err(where, f'must be one of {", ".join(spec_in["values"])}')
            return None
        case 'enum':
            return ('str', value)
        case 'boolean' if not isinstance(value, (bool, dict)):
            ctx.err(where, 'must be true or false')
            return None
        case 'integer' if isinstance(value, bool) or not isinstance(value, (int, dict)):
            ctx.err(where, 'must be a whole number')
            return None
        case 'reference':
            node = record_ref_node(ctx, value, where, name)
        case _:
            node = scalar_node(ctx, value, where, allow_text=key in rules['template_text'], allow_pill=key not in rules['no_pills'])
    if node is None:
        return None
    if node[0] == 'pill' and key in rules['wrap_pill']:
        node = ('tpl', [node])
    if name == 'log_message' and node[0] == 'str' and len(node[1]) > 255:
        ctx.err(where, 'log_message is at most 255 characters (action-api.md:368)')
    if name == 'approval_reason' and node[0] == 'str' and len(node[1]) > 160:
        ctx.err(where, 'approval_reason is at most 160 characters (action-api.md:492)')
    if name == 'conditions':
        table = step_inputs.get('table') or step_inputs.get('table_name')
        node = check_query(ctx, node, table if table in ctx.tables else None, where)
    return node


def check_link(ctx: Ctx, step: dict, where: str) -> None:
    """A task created by a record-triggered flow must point back at the trigger record."""
    if ctx.trigger_table is None:
        return
    inputs = step.get('inputs') or {}
    task_table = inputs.get('task_table')
    fv = inputs.get('field_values') or {}
    targets = set(ctx.table_chain(ctx.trigger_table))
    if not isinstance(fv, dict):
        return
    if not isinstance(task_table, str) or task_table not in ctx.tables:   # untyped task table: any link pill will do
        if any(isinstance(v, dict) and v.get('pill') in ('trigger.current', 'trigger.current.sys_id') for v in fv.values()):
            return
        task_table = 'task'
    for field, v in fv.items():
        info = ctx.field_info(task_table, field)
        if info and info[0] in ('reference', 'list') and info[1] in targets and isinstance(v, dict) \
                and v.get('pill') in ('trigger.current', 'trigger.current.sys_id'):
            return
    cands = sorted({f for t in ctx.table_chain(task_table) for f, d in ctx.tables[t]['fields'].items()
                    if d['kind'] == 'reference' and d.get('ref') in targets})
    hint = (f'set one of {", ".join(cands)} to {{"pill": "trigger.current.sys_id"}}' if cands else
            f'{task_table} has no reference to {ctx.trigger_table}; make the procedure a catalog item ("form", trigger '
            f'service_catalog) so its tasks are catalog tasks of the request')
    ctx.err(where, f'the task created on {task_table} is not linked to the {ctx.trigger_table} record that started the flow — {hint}')


def step_key(ctx: Ctx, sid: str) -> str:
    return f'{ctx.key_prefix}__{sid}'


def check_id(ctx: Ctx, obj: dict, where: str) -> str | None:
    sid = obj.get('id')
    if not isinstance(sid, str) or not ID_RE.match(sid) or sid in RESERVED:
        ctx.err(where, f'id {json.dumps(sid)} must be snake_case (≤ 40 chars, no "__") and not a reserved word')
        return None
    if not isinstance(obj.get('label'), str) or not obj['label'].strip() or len(obj['label']) > 200:
        ctx.err(where, 'needs a "label" (1–200 characters, in the procedure\'s language)')
    return sid


def body(ctx: Ctx, steps, where: str, frame: dict | None = None) -> list:
    if not isinstance(steps, list) or not steps:
        ctx.err(where, 'needs a non-empty "steps" list')
        return []
    ctx.frames.append(frame or {})
    out = [s for s in (step_ir(ctx, st, f'{where}[{k}]') for k, st in enumerate(steps)) if s is not None]
    ctx.frames.pop()
    return out


def step_ir(ctx: Ctx, st, where: str):
    if not isinstance(st, dict):
        ctx.err(where, 'a step must be an object')
        return None
    sid = check_id(ctx, st, where)
    if sid:
        where = f'{where} ({sid})'
    extra = set(st) - STEP_KEYS
    for k in sorted(extra):
        msg = ctx.vocab['unsupported'].get(k)
        ctx.err(where, f'"{k}" is not part of a step' + (f' — {msg}' if msg else ''))
    if 'ask' in st:
        if 'action' in st or 'logic' in st:
            ctx.err(where, 'a step is an action, an ask or a logic block — not two of them')
        return ask_ir(ctx, st, sid, where)
    if 'action' in st:
        return action_ir(ctx, st, sid, where)
    kind = st.get('logic')
    if kind not in LOGIC_KINDS:
        msg = ctx.vocab['unsupported'].get(kind) if isinstance(kind, str) else None
        ctx.err(where, f'logic "{kind}" is not available' + (f' — {msg}' if msg else f'; use one of {", ".join(LOGIC_KINDS)}'))
        return None
    ir = {'kind': kind, 'id': sid, 'key': step_key(ctx, sid or 'x'), 'label': st.get('label', '')}
    match kind:
        case 'if':
            ir['cond'] = scalar_node(ctx, st.get('condition'), where + '.condition', True)
            ir['body'] = body(ctx, st.get('steps'), where + '.steps')
            ir['elifs'] = []
            for k, e in enumerate(st.get('else_if') or []):
                w = f'{where}.else_if[{k}]'
                eid = check_id(ctx, e, w) if isinstance(e, dict) else None
                if eid:
                    ir['elifs'].append({'id': eid, 'key': step_key(ctx, eid), 'label': e.get('label', ''),
                                        'cond': scalar_node(ctx, e.get('condition'), w + '.condition', True),
                                        'body': body(ctx, e.get('steps'), w + '.steps')})
            if st.get('else') is not None:
                e, w = st['else'], where + '.else'
                eid = check_id(ctx, e, w) if isinstance(e, dict) else None
                ir['else'] = eid and {'id': eid, 'key': step_key(ctx, eid), 'label': e.get('label', ''),
                                      'body': body(ctx, e.get('steps'), w + '.steps')}
            else:
                ir['else'] = None
        case 'for_each':
            items, node = st.get('items'), None
            if isinstance(items, dict) and 'pill' in items:
                node = pill_node(ctx, items, where + '.items')
            else:
                ctx.err(where, '"items" must be a pill to a list: a lookUpRecords "Records" output or a list field (wfa-flow-logic-api.md:132)')
            alias = st.get('item')
            if not isinstance(alias, str) or not ID_RE.match(alias) or alias in RESERVED:
                ctx.err(where, '"item" names the loop item, e.g. "open_task" ([a-z][a-z0-9_]*)')
                alias = 'item'
            item_table = None
            if node is not None:
                match node[2]:
                    case 'records':
                        item_table = node[3]
                    case 'array.string' | '?':
                        pass
                    case other:
                        ctx.err(where, f'"items" is a {other}, not a list — use a lookUpRecords "Records" output or a list field')
            ctx.in_for_each += 1
            ir.update(items=node, alias=alias,
                      body=body(ctx, st.get('steps'), where + '.steps', {alias: {'kind': 'item', 'table': item_table}}))
            ctx.in_for_each -= 1
        case 'parallel':
            if ctx.in_parallel:
                ctx.err(where, 'a parallel block cannot contain another parallel block (wfa-flow-logic-api.md:266)')
            br = st.get('branches')
            if not isinstance(br, list) or len(br) < 2:
                ctx.err(where, '"branches" must list at least two step lists')
                br = []
            ctx.in_parallel += 1
            ir['branches'] = [body(ctx, b, f'{where}.branches[{k}]') for k, b in enumerate(br)]
            ctx.in_parallel -= 1
        case 'try_catch':
            ir['try'] = body(ctx, st.get('try'), where + '.try')
            ir['catch'] = body(ctx, st.get('catch'), where + '.catch')
        case 'do_until':
            frame: dict = {}
            ctx.frames.append(frame)
            steps = st.get('steps')
            if not isinstance(steps, list) or not steps:
                ctx.err(where, 'needs a non-empty "steps" list')
                steps = []
            ir['body'] = [s for s in (step_ir(ctx, x, f'{where}.steps[{k}]') for k, x in enumerate(steps)) if s is not None]
            ir['until'] = scalar_node(ctx, st.get('until'), where + '.until', True)   # may read the body (wfa-flow-logic-api.md:409)
            ctx.frames.pop()
        case 'wait_duration':
            ir['duration'] = duration_node(ctx, st.get('duration'), where + '.duration')
            ir['schedule'] = record_ref_node(ctx, st['schedule'], where + '.schedule', 'schedule') if 'schedule' in st else None
            if 'relative_to' in st:
                if st.get('direction') not in ('before', 'after'):
                    ctx.err(where, '"direction" must be "before" or "after" when "relative_to" is given (wfa-flow-logic-api.md:491)')
                rel, n = st['relative_to'], None
                if isinstance(rel, dict) and 'pill' in rel:
                    n = pill_node(ctx, rel, where + '.relative_to')
                else:
                    ctx.err(where, '"relative_to" must be a pill to a date or date/time')
                if n is not None and n[2] not in ('glide_date', 'glide_date_time', 'datetime', 'date', 'due_date', '?'):
                    ctx.err(where, f'"relative_to" must be a date or date/time pill, not {n[2]}')
                ir.update(mode='relative', relative=n and ('tpl', [n]), direction=st.get('direction'))
            else:
                ir['mode'] = 'explicit'
    if sid:
        ctx.frames[-1][sid] = {'kind': 'logic', 'action': kind, 'outputs': {}, 'tables': {}}
    return ir


def action_ir(ctx: Ctx, st: dict, sid: str | None, where: str):
    """Validate an action step (name, inputs, pills) → the step IR."""
    name = st.get('action')
    actions = ctx.vocab['actions']
    if name not in actions or not actions[name]['documented']:
        msg = ctx.vocab['unsupported'].get(name) if isinstance(name, str) else None
        usable = sorted(k for k, a in actions.items() if a['documented'])
        ctx.err(where, f'action "{name}" is not a documented core action' + (f' — {msg}' if msg else f'; use one of: {", ".join(usable)}'))
        return None
    a = actions[name]
    if a.get('dynamic_outputs'):
        ctx.err(where, f'{name} is written by the script: read the order form with {{"pill": "form.<variable>"}} '
                       f'and a person\'s answers with an ask step')
        return None
    ctx.uses_action = True
    inputs = st.get('inputs') or {}
    if not isinstance(inputs, dict):
        ctx.err(where, '"inputs" must be an object')
        inputs = {}
    if a.get('catalog_only') and not ctx.catalog:
        ctx.err(where, f'{name} works only in a flow started from a catalog item ({a["catalog_only"]}) — '
                       f'give the procedure a "form" and the trigger service_catalog')
    if name == 'createTask' and ctx.catalog:
        ctx.err(where, 'in a catalog flow the procedure\'s tasks are catalog tasks of the request: use createCatalogTask '
                       'with "ah_requested_item": {"pill": "trigger.request_item"} (wfa-flow-actions-guide.md:948)')
    if name == 'createCatalogTask':
        inputs = dict(inputs)
        for k in ('catalog_variables', 'template_catalog_item', 'ah_table_name'):
            if inputs.pop(k, None) is not None:
                ctx.err(where, f'"{k}" is set by the script — to ask a person for data, use an ask step')
        if not isinstance(inputs.get('ah_wait'), bool):
            ctx.err(where, 'createCatalogTask needs "ah_wait": false for work the flow does not wait for, or true to wait '
                           'until the task is closed (wfa-flow-actions-guide.md:1092) — to ask a person, use an ask step')
        if ctx.catalog and inputs.get('ah_requested_item') != {'pill': 'trigger.request_item'}:
            ctx.err(where, 'a catalog task belongs to the request item that started the flow: '
                           '"ah_requested_item": {"pill": "trigger.request_item"}')
    for req in (k for k, i in a['inputs'].items() if i['required']):
        if req not in inputs:
            ctx.err(where, f'{name} needs input "{req}" ({a["inputs"][req]["doc"]})')
    for k in inputs:
        if k not in a['inputs']:
            ctx.err(where, f'{name} has no input "{k}" — its inputs are {", ".join(a["inputs"])} ({a["doc"]})')
    for k, v in inputs.items():
        if k in ('table', 'table_name', 'task_table') and isinstance(v, str) and v not in ctx.tables:
            ctx.notes.append(f'{where}: table "{v}" is not typed by SDK {SDK_VERSION} — its field names are not checked here; '
                             f'the build still checks the flow')
    nodes = []
    for k in a['inputs']:   # vocabulary order → stable output
        if k in inputs:
            n = input_node(ctx, name, a['inputs'][k], k, inputs[k], inputs, f'{where}.inputs.{k}')
            if n is not None:
                nodes.append((k, n))
    if name == 'createTask':
        check_link(ctx, st, where)
    tables = {out: (inputs.get(o['table_from']) if inputs.get(o['table_from']) in ctx.tables else '*')
              for out, o in a['outputs'].items() if o.get('table_from')}
    tables.update({out: o['table'] for out, o in a['outputs'].items() if o.get('table')})
    if sid:
        ctx.frames[-1][sid] = {'kind': 'step', 'action': name, 'outputs': a['outputs'], 'tables': tables}
    return {'kind': 'action', 'id': sid, 'key': step_key(ctx, sid or 'x'), 'label': st.get('label', ''),
            'fluent': a['fluent'], 'inputs': nodes}


def ask_ir(ctx: Ctx, st: dict, sid: str | None, where: str):
    """A question to a person mid-run: a blocking catalog task showing the step's variables, then their answers
    (forms research §3a: createCatalogTask + catalog_variables + ah_wait, then getCatalogVariables)."""
    a = st.get('ask')
    if not ctx.catalog:
        ctx.err(where, 'an ask step is a catalog task of the request: the flow needs a "form" and the trigger service_catalog')
        return None
    if ctx.in_for_each:
        ctx.err(where, 'an ask step cannot be inside for_each — its answers are variables of the request item, one value each')
    if not isinstance(a, dict):
        ctx.err(where, '"ask" is {"short_description", "assignment_group" and/or "assigned_to", "variables": […]}')
        return None
    for k in sorted(set(a) - ASK_KEYS):
        ctx.err(where, f'"ask" has no key "{k}" — its keys are {", ".join(sorted(ASK_KEYS))}')
    short = a.get('short_description')
    if not isinstance(short, str) or not short.strip():
        ctx.err(where, '"ask.short_description" is the task title the person sees (text, in the user\'s language)')
        short = ''
    fields = []
    for k in ('assignment_group', 'assigned_to'):
        if k in a:
            n = record_ref_node(ctx, a[k], f'{where}.ask.{k}', k)
            if n is not None:
                fields.append((k, n))
    if not ('assignment_group' in a or 'assigned_to' in a):
        ctx.err(where, 'an ask step needs "assignment_group" or "assigned_to" (a placeholder or a pill): who answers')
    names = [n for n, v in ctx.variables.items() if v['owner'] == sid]
    ctx.uses_params = ctx.uses_action = True
    if sid:
        outputs = {n: {'pill': ctx.variables[n]['pill']} for n in names}
        outputs['catalog_task'] = {'pill': 'reference'}
        tables = {n: ctx.variables[n]['table'] for n in names}
        tables['catalog_task'] = 'sc_task'
        ctx.frames[-1][sid] = {'kind': 'ask', 'action': 'ask', 'outputs': outputs, 'tables': tables,
                               'const': {'catalog_task': f'{sid}__task'}}
    return {'kind': 'ask', 'id': sid, 'key': step_key(ctx, sid or 'x'), 'label': st.get('label', ''),
            'short': ('str', short), 'fields': ('tv', fields), 'vars': names}


def iter_steps(steps):
    """Every step object, nested ones included, in document order."""
    for st in steps if isinstance(steps, list) else []:
        if not isinstance(st, dict):
            continue
        yield st
        for k in ('steps', 'try', 'catch'):
            yield from iter_steps(st.get(k))
        for e in list(st.get('else_if') or []) + ([st['else']] if isinstance(st.get('else'), dict) else []):
            if isinstance(e, dict):
                yield from iter_steps(e.get('steps'))
        for b in st.get('branches') or []:
            yield from iter_steps(b)


def add_variable(ctx: Ctx, v, owner: str, where: str, order: int) -> None:
    types = ctx.vocab['catalog']['variables']
    if not isinstance(v, dict):
        ctx.err(where, 'a variable is {"name", "label", "type", "mandatory"?, "help"?}')
        return
    name, typ = v.get('name'), v.get('type')
    if not isinstance(name, str) or not VAR_RE.match(name) or name in ('catalog_task', 'sys_id'):
        ctx.err(where, f'variable name {json.dumps(name)} must be snake_case, 2–30 characters (the catalog item\'s variable name)')
        return
    if name in ctx.variables:
        ctx.err(where, f'variable "{name}" is already declared at {ctx.variables[name]["where"]} — '
                       f'names are unique on the catalog item')
        return
    if typ not in types:
        ctx.err(where, f'variable type {json.dumps(typ)} is not offered; use one of {", ".join(types)} '
                       f'(fluent-vocabulary.md → Catalog)')
        return
    info = types[typ]
    extra_key = info.get('extra', {}).get('spec_key')
    for k in sorted(set(v) - {'name', 'label', 'type', 'mandatory', 'help'} - ({extra_key} if extra_key else set())):
        ctx.err(where, f'a {typ} variable has no key "{k}"')
    if not isinstance(v.get('label'), str) or not v['label'].strip() or len(v['label']) > 200:
        ctx.err(where, 'needs a "label": the question shown on the form (1–200 characters, the user\'s language)')
    if 'mandatory' in v and not isinstance(v['mandatory'], bool):
        ctx.err(where, '"mandatory" is true or false')
    if v.get('mandatory') and 'mandatory' not in info['props']:
        ctx.err(where, f'a {typ} variable cannot be mandatory ({info["doc"]} has no "mandatory") — use yes_no for a required answer')
    if 'help' in v and not isinstance(v['help'], str):
        ctx.err(where, '"help" is text shown under the question')
    table, extra = info.get('table'), None
    match extra_key:
        case 'reference':
            if known_table(ctx, where + '.reference', v.get('reference'), 'referenced table'):
                extra = (info['extra']['prop'], ('str', v['reference']))
                table = v['reference'] if typ == 'reference' else None   # a list collector answer is a string of sys_ids
        case 'choices':
            ch = v.get('choices')
            if not isinstance(ch, dict) or not ch or not all(isinstance(k, str) and VAR_RE.match(k) and isinstance(t, str) and t.strip()
                                                             for k, t in ch.items()):
                ctx.err(where, f'a {typ} variable needs "choices": {{"<value>": "<label>", …}} (snake_case values)')
            else:
                extra = ('choices', ('choices', list(ch.items())))
    ctx.variables[name] = {'name': name, 'label': v.get('label', ''), 'type': typ, 'builder': info['builder'],
                           'pill': info['pill'], 'table': table, 'extra': extra, 'mandatory': bool(v.get('mandatory')),
                           'help': v.get('help') or '', 'owner': owner, 'where': where, 'order': order}


def form_ir(ctx: Ctx, spec: dict) -> dict | None:
    """The catalog item: order-form variables here, ask variables from the ask steps (declared where they are asked)."""
    form = spec.get('form')
    if ctx.catalog and form is None:
        ctx.err('form', 'a service_catalog flow starts when someone orders its catalog item: describe the order form '
                        'under "form" (references/flow-spec.md → Form)')
    if form is not None and not ctx.catalog:
        ctx.err('form', 'a "form" makes the procedure a catalog item: set "trigger": {"type": "service_catalog"} and '
                        'write the procedure\'s start condition in form.submitted_by')
    if not isinstance(form, dict) or not ctx.catalog:
        return None
    for k in sorted(set(form) - FORM_KEYS):
        ctx.err('form', f'unknown key "{k}" — the keys are {", ".join(sorted(FORM_KEYS))}')
    if not isinstance(form.get('submitted_by'), str) or not form['submitted_by'].strip():
        ctx.err('form.submitted_by', 'who submits this form and when — the procedure\'s start condition, in its language '
                                     '(it becomes the catalog item\'s description)')
    refs = {}
    for k in ('catalog', 'category'):
        val = form.get(k)
        if isinstance(val, dict) and 'placeholder' in val:
            n = placeholder_node(ctx, val, f'form.{k}')
            if n and ctx.placeholders[n[1]]['kind'] != k:
                ctx.err(f'form.{k}', f'placeholder "{n[1]}" must be of kind "{k}"')
            refs[k] = n
        else:
            ctx.err(f'form.{k}', f'the {k} the item is listed in: {{"placeholder": "<name>"}} of kind "{k}" '
                                 f'(catalogitem-api.md:280 — every item needs a catalog and a category)')
    variables = form.get('variables')
    if not isinstance(variables, list) or not variables:
        ctx.err('form.variables', 'the order form needs at least one variable')
        variables = []
    order = 0
    for k, v in enumerate(variables):
        order += 100
        add_variable(ctx, v, 'form', f'form.variables[{k}]', order)
    for st in iter_steps(spec.get('steps')):
        if isinstance(st.get('ask'), dict) and isinstance(st.get('id'), str):
            asked = st['ask'].get('variables')
            if not isinstance(asked, list) or not asked:
                ctx.err(f'step "{st["id"]}"', 'an ask step needs "variables": the fields the person fills in')
                continue
            for k, v in enumerate(asked):
                order += 100
                add_variable(ctx, v, st['id'], f'step "{st["id"]}".ask.variables[{k}]', order)
    return {'submitted_by': form.get('submitted_by', ''), 'catalog': refs.get('catalog'), 'category': refs.get('category')}


def collect_ids(ctx: Ctx, steps, seen: dict) -> None:
    """Gather every step id (nested included) into seen, reporting duplicates."""
    for st in steps if isinstance(steps, list) else []:
        if not isinstance(st, dict):
            continue
        for obj in [st] + list(st.get('else_if') or []) + ([st['else']] if isinstance(st.get('else'), dict) else []):
            if isinstance(obj, dict) and isinstance(obj.get('id'), str):
                if obj['id'] in seen:
                    ctx.err(f'step "{obj["id"]}"', 'id is used twice — ids must be unique in the flow')
                seen[obj['id']] = 1
        if isinstance(st.get('item'), str):
            if st['item'] in seen:
                ctx.err(f'step "{st.get("id")}"', f'loop item name "{st["item"]}" is already used as an id')
            seen[st['item']] = 1
        for k in ('steps', 'try', 'catch'):
            collect_ids(ctx, st.get(k), seen)
        for e in list(st.get('else_if') or []) + ([st['else']] if isinstance(st.get('else'), dict) else []):
            if isinstance(e, dict):
                collect_ids(ctx, e.get('steps'), seen)
        for b in st.get('branches') or []:
            collect_ids(ctx, b, seen)


def trigger_ir(ctx: Ctx, t) -> dict | None:
    """Validate the trigger object → {fluent, inputs} IR."""
    if not isinstance(t, dict):
        ctx.err('trigger', 'must be an object with a "type"')
        return None
    typ = t.get('type')
    trig = ctx.vocab['triggers'].get(typ)
    if trig is None:
        msg = ctx.vocab['unsupported'].get(typ) if isinstance(typ, str) else None
        ctx.err('trigger', f'type "{typ}" is not available' + (f' — {msg}' if msg else f'; use one of {", ".join(ctx.vocab["triggers"])}'))
        return None
    ctx.trigger = trig
    ins = {k: v for k, v in t.items() if k != 'type'}
    nodes = []
    for k in ins:
        if k not in trig['inputs']:
            ctx.err('trigger', f'{typ} has no input "{k}" — its inputs are {", ".join(trig["inputs"]) or "none"} ({trig["doc"]})')
    for k, i in trig['inputs'].items():
        if i['required'] and k not in ins:
            ctx.err('trigger', f'{typ} needs "{k}" ({i["doc"]})')
    if 'table' in trig['inputs'] and 'table' in ins and known_table(ctx, 'trigger.table', ins['table'], 'trigger table'):
        ctx.trigger_table = ins['table']
    if 'run_flow_in' in trig['inputs']:
        ins.setdefault('run_flow_in', 'background')     # wfa-flow-guide.md:97
    for k, i in trig['inputs'].items():
        if k not in ins:
            continue
        v, w = ins[k], f'trigger.{k}'
        match i['type']:
            case 'Time':
                ok = isinstance(v, dict) and set(v) <= {'hours', 'minutes', 'seconds', 'timezone'} and all(
                    isinstance(v.get(x, 0), int) for x in ('hours', 'minutes', 'seconds'))
                if not ok:
                    ctx.err(w, 'time is {"hours": 9, "minutes": 0, "seconds": 0, "timezone": "UTC"} (trigger-api.md:188)')
                    continue
                nodes.append((k, ('time', v.get('hours', 0), v.get('minutes', 0), v.get('seconds', 0), v.get('timezone', 'UTC'))))
            case 'Duration':
                n = duration_node(ctx, v, w)
                if n:
                    nodes.append((k, n))
            case 'enum':
                if v not in i['values']:
                    ctx.err(w, f'must be one of {", ".join(i["values"])}')
                else:
                    nodes.append((k, ('str', v)))
            case 'integer':
                if not isinstance(v, int) or isinstance(v, bool):
                    ctx.err(w, 'must be a whole number')
                else:
                    nodes.append((k, ('num', v)))
            case 'string' | 'datetime' if isinstance(v, str):
                node = ('str', v)
                if k == 'condition':
                    node = check_query(ctx, node, ctx.trigger_table, w)
                nodes.append((k, node))
            case _:
                ctx.err(w, f'input "{k}" ({i["type"]}) is not supported by this skill — leave it out')
    return {'fluent': trig['fluent'], 'inputs': nodes}


def check_spec(spec, vocab: dict) -> tuple[list[str], dict | None]:
    """Validate the spec; return (errors, IR). The IR is only meaningful when errors is empty."""
    ctx = Ctx(spec if isinstance(spec, dict) else {}, vocab)
    if not isinstance(spec, dict):
        return ['the spec must be a JSON object'], None
    for k in ('name', 'title', 'description', 'language', 'trigger', 'steps'):
        if k not in spec:
            ctx.err('spec', f'missing "{k}"')
    name = spec.get('name', '')
    if not isinstance(name, str) or not NAME_RE.match(name):
        ctx.err('name', 'must be a kebab-case slug of 4–50 characters, e.g. "new-employee-onboarding"')
        name = 'flow'
    ctx.key_prefix = name.replace('-', '_')
    for k in ('title', 'description', 'language'):
        if k in spec and (not isinstance(spec[k], str) or not spec[k].strip()):
            ctx.err(k, 'must be a non-empty string')
    for k in sorted(set(spec) - {'name', 'title', 'description', 'language', 'trigger', 'steps', 'placeholders', 'form'}):
        msg = ' — a value a person supplies is a variable of the "form" or of an ask step' if k == 'new_fields' else ''
        ctx.err('spec', f'unknown key "{k}"{msg}')
    if not isinstance(spec.get('placeholders') or {}, dict):
        ctx.err('placeholders', 'must be an object {"name": {"kind", "description"}, …}')
        ctx.spec = spec = {k: v for k, v in spec.items() if k != 'placeholders'}
    for pname, p in (spec.get('placeholders') or {}).items():
        if not PH_RE.match(pname) or not isinstance(p, dict) or not str(p.get('description', '')).strip() \
                or p.get('kind') not in PH_KINDS or ('value' in p and not isinstance(p['value'], str)):
            ctx.err(f'placeholders.{pname}', f'needs a snake_case name, a "description" and a "kind" in {", ".join(PH_KINDS)} ("value" optional, a string)')
        else:
            ctx.placeholders[pname] = p
    ctx.catalog = isinstance(spec.get('trigger'), dict) and spec['trigger'].get('type') == 'service_catalog'
    form = form_ir(ctx, spec)
    if form:
        ctx.uses_params = ctx.uses_action = True    # the form is read at the start (getCatalogVariables)
    trig = trigger_ir(ctx, spec.get('trigger'))
    seen: dict = {}
    collect_ids(ctx, spec.get('steps'), seen)
    ctx.all_ids = set(seen)
    steps = body(ctx, spec.get('steps'), 'steps')
    for p in sorted(set(ctx.placeholders) - ctx.used_placeholders):
        ctx.err(f'placeholders.{p}', 'is declared but never used')
    ir = {'name': name, 'key': ctx.key_prefix, 'title': spec.get('title', ''), 'description': spec.get('description', ''),
          'trigger': trig, 'steps': steps, 'uses_params': ctx.uses_params, 'uses_action': ctx.uses_action,
          'referenced': ctx.referenced, 'placeholders': ctx.placeholders, 'notes': ctx.notes,
          'form': form, 'variables': list(ctx.variables.values())}
    return ctx.errors, ir

# ------------------------------------------------------------------ rendering (Fluent)
IND = '    '


def ts_str(s: str) -> str:
    """A TypeScript single-quoted string literal."""
    return "'" + s.replace('\\', '\\\\').replace("'", "\\'").replace('\r', '\\r').replace('\n', '\\n') + "'"


def tpl_text(s: str) -> str:
    """Text escaped for the inside of a TypeScript template literal."""
    return s.replace('\\', '\\\\').replace('`', '\\`').replace('${', '\\${')


def pill_ts(node) -> str:
    """A pill node as wfa.dataPill(path, 'type')."""
    return f'wfa.dataPill({node[1]}, {ts_str(node[2])})'


def ph_text(node) -> str:
    """A placeholder's value when the user gave one, else PLACEHOLDER:<name>."""
    return node[2] if node[2] is not None else f'PLACEHOLDER:{node[1]}'


def tpl_part_ts(part) -> str:
    """One part of a template-literal node: text, a pill, or a placeholder."""
    match part[0]:
        case 'text':
            return tpl_text(part[1])
        case 'pill':
            return '${' + pill_ts(part) + '}'
        case _:
            return tpl_text(ph_text(part))


def approval_ts(node, depth: int) -> str:
    """wfa.approvalRules: one OR rule set holding one rule (action-api.md:505-532)."""
    users = ', '.join(value_ts(u, 0) for u in node[2])
    groups = ', '.join(value_ts(g, 0) for g in node[3])
    p = IND * depth
    return '\n'.join([
        'wfa.approvalRules({',
        f"{p}{IND}conditionType: 'OR',",
        f'{p}{IND}ruleSets: [',
        f'{p}{IND * 2}{{',
        f"{p}{IND * 3}action: 'ApprovesRejects',",
        f"{p}{IND * 3}conditionType: 'AND',",
        f"{p}{IND * 3}rules: [[{{ ruleType: {ts_str(node[1])}, users: [{users}], groups: [{groups}], manual: false }}]],",
        f'{p}{IND * 2}}},',
        f'{p}{IND}],',
        f'{p}}})',
    ])


def value_ts(node, depth: int) -> str:
    """The TypeScript expression for an IR value node; depth indents multi-line values (TemplateValue, approvalRules)."""
    match node[0]:
        case 'str':
            return ts_str(node[1])
        case 'num':
            return repr(node[1])
        case 'bool':
            return 'true' if node[1] else 'false'
        case 'pill':
            return pill_ts(node)
        case 'ph':
            return ts_str(ph_text(node))
        case 'tpl':
            return '`' + ''.join(tpl_part_ts(p) for p in node[1]) + '`'
        case 'dur':
            return 'Duration({ ' + ', '.join(f'{k}: {v}' for k, v in node[1]) + ' })'
        case 'time':
            return f'Time({{ hours: {node[1]}, minutes: {node[2]}, seconds: {node[3]} }}, {ts_str(node[4])})'
        case 'tv':
            pad = IND * (depth + 1)
            return '\n'.join(['TemplateValue({'] + [f'{pad}{col}: {value_ts(v, depth + 1)},' for col, v in node[1]]
                             + [IND * depth + '})'])
        case 'approval':
            return approval_ts(node, depth)
        case kind:
            raise ValueError(f'unknown node {kind}')


def config_ts(key: str, label: str, extra: list[tuple[str, str]] = ()) -> str:
    """The inline config object { $id: Now.ID['key'], <extra>, annotation: 'label' }."""
    parts = [f"$id: Now.ID[{ts_str(key)}]"] + [f'{k}: {v}' for k, v in extra]
    if label:
        parts.append(f'annotation: {ts_str(label)}')
    return '{ ' + ', '.join(parts) + ' }'


def arrow(param: str | None) -> str:
    """An arrow-function head, with its parameter only when the body uses it (TS6133 otherwise)."""
    return f'({param}) => {{' if param else '() => {'


def render_steps(ir: dict, steps: list, depth: int) -> list[str]:
    """The lines of a list of steps at the given depth."""
    out: list[str] = []
    for st in steps:
        out += render_step(ir, st, depth)
    return out


def block(ir: dict, call: str, config: str, steps: list, d: int, param: str | None = None) -> list[str]:
    """call(config, (param) => { steps }) — the shape of if / elseIf / else / forEach / doTheFollowing."""
    p, p1 = IND * d, IND * (d + 1)
    return [f'{p}{call}(', f'{p1}{config},', f'{p1}{arrow(param)}'] + render_steps(ir, steps, d + 2) + [f'{p1}}}', f'{p})']


def render_action(ir: dict, st: dict, d: int) -> list[str]:
    """wfa.action(action.core.<name>, { $id, annotation }, { inputs }), captured as a const only when used (action-api.md:51-65)."""
    p, p1, p2 = IND * d, IND * (d + 1), IND * (d + 2)
    capture = f"const {st['id']} = " if st['id'] in ir['referenced'] else ''
    lines = [f'{p}{capture}wfa.action(', f"{p1}{st['fluent']},", f"{p1}{config_ts(st['key'], st['label'])},", f'{p1}{{']
    lines += [f'{p2}{name}: {value_ts(v, d + 2)},' for name, v in st['inputs']]
    return lines + [f'{p1}}}', f'{p})']


def render_if(ir: dict, st: dict, d: int) -> list[str]:
    """wfa.flowLogic.if, then each elseIf, then else (wfa-flow-logic-api.md:23-102)."""
    lines = block(ir, 'wfa.flowLogic.if', config_ts(st['key'], st['label'], [('condition', value_ts(st['cond'], 0))]), st['body'], d)
    for e in st['elifs']:
        lines += block(ir, 'wfa.flowLogic.elseIf', config_ts(e['key'], e['label'], [('condition', value_ts(e['cond'], 0))]),
                       e['body'], d)
    if els := st['else']:
        lines += block(ir, 'wfa.flowLogic.else', config_ts(els['key'], els['label']), els['body'], d)
    return lines


def render_for_each(ir: dict, st: dict, d: int) -> list[str]:
    """wfa.flowLogic.forEach(pill, { $id }, (item) => {…}) (wfa-flow-logic-api.md:106-135)."""
    p, p1 = IND * d, IND * (d + 1)
    param = st['alias'] if st['alias'] in ir['referenced'] else None
    return [f'{p}wfa.flowLogic.forEach(', f"{p1}{pill_ts(st['items'])},", f"{p1}{config_ts(st['key'], st['label'])},",
            f'{p1}{arrow(param)}'] + render_steps(ir, st['body'], d + 2) + [f'{p1}}}', f'{p})']


def render_parallel(ir: dict, st: dict, d: int) -> list[str]:
    """wfa.flowLogic.doInParallel({ $id }, () => {…}, () => {…}) — rest-argument blocks (wfa-flow-logic-api.md:211-269)."""
    p, p1 = IND * d, IND * (d + 1)
    lines = [f'{p}wfa.flowLogic.doInParallel(', f"{p1}{config_ts(st['key'], st['label'])},"]
    for b in st['branches']:
        lines += [f'{p1}() => {{'] + render_steps(ir, b, d + 2) + [f'{p1}}},']
    return lines + [f'{p})']


def render_try_catch(ir: dict, st: dict, d: int) -> list[str]:
    """wfa.flowLogic.tryCatch({ $id }, { try, catch }) (wfa-flow-logic-api.md:273-342)."""
    p, p1, p2 = IND * d, IND * (d + 1), IND * (d + 2)
    return ([f'{p}wfa.flowLogic.tryCatch(', f"{p1}{config_ts(st['key'], st['label'])},", f'{p1}{{', f'{p2}try: () => {{']
            + render_steps(ir, st['try'], d + 3) + [f'{p2}}},', f'{p2}catch: () => {{']
            + render_steps(ir, st['catch'], d + 3) + [f'{p2}}},', f'{p1}}}', f'{p})'])


def render_do_until(ir: dict, st: dict, d: int) -> list[str]:
    """wfa.flowLogic.doTheFollowing, with until(...) as the last statement (wfa-flow-logic-api.md:346-409)."""
    lines = block(ir, 'wfa.flowLogic.doTheFollowing', config_ts(st['key'], st['label']), st['body'], d)
    return lines[:-2] + [f"{IND * (d + 2)}wfa.flowLogic.until({value_ts(st['until'], 0)})"] + lines[-2:]


def render_wait(ir: dict, st: dict, d: int) -> list[str]:
    """wfa.flowLogic.waitForADuration, relative to a date or explicit (wfa-flow-logic-api.md:459-507).

    With a schedule, a day of the duration is 24 hours of schedule time (the business-hours calculation, :493):
    "N working days" needs a Monday–Friday all-day schedule, or the offset in schedule hours.
    """
    p, p1 = IND * d, IND * (d + 1)
    lines = [f'{p}wfa.flowLogic.waitForADuration({{', f"{p1}$id: Now.ID[{ts_str(st['key'])}],"]
    if st['label']:
        lines.append(f"{p1}annotation: {ts_str(st['label'])},")
    if st['mode'] == 'relative':
        lines += [f"{p1}durationType: 'relative_duration',", f"{p1}duration: {value_ts(st['duration'], 0)},",
                  f"{p1}relativeOperator: {ts_str(st['direction'])},", f"{p1}relativeDatetime: {value_ts(st['relative'], 0)},"]
    else:
        lines += [f"{p1}durationType: 'explicit_duration',", f"{p1}duration: {value_ts(st['duration'], 0)},"]
    if st['schedule'] is not None:
        lines.append(f"{p1}schedule: {value_ts(st['schedule'], 0)},")
    return lines + [f'{p}}})']


def render_ask(ir: dict, st: dict, d: int) -> list[str]:
    """A blocking createCatalogTask carrying the asked variables, then getCatalogVariables for the answers (forms research §3a)."""
    p, p1, p2 = IND * d, IND * (d + 1), IND * (d + 2)
    item = item_name(ir['name'])
    task = f"{st['id']}__task"
    lines = [f"{p}{'const ' + task + ' = ' if task in ir['referenced'] else ''}wfa.action(",
             f'{p1}action.core.createCatalogTask,', f"{p1}{config_ts(st['key'], st['label'])},", f'{p1}{{',
             f'{p2}ah_requested_item: {REQUESTED_ITEM},', f"{p2}ah_short_description: {value_ts(st['short'], 0)},"]
    if st['fields'][1]:
        lines.append(f"{p2}ah_fields: {value_ts(st['fields'], d + 2)},")
    lines += [f'{p2}ah_wait: true,', f'{p2}template_catalog_item: `${{{item}}}`,',
              f"{p2}catalog_variables: {variables_ts(item, st['vars'])},", f'{p1}}}', f'{p})']
    return lines + read_variables(p, st['id'] if st['id'] in ir['referenced'] else None,
                                  st['key'] + '__answers', st['label'], item, st['vars'])


def render_step(ir: dict, st: dict, d: int) -> list[str]:
    """The lines of one step (an action, an ask or a flow-logic construct) at depth d."""
    match st['kind']:
        case 'action':
            return render_action(ir, st, d)
        case 'ask':
            return render_ask(ir, st, d)
        case 'if':
            return render_if(ir, st, d)
        case 'for_each':
            return render_for_each(ir, st, d)
        case 'parallel':
            return render_parallel(ir, st, d)
        case 'try_catch':
            return render_try_catch(ir, st, d)
        case 'do_until':
            return render_do_until(ir, st, d)
        case 'wait_duration':
            return render_wait(ir, st, d)
        case 'end':
            return [f"{IND * d}wfa.flowLogic.endFlow({config_ts(st['key'], st['label'])})"]
        case kind:
            raise ValueError(kind)


REQUESTED_ITEM = "`${wfa.dataPill(params.trigger.request_item, 'reference')}`"   # sample flow-trigger-catalog-item.now.ts:142


def variables_ts(item: str, names: list[str]) -> str:
    """[item.variables.a, item.variables.b, …]"""
    return '[' + ', '.join(f'{item}.variables.{n}' for n in names) + ']'


def read_variables(p: str, const: str | None, key: str, label: str, item: str, names: list[str]) -> list[str]:
    """getCatalogVariables: the answers become pills <const>.<variable> (typings + sample :158; the md says no outputs)."""
    p1, p2 = p + IND, p + IND * 2
    return [f"{p}{'const ' + const + ' = ' if const else ''}wfa.action(", f'{p1}action.core.getCatalogVariables,',
            f'{p1}{config_ts(key, label)},', f'{p1}{{', f'{p2}requested_item: {REQUESTED_ITEM},',
            f'{p2}template_catalog_item: `${{{item}}}`,', f'{p2}catalog_variables: {variables_ts(item, names)},',
            f'{p1}}}', f'{p})']


def camel(name: str) -> str:
    """kebab-case flow name → camelCase (new-employee-onboarding → newEmployeeOnboarding)."""
    head, *rest = name.split('-')
    return head + ''.join(w[:1].upper() + w[1:] for w in rest)


def export_name(name: str) -> str:
    """The flow's exported const: newEmployeeOnboardingFlow."""
    return camel(name) + 'Flow'


def item_name(name: str) -> str:
    """The catalog item's exported const: newEmployeeOnboardingItem."""
    return camel(name) + 'Item'


def variable_ts(v: dict) -> str:
    """One catalog variable: name: XVariable({ question, <extra>, mandatory?, order, helpText? }) (variables/*.md)."""
    props = [f"question: {ts_str(v['label'])}"]
    if v['extra'] and v['extra'][1][0] == 'choices':
        props.append('choices: { ' + ', '.join(f'{k}: {{ label: {ts_str(t)}, sequence: {i + 1} }}'
                                               for i, (k, t) in enumerate(v['extra'][1][1])) + ' }')
    elif v['extra']:
        props.append(f"{v['extra'][0]}: {value_ts(v['extra'][1], 0)}")
    if v['mandatory'] and v['owner'] == 'form':     # asked variables are required on the task by the UI policy
        props.append('mandatory: true')
    props.append(f"order: {v['order']}")
    if v['help']:
        props.append(f"helpText: {ts_str(v['help'])}")
    return f"{IND * 2}{v['name']}: {v['builder']}({{ {', '.join(props)} }}),"


def render_catalog(ir: dict) -> list[str]:
    """CatalogItem (catalogitem-api.md) + the two CatalogUiPolicy records (cataloguipolicy-api.md) for asked variables.

    shortDescription is the one-line summary shown in the catalog (the flow title); description says who submits it.
    """
    f, item = ir['form'], item_name(ir['name'])
    lines = [f'export const {item} = CatalogItem({{', f"{IND}$id: Now.ID[{ts_str(ir['key'] + '__item')}],",
             f"{IND}name: {ts_str(ir['title'])},", f"{IND}shortDescription: {ts_str(ir['title'])},",
             f"{IND}description: {ts_str(f['submitted_by'])},", f"{IND}flow: Now.ref('sys_hub_flow', {ts_str(ir['key'])}),",
             f"{IND}catalogs: [{value_ts(f['catalog'], 0)}],", f"{IND}categories: [{value_ts(f['category'], 0)}],",
             f'{IND}variables: {{']
    lines += [variable_ts(v) for v in ir['variables']]
    lines += [f'{IND}}},', '})', '']
    asked = [v for v in ir['variables'] if v['owner'] != 'form']
    if asked:
        for suffix, short, on_item, on_task, action in (
                ('__hide_asked', 'Answered later on a catalog task: hidden on the order form', 'true', 'false',
                 lambda v: 'visible: false'),
                ('__show_asked', 'Shown on the catalog tasks that ask them', 'false', 'true',
                 lambda v: 'visible: true' + (', mandatory: true' if v['mandatory'] else ''))):
            lines += ['CatalogUiPolicy({', f"{IND}$id: Now.ID[{ts_str(ir['key'] + suffix)}],", f'{IND}shortDescription: {ts_str(short)},',
                      f'{IND}catalogItem: {item},', f'{IND}appliesOnCatalogItemView: {on_item},',
                      f'{IND}appliesOnRequestedItems: false,', f'{IND}appliesOnCatalogTasks: {on_task},', f'{IND}onLoad: true,',
                      f'{IND}actions: [']
            lines += [f"{IND * 2}{{ variableName: {item}.variables.{v['name']}, {action(v)} }}," for v in asked]
            lines += [f'{IND}],', '})', '']
    return lines


def render_flow(ir: dict) -> str:
    """The whole .now.ts file: imports, the catalog item and UI policies when there is a form, then the Flow."""
    imports = (['action'] if ir['uses_action'] else []) + ['Flow', 'wfa', 'trigger']
    t = ir['trigger']
    tin = '{ ' + ', '.join(f'{k}: {value_ts(v, 0)}' for k, v in t['inputs']) + ' }' if t['inputs'] else '{}'
    lines = [
        f'// Generated by servicenow-flow-author from flow-specs/{ir["name"]}.json — edit the spec and re-run, not this file.',
        f"import {{ {', '.join(imports)} }} from '@servicenow/sdk/automation'",
    ]
    if ir['form']:
        core = ['CatalogItem'] + (['CatalogUiPolicy'] if any(v['owner'] != 'form' for v in ir['variables']) else [])
        core += sorted({v['builder'] for v in ir['variables']})
        lines += [f"import {{ {', '.join(core)} }} from '@servicenow/sdk/core'", ''] + render_catalog(ir)
    else:
        lines.append('')
    lines += [
        f'export const {export_name(ir["name"])} = Flow(',
        f'{IND}{{',
        f"{IND * 2}$id: Now.ID[{ts_str(ir['key'])}],",
        f"{IND * 2}name: {ts_str(ir['title'])},",
        f"{IND * 2}description: {ts_str(ir['description'])},",
        f"{IND * 2}runAs: 'system',",
        f'{IND}}},',
        f'{IND}wfa.trigger(',
        f"{IND * 2}{t['fluent']},",
        f"{IND * 2}{{ $id: Now.ID[{ts_str(ir['key'] + '__trigger')}] }},",
        f'{IND * 2}{tin}',
        f'{IND}),',
        f"{IND}{arrow('params' if ir['uses_params'] else None)}",
    ]
    if ir['form']:
        item = item_name(ir['name'])
        lines += read_variables(IND * 2, 'form' if 'form' in ir['referenced'] else None, ir['key'] + '__form', '', item,
                                [v['name'] for v in ir['variables'] if v['owner'] == 'form'])
    lines += render_steps(ir, ir['steps'], 2)
    lines += [f'{IND}}}', ')', '']
    return '\n'.join(lines)


# ------------------------------------------------------------------ project
# One shared app per vendor (spec decision 2) under <out>/x_<vendor>_synergy/. The user builds and installs from that
# folder every time (decision 7); the zip beside it only moves the project to another machine.
ZIP_SKIP = ('node_modules/', 'dist/', 'target/', '.now/', 'src/fluent/generated/')
NEED_VENDOR_TEXT = ('NEED_VENDOR — the app scope is x_<company code>_synergy and the company code is not known yet.\n'
                    'NEXT: ask the user once for their instance\'s company code (in ServiceNow Studio, the prefix shown '
                    'when creating an app, e.g. x_1234567 → 1234567), then run again with "--vendor", "<code>".')
SCHEDULE_NOTE = ('a schedule "day" counts as 24h of schedule time — use a Mon–Fri all-day (24h) schedule with holidays '
                 'for "N working days", or give the offset in schedule hours (e.g. 16h = 2 days of an 8h schedule)')


def resolve_vendor(out: Path, given: str | None) -> tuple[str | None, str | None]:
    """(vendor, error). Normalises " X_1234567 " → "1234567"; falls back to <out>/.vendor.

    Why: the user is asked for the company code once; the script remembers it so a second flow joins the same app
    instead of asking again. (None, None) means nobody gave it yet — the caller prints NEED_VENDOR.
    """
    raw = given
    if raw is None and (out / '.vendor').is_file():
        raw = (out / '.vendor').read_text(encoding='utf-8')
    if raw is None:
        return None, None
    v = raw.strip().lower()
    v = v[2:] if v.startswith('x_') else v
    v = v.strip('_')
    if not VENDOR_RE.match(v):
        return None, (f'vendor code "{raw.strip()}" must be 1–8 lowercase letters or digits, so the scope '
                      f'x_<code>_synergy stays within 18 characters (developing-apps-guide.md, scope rule)')
    return v, None


class Project:
    """The shared app x_<vendor>_synergy under <out>: its files, FLOWS.md and the zip.

    Why a class: every write needs the same output root, vendor, scope and project folder. First use creates the app
    (now.config.json with a fresh scopeId, package.json, .gitignore, README.md); every run adds or replaces one flow
    (its spec in flow-specs/ and its .now.ts), then regenerates FLOWS.md from all stored specs and re-zips. It never
    writes or deletes src/fluent/generated/keys.ts: the build keeps each record's sys_id there, and losing it makes the
    next install create duplicates.
    """

    def __init__(self, out: Path, vendor: str):
        self.out, self.vendor = out, vendor
        self.scope = f'x_{vendor}_synergy'
        self.root = out / self.scope
        self.written: list[str] = []

    def exists(self) -> bool:
        """The app was created by an earlier run (its now.config.json holds the scopeId that must never change)."""
        return (self.root / 'now.config.json').is_file()

    def put(self, rel: str, text: str) -> None:
        """Write one file of the project and remember it for the report."""
        f = self.root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding='utf-8')
        self.written.append(rel)

    def specs(self) -> dict[str, dict]:
        """Every flow of the app by name, from flow-specs/*.json — the source of truth for FLOWS.md."""
        folder = self.root / 'flow-specs'
        if not folder.is_dir():
            return {}
        return {f.stem: json.loads(f.read_text(encoding='utf-8')) for f in sorted(folder.glob('*.json'))}

    def package_json(self) -> str:
        """package.json pinned to the SDK the vocabulary was read from (4.13.3) and the lab's glide/typescript."""
        return json.dumps({'name': self.scope.replace('_', '-'), 'version': '1.0.0', 'private': True,
                           'scripts': {'build': 'now-sdk build', 'deploy': 'now-sdk install'},
                           'devDependencies': {'@servicenow/sdk': SDK_VERSION, '@servicenow/glide': '27.0.5',
                                               'typescript': '5.8.2'}}, indent=2) + '\n'

    def readme(self) -> str:
        """The project README: how to build and install from this folder, every time (spec decision 7)."""
        return f"""# SynergyAI flows ({self.scope})

ServiceNow flows written by SynergyAI's servicenow-flow-author skill, as one ServiceNow SDK (Fluent) app.
`FLOWS.md` lists every flow, its trigger and the placeholders to fill.

First time (Node 20.18 or later):

    npm install
    npx now-sdk auth --add https://<instance>.service-now.com --type basic

Every time:

    npx now-sdk build
    npx now-sdk install

`now-sdk install` sends the whole app; flows that did not change stay as they are. Open Workflow Studio →
Flows and look for the app "SynergyAI flows". A flow with an order form starts when someone orders its catalog
item (Service Catalog → the category named in `FLOWS.md`).

Schedule placeholders: {SCHEDULE_NOTE}.

Build and install from this folder every time. Keep `src/fluent/generated/keys.ts` (it maps each step to its
record on the instance; losing it makes the next install create duplicates). The zip next to this folder is only
for moving the project to another machine.
"""

    def flows_md(self) -> str:
        """FLOWS.md: per flow its file, trigger, who submits the form, the variables, and the placeholders to fill.

        Regenerated from every stored spec on each run, oldest flow first, so adding a flow never loses another.
        """
        lines = [f'# SynergyAI flows ({self.scope})', '',
                 'Fill every placeholder before `npx now-sdk install`: give SynergyAI the values (it re-runs the flow with '
                 'them), or replace the `PLACEHOLDER:<name>` text in the flow file.', '']
        for name, s in sorted(self.specs().items(), key=lambda kv: (kv[1].get('_generated_on', ''), kv[0])):
            t, form = s['trigger'], s.get('form')
            if form:     # decision 6: the procedure's start condition is who submits the order form
                where = f' — ordering the catalog item "{s["title"]}"'
            else:
                where = f" on {t['table']}" if t.get('table') else ''
            cond = f" — {t['condition']}" if t.get('condition') else ''
            lines += [f"## {s['title']}", '',
                      f'- File: `src/fluent/flows/{name}.now.ts` (spec: `flow-specs/{name}.json`)',
                      f"- Trigger: {t['type']}{where}{cond}"]
            if form:
                asked = [(st['id'], v) for st in iter_steps(s['steps']) if isinstance(st.get('ask'), dict)
                         for v in st['ask'].get('variables') or []]
                lines += [f"- Who submits this form and when: {form['submitted_by']}",
                          f"- Catalog item listed in: catalog {self.listed_in(s, form, 'catalog')} / "
                          f"category {self.listed_in(s, form, 'category')}",
                          '- Order form: ' + ', '.join(f"{v['label']} (`{v['name']}`, {v['type']})" for v in form['variables'])]
                if asked:
                    lines.append('- Asked during the run (catalog tasks): '
                                 + ', '.join(f"{v['label']} (`{v['name']}`, step `{sid}`)" for sid, v in asked))
            lines += [f"- Language: {s['language']}", f"- Generated: {s.get('_generated_on', '')}"]
            phs = s.get('placeholders') or {}
            lines.append('- Placeholders to fill:' if phs else '- Placeholders to fill: none')
            for pn, p in phs.items():
                state = f"filled: {p['value']}" if 'value' in p else 'to fill'
                lines.append(f"  - `{pn}` ({p['kind']}, {state}) — {p['description']}")
                if p['kind'] == 'schedule':
                    lines.append(f'    Note: {SCHEDULE_NOTE}.')
            lines.append('')
        return '\n'.join(lines)

    @staticmethod
    def listed_in(spec: dict, form: dict, key: str) -> str:
        """`<placeholder>` (filled: <value> | to fill) for form.catalog / form.category.

        Why: the README sends the reader to the item's catalog and category, so FLOWS.md must name them; the spec
        always gives them as placeholders (form_ir), which the user fills with the instance's records.
        """
        name = form[key]['placeholder']
        p = (spec.get('placeholders') or {}).get(name, {})
        return f"`{name}` ({'filled: ' + p['value'] if 'value' in p else 'to fill'})"

    def zip(self) -> Path:
        """<out>/x_<vendor>_synergy.zip of the project without dependencies, build outputs or generated keys."""
        zpath = self.out / f'{self.scope}.zip'
        with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED) as z:
            for f in sorted(self.root.rglob('*')):
                rel = f.relative_to(self.root).as_posix()
                if f.is_file() and not any(rel.startswith(s) for s in ZIP_SKIP):
                    z.write(f, f'{self.scope}/{rel}')
        return zpath

    def write(self, spec: dict, ir: dict, today: str) -> tuple[bool, Path]:
        """Add or replace one flow (creating the app on first use); return (first_use, zip path)."""
        first = not self.exists()
        if first:
            self.put('now.config.json', json.dumps({'scope': self.scope, 'scopeId': uuid.uuid4().hex,
                                                    'name': 'SynergyAI flows'}, indent=2) + '\n')
            self.put('package.json', self.package_json())
            self.put('.gitignore', 'node_modules/\ndist/\ntarget/\n')
            self.put('README.md', self.readme())
        stored = dict(spec, _generated_on=today)
        self.put(f'flow-specs/{ir["name"]}.json', json.dumps(stored, indent=2, ensure_ascii=False) + '\n')
        self.put(f'src/fluent/flows/{ir["name"]}.now.ts', render_flow(ir))
        self.put('FLOWS.md', self.flows_md())
        (self.out / '.vendor').write_text(self.vendor + '\n', encoding='utf-8')
        return first, self.zip()


def write_project(out: Path, vendor: str, spec: dict, ir: dict, today: str) -> tuple[Path, list[str], bool, Path]:
    """(project_dir, written_paths, first_use, zip_path) — the plan's entry point, one call for the CLI and the tests."""
    project = Project(out, vendor)
    first, zpath = project.write(spec, ir, today)
    return project.root, project.written, first, zpath


# ------------------------------------------------------------------ CLI
def main(argv: list[str] | None = None) -> int:
    """Check the spec, then write it into the shared app. Nothing is written unless the spec is VALID and the vendor
    code is known; every problem is listed at once so the model fixes them in one revision."""
    ap = argparse.ArgumentParser(description='Check a JSON flow spec and write it as a ServiceNow SDK (Fluent) flow.')
    ap.add_argument('-i', '--input', required=True, help='the JSON flow spec (references/flow-spec.md)')
    ap.add_argument('--vendor', help="the instance's company code (asked once, then remembered in <out>/.vendor)")
    ap.add_argument('--out', default='/outputs/servicenow', help='output root (default /outputs/servicenow)')
    ap.add_argument('--vocab', help='fluent-vocabulary.json (auto-detected)')
    ap.add_argument('--today', default=datetime.date.today().isoformat(), help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    try:
        spec = json.loads(Path(a.input).read_text(encoding='utf-8'))
    except Exception as e:  # noqa: BLE001 — any unreadable input is reported the same way
        print(f'error: cannot read the spec JSON: {e}', file=sys.stderr)
        return 2
    vocab = load_vocabulary(a.vocab)
    out = Path(a.out)
    vendor, verr = resolve_vendor(out, a.vendor)
    errors, ir = check_spec(spec, vocab)
    if verr:
        errors.append(f'--vendor: {verr}')
    if errors:
        print(f'INVALID — {len(errors)} problem(s):\n  - ' + '\n  - '.join(errors), file=sys.stderr)
        print('NEXT: fix every problem in one revision of the spec and run again (at most two revisions).', file=sys.stderr)
        return 1
    if vendor is None:
        print(NEED_VENDOR_TEXT)
        return 1
    proj, written, first, zpath = write_project(out, vendor, spec, ir, a.today)
    flows = len(list((proj / 'flow-specs').glob('*.json')))
    print(f"VALID — flow \"{ir['title']}\" ({ir['name']})")
    print(f"project: {proj} ({'created' if first else 'existing'}; {flows} flow{'s' if flows != 1 else ''})")
    print('wrote:\n' + '\n'.join(f'  {w}' for w in written))
    print(f'zip: {zpath}')
    phs = ir['placeholders']
    if phs:
        print('placeholders (fill before install):')
        for pn, p in phs.items():
            print(f"  {pn} ({p['kind']}{', filled' if 'value' in p else ''}) — {p['description']}")
    else:
        print('placeholders: none')
    for n in ir['notes']:
        print(f'note: {n}')
    if ir['form']:
        print(f'catalog item: "{ir["title"]}" — {len(ir["variables"])} variables; the flow starts when someone orders it '
              f'(who and when: {ir["form"]["submitted_by"]})')
    print(f'NEXT: reply in 3–5 lines — the flow title, the folder {proj}, the placeholders to fill, and the commands '
          '`npx now-sdk build` then `npx now-sdk install` (first time in that folder: `npm install` and '
          '`npx now-sdk auth --add <instance> --type basic`).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
