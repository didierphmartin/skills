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
        if not isinstance(self.name, str) or not NAME_RE.match(self.name):
            self.err('name', 'must be a lowercase slug of 3–24 letters, digits and single hyphens (e.g. "customer-portal")')
        self.title = raw.get('title')
        if not isinstance(self.title, str) or not self.title.strip():
            self.err('title', 'must be a non-empty string (the application, in the user\'s words)')
        self.environment = raw.get('environment', 'prod')
        if not isinstance(self.environment, str) or not ENV_RE.match(self.environment):
            self.err('environment', 'must be 2–10 lowercase letters or digits, starting with a letter (e.g. "prod", "dev")')
        self.tags = raw.get('tags', {})
        if not isinstance(self.tags, dict):
            self.err('tags', 'must be an object of tag name → text')
            self.tags = {}
        for k, v in self.tags.items():
            if not TAG_KEY_RE.match(k) or not isinstance(v, str) or len(v) > 256:
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
            if ctype in self.catalog['out_of_v1']:
                self.err(where, f'type "{ctype}" is out of v1 — remove it, add "{ctype}" to "left_out" and tell the '
                                f'user: {self.catalog["out_of_v1"][ctype]}')
                continue
            if ctype not in kinds:
                self.err(where, f'unknown type "{ctype}"; v1 types: {", ".join(kinds)}; out of v1 (put in '
                                f'"left_out"): {", ".join(self.catalog["out_of_v1"])}')
                continue
            if not isinstance(name, str) or not COMP_RE.match(name):
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
            out[k] = c.get(k, o.get('default'))
        for k, o in opts.items():
            v = out[k]
            if isinstance(o.get('default'), bool) and not isinstance(v, bool):
                self.err(f'{where}.{k}', 'must be true or false')
            elif 'values' in o and v not in o['values']:
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
        if cpu not in self.catalog['components']['container_app']['options']['cpu']['values']:
            cpu = 0.5   # already reported by _options; keeps the memory check meaningful
        out['memory_gb'] = cpu * 2 if mem is None else mem
        if out['memory_gb'] != cpu * 2:
            self.err(f'{where}.memory_gb', f'must be 2 × cpu ({cpu * 2:g} for cpu {cpu:g}): the sizes both clouds accept '
                                           f'are 0.25/0.5, 0.5/1, 1/2, 2/4, 4/8 (cpu/memory_gb)')
        if out['image'] is None:
            self.err(f'{where}.image', 'required: the image the user gave, or {"placeholder": "<name>"} — never an invented image')
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
            if not ENV_VAR_RE.match(k):
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
            src, dst = self.components.get(e.get('from')), self.components.get(e.get('to'))
            if src is None or src['type'] != 'container_app':
                self.err(where, f'from "{e.get("from")}" must be the name of a container_app in components')
                continue
            if dst is None:
                self.err(where, f'to "{e.get("to")}" is not a component name')
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
            if not PH_RE.match(n) or n in TF_RESERVED_VARS:
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
