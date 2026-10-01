#!/usr/bin/env python3
"""
terraform-author / create.py — check a cloud-neutral JSON infrastructure spec, map it to AWS or Azure, and write
a Terraform project that passes `terraform validate`.

Usage (the argv of run_skill_script):
  create.py -i /scratch/<name>.json [--cloud aws|azure] [--region <region>] [--out /outputs/terraform]
  create.py -i /outputs/terraform/<name>-aws/spec.json --cloud azure      (the same infrastructure on the other cloud)

Exit 0  VALID: project written, report + placeholders + secrets + NEXT on stdout.
Exit 1  INVALID (every problem listed, on stderr) or NEED_TARGET (the spec is valid but the cloud or region is
        unknown) — nothing written.
Exit 2  the spec file cannot be read as JSON, or the argv is wrong.
Standard library only: it runs in the app's browser Pyodide.
"""
from __future__ import annotations
import argparse, copy, ipaddress, json, re, sys
from pathlib import Path

CATALOG_CANDIDATES = [
    Path(__file__).resolve().parent.parent / 'references' / 'catalog.json',
    Path('/skill/terraform-author/references/catalog.json'),
]
NAME_RE = re.compile(r'^(?!.*--)[a-z][a-z0-9-]{1,22}[a-z0-9]$')
ENV_RE = re.compile(r'^[a-z][a-z0-9]{1,9}$')
COMP_RE = re.compile(r'^(?!.*__)[a-z][a-z0-9_]{0,18}[a-z0-9]$|^[a-z]$')
PH_RE = re.compile(r'^[a-z][a-z0-9_]{1,40}$')
ENV_VAR_RE = re.compile(r'^[A-Z][A-Z0-9_]{0,63}$')
TAG_KEY_RE = re.compile(r'^[A-Za-z][A-Za-z0-9_.-]{0,62}$')
IDENT_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')   # object keys written bare; others quoted
TOP_KEYS = {'name', 'title', 'environment', 'target', 'tags', 'network', 'components', 'access', 'placeholders',
            'left_out'}
# Component names that would give a generated resource the label of a shared one (the Azure blob DNS zone, the
# deployer's key vault role assignment).
RESERVED = {'blob', 'deployer'}
# Names Terraform refuses for a variable (placeholders and secrets become variables).
TF_RESERVED_VARS = {'source', 'version', 'providers', 'count', 'for_each', 'lifecycle', 'depends_on', 'locals'}
# Every file a run may write; a rerun deletes the ones this run does not write (nothing else in the folder).
GENERATED = ('versions.tf', 'providers.tf', 'variables.tf', 'network.tf', 'storage.tf', 'database.tf', 'secrets.tf',
             'app.tf', 'access.tf', 'outputs.tf', 'terraform.tfvars.example', 'README.md', '.gitignore', 'spec.json')
USER_ROOT = '~/Documents/synergyAI/outputs/terraform'


def load_catalog(explicit: str | None = None) -> dict:
    """The tables the script checks against (regions, sizes, roles, provider versions, out-of-v1 messages).

    Why a file: catalog.md, which the model reads, is checked against the same JSON by a test, so the model and the
    script never disagree on what is allowed."""
    paths = [Path(explicit)] if explicit else CATALOG_CANDIDATES
    for p in paths:
        if p.is_file():
            return json.loads(p.read_text(encoding='utf-8'))
    raise FileNotFoundError('catalog.json not found in: ' + ', '.join(map(str, paths)))


