import copy, json, re, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / 'scripts' / 'create.py'
FIXTURE = json.loads((HERE / 'fixtures' / 'okta-reset.json').read_text())
SCOPE = 'x_2215723_synergy'


CATALOG = HERE / 'fixtures' / 'mcp-catalog.md'


def run(spec, version=3, extra=('--vendor', '2215723')):
    """Run create.py on `spec` with --out a fresh tempdir; return (code, stdout, stderr, out dir).
    The MCP catalog is the fixture's (tests/fixtures/mcp-catalog.md) unless `extra` names another."""
    if '--catalog' not in extra:
        extra = (*extra, '--catalog', str(CATALOG))
    td = tempfile.mkdtemp()
    unittest.addModuleCleanup(shutil.rmtree, td, ignore_errors=True)
    i = Path(td) / 'spec.json'
    i.write_text(json.dumps(spec))
    out = Path(td) / 'outputs'
    r = subprocess.run([sys.executable, str(SCRIPT), '-i', str(i), '--version', str(version), '--out', str(out), *extra],
                       capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr, out


class CreateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code, cls.out, cls.err, cls.dir = run(FIXTURE)

    def test_fixture_writes_the_three_parts_and_the_record(self):
        self.assertEqual(self.code, 0, self.out + self.err)
        self.assertTrue((self.dir / 'servicenow' / SCOPE / 'flow-specs' / 'okta-reset.json').is_file())
        self.assertTrue((self.dir / 'servicenow' / SCOPE / 'src' / 'fluent' / 'flows' / 'okta-reset.now.ts').is_file())
        self.assertTrue((self.dir / 'okta-reset.playbook.md').is_file())
        self.assertTrue((self.dir / 'okta-reset.workflow.json').is_file())
        self.assertTrue((self.dir / 'okta-reset.automation.json').is_file())

    def test_report_lines(self):
        lines = self.out.splitlines()
        self.assertEqual(lines[0], 'VALID — automation "Okta password reset" v3')
        self.assertTrue(lines[1].startswith('flow: '), lines[1])
        self.assertTrue(lines[2].startswith('playbook: '), lines[2])
        self.assertTrue(lines[3].startswith('workflow: '), lines[3])
        self.assertTrue(lines[4].startswith('automation: '), lines[4])
        self.assertEqual(lines[5], 'NEXT: save_playbook_agent then save_workflow then save_automation')
        self.assertIn('----- playbook -----', self.out)
        self.assertIn('Title: Okta password reset', self.out)

    def flow(self):
        return json.loads((self.dir / 'servicenow' / SCOPE / 'flow-specs' / 'okta-reset.json').read_text())

    def task(self):
        return next(s for s in self.flow()['steps'] if s.get('action') == 'createCatalogTask')

    def test_flow_tag_and_stamp(self):
        t = self.task()
        self.assertEqual(t['inputs']['ah_fields']['correlation_display'], 'synergyai:okta-reset')
        self.assertEqual(t['inputs']['ah_fields']['correlation_id'], 'okta-reset@3')
        self.assertEqual(t['inputs']['ah_fields']['assignment_group'], {'placeholder': 'synergyai_group'})
        self.assertIs(t['inputs']['ah_wait'], True)
        self.assertEqual([s['id'] for s in self.flow()['steps']], ['synergyai_task', 'log_closed'])

    def test_flow_stamp_variable(self):
        v = {x['name']: x for x in self.flow()['form']['variables']}
        self.assertEqual(v['synergyai_automation']['default'], 'okta-reset@3')
        self.assertIs(v['synergyai_automation']['hidden'], True)
        self.assertIs(v['synergyai_automation']['read_only'], True)
        self.assertEqual(list(v)[:2], ['user_email', 'reason'])

    def test_playbook_inputs_are_the_form_variables(self):
        text = (self.dir / 'okta-reset.playbook.md').read_text()
        line = next(l for l in text.splitlines() if l.startswith('The request is a ServiceNow job'))
        names = re.findall(r'(\w+) \(', line.split('carry', 1)[1])
        self.assertEqual(names, [v['name'] for v in FIXTURE['form']['variables']])
        self.assertIn('Trigger: An employee asks for an Okta password reset. (automation okta-reset@3)', text)

    def test_workflow_start_and_back_args(self):
        d = json.loads((self.dir / 'okta-reset.workflow.json').read_text())
        nodes = d['definition']['nodes']
        start = next(n for n in nodes if n['node_type'] == 'start')
        self.assertEqual(start['config']['start_option_ref'], 'ServiceNow: get new job')
        self.assertEqual(start['config']['start_args'],
                         {'tag': 'okta-reset', 'version': 3, 'hand_back_group': 'Service Desk'})
        self.assertIs(type(start['config']['start_args']['version']), int)
        pb = next(n for n in nodes if n['node_type'] == 'playbook')
        self.assertEqual(pb['config']['playbook_ref'], 'okta-reset')
        comps = {n['config']['code_component_ref']: n['config']['args'] for n in nodes if n['node_type'] == 'code'}
        self.assertEqual(comps, {'ServiceNow: complete job': {}, 'ServiceNow: hand back': {'group': 'Service Desk'}})

    def test_automation_record(self):
        rec = json.loads((self.dir / 'okta-reset.automation.json').read_text())
        self.assertEqual(rec['version'], 3)
        self.assertEqual(rec['spec']['name'], 'okta-reset')
        self.assertEqual(rec['derived']['tag'], 'okta-reset')
        self.assertEqual(rec['derived']['stamp'], 'okta-reset@3')
        self.assertEqual(rec['derived']['scope'], SCOPE)


class RejectTest(unittest.TestCase):
    def assertRejected(self, spec, needle, **kw):
        code, out, err, d = run(spec, **kw)
        self.assertEqual(code, 1, out + err)
        self.assertIn(needle, out + err)
        self.assertFalse(d.exists() and any(d.rglob('*.json')), 'nothing may be written on INVALID')
        return out + err

    def test_variable_with_space_and_uppercase(self):
        s = copy.deepcopy(FIXTURE); s['form']['variables'][0]['name'] = 'User Email'
        self.assertRejected(s, 'User Email')

    def test_reserved_stamp_variable(self):
        s = copy.deepcopy(FIXTURE)
        s['form']['variables'].append({'name': 'synergyai_automation', 'label': 'x', 'type': 'single_line_text'})
        self.assertRejected(s, 'synergyai_automation')

    def test_trigger_form_is_a_playbook_error(self):
        s = copy.deepcopy(FIXTURE)
        s['playbook']['steps'].insert(0, {'text': 'If the email is missing, #Trigger Form asking for it.'})
        msg = self.assertRejected(s, '[playbook]')
        self.assertTrue(any(l.strip().lstrip('- ').startswith('[playbook]') and 'Trigger Form' in l for l in msg.splitlines()), msg)

    def test_bad_key(self):
        s = copy.deepcopy(FIXTURE); s['name'] = 'Okta Reset'
        self.assertRejected(s, 'name')

    def test_no_playbook_step(self):
        s = copy.deepcopy(FIXTURE); s['playbook']['steps'] = []
        self.assertRejected(s, 'step')

    def test_flow_errors_are_prefixed(self):
        s = copy.deepcopy(FIXTURE); s['form']['variables'][1]['type'] = 'no_such_type'
        self.assertRejected(s, '[flow]')

    def test_bad_outcomes_and_placeholders(self):
        s = copy.deepcopy(FIXTURE); s['outcomes'] = []; s['servicenow']['placeholders'] = {'synergyai_group': 'x'}
        msg = self.assertRejected(s, 'outcomes')
        self.assertIn('placeholders', msg)

    def test_string_step_is_invalid_naming_the_step(self):
        s = copy.deepcopy(FIXTURE); s['playbook']['steps'] = ['#Unlock Okta User']
        msg = self.assertRejected(s, 'playbook.steps[1]')
        self.assertNotIn('Traceback', msg)

    def test_bad_sub_is_invalid(self):
        s = copy.deepcopy(FIXTURE); s['playbook']['steps'][0]['sub'] = 'not a list'
        msg = self.assertRejected(s, 'playbook.steps[1]')
        self.assertNotIn('Traceback', msg)

    def test_sub_skill_crash_is_invalid_with_prefix(self):
        import contextlib, importlib.util, io, types
        spec = importlib.util.spec_from_file_location('_automation_under_test', SCRIPT)
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        boom = types.SimpleNamespace(main=lambda argv: (_ for _ in ()).throw(KeyError('nodes')))
        self.assertEqual(mod.call(boom, []), (1, '', "- KeyError: 'nodes'"))
        real = mod.load_script
        mod.load_script = lambda skill, script: boom if skill == 'workflow-compile' else real(skill, script)
        td = tempfile.mkdtemp(); unittest.addModuleCleanup(shutil.rmtree, td, ignore_errors=True)
        i = Path(td) / 'spec.json'; i.write_text(json.dumps(FIXTURE))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = mod.main(['-i', str(i), '--version', '3', '--out', str(Path(td) / 'o'), '--vendor', '2215723'])
        self.assertEqual(code, 1, out.getvalue() + err.getvalue())
        self.assertIn("[workflow] KeyError: 'nodes'", err.getvalue())
        self.assertFalse((Path(td) / 'o').exists())

    def test_need_vendor_passes_through(self):
        code, out, err, d = run(FIXTURE, extra=())
        self.assertEqual(code, 1, out + err)
        self.assertIn('NEED_VENDOR', out)
        self.assertFalse((d / 'okta-reset.playbook.md').exists())

    def test_missing_catalog_fails_clearly(self):
        code, out, err, d = run(FIXTURE, extra=('--vendor', '2215723', '--catalog', '/nonexistent/mcp-catalog.md'))
        self.assertNotEqual(code, 0)
        self.assertIn('catalog', (out + err).lower())
        self.assertFalse((d / 'okta-reset.playbook.md').exists())


REAL_CATALOG = HERE.parent.parent / 'playbook-author' / 'references' / 'mcp-catalog.md'
TEAM_TRANSFER = json.loads((HERE / 'fixtures' / 'team-transfer.json').read_text())


class DocumentedExamplesTest(unittest.TestCase):
    """What references/automation-spec.md tells the model to copy must pass as written."""

    def test_documented_approvals_are_the_fixtures_and_valid(self):
        doc = (HERE.parent / 'references' / 'automation-spec.md').read_text()
        block = re.search(r'```json\n("before": \[.*?\n\])\n```', doc, re.S)
        self.assertIsNotNone(block, 'the two-approval example is missing from automation-spec.md')
        self.assertEqual(json.loads('{' + block.group(1) + '}')['before'], TEAM_TRANSFER['servicenow']['before'])
        code, out, err, d = run(TEAM_TRANSFER, version=1, extra=('--vendor', '2215723', '--catalog', str(REAL_CATALOG)))
        self.assertEqual(code, 0, out + err)
        self.assertIn('0 unbound', out)
        flow = json.loads((d / 'servicenow' / SCOPE / 'flow-specs' / 'team-transfer.json').read_text())
        self.assertEqual([s.get('action') for s in flow['steps'][:4:2]], ['askForApproval', 'askForApproval'])

    def test_documented_action_names_bind(self):
        sys.path.insert(0, str(REAL_CATALOG.parent.parent / 'scripts'))
        try:
            import create as pb
        finally:
            sys.path.pop(0)
        tools, _ = pb.load_catalog(str(REAL_CATALOG))
        doc = (HERE.parent / 'references' / 'automation-spec.md').read_text()
        table = doc.split('## Playbook action names', 1)[1].split('## Approvals', 1)[0]
        rows = '\n'.join(line for line in table.splitlines() if line.startswith('|'))   # not the counterexamples
        names = re.findall(r'`(#[A-Z][^`]*)`', rows)
        self.assertGreaterEqual(len(names), 10)
        for name in names:
            if name.lower() in pb.NATIVE_VERBS:
                continue
            self.assertIsNotNone(pb.auto_bind(name, tools)[0], name)


if __name__ == '__main__':
    unittest.main()