# ------------------------------------------------------------------ spec validation
class Spec:
    """A checked spec: every problem found at once, then the normalised values the mappers read.

    Why a class: validation fills defaults while it checks (cpu → memory_gb, edge levels, target), and the mappers
    need exactly those normalised values; keeping both in one object means a mapper never sees an unchecked field.
    `errors` lists every problem; `need_target` is set when only the cloud or region is missing."""

    def __init__(self, raw, catalog: dict, cloud: str | None = None, region: str | None = None):
        self.catalog = catalog
        self.errors: list[str] = []
        self.need_target = False
        self.notes: list[str] = []
        self.components: dict[str, dict] = {}
        self.access: list[dict] = []
        self.placeholders: dict[str, str] = {}
        self.used_placeholders: set[str] = set()
        self.left_out: list[str] = []
        # set before the early return so a caller can read them even when the spec is not an object
        self.raw = raw
        self.name = self.title = self.cloud = self.region = self.cidr = None
        self.environment = 'prod'
        self.tags: dict = {}
        if not isinstance(raw, dict):
            self.errors.append('spec: must be a JSON object (references/infra-spec.md)')
            return
        self.raw = copy.deepcopy(raw)
        self._top(raw)
        self._components(raw.get('components'))
        self._access(raw.get('access', []))
        self._placeholders(raw.get('placeholders', {}))
        self._target(raw.get('target'), cloud, region)
        if self.cloud and not self.errors:
            self._cloud_rules()

    # ---- top level
    def _top(self, raw: dict) -> None:
        for k in sorted(set(raw) - TOP_KEYS):
            self.err(k, f'unknown key; allowed: {", ".join(sorted(TOP_KEYS))}')
        self.name = raw.get('name')
        if not isinstance(self.name, str) or not NAME_RE.fullmatch(self.name):
            self.err('name', 'must be a lowercase slug of 3–24 letters, digits and single hyphens (e.g. "customer-portal")')
        self.title = raw.get('title')
        if not isinstance(self.title, str) or not self.title.strip():
            self.err('title', 'must be a non-empty string (the application, in the user\'s words)')
        self.environment = raw.get('environment', 'prod')
        if not isinstance(self.environment, str) or not ENV_RE.fullmatch(self.environment):
            self.err('environment', 'must be 2–10 lowercase letters or digits, starting with a letter (e.g. "prod", "dev")')
        self.tags = raw.get('tags', {})
        if not isinstance(self.tags, dict):
            self.err('tags', 'must be an object of tag name → text')
            self.tags = {}
        for k, v in self.tags.items():
            if not TAG_KEY_RE.fullmatch(k) or not isinstance(v, str) or len(v) > 256:
                self.err(f'tags.{k}', 'tag names are letters, digits, "_", "." or "-" (≤ 63) and values text (≤ 256)')
        net = raw.get('network', {})
        self.cidr = None
        if not isinstance(net, dict) or set(net) - {'cidr'}:
            self.err('network', 'must be {"cidr": "<private IPv4 range>"} or left out')
        else:
            self.cidr = self._cidr(net.get('cidr', '10.0.0.0/16'))
        lo = raw.get('left_out', [])
        if not isinstance(lo, list) or not all(isinstance(x, str) for x in lo):
            self.err('left_out', f'must be a list of out-of-v1 names: {", ".join(self.catalog["out_of_v1"])}')
        else:
            for x in lo:
                if x not in self.catalog['out_of_v1']:
                    self.err('left_out', f'"{x}" is not an out-of-v1 name; use one of: {", ".join(self.catalog["out_of_v1"])}')
            self.left_out = [x for x in lo if x in self.catalog['out_of_v1']]

    def _cidr(self, value):
        try:
            net = ipaddress.ip_network(value)
        except (ValueError, TypeError):
            self.err('network.cidr', f'"{value}" is not an IPv4 range like "10.0.0.0/16"')
            return None
        if net.version != 4 or not net.is_private or not 16 <= net.prefixlen <= 22:
            self.err('network.cidr', 'must be a private IPv4 range from /16 to /22 (e.g. "10.0.0.0/16"); '
                                     'the project carves four /24 subnets from it')
            return None
        return str(net)

    # ---- components
    def _components(self, comps) -> None:
        if not isinstance(comps, list) or not comps:
            self.err('components', 'must be a non-empty list (references/infra-spec.md → Components)')
            return
        kinds = self.catalog['components']
        for i, c in enumerate(comps):
            where = f'components[{i}]'
            if not isinstance(c, dict):
                self.err(where, 'must be an object with "type" and "name"')
                continue
            ctype, name = c.get('type'), c.get('name')
            if isinstance(ctype, str) and ctype in self.catalog['out_of_v1']:
                self.err(where, f'type "{ctype}" is out of v1 — remove it, add "{ctype}" to "left_out" and tell the '
                                f'user: {self.catalog["out_of_v1"][ctype]}')
                continue
            if not isinstance(ctype, str) or ctype not in kinds:
                self.err(where, f'unknown type {json.dumps(ctype)}; v1 types: {", ".join(kinds)}; out of v1 (put in '
                                f'"left_out"): {", ".join(self.catalog["out_of_v1"])}')
                continue
            if not isinstance(name, str) or not COMP_RE.fullmatch(name):
                self.err(where, 'name must be a lowercase slug of 1–20 letters, digits and single "_" (e.g. "web", "uploads")')
                continue
            where = f'components.{name}'
            if name in self.components:
                self.err(where, 'name used twice; component names are unique')
                continue
            if name in RESERVED:
                self.err(where, f'"{name}" is reserved for a generated resource; pick another name')
                continue
            if ctype == 'container_app' and 'domain' in c:
                self.err(f'{where}.domain', 'out of v1 — remove it, add "custom_domain" to "left_out" and tell the '
                                            f'user: {self.catalog["out_of_v1"]["custom_domain"]}')
                c = {k: v for k, v in c.items() if k != 'domain'}
            match ctype:
                case 'container_app':
                    self.components[name] = self._container_app(c, where)
                case 'postgres':
                    self.components[name] = self._options(c, where, 'postgres')
                case 'object_storage':
                    self.components[name] = self._options(c, where, 'object_storage')
                case 'secret':
                    self.components[name] = self._options(c, where, 'secret')

    def _options(self, c: dict, where: str, ctype: str) -> dict:
        """Unknown keys are errors; known ones get their catalog default when absent."""
        opts = self.catalog['components'][ctype]['options']
        for k in sorted(set(c) - set(opts) - {'type', 'name'}):
            self.err(f'{where}.{k}', f'unknown option for {ctype}; allowed: {", ".join(opts) or "none"}')
        out = {'type': ctype, 'name': c['name']}
        for k, o in opts.items():
            out[k] = c[k] if k in c else copy.deepcopy(o.get('default'))
        for k, o in opts.items():
            v = out[k]
            if isinstance(o.get('default'), bool) and not isinstance(v, bool):
                self.err(f'{where}.{k}', 'must be true or false')
            elif 'values' in o and (isinstance(v, bool) or v not in o['values']):   # True == 1 in Python
                self.err(f'{where}.{k}', f'must be one of {", ".join(map(str, o["values"]))}')
            elif 'min' in o and (not isinstance(v, int) or isinstance(v, bool) or not o['min'] <= v <= o['max']):
                self.err(f'{where}.{k}', f'must be a whole number from {o["min"]} to {o["max"]}')
        return out

    def _value(self, v, where: str) -> bool:
        """A text value or {"placeholder": name}; records placeholder use."""
        if isinstance(v, str):
            return True
        if isinstance(v, dict) and set(v) == {'placeholder'} and isinstance(v['placeholder'], str):
            self.used_placeholders.add(v['placeholder'])
            return True
        self.err(where, 'must be text or {"placeholder": "<name>"}')
        return False

    def _container_app(self, c: dict, where: str) -> dict:
        c = dict(c)
        mem = c.pop('memory_gb', None)   # checked here against cpu, not by _options
        out = self._options(c, where, 'container_app')
        cpu = out['cpu']
        cpu_ok = not isinstance(cpu, bool) and cpu in self.catalog['components']['container_app']['options']['cpu']['values']
        if mem is not None and (isinstance(mem, bool) or not isinstance(mem, (int, float))):
            self.err(f'{where}.memory_gb', 'must be a number: 2 × cpu')
            out['memory_gb'] = None
        elif not cpu_ok:
            out['memory_gb'] = None   # the cpu error is already reported; no 2 × cpu check against a wrong cpu
        else:
            out['memory_gb'] = cpu * 2 if mem is None else mem
        if cpu_ok and out['memory_gb'] is not None and out['memory_gb'] != cpu * 2:
            self.err(f'{where}.memory_gb', f'must be 2 × cpu ({cpu * 2:g} for cpu {cpu:g}): the sizes both clouds accept '
                                           f'are 0.25/0.5, 0.5/1, 1/2, 2/4, 4/8 (cpu/memory_gb)')
        if out['image'] is None:
            self.err(f'{where}.image', 'required: the image the user gave, or {"placeholder": "<name>"} — never an invented image')
        elif isinstance(out['image'], str) and not out['image'].strip():
            self.err(f'{where}.image', 'must not be empty: the image the user gave, or {"placeholder": "<name>"}')
        else:
            self._value(out['image'], f'{where}.image')
        if isinstance(out['min_instances'], int) and isinstance(out['max_instances'], int) \
                and out['min_instances'] > out['max_instances']:
            self.err(f'{where}.max_instances', 'must be ≥ min_instances')
        env = out['env']
        if not isinstance(env, dict):
            self.err(f'{where}.env', 'must be an object of NAME → text or {"placeholder": "<name>"}')
            out['env'] = {}
        for k, v in out['env'].items():
            if not ENV_VAR_RE.fullmatch(k):
                self.err(f'{where}.env.{k}', 'environment variable names are UPPER_CASE letters, digits and "_"')
            self._value(v, f'{where}.env.{k}')
        return out

    # ---- access edges
    def _access(self, edges) -> None:
        if not isinstance(edges, list):
            self.err('access', 'must be a list of {"from", "to", "level"}')
            return
        seen = set()
        for i, e in enumerate(edges):
            where = f'access[{i}]'
            if not isinstance(e, dict) or set(e) - {'from', 'to', 'level'}:
                self.err(where, 'must be {"from": <container_app>, "to": <component>, "level": …}')
                continue
            # a JSON list or object is unhashable: look names up only when they are text
            src = self.components.get(e['from']) if isinstance(e.get('from'), str) else None
            dst = self.components.get(e['to']) if isinstance(e.get('to'), str) else None
            if src is None or src['type'] != 'container_app':
                self.err(where, f'from {json.dumps(e.get("from"))} must be the name of a container_app in components')
                continue
            if dst is None:
                self.err(where, f'to {json.dumps(e.get("to"))} is not a component name')
                continue
            if dst['type'] == 'container_app':
                self.err(where, 'apps reach each other over their HTTPS address; an access edge goes to postgres, '
                                'object_storage or a secret')
                continue
            spec = self.catalog['components'][dst['type']]
            level = e.get('level', spec.get('default_level'))
            if level not in spec['levels']:
                self.err(where, f'level for {dst["type"]} must be one of: {", ".join(spec["levels"])}')
                continue
            if (src['name'], dst['name']) in seen:
                self.err(where, f'{src["name"]} → {dst["name"]} is listed twice')
                continue
            seen.add((src['name'], dst['name']))
            self.access.append({'from': src['name'], 'to': dst['name'], 'level': level, 'type': dst['type']})

    def _placeholders(self, phs) -> None:
        if not isinstance(phs, dict):
            self.err('placeholders', 'must be an object of name → description')
            return
        secrets = {n for n, c in self.components.items() if c['type'] == 'secret'}
        for d in self.components.values():
            if d['type'] == 'postgres' and f'{d["name"]}_password' in secrets:
                self.err(f'components.{d["name"]}_password', f'this name is taken by the generated password of the '
                                                             f'database "{d["name"]}"; rename the secret')
        for n in sorted(secrets & TF_RESERVED_VARS):
            self.err(f'components.{n}', 'Terraform reserves this name for itself; rename the secret')
        for n, d in phs.items():
            if not PH_RE.fullmatch(n) or n in TF_RESERVED_VARS:
                self.err(f'placeholders.{n}', 'names are lowercase letters, digits and "_" (2–41), not a name '
                                              f'Terraform reserves ({", ".join(sorted(TF_RESERVED_VARS))})')
            elif n in secrets:
                self.err(f'placeholders.{n}', 'a secret has this name; a secret is not a placeholder (its value is typed at apply time)')
            elif not isinstance(d, str) or not d.strip():
                self.err(f'placeholders.{n}', 'needs a description: what the user must provide')
            elif n not in self.used_placeholders:
                self.err(f'placeholders.{n}', 'declared but never used; remove it')
            else:
                self.placeholders[n] = d
        for n in sorted(self.used_placeholders - set(phs)):
            self.err(f'placeholders.{n}', 'used but not declared: add "<name>": "<what the user must provide>"')

    # ---- target
    def _target(self, t, cloud: str | None, region: str | None) -> None:
        """The cloud and region: argv wins over the spec; switching cloud without a region maps the old region
        through catalog region_pairs (the nearest region of the other cloud)."""
        if t is not None and (not isinstance(t, dict) or set(t) - {'cloud', 'region'}):
            self.err('target', 'must be {"cloud": "aws"|"azure", "region": "<region>"} or left out')
            t = None
        t = t or {}
        old_cloud, old_region = t.get('cloud'), t.get('region')
        self.cloud = cloud or old_cloud
        self.region = region or (old_region if self.cloud == old_cloud else None)
        clouds = self.catalog['clouds']
        # JSON lists/objects are unhashable: refuse anything that is not text before any lookup
        if self.cloud is not None and not isinstance(self.cloud, str):
            self.err('target.cloud', f'must be text: one of {", ".join(clouds)}')
            self.cloud = None
            return
        if self.region is not None and not isinstance(self.region, str):
            self.err('target.region', 'must be text: a region name (references/catalog.md → Regions)')
            self.region = None
            return
        if self.cloud is not None and self.cloud not in clouds:
            self.err('target.cloud', f'"{self.cloud}" is not a v1 target; v1 targets: {", ".join(clouds)}')
            self.cloud = None
            return
        if self.cloud and not self.region and old_cloud and old_region and old_cloud != self.cloud:
            pairs = self.catalog['region_pairs']
            col = 0 if old_cloud == 'aws' else 1
            self.region = next((p[1 - col] for p in pairs if p[col] == old_region), None)
            if self.region:
                self.notes.append(f'region {self.region} is the {self.cloud} region nearest to {old_region} (catalog region pairs)')
        if self.cloud and self.region is not None and self.region not in clouds[self.cloud]['regions']:
            self.err('target.region', f'"{self.region}" is not a {self.cloud} region; see references/catalog.md → Regions')
            return
        if not self.cloud or not self.region:
            self.need_target = True

    # ---- per-cloud rules
    def _cloud_rules(self) -> None:
        apps = [c for c in self.components.values() if c['type'] == 'container_app']
        match self.cloud:
            case 'aws':
                if len({a['public_https'] for a in apps}) > 1:
                    self.err('components', 'on AWS all container apps of one project share public_https: the first '
                                           'ECS Express service of a network fixes whether its load balancer faces the '
                                           'internet; split them into two projects or give them the same value')
            case 'azure':
                seen = {}
                for c in self.of_type('object_storage'):
                    key = re.sub('[^a-z0-9]', '', c['name'])[:8]
                    if key in seen:
                        self.err(f'components.{c["name"]}', f'on Azure its storage account name would match the one of '
                                                            f'"{seen[key]}" (first 8 letters and digits); rename one')
                    seen[key] = c['name']
                for c in self.components.values():
                    if c['type'] == 'postgres' and c['high_availability'] and not self.catalog['sizes'][c['size']]['azure_ha']:
                        self.err(f'components.{c["name"]}.high_availability',
                                 f'size "{c["size"]}" is Burstable on Azure, which has no high availability; use size '
                                 f'"medium" or set high_availability to false')

    def err(self, where: str, msg: str) -> None:
        """Record one problem as "<where>: <what to do>"; the CLI prints them all at once."""
        self.errors.append(f'{where}: {msg}')

    # ---- helpers for the mappers
    def of_type(self, ctype: str) -> list[dict]:
        """The components of one type, in spec order (so generated files keep the user's order)."""
        return [c for c in self.components.values() if c['type'] == ctype]

    def edges_from(self, app: str, ctype: str | None = None) -> list[dict]:
        """The checked access edges of one app, optionally only those to one component type."""
        return [e for e in self.access if e['from'] == app and (ctype is None or e['type'] == ctype)]

    def saved(self) -> dict:
        """The spec as written to spec.json: the model's spec with the target used, so a rerun regenerates it."""
        out = copy.deepcopy(self.raw)
        out['target'] = {'cloud': self.cloud, 'region': self.region}
        return out


# ------------------------------------------------------------------ HCL writer
class Raw(str):
    """An HCL expression written as is (a reference, a function call, an interpolated string)."""


class Hcl:
    """One HCL block — attributes and nested blocks — rendered exactly as `terraform fmt` writes it.

    Why a writer: the gate runs `terraform fmt -check`, so alignment matters. fmt aligns the "=" of consecutive
    single-line attributes; a nested block or a comment ends the run. Values are Python data (str → quoted,
    Raw → as is, bool, int/float, list, dict) so mappers never build HCL text by hand."""

    def __init__(self, kind: str, *labels: str, comment: str | None = None):
        self.kind, self.labels, self.comment = kind, labels, comment
        self.items: list = []

    def set(self, key: str, value) -> 'Hcl':
        """Add an attribute; returns self so attributes chain."""
        self.items.append(('attr', key, value))
        return self

    def block(self, kind: str, *labels: str) -> 'Hcl':
        """Add a nested block and return it (to fill it)."""
        child = Hcl(kind, *labels)
        self.items.append(('block', child))
        return child

    def object(self, key: str) -> 'Hcl':
        """Add a multi-line object attribute (`key = { … }`, as in required_providers) and return it to fill."""
        child = Hcl(key)
        self.items.append(('object', key, child))
        return child

    def note(self, text: str) -> 'Hcl':
        """A comment line inside the block (ends an alignment run, as in fmt)."""
        self.items.append(('comment', text))
        return self

    @staticmethod
    def value(v) -> str:
        """One value on one line."""
        match v:
            case Raw():
                return str(v)
            case bool():
                return 'true' if v else 'false'
            case int():
                return str(v)
            case float():
                return str(int(v)) if v.is_integer() else repr(v)
            case str():
                esc = v.replace('\\', '\\\\').replace('"', '\\"').replace('${', '$${').replace('%{', '%%{')
                # control characters last, so the backslashes they add are not escaped again; a raw newline in a
                # quoted HCL string is a syntax error
                esc = esc.replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')
                esc = re.sub(r'[\x00-\x1f]', lambda m: f'\\u{ord(m.group()):04x}', esc)
                return f'"{esc}"'
            case list():
                return '[' + ', '.join(Hcl.value(x) for x in v) + ']'
            case dict():
                if not v:
                    return '{}'
                keys = [k if IDENT_RE.match(k) else Hcl.value(k) for k in v]
                return '{ ' + ', '.join(f'{k} = {Hcl.value(x)}' for k, x in zip(keys, v.values())) + ' }'
        raise TypeError(f'no HCL form for {v!r}')

    def render(self, depth: int = 0, head: str | None = None) -> list[str]:
        """The block's lines at this depth (two spaces per level); `head` replaces "kind labels" for objects.

        Attributes are aligned in runs: a run ends before an object() attribute (and at any nested block), so the
        `=` of attributes written after an object() align among themselves, as `terraform fmt` does."""
        pad = '  ' * depth
        head = head or ' '.join([self.kind] + [f'"{lab}"' for lab in self.labels])
        lines = [f'{pad}# {self.comment}'] if self.comment else []
        if not self.items:
            return lines + [f'{pad}{head} {{}}']
        lines.append(f'{pad}{head} {{')
        run: list[tuple[str, str]] = []

        def flush():
            width = max((len(k) for k, _ in run), default=0)
            lines.extend(f'{pad}  {k.ljust(width)} = {v}' for k, v in run)
            run.clear()

        for item in self.items:
            match item:
                case ('attr', key, value):
                    run.append((key, self.value(value)))
                case ('block', child):
                    flush()
                    lines.extend(child.render(depth + 1))
                case ('object', key, child):
                    flush()
                    lines.extend(child.render(depth + 1, head=f'{key} ='))
                case ('comment', text):
                    flush()
                    lines.append(f'{pad}  # {text}')
        flush()
        lines.append(f'{pad}}}')
        return lines


def hcl_file(blocks: list[Hcl]) -> str:
    """Top-level blocks separated by one blank line, ending with a newline (fmt's file layout)."""
    return '\n\n'.join('\n'.join(b.render()) for b in blocks) + '\n'


def ref(text: str) -> Raw:
    """A reference or expression (aws_vpc.main.id, var.x, a function call), written unquoted."""
    return Raw(text)


def interp(text: str) -> Raw:
    """A quoted string with ${...} interpolation, e.g. interp('${aws_s3_bucket.x.arn}/*').

    For internal expressions only: it escapes quotes but not backslashes, so never pass user text through it."""
    return Raw('"' + text.replace('"', '\\"') + '"')


def prefix(text: str, maxlen: int) -> str:
    """A name prefix that ends with "-", fits in maxlen and never has "--" (AWS *_prefix arguments)."""
    text = re.sub('-+', '-', text.replace('_', '-'))
    return text[:maxlen - 1].rstrip('-') + '-'


# ------------------------------------------------------------------ projects
class Project:
    """The Terraform project for one spec on one cloud: every generated file as text.

    Why a base class: the files every target shares (versions, variables, tfvars example, README, .gitignore,
    spec.json) are written once here; AwsProject and AzureProject fill the resource files (`resource_files`) and
    say which provider they pin, the trade-offs they took and the env names they inject."""

    cloud = ''
    providers: tuple[str, ...] = ()

    def __init__(self, spec: Spec):
        self.spec, self.catalog = spec, spec.catalog
        self.base = f'{spec.name}-{spec.environment}'
        self.warnings: list[str] = []
        net = ipaddress.ip_network(spec.cidr)
        self.subnets = [str(s) for s in list(net.subnets(new_prefix=24))[:4]]
        self.newbits = 24 - net.prefixlen

    # ---- shared files
    def files(self) -> dict[str, str]:
        """File name → content, in the order they are listed in the report."""
        out = {'versions.tf': self.versions_tf(), 'providers.tf': hcl_file(self.provider_blocks()),
               'variables.tf': self.variables_tf()}
        for fname, blocks in self.resource_files().items():
            if blocks:
                out[fname] = hcl_file(blocks)
        out['terraform.tfvars.example'] = self.tfvars_example()
        out['README.md'] = self.readme()
        out['.gitignore'] = '.terraform/\n*.tfstate\n*.tfstate.*\n*.tfvars\n!terraform.tfvars.example\ncrash.log\ntfplan\n'
        out['spec.json'] = json.dumps(self.spec.saved(), indent=2, ensure_ascii=False) + '\n'
        return out

    def versions_tf(self) -> str:
        """Terraform >= 1.11 (write-only arguments) and the providers pinned to the majors the lab validated."""
        tf = Hcl('terraform').set('required_version', self.catalog['terraform'])
        req = tf.block('required_providers')
        for p in self.providers:
            pv = self.catalog['providers'][p]
            req.object(p).set('source', pv['source']).set('version', pv['version'])
        return hcl_file([tf])

    def variables_tf(self) -> str:
        """Placeholders become variables without a default (plan stops until they are filled); secrets become
        ephemeral sensitive variables, so their values reach only write-only arguments and never the state."""
        blocks = []
        for n, d in self.spec.placeholders.items():
            blocks.append(Hcl('variable', n).set('description', d).set('type', ref('string')))
        for s in self.spec.of_type('secret'):
            blocks.append(Hcl('variable', s['name'])
                          .set('description', f'Value of the secret "{s["name"]}". Typed at apply time (TF_VAR_{s["name"]}); never stored in state.')
                          .set('type', ref('string')).set('sensitive', True).set('ephemeral', True))
        return hcl_file(blocks) if blocks else '# No input variables: nothing to fill before plan.\n'

    def tfvars_example(self) -> str:
        """The file the user copies to terraform.tfvars: one line per placeholder, its description above it."""
        lines = ['# Copy to terraform.tfvars and fill every value. Secrets are not here: export them as TF_VAR_<name>.']
        for n, d in self.spec.placeholders.items():
            lines += [f'# {" ".join(d.split())}', f'{n} = ""']
        return '\n'.join(lines) + '\n'

    def placeholder_or_text(self, v):
        """A spec value as HCL: {"placeholder": n} → var.n, text → a quoted string."""
        return ref(f'var.{v["placeholder"]}') if isinstance(v, dict) else v

    def readme(self) -> str:
        """The project's README: what to fill and type, the commands, what was generated, the env names the apps
        receive, and the trade-offs — so the chat reply can stay a few lines."""
        s = self.spec
        secrets = [c['name'] for c in s.of_type('secret')]
        lines = [f'# {s.title} — Terraform for {self.cloud_title} ({s.region})', '',
                 f'Generated by the SynergyAI terraform-author skill from `spec.json` (environment `{s.environment}`).',
                 'Rerun the skill to change it: generated files are replaced, other files in this folder are kept.', '',
                 '## Before the first plan', '']
        lines += self.login_lines()
        if s.placeholders:
            lines += ['', 'Fill the placeholders: `cp terraform.tfvars.example terraform.tfvars`, then set:', '']
            lines += [f'- `{n}` — {" ".join(d.split())}' for n, d in s.placeholders.items()]
        if secrets:
            lines += ['', 'Secrets are typed at apply time and never written to state or to a file:', '']
            lines += [f'- `export TF_VAR_{n}=\'…\'`' for n in secrets]
        lines += ['', '## Commands', '', '```sh', f'cd {USER_ROOT}/{s.name}-{self.cloud}',
                  'terraform init', 'terraform validate', 'terraform plan -out tfplan', 'terraform apply tfplan',
                  '```', '', 'Commit `.terraform.lock.hcl` with the project. State is local (`terraform.tfstate`): '
                  'keep it safe, or move it to a remote backend before a team works on it.',
                  self.destroy_line(), '', '## What was generated', '']
        lines += self.summary_lines()
        env = self.injected_env()
        if env:
            lines += ['', '## Environment variables the apps receive', '']
            for app, names in env.items():
                lines.append(f'- `{app}`: ' + ', '.join(f'`{n}`' for n in names))
        if self.warnings or s.left_out:
            lines += ['', '## Trade-offs and what was left out', '']
            lines += [f'- {w}' for w in self.warnings]
            lines += [f'- Left out: {self.catalog["out_of_v1"][x]}' for x in s.left_out]
        return '\n'.join(lines) + '\n'

    # ---- what subclasses provide
    cloud_title = ''

    def destroy_line(self) -> str:
        """How `terraform destroy` meets this cloud's deletion protection (README)."""
        raise NotImplementedError

    def provider_blocks(self) -> list[Hcl]:
        """providers.tf: the provider configured for the spec's region."""
        raise NotImplementedError

    def resource_files(self) -> dict[str, list[Hcl]]:
        """File name → blocks; a file whose list is empty is not written (no app → no app.tf)."""
        raise NotImplementedError

    def login_lines(self) -> list[str]:
        """README prerequisites: Terraform version and how to sign in to this cloud."""
        raise NotImplementedError

    def summary_lines(self) -> list[str]:
        """README "What was generated": one line per component with its secure defaults."""
        raise NotImplementedError

    def injected_env(self) -> dict[str, list[str]]:
        """App → environment variable names its access edges set (README, and the env collision check)."""
        raise NotImplementedError


class AwsProject(Project):
    """AWS (hashicorp/aws ~> 6.0), in the forms of the lab's validated scratch/aws-full/main.tf.

    Network: VPC, 2 public + 2 private subnets over 2 AZs, internet gateway, one NAT gateway; security groups with
    separate rule resources (inline rules referencing each other made the baseline's Cycle). Apps: ECS Express
    (HTTPS load balancer and certificate managed by ECS). Public apps run in the public subnets because Express
    makes an internet-facing load balancer only there."""

    cloud = 'aws'
    cloud_title = 'AWS'
    providers = ('aws',)

    def provider_blocks(self) -> list[Hcl]:
        p = Hcl('provider', 'aws').set('region', self.spec.region)
        p.block('default_tags').set('tags', {**self.spec.tags, 'managed_by': 'terraform'})
        return [p]

    def resource_files(self) -> dict[str, list[Hcl]]:
        return {'network.tf': self.network(), 'storage.tf': self.storage(), 'database.tf': self.database(),
                'secrets.tf': self.secrets(), 'app.tf': self.apps(), 'access.tf': self.access(),
                'outputs.tf': self.outputs()}

    def network(self) -> list[Hcl]:
        """VPC over 2 AZs, public and private subnets, one NAT gateway, one security group per app and database
        (rules are separate resources: inline rules referencing each other made the baseline's Cycle)."""
        b = self.base
        out = [Hcl('data', 'aws_availability_zones', 'available').set('state', 'available'),
               Hcl('locals').set('azs', ref('slice(data.aws_availability_zones.available.names, 0, 2)')),
               Hcl('resource', 'aws_vpc', 'main').set('cidr_block', self.spec.cidr).set('enable_dns_support', True)
               .set('enable_dns_hostnames', True).set('tags', {'Name': f'{b}-vpc'}),
               Hcl('resource', 'aws_subnet', 'public').set('count', 2).set('vpc_id', ref('aws_vpc.main.id'))
               .set('cidr_block', ref(f'cidrsubnet(aws_vpc.main.cidr_block, {self.newbits}, count.index)'))
               .set('availability_zone', ref('local.azs[count.index]')).set('map_public_ip_on_launch', False)
               .set('tags', {'Name': Raw(f'"{b}-public-${{count.index}}"')}),
               Hcl('resource', 'aws_subnet', 'private').set('count', 2).set('vpc_id', ref('aws_vpc.main.id'))
               .set('cidr_block', ref(f'cidrsubnet(aws_vpc.main.cidr_block, {self.newbits}, count.index + 2)'))
               .set('availability_zone', ref('local.azs[count.index]'))
               .set('tags', {'Name': Raw(f'"{b}-private-${{count.index}}"')}),
               Hcl('resource', 'aws_internet_gateway', 'main').set('vpc_id', ref('aws_vpc.main.id')),
               Hcl('resource', 'aws_eip', 'nat').set('domain', 'vpc'),
               Hcl('resource', 'aws_nat_gateway', 'main').set('allocation_id', ref('aws_eip.nat.id'))
               .set('subnet_id', ref('aws_subnet.public[0].id')).set('depends_on', [ref('aws_internet_gateway.main')])]
        pub = Hcl('resource', 'aws_route_table', 'public').set('vpc_id', ref('aws_vpc.main.id'))
        pub.block('route').set('cidr_block', '0.0.0.0/0').set('gateway_id', ref('aws_internet_gateway.main.id'))
        priv = Hcl('resource', 'aws_route_table', 'private').set('vpc_id', ref('aws_vpc.main.id'))
        priv.block('route').set('cidr_block', '0.0.0.0/0').set('nat_gateway_id', ref('aws_nat_gateway.main.id'))
        out += [pub, priv]
        for kind in ('public', 'private'):
            out.append(Hcl('resource', 'aws_route_table_association', kind).set('count', 2)
                       .set('subnet_id', ref(f'aws_subnet.{kind}[count.index].id'))
                       .set('route_table_id', ref(f'aws_route_table.{kind}.id')))
        for a in self.spec.of_type('container_app'):
            n = a['name']
            out.append(Hcl('resource', 'aws_security_group', n, comment=f'App {n}: outbound only; its load balancer is managed by ECS Express.')
                       .set('name_prefix', prefix(f'{b}-{n}', 100)).set('description', f'App {n}')
                       .set('vpc_id', ref('aws_vpc.main.id')))
            out.append(Hcl('resource', 'aws_vpc_security_group_egress_rule', f'{n}_all')
                       .set('security_group_id', ref(f'aws_security_group.{n}.id')).set('ip_protocol', '-1')
                       .set('cidr_ipv4', '0.0.0.0/0'))
        for d in self.spec.of_type('postgres'):
            n = d['name']
            out.append(Hcl('resource', 'aws_security_group', n, comment=f'Database {n}: inbound only from the apps given access (access.tf).')
                       .set('name_prefix', prefix(f'{b}-{n}', 100)).set('description', f'Database {n}')
                       .set('vpc_id', ref('aws_vpc.main.id')))
        return out

    def storage(self) -> list[Hcl]:
        """Per bucket: the four public-access blocks, owner-enforced objects, KMS encryption, versioning if asked."""
        out = []
        for s in self.spec.of_type('object_storage'):
            n = s['name']
            out.append(Hcl('resource', 'aws_s3_bucket', n).set('bucket_prefix', prefix(f'{self.base}-{n}', 37)))
            out.append(Hcl('resource', 'aws_s3_bucket_public_access_block', n).set('bucket', ref(f'aws_s3_bucket.{n}.id'))
                       .set('block_public_acls', True).set('block_public_policy', True).set('ignore_public_acls', True)
                       .set('restrict_public_buckets', True))
            own = Hcl('resource', 'aws_s3_bucket_ownership_controls', n).set('bucket', ref(f'aws_s3_bucket.{n}.id'))
            own.block('rule').set('object_ownership', 'BucketOwnerEnforced')
            sse = Hcl('resource', 'aws_s3_bucket_server_side_encryption_configuration', n).set('bucket', ref(f'aws_s3_bucket.{n}.id'))
            rule = sse.block('rule')
            rule.block('apply_server_side_encryption_by_default').set('sse_algorithm', 'aws:kms')
            rule.set('bucket_key_enabled', True)
            out += [own, sse]
            if s['versioning']:
                v = Hcl('resource', 'aws_s3_bucket_versioning', n).set('bucket', ref(f'aws_s3_bucket.{n}.id'))
                v.block('versioning_configuration').set('status', 'Enabled')
                out.append(v)
        return out

    def database(self) -> list[Hcl]:
        """RDS PostgreSQL in the private subnets: encrypted, private, password managed by RDS, backups as asked,
        deletion protection and a final snapshot."""
        dbs = self.spec.of_type('postgres')
        if not dbs:
            return []
        out = [Hcl('resource', 'aws_db_subnet_group', 'main').set('name_prefix', prefix(f'{self.base}-db', 100))
               .set('subnet_ids', ref('aws_subnet.private[*].id'))]
        for d in dbs:
            n = d['name']
            out.append(Hcl('resource', 'aws_db_instance', n)
                       .set('identifier_prefix', prefix(f'{self.base}-{n}', 37)).set('engine', 'postgres')
                       .set('engine_version', d['version'])
                       .set('instance_class', self.catalog['sizes'][d['size']]['aws'])
                       .set('allocated_storage', d['storage_gb']).set('max_allocated_storage', d['storage_gb'] * 2)
                       .set('storage_type', 'gp3').set('storage_encrypted', True).set('db_name', n)
                       .set('username', 'appadmin').set('manage_master_user_password', True)
                       .set('db_subnet_group_name', ref('aws_db_subnet_group.main.name'))
                       .set('vpc_security_group_ids', [ref(f'aws_security_group.{n}.id')])
                       .set('publicly_accessible', False).set('multi_az', d['high_availability'])
                       .set('backup_retention_period', d['backup_days']).set('deletion_protection', True)
                       .set('skip_final_snapshot', False)
                       .set('final_snapshot_identifier', f'{self.base}-{n.replace("_", "-")}-final')
                       .set('copy_tags_to_snapshot', True))
        return out

    def secrets(self) -> list[Hcl]:
        """Secrets Manager secrets whose value is written once from the ephemeral variable (secret_string_wo)."""
        out = []
        for s in self.spec.of_type('secret'):
            n = s['name']
            out.append(Hcl('resource', 'aws_secretsmanager_secret', n)
                       .set('name_prefix', f'{self.base}/{n.replace("_", "-")}-').set('recovery_window_in_days', 7))
            out.append(Hcl('resource', 'aws_secretsmanager_secret_version', n)
                       .set('secret_id', ref(f'aws_secretsmanager_secret.{n}.id'))
                       .set('secret_string_wo', ref(f'var.{n}')).set('secret_string_wo_version', 1))
        return out

    def apps(self) -> list[Hcl]:
        """ECS Express services: roles, log group, container with its env and secrets, scaling; public apps in the
        public subnets (internet-facing load balancer), others in the private ones (internal load balancer)."""
        apps = self.spec.of_type('container_app')
        if not apps:
            return []
        public = apps[0]['public_https']
        out = []
        for doc, svc in (('ecs_tasks_assume', 'ecs-tasks.amazonaws.com'), ('ecs_infra_assume', 'ecs.amazonaws.com')):
            d = Hcl('data', 'aws_iam_policy_document', doc)
            st = d.block('statement').set('actions', ['sts:AssumeRole'])
            st.block('principals').set('type', 'Service').set('identifiers', [svc])
            out.append(d)
        out.append(Hcl('resource', 'aws_iam_role', 'infrastructure').set('name_prefix', prefix(f'{self.base}-infra', 38))
                   .set('assume_role_policy', ref('data.aws_iam_policy_document.ecs_infra_assume.json')))
        out.append(Hcl('resource', 'aws_iam_role_policy_attachment', 'infrastructure')
                   .set('role', ref('aws_iam_role.infrastructure.name'))
                   .set('policy_arn', 'arn:aws:iam::aws:policy/service-role/AmazonECSInfrastructureRoleforExpressGatewayServices'))
        out.append(Hcl('resource', 'aws_ecs_cluster', 'main').set('name', self.base))
        for a in apps:
            n = a['name']
            out.append(Hcl('resource', 'aws_iam_role', f'{n}_task', comment=f'App {n}: its own identity (task role) and the role ECS uses to start it (execution role).')
                       .set('name_prefix', prefix(f'{self.base}-{n}-task', 38))
                       .set('assume_role_policy', ref('data.aws_iam_policy_document.ecs_tasks_assume.json')))
            out.append(Hcl('resource', 'aws_iam_role', f'{n}_execution').set('name_prefix', prefix(f'{self.base}-{n}-exec', 38))
                       .set('assume_role_policy', ref('data.aws_iam_policy_document.ecs_tasks_assume.json')))
            out.append(Hcl('resource', 'aws_iam_role_policy_attachment', f'{n}_execution')
                       .set('role', ref(f'aws_iam_role.{n}_execution.name'))
                       .set('policy_arn', 'arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy'))
            out.append(Hcl('resource', 'aws_cloudwatch_log_group', n).set('name', f'/ecs/{self.base}/{n}')
                       .set('retention_in_days', 30))
            svc = Hcl('resource', 'aws_ecs_express_gateway_service', n).set('service_name', f'{self.base}-{n.replace("_", "-")}') \
                .set('cluster', ref('aws_ecs_cluster.main.name')) \
                .set('execution_role_arn', ref(f'aws_iam_role.{n}_execution.arn')) \
                .set('infrastructure_role_arn', ref('aws_iam_role.infrastructure.arn')) \
                .set('task_role_arn', ref(f'aws_iam_role.{n}_task.arn')) \
                .set('cpu', str(int(a['cpu'] * 1024))).set('memory', str(int(a['memory_gb'] * 1024)))
            c = svc.block('primary_container').set('image', self.placeholder_or_text(a['image'])).set('container_port', a['port'])
            c.block('aws_logs_configuration').set('log_group', ref(f'aws_cloudwatch_log_group.{n}.name'))
            for k, v in self.env_values(a).items():
                c.block('environment').set('name', k).set('value', v)
            for k, v in self.secret_values(a).items():
                c.block('secret').set('name', k).set('value_from', v)
            svc.note('Public subnets: internet-facing HTTPS load balancer, tasks get public IPs (inbound only from it).' if public
                     else 'Private subnets: internal HTTPS load balancer, reachable only from inside the network.')
            svc.block('network_configuration').set('subnets', ref(f'aws_subnet.{"public" if public else "private"}[*].id')) \
                .set('security_groups', [ref(f'aws_security_group.{n}.id')])
            svc.block('scaling_target').set('min_task_count', a['min_instances']).set('max_task_count', a['max_instances'])
            deps = [ref(f'aws_iam_role_policy_attachment.{n}_execution'), ref('aws_iam_role_policy_attachment.infrastructure')]
            if self.secret_values(a):
                deps.insert(0, ref(f'aws_iam_role_policy.{n}_execution'))
            svc.set('depends_on', deps)
            out.append(svc)
        if public:
            self.warnings.append('The apps run in public subnets: ECS Express creates an internet-facing load balancer '
                                 'only there. Their security group admits nothing directly; the database, storage and '
                                 'secrets stay private.')
        return out

    def env_values(self, a: dict) -> dict:
        """The app's own env, then what its access edges inject (names in the README)."""
        return {**{k: self.placeholder_or_text(v) for k, v in a['env'].items()}, **self.edge_env(a)}

    def edge_env(self, a: dict) -> dict:
        """Plain environment variables the app's edges set: bucket name, database host, port and name."""
        env = {}
        for e in self.spec.edges_from(a['name']):
            t, T = e['to'], e['to'].upper()
            match e['type']:
                case 'object_storage':
                    env[f'{T}_BUCKET'] = ref(f'aws_s3_bucket.{t}.bucket')
                case 'postgres':
                    env[f'{T}_HOST'] = ref(f'aws_db_instance.{t}.address')
                    env[f'{T}_PORT'] = '5432'
                    env[f'{T}_NAME'] = t
        return env

    def secret_values(self, a: dict) -> dict:
        """Variables ECS reads from Secrets Manager at start: each secret, and the database user and password
        from the RDS-managed secret (JSON keys)."""
        out = {}
        for e in self.spec.edges_from(a['name']):
            t, T = e['to'], e['to'].upper()
            match e['type']:
                case 'secret':
                    out[T] = ref(f'aws_secretsmanager_secret.{t}.arn')
                case 'postgres':
                    arn = f'aws_db_instance.{t}.master_user_secret[0].secret_arn'
                    out[f'{T}_USER'] = interp(f'${{{arn}}}:username::')
                    out[f'{T}_PASSWORD'] = interp(f'${{{arn}}}:password::')
        return out

    def access(self) -> list[Hcl]:
        """Least privilege per edge: task-role statements on each bucket, execution-role read of each secret, a
        security-group rule app → database:5432."""
        out = []
        roles = self.catalog['roles']['aws']
        for a in self.spec.of_type('container_app'):
            n = a['name']
            stores = self.spec.edges_from(n, 'object_storage')
            if stores:
                doc = Hcl('data', 'aws_iam_policy_document', f'{n}_task')
                for e in stores:
                    r = roles[f'object_storage.{e["level"]}']
                    doc.block('statement').set('sid', f'{e["to"].replace("_", "").capitalize()}Objects') \
                        .set('actions', r['objects']).set('resources', [interp(f'${{aws_s3_bucket.{e["to"]}.arn}}/*')])
                    doc.block('statement').set('sid', f'{e["to"].replace("_", "").capitalize()}List') \
                        .set('actions', r['bucket']).set('resources', [ref(f'aws_s3_bucket.{e["to"]}.arn')])
                out.append(doc)
                out.append(Hcl('resource', 'aws_iam_role_policy', f'{n}_task').set('role', ref(f'aws_iam_role.{n}_task.id'))
                           .set('policy', ref(f'data.aws_iam_policy_document.{n}_task.json')))
            arns = []
            for e in self.spec.edges_from(n):
                match e['type']:
                    case 'secret':
                        arns.append(ref(f'aws_secretsmanager_secret.{e["to"]}.arn'))
                    case 'postgres':
                        arns.append(ref(f'aws_db_instance.{e["to"]}.master_user_secret[0].secret_arn'))
            if arns:
                doc = Hcl('data', 'aws_iam_policy_document', f'{n}_execution', comment=f'ECS reads these secrets when it starts {n}.')
                doc.block('statement').set('actions', roles['secret.read']['secret']).set('resources', arns)
                out.append(doc)
                out.append(Hcl('resource', 'aws_iam_role_policy', f'{n}_execution')
                           .set('role', ref(f'aws_iam_role.{n}_execution.id'))
                           .set('policy', ref(f'data.aws_iam_policy_document.{n}_execution.json')))
            for e in self.spec.edges_from(n, 'postgres'):
                out.append(Hcl('resource', 'aws_vpc_security_group_ingress_rule', f'{e["to"]}_from_{n}')
                           .set('security_group_id', ref(f'aws_security_group.{e["to"]}.id'))
                           .set('referenced_security_group_id', ref(f'aws_security_group.{n}.id'))
                           .set('ip_protocol', 'tcp').set('from_port', 5432).set('to_port', 5432))
        return out

    def outputs(self) -> list[Hcl]:
        """Addresses and names the user needs after apply; no output carries a secret value."""
        out = []
        for a in self.spec.of_type('container_app'):
            out.append(Hcl('output', f'{a["name"]}_url').set('description', f'HTTPS address of {a["name"]} (ECS Express ingress paths).')
                       .set('value', ref(f'aws_ecs_express_gateway_service.{a["name"]}.ingress_paths')))
        for s in self.spec.of_type('object_storage'):
            out.append(Hcl('output', f'{s["name"]}_bucket').set('value', ref(f'aws_s3_bucket.{s["name"]}.bucket')))
        for d in self.spec.of_type('postgres'):
            out.append(Hcl('output', f'{d["name"]}_endpoint').set('value', ref(f'aws_db_instance.{d["name"]}.address')))
            out.append(Hcl('output', f'{d["name"]}_master_secret_arn')
                       .set('description', 'Secrets Manager secret holding the database admin user and password (managed by RDS).')
                       .set('value', ref(f'aws_db_instance.{d["name"]}.master_user_secret[0].secret_arn')))
        for s in self.spec.of_type('secret'):
            out.append(Hcl('output', f'{s["name"]}_arn').set('value', ref(f'aws_secretsmanager_secret.{s["name"]}.arn')))
        return out

    def destroy_line(self) -> str:
        return ('`terraform destroy` is refused while a database has `deletion_protection = true`: set it to false and '
                'apply first. A final snapshot is kept; secrets stay recoverable for 7 days.')

    def login_lines(self) -> list[str]:
        return ['- Terraform 1.11 or later (write-only secret arguments need it).',
                '- AWS credentials for the target account: `aws configure` or `aws sso login` (or `AWS_PROFILE`).']

    def summary_lines(self) -> list[str]:
        s = self.spec
        lines = [f'- Network {s.cidr}: 2 public + 2 private subnets over 2 availability zones, one NAT gateway.']
        for a in s.of_type('container_app'):
            lines.append(f'- `{a["name"]}`: ECS Express service ({a["cpu"]:g} vCPU, {a["memory_gb"]:g} GB, '
                         f'{a["min_instances"]}–{a["max_instances"]} tasks, port {a["port"]}), '
                         f'{"public" if a["public_https"] else "internal"} HTTPS address; the load balancer checks '
                         f'GET / (it must answer 200); logs kept 30 days.')
        for d in s.of_type('postgres'):
            lines.append(f'- `{d["name"]}`: RDS PostgreSQL {d["version"]} ({self.catalog["sizes"][d["size"]]["aws"]}, '
                         f'{d["storage_gb"]} GB gp3, encrypted, private, backups {d["backup_days"]} days'
                         f'{", Multi-AZ" if d["high_availability"] else ""}, deletion protection on; admin password '
                         f'managed by RDS in Secrets Manager).')
        for o in s.of_type('object_storage'):
            lines.append(f'- `{o["name"]}`: S3 bucket, all public access blocked, encrypted (KMS)'
                         f'{", versioned" if o["versioning"] else ""}.')
        for x in s.of_type('secret'):
            lines.append(f'- `{x["name"]}`: Secrets Manager secret (value written once from `TF_VAR_{x["name"]}`, 7-day recovery window).')
        return lines

    def injected_env(self) -> dict[str, list[str]]:
        return {a['name']: list(self.edge_env(a)) + list(self.secret_values(a)) for a in self.spec.of_type('container_app')
                if self.edge_env(a) or self.secret_values(a)}


def storage_account_prefix(project: str, comp: str) -> str:
    """st + 8 letters/digits of the project + 8 of the component: with the 6-character random suffix it fits the
    24-character limit of a storage account name (lowercase letters and digits only, globally unique)."""
    return 'st' + re.sub('[^a-z0-9]', '', project)[:8] + re.sub('[^a-z0-9]', '', comp)[:8]


class AzureProject(Project):
    """Azure (hashicorp/azurerm ~> 5.0), in the forms of the lab's validated scratch/azure-full/main.tf.

    One resource group; a VNet with only the subnets the components need (apps delegated to Microsoft.App,
    databases delegated to PostgreSQL, private endpoints), each with default outbound access off and its NSG; the
    NAT gateway on the apps subnet. Globally unique names get a random suffix. azurerm 5 registers no resource
    provider by itself, so the provider block lists the ones the emitted resources need."""

    cloud = 'azure'
    cloud_title = 'Azure'
    providers = ('azurerm', 'random')

    def __init__(self, spec: Spec):
        super().__init__(spec)
        self.zones = self.catalog['clouds']['azure']['regions'][spec.region]
        self.apps_list = spec.of_type('container_app')
        self.dbs = spec.of_type('postgres')
        self.stores = spec.of_type('object_storage')
        self.has_vault = bool(spec.of_type('secret') or self.dbs)
        self.any_public = any(a['public_https'] for a in self.apps_list)

    def common(self) -> list[Hcl]:
        """Deployer identity, the random suffix for global names, tags (Azure has no provider default tags), the
        resource group."""
        return [Hcl('data', 'azurerm_client_config', 'current'),
                Hcl('resource', 'random_string', 'suffix', comment='Storage accounts, key vaults and database servers need globally unique names.')
                .set('length', 6).set('upper', False).set('special', False),
                Hcl('locals').set('tags', {**self.spec.tags, 'managed_by': 'terraform'}),
                Hcl('resource', 'azurerm_resource_group', 'main').set('name', f'rg-{self.base}')
                .set('location', self.spec.region).set('tags', ref('local.tags'))]

    def provider_blocks(self) -> list[Hcl]:
        rps = set(self.catalog['azure_resource_providers']['network'])
        for c in self.spec.components.values():
            rps.update(self.catalog['azure_resource_providers'][c['type']])
        p = Hcl('provider', 'azurerm', comment='azurerm 5 registers no resource provider by itself: only the ones this project uses.')
        p.set('resource_providers_to_register', sorted(rps)).set('storage_use_azuread', True)
        p.block('features')
        return [p]

    def resource_files(self) -> dict[str, list[Hcl]]:
        return {'network.tf': self.common() + self.network(), 'storage.tf': self.storage(),
                'database.tf': self.database(), 'secrets.tf': self.secrets(), 'app.tf': self.apps(),
                'access.tf': self.access(), 'outputs.tf': self.outputs()}

    def rg(self, block: Hcl, location: bool = True) -> Hcl:
        """Add location and resource group (every Azure resource needs them; some take no location)."""
        if location:
            block.set('location', ref('azurerm_resource_group.main.location'))
        return block.set('resource_group_name', ref('azurerm_resource_group.main.name'))

    def subnet(self, label: str, cidr: str, delegation: tuple[str, str] | None) -> list[Hcl]:
        """A subnet with default outbound access off (azurerm still defaults it on), its delegation and its NSG."""
        s = self.rg(Hcl('resource', 'azurerm_subnet', label).set('name', f'snet-{label}'), location=False) \
            .set('virtual_network_name', ref('azurerm_virtual_network.main.name')).set('address_prefixes', [cidr]) \
            .set('default_outbound_access_enabled', False)
        if delegation:
            d = s.block('delegation').set('name', delegation[0])
            d.block('service_delegation').set('name', delegation[1]) \
                .set('actions', ['Microsoft.Network/virtualNetworks/subnets/join/action'])
        nsg = self.rg(Hcl('resource', 'azurerm_network_security_group', label).set('name', f'nsg-{self.base}-{label}'))
        for rule in self.nsg_rules(label):
            r = nsg.block('security_rule')
            for k, v in rule.items():
                r.set(k, v)
        nsg.set('tags', ref('local.tags'))
        assoc = Hcl('resource', 'azurerm_subnet_network_security_group_association', label) \
            .set('subnet_id', ref(f'azurerm_subnet.{label}.id')) \
            .set('network_security_group_id', ref(f'azurerm_network_security_group.{label}.id'))
        return [s, nsg, assoc]

    def nsg_rules(self, label: str) -> list[dict]:
        """Inbound rules per subnet. Apps: HTTPS from the internet when an app is public. Databases: 5432 from the
        apps subnet when an app has access, from the database subnet itself (HA replication), nothing else."""
        rule = {'source_port_range': '*', 'destination_address_prefix': '*', 'protocol': 'Tcp', 'direction': 'Inbound'}
        match label:
            case 'apps' if self.any_public:
                return [{'name': 'allow-https-from-internet', 'priority': 100, **rule, 'access': 'Allow',
                         'destination_port_range': '443', 'source_address_prefix': 'Internet'}]
            case 'db':
                rules = []
                if any(e['type'] == 'postgres' for e in self.spec.access):
                    rules.append({'name': 'allow-pg-from-apps', 'priority': 100, **rule, 'access': 'Allow',
                                  'destination_port_range': '5432', 'source_address_prefix': self.subnets[0]})
                rules.append({'name': 'allow-pg-within-db-subnet', 'priority': 110, **rule, 'access': 'Allow',
                              'destination_port_range': '5432', 'source_address_prefix': self.subnets[1]})
                rules.append({'name': 'deny-pg-from-vnet', 'priority': 4000, **rule, 'access': 'Deny',
                              'destination_port_range': '5432', 'source_address_prefix': 'VirtualNetwork'})
                return rules
        return []

    def network(self) -> list[Hcl]:
        """The VNet and only the subnets the components need; the NAT gateway gives the apps outbound access."""
        out = [self.rg(Hcl('resource', 'azurerm_virtual_network', 'main').set('name', f'vnet-{self.base}'))
               .set('address_space', [self.spec.cidr]).set('tags', ref('local.tags'))]
        if self.apps_list:
            out += self.subnet('apps', self.subnets[0], ('aca', 'Microsoft.App/environments'))
            out.append(self.rg(Hcl('resource', 'azurerm_public_ip', 'nat', comment='Outbound internet for the apps (image pulls) goes through one NAT gateway.')
                               .set('name', f'pip-{self.base}-nat')).set('allocation_method', 'Static').set('sku', 'Standard')
                       .set('tags', ref('local.tags')))
            out.append(self.rg(Hcl('resource', 'azurerm_nat_gateway', 'main').set('name', f'ng-{self.base}'))
                       .set('sku_name', 'Standard').set('tags', ref('local.tags')))
            out.append(Hcl('resource', 'azurerm_nat_gateway_public_ip_association', 'main')
                       .set('nat_gateway_id', ref('azurerm_nat_gateway.main.id')).set('public_ip_address_id', ref('azurerm_public_ip.nat.id')))
            out.append(Hcl('resource', 'azurerm_subnet_nat_gateway_association', 'apps')
                       .set('subnet_id', ref('azurerm_subnet.apps.id')).set('nat_gateway_id', ref('azurerm_nat_gateway.main.id')))
        if self.dbs:
            out += self.subnet('db', self.subnets[1], ('pgflex', 'Microsoft.DBforPostgreSQL/flexibleServers'))
        if self.stores:
            out += self.subnet('endpoints', self.subnets[2], None)
        return out

    def storage(self) -> list[Hcl]:
        """Per storage: a keyless, TLS 1.2, network-closed account with one private container and a private
        endpoint (blob DNS zone shared)."""
        if not self.stores:
            return []
        out = [self.rg(Hcl('resource', 'azurerm_private_dns_zone', 'blob').set('name', 'privatelink.blob.core.windows.net'), location=False)
               .set('tags', ref('local.tags')),
               Hcl('resource', 'azurerm_private_dns_zone_virtual_network_link', 'blob').set('name', 'blob-link')
               .set('private_dns_zone_id', ref('azurerm_private_dns_zone.blob.id'))
               .set('virtual_network_id', ref('azurerm_virtual_network.main.id'))]
        for s in self.stores:
            n = s['name']
            short = storage_account_prefix(self.spec.name, n)
            acct = self.rg(Hcl('resource', 'azurerm_storage_account', n).set('name', interp(f'{short}${{random_string.suffix.result}}'))) \
                .set('account_tier', 'Standard').set('account_replication_type', 'ZRS' if self.zones else 'LRS') \
                .set('min_tls_version', 'TLS1_2').set('https_traffic_only_enabled', True) \
                .set('allow_nested_items_to_be_public', False).set('shared_access_key_enabled', False) \
                .set('default_to_oauth_authentication', True).set('public_network_access', 'Disabled')
            bp = acct.block('blob_properties')
            if s['versioning']:
                bp.set('versioning_enabled', True)
            bp.block('delete_retention_policy').set('days', 7)
            acct.set('tags', ref('local.tags'))
            acct.block('lifecycle').set('prevent_destroy', True)
            out.append(acct)
            out.append(Hcl('resource', 'azurerm_storage_container', n, comment='Created through the management API (storage_account_id), so Terraform works from outside the network.')
                       .set('name', n.replace('_', '-')).set('storage_account_id', ref(f'azurerm_storage_account.{n}.id'))
                       .set('container_access_type', 'private'))
            pe = self.rg(Hcl('resource', 'azurerm_private_endpoint', n).set('name', f'pe-{self.base}-{n.replace("_", "-")}-blob')) \
                .set('subnet_id', ref('azurerm_subnet.endpoints.id'))
            pe.block('private_service_connection').set('name', 'blob') \
                .set('private_connection_resource_id', ref(f'azurerm_storage_account.{n}.id')) \
                .set('subresource_names', ['blob']).set('is_manual_connection', False)
            pe.block('private_dns_zone_group').set('name', 'blob').set('private_dns_zone_ids', [ref('azurerm_private_dns_zone.blob.id')])
            pe.set('tags', ref('local.tags'))
            out.append(pe)
        return out

    def database(self) -> list[Hcl]:
        """Per database: private DNS zone + link, an ephemeral admin password written only to the server and the key
        vault (write-only arguments), the flexible server in the delegated subnet, its database."""
        out = []
        for d in self.dbs:
            n, h = d['name'], d['name'].replace('_', '-')
            out.append(self.rg(Hcl('resource', 'azurerm_private_dns_zone', n).set('name', f'{self.spec.name}-{h}.private.postgres.database.azure.com'), location=False)
                       .set('tags', ref('local.tags')))
            out.append(Hcl('resource', 'azurerm_private_dns_zone_virtual_network_link', n).set('name', f'{h}-link')
                       .set('private_dns_zone_id', ref(f'azurerm_private_dns_zone.{n}.id'))
                       .set('virtual_network_id', ref('azurerm_virtual_network.main.id'))
                       .set('depends_on', [ref('azurerm_subnet.db')]))
            out.append(Hcl('ephemeral', 'random_password', n, comment=f'Admin password of {n}: generated at apply time, written only to the server and the key vault, never to state.')
                       .set('length', 32).set('special', True).set('override_special', '-_.~'))
            srv = self.rg(Hcl('resource', 'azurerm_postgresql_flexible_server', n)
                          .set('name', interp(f'{prefix(f"psql-{self.base}-{h}", 56)}${{random_string.suffix.result}}'))) \
                .set('version', d['version']).set('sku_name', self.catalog['sizes'][d['size']]['azure']) \
                .set('storage_mb', d['storage_gb'] * 1024).set('auto_grow_enabled', True) \
                .set('backup_retention_days', d['backup_days']) \
                .set('delegated_subnet_id', ref('azurerm_subnet.db.id')) \
                .set('private_dns_zone_id', ref(f'azurerm_private_dns_zone.{n}.id')) \
                .set('public_network_access_enabled', False).set('administrator_login', 'appadmin') \
                .set('administrator_password_wo', ref(f'ephemeral.random_password.{n}.result')) \
                .set('administrator_password_wo_version', 1)
            if self.zones:
                srv.set('zone', '1')
            srv.block('authentication').set('active_directory_auth_enabled', False).set('password_auth_enabled', True)
            if d['high_availability']:
                srv.block('high_availability').set('mode', 'ZoneRedundant' if self.zones else 'SameZone')
            # The vault secret is written first: if it fails (role assignment still propagating) no server is left
            # behind holding a password the vault does not have. The secret depends only on the role assignment.
            srv.set('tags', ref('local.tags')).set('depends_on', [ref(f'azurerm_private_dns_zone_virtual_network_link.{n}'),
                                                                  ref(f'azurerm_key_vault_secret.{n}_password')])
            srv.block('lifecycle').set('prevent_destroy', True)
            out.append(srv)
            out.append(Hcl('resource', 'azurerm_postgresql_flexible_server_database', n).set('name', n)
                       .set('server_id', ref(f'azurerm_postgresql_flexible_server.{n}.id')))
            out.append(Hcl('resource', 'azurerm_key_vault_secret', f'{n}_password', comment='The same password, for the apps given access to the database.')
                       .set('name', f'{h}-admin-password').set('key_vault_id', ref('azurerm_key_vault.main.id'))
                       .set('value_wo', ref(f'ephemeral.random_password.{n}.result')).set('value_wo_version', 1)
                       .set('depends_on', [ref('azurerm_role_assignment.deployer_secrets')]))
        if self.dbs:
            self.warnings.append('The database password is written to the server and to the key vault by the same apply. '
                                 'After any partial or failed apply, raise `administrator_password_wo_version` of the server '
                                 'and `value_wo_version` of its `<db>_password` key vault secret together (to the same new '
                                 'number) so both receive the new password. The first apply may need one retry while the '
                                 'key vault role assignment propagates.')
        if self.dbs and any(d['high_availability'] and not self.zones for d in self.dbs):
            self.warnings.append(f'{self.spec.region} has no availability zones: the database standby is in the same zone.')
        return out

    def secrets(self) -> list[Hcl]:
        """One key vault for all secrets and database passwords; the deployer gets Secrets Officer to write them."""
        if not self.has_vault:
            return []
        short = re.sub('-+', '-', f'kv-{self.spec.name}')[:17].rstrip('-')
        kv = self.rg(Hcl('resource', 'azurerm_key_vault', 'main', comment='Public network access with role-based access only: Terraform writes the values through the vault API.')
                     .set('name', interp(f'{short}-${{random_string.suffix.result}}'))) \
            .set('tenant_id', ref('data.azurerm_client_config.current.tenant_id')).set('sku_name', 'standard') \
            .set('rbac_authorization_enabled', True).set('purge_protection_enabled', True) \
            .set('soft_delete_retention_days', 7).set('tags', ref('local.tags'))
        out = [kv, Hcl('resource', 'azurerm_role_assignment', 'deployer_secrets', comment='The identity running Terraform writes the secret values.')
               .set('scope', ref('azurerm_key_vault.main.id'))
               .set('role_definition_name', self.catalog['roles']['azure']['deployer.secrets'])
               .set('principal_id', ref('data.azurerm_client_config.current.object_id'))]
        for s in self.spec.of_type('secret'):
            n = s['name']
            out.append(Hcl('resource', 'azurerm_key_vault_secret', n).set('name', n.replace('_', '-'))
                       .set('key_vault_id', ref('azurerm_key_vault.main.id')).set('value_wo', ref(f'var.{n}'))
                       .set('value_wo_version', 1).set('depends_on', [ref('azurerm_role_assignment.deployer_secrets')]))
        self.warnings.append('The key vault keeps public network access (role-based access only): Terraform writes '
                             'secret values through its API.')
        return out

    def vault_secrets_of(self, app: str) -> list[tuple[str, str, str]]:
        """(env name, container-app secret name, Terraform label of the key vault secret) the app reads."""
        out = []
        for e in self.spec.edges_from(app):
            match e['type']:
                case 'secret':
                    out.append((e['to'].upper(), e['to'].replace('_', '-'), e['to']))
                case 'postgres':
                    out.append((f'{e["to"].upper()}_PASSWORD', f'{e["to"].replace("_", "-")}-password', f'{e["to"]}_password'))
        return out

    def apps(self) -> list[Hcl]:
        """Log Analytics, one Container Apps environment in the apps subnet (internal when no app is public), and
        per app its identity and container app with HTTPS ingress, env and key vault secrets."""
        if not self.apps_list:
            return []
        out = [self.rg(Hcl('resource', 'azurerm_log_analytics_workspace', 'main').set('name', f'log-{self.base}'))
               .set('sku', 'PerGB2018').set('retention_in_days', 30).set('tags', ref('local.tags'))]
        env = self.rg(Hcl('resource', 'azurerm_container_app_environment', 'main').set('name', f'cae-{self.base}')) \
            .set('infrastructure_subnet_id', ref('azurerm_subnet.apps.id')) \
            .set('internal_load_balancer_enabled', not self.any_public) \
            .set('logs_destination', 'log-analytics') \
            .set('log_analytics_workspace_id', ref('azurerm_log_analytics_workspace.main.id'))
        env.block('workload_profile').set('name', 'Consumption').set('workload_profile_type', 'Consumption')
        env.set('tags', ref('local.tags')).set('depends_on', [ref('azurerm_subnet_nat_gateway_association.apps')])
        out.append(env)
        for a in self.apps_list:
            n = a['name']
            out.append(self.rg(Hcl('resource', 'azurerm_user_assigned_identity', n).set('name', f'id-{self.base}-{n.replace("_", "-")}'))
                       .set('tags', ref('local.tags')))
            app = self.rg(Hcl('resource', 'azurerm_container_app', n).set('name', f'ca-{n.replace("_", "-")}'), location=False) \
                .set('container_app_environment_id', ref('azurerm_container_app_environment.main.id')) \
                .set('revision_mode', 'Single').set('workload_profile_name', 'Consumption')
            app.block('identity').set('type', 'UserAssigned').set('identity_ids', [ref(f'azurerm_user_assigned_identity.{n}.id')])
            for _, sname, label in self.vault_secrets_of(n):
                app.block('secret').set('name', sname).set('key_vault_secret_id', ref(f'azurerm_key_vault_secret.{label}.versionless_id')) \
                    .set('identity', ref(f'azurerm_user_assigned_identity.{n}.id'))
            ing = app.block('ingress').set('external_enabled', a['public_https'] or not self.any_public) \
                .set('target_port', a['port']).set('allow_insecure_connections', False)
            ing.block('traffic_weight').set('latest_revision', True).set('percentage', 100)
            tpl = app.block('template').set('min_replicas', a['min_instances']).set('max_replicas', a['max_instances'])
            c = tpl.block('container').set('name', n.replace('_', '-')).set('image', self.placeholder_or_text(a['image'])) \
                .set('cpu', a['cpu']).set('memory', f'{a["memory_gb"]:g}Gi')
            for k, v in self.env_values(a).items():
                c.block('env').set('name', k).set('value', v)
            for k, sname, _ in self.vault_secrets_of(n):
                c.block('env').set('name', k).set('secret_name', sname)
            app.set('tags', ref('local.tags'))
            deps = [ref(f'azurerm_role_assignment.{n}_{e["to"]}') for e in self.spec.edges_from(n) if e['type'] in ('secret', 'postgres')]
            if deps:
                app.set('depends_on', deps)
            out.append(app)
        if self.any_public and not all(a['public_https'] for a in self.apps_list):
            self.warnings.append('Apps without public_https are reachable only from the other apps of the environment '
                                 '(the environment faces the internet because another app is public).')
        return out

    def env_values(self, a: dict) -> dict:
        """The app's own env, then what the platform and its access edges inject (names in the README)."""
        return {**{k: self.placeholder_or_text(v) for k, v in a['env'].items()}, **self.edge_env(a)}

    def edge_env(self, a: dict) -> dict:
        """Plain environment variables: the app's identity client id, blob endpoint and container, database host,
        port, name and user."""
        env = {}
        env['AZURE_CLIENT_ID'] = ref(f'azurerm_user_assigned_identity.{a["name"]}.client_id')
        for e in self.spec.edges_from(a['name']):
            t, T = e['to'], e['to'].upper()
            match e['type']:
                case 'object_storage':
                    env[f'{T}_BLOB_ENDPOINT'] = ref(f'azurerm_storage_account.{t}.primary_blob_endpoint')
                    env[f'{T}_CONTAINER'] = ref(f'azurerm_storage_container.{t}.name')
                case 'postgres':
                    env[f'{T}_HOST'] = ref(f'azurerm_postgresql_flexible_server.{t}.fqdn')
                    env[f'{T}_PORT'] = '5432'
                    env[f'{T}_NAME'] = t
                    env[f'{T}_USER'] = 'appadmin'
        return env

    def access(self) -> list[Hcl]:
        """One role assignment per edge, scoped to the storage account or to the one key vault secret."""
        out = []
        roles = self.catalog['roles']['azure']
        for e in self.spec.access:
            a, t = e['from'], e['to']
            match e['type']:
                case 'object_storage':
                    scope, role = ref(f'azurerm_storage_account.{t}.id'), roles[f'object_storage.{e["level"]}']
                case 'secret':
                    scope, role = ref(f'azurerm_key_vault_secret.{t}.resource_versionless_id'), roles['secret.read']
                case 'postgres':
                    scope, role = ref(f'azurerm_key_vault_secret.{t}_password.resource_versionless_id'), roles['secret.read']
            out.append(Hcl('resource', 'azurerm_role_assignment', f'{a}_{t}').set('scope', scope)
                       .set('role_definition_name', role)
                       .set('principal_id', ref(f'azurerm_user_assigned_identity.{a}.principal_id'))
                       .set('principal_type', 'ServicePrincipal'))
        return out

    def outputs(self) -> list[Hcl]:
        """Resource group, app addresses, storage accounts, database hosts, key vault URI; no secret values."""
        out = [Hcl('output', 'resource_group').set('value', ref('azurerm_resource_group.main.name'))]
        for a in self.apps_list:
            out.append(Hcl('output', f'{a["name"]}_url').set('value', interp(f'https://${{azurerm_container_app.{a["name"]}.ingress[0].fqdn}}')))
        for s in self.stores:
            out.append(Hcl('output', f'{s["name"]}_storage_account').set('value', ref(f'azurerm_storage_account.{s["name"]}.name')))
        for d in self.dbs:
            out.append(Hcl('output', f'{d["name"]}_fqdn').set('value', ref(f'azurerm_postgresql_flexible_server.{d["name"]}.fqdn')))
        if self.has_vault:
            out.append(Hcl('output', 'key_vault_uri').set('value', ref('azurerm_key_vault.main.vault_uri')))
        return out

    def destroy_line(self) -> str:
        return ('`terraform destroy` is refused while the database servers and storage accounts carry '
                '`lifecycle { prevent_destroy = true }`: remove those blocks first. The key vault has purge '
                'protection: its name stays reserved for 7 days after deletion.')

    def login_lines(self) -> list[str]:
        return ['- Terraform 1.11 or later (write-only secret arguments need it).',
                '- `az login`, then `export ARM_SUBSCRIPTION_ID=$(az account show --query id -o tsv)`.',
                '- Your account needs Owner (or Contributor + User Access Administrator) on the subscription: the '
                'project creates role assignments.']

    def summary_lines(self) -> list[str]:
        s = self.spec
        lines = [f'- Resource group `rg-{self.base}`; VNet {s.cidr} with one subnet per need, default outbound access '
                 f'off{", NAT gateway for the apps" if self.apps_list else ""}; network security groups on the subnets filter the '
                 f'apps and database subnets (the endpoints subnet has none: private endpoints are protected by the '
                 f'storage account refusing public access and by role-based access).']
        for a in self.apps_list:
            reach = 'public HTTPS address' if a['public_https'] else ('HTTPS address inside the network' if not self.any_public
                                                                      else 'reachable from the other apps only')
            lines.append(f'- `{a["name"]}`: Container App ({a["cpu"]:g} vCPU, {a["memory_gb"]:g} GB, '
                         f'{a["min_instances"]}–{a["max_instances"]} replicas, port {a["port"]}), {reach}; its own managed identity.')
        for d in self.dbs:
            lines.append(f'- `{d["name"]}`: PostgreSQL flexible server {d["version"]} ({self.catalog["sizes"][d["size"]]["azure"]}, '
                         f'{d["storage_gb"]} GB, private, backups {d["backup_days"]} days'
                         f'{(", zone-redundant standby" if self.zones else ", same-zone standby") if d["high_availability"] else ""}); admin password generated at '
                         f'apply time and kept only in the key vault.')
        for o in self.stores:
            lines.append(f'- `{o["name"]}`: storage account (no shared keys, TLS 1.2, public network access disabled, private '
                         f'endpoint){", versioned" if o["versioning"] else ""}, 7-day soft delete.')
        if self.has_vault:
            lines.append('- One key vault (role-based access, purge protection) for the secrets and database passwords.')
        return lines

    def injected_env(self) -> dict[str, list[str]]:
        return {a['name']: list(self.edge_env(a)) + [k for k, _, _ in self.vault_secrets_of(a['name'])] for a in self.apps_list}


# ------------------------------------------------------------------ CLI
def project_for(spec: Spec) -> Project:
    """The mapper for the spec's cloud."""
    match spec.cloud:
        case 'aws':
            return AwsProject(spec)
        case 'azure':
            return AzureProject(spec)
    raise ValueError(spec.cloud)


def check_env_names(spec: Spec, project: Project) -> None:
    """An env name the user gave must not collide with one the mapper injects for an access edge."""
    for app, names in project.injected_env().items():
        given = set(spec.components[app]['env'])
        for n in names:
            if n in given:
                spec.err(f'components.{app}.env.{n}', 'this name is set by an access edge; remove it from env')


def write_project(out: Path, files: dict[str, str], folder: str) -> Path:
    """Write the generated files; delete generated files a previous run wrote and this one does not."""
    root = out / folder
    root.mkdir(parents=True, exist_ok=True)
    for name in GENERATED:
        if name not in files and (root / name).is_file():
            (root / name).unlink()
    for name, text in files.items():
        (root / name).write_text(text, encoding='utf-8')
    return root


NEED_TARGET_TEXT = ('NEED_TARGET — the spec is valid, but the cloud and region are not known. Nothing was written.\n'
                    'NEXT: ask the user once: "AWS or Azure, and which region?" (catalog.md → Regions), then run the '
                    'same call with "--cloud", "<aws|azure>", "--region", "<region>" added to argv.')


def main(argv: list[str] | None = None) -> int:
    """Check the spec, map it for the target, write the project, print the report. Nothing is written unless the
    spec is VALID and the target known; every problem is listed at once so the model fixes them in one revision."""
    ap = argparse.ArgumentParser(description='Check a JSON infrastructure spec and write it as a Terraform project.')
    ap.add_argument('-i', '--input', required=True, help='the JSON spec (references/infra-spec.md) or a saved spec.json')
    ap.add_argument('--cloud', choices=('aws', 'azure'), help='target cloud (overrides the spec)')
    ap.add_argument('--region', help='target region (overrides the spec)')
    ap.add_argument('--out', default='/outputs/terraform', help='output root (default /outputs/terraform)')
    ap.add_argument('--catalog', help='catalog.json (auto-detected)')
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:   # argparse exits 0 for --help, 2 for a wrong argv
        return e.code if isinstance(e.code, int) else 0 if e.code is None else 2
    try:
        raw = json.loads(Path(a.input).read_text(encoding='utf-8'))
    except Exception as e:  # noqa: BLE001 — any unreadable input is reported the same way
        print(f'error: cannot read the spec JSON: {e}', file=sys.stderr)
        return 2
    spec = Spec(raw, load_catalog(a.catalog), a.cloud, a.region)
    project = None
    if not spec.errors and spec.cloud and spec.region:
        project = project_for(spec)
        check_env_names(spec, project)
    if spec.errors:
        print(f'INVALID — {len(spec.errors)} problem(s):\n  - ' + '\n  - '.join(spec.errors), file=sys.stderr)
        print('NEXT: fix every problem in one revision of the spec and run again (at most two revisions).', file=sys.stderr)
        return 1
    if spec.need_target:
        print(NEED_TARGET_TEXT)
        return 1
    files = project.files()
    folder = f'{spec.name}-{spec.cloud}'
    try:
        root = write_project(Path(a.out), files, folder)
    except OSError as e:   # unwritable or impossible --out: one line, like any other usage error
        print(f'ERROR: cannot write {Path(a.out) / folder}: {e.strerror or e}', file=sys.stderr)
        return 2
    secrets = [c['name'] for c in spec.of_type('secret')]
    print(f'VALID — "{spec.title}" ({spec.name}) on {project.cloud_title} {spec.region}')
    print(f'project: {root}')
    print('wrote: ' + ', '.join(files))
    for n in spec.notes:
        print(f'note: {n}')
    if spec.placeholders:
        print('placeholders (variables without a default — fill terraform.tfvars):')
        for n, d in spec.placeholders.items():
            print(f'  {n} — {d}')
    else:
        print('placeholders: none')
    if secrets:
        print('secrets (typed at apply time, never stored): ' + ', '.join(f'TF_VAR_{n}' for n in secrets))
    for w in project.warnings:
        print(f'warning: {w}')
    for x in spec.left_out:
        print(f'left out: {spec.catalog["out_of_v1"][x]}')
    print(f'commands: cd {USER_ROOT}/{folder} && terraform init && terraform plan -out tfplan && terraform apply tfplan')
    print(f'NEXT: your reply is exactly the lines of SKILL.md rule 7, nothing else — "{spec.title}", {project.cloud_title} '
          f'{spec.region}, folder {USER_ROOT}/{folder}/, placeholders: {", ".join(spec.placeholders) or "none (skip that line)"}, '
          f'secrets: {", ".join(f"TF_VAR_{n}" for n in secrets) or "none"}.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
