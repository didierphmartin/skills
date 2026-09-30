"""Project writer + CLI: first use vs add flow, FLOWS.md, the zip and the remembered vendor code."""
import json, os, subprocess, tempfile, unittest, zipfile
from pathlib import Path
from helpers import fixture, minimal, run_cli

SCOPE = 'x_1234567_synergy'


class ProjectTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.out = Path(self._td.name)
        self.proj = self.out / SCOPE

    def tearDown(self):
        self._td.cleanup()

    def test_first_use_writes_the_app(self):
        r = run_cli(fixture('onboarding-en.json'), self.out, '--vendor', '1234567')
        self.assertEqual(r.returncode, 0, r.stderr)
        cfg = json.loads((self.proj / 'now.config.json').read_text())
        self.assertEqual(cfg['scope'], SCOPE)
        self.assertRegex(cfg['scopeId'], r'^[0-9a-f]{32}$')
        pkg = json.loads((self.proj / 'package.json').read_text())
        self.assertEqual(pkg['devDependencies']['@servicenow/sdk'], '4.13.3')
        for rel in ('.gitignore', 'README.md', 'FLOWS.md', 'flow-specs/new-employee-onboarding.json',
                    'src/fluent/flows/new-employee-onboarding.now.ts'):
            self.assertTrue((self.proj / rel).is_file(), rel)
        self.assertEqual(sorted(p.name for p in (self.proj / 'src/fluent').iterdir()), ['flows'])   # no table files
        self.assertEqual((self.out / '.vendor').read_text().strip(), '1234567')
        self.assertIn('VALID — flow "New employee onboarding" (new-employee-onboarding)', r.stdout)
        self.assertIn('(created; 1 flow)', r.stdout)
        self.assertIn('  working_days_schedule (schedule) — ', r.stdout)
        self.assertIn('catalog item: "New employee onboarding" — 6 variables; the flow starts when someone orders it', r.stdout)
        self.assertIn('NEXT: your reply is exactly the lines of SKILL.md rule 6', r.stdout)
        flows = (self.proj / 'FLOWS.md').read_text()
        self.assertIn('## New employee onboarding', flows)
        self.assertIn('- Trigger: service_catalog — ordering the catalog item "New employee onboarding"', flows)
        self.assertIn("- Who submits this form and when: HR submits this form when it creates a new user record for an "
                      "employee who hasn't started yet.", flows)
        self.assertIn('- Order form: New hire (`new_hire`, reference), Manager (`manager`, reference), ', flows)
        self.assertIn('  - `hr_rejection_notification` (notification, to fill) — ', flows)
        self.assertIn('- Catalog item listed in: catalog `service_catalog` (to fill) / category `hr_category` (to fill)', flows)

    def test_filled_catalog_placeholder_shows_its_value(self):
        spec = fixture('onboarding-en.json')
        spec['placeholders']['service_catalog']['value'] = 'e0d08b13c3330100c8b837659bba8fb4'
        run_cli(spec, self.out, '--vendor', '1234567')
        self.assertIn('- Catalog item listed in: catalog `service_catalog` (filled: e0d08b13c3330100c8b837659bba8fb4) '
                      '/ category `hr_category` (to fill)', (self.proj / 'FLOWS.md').read_text())

    def test_zip_holds_the_project_and_keys_without_build_outputs(self):
        run_cli(fixture('onboarding-en.json'), self.out, '--vendor', '1234567')
        (self.proj / 'src/fluent/generated').mkdir(parents=True)
        (self.proj / 'src/fluent/generated/keys.ts').write_text('// keys')
        (self.proj / 'src/fluent/generated/other.ts').write_text('// build scratch')
        (self.proj / 'node_modules').mkdir()
        (self.proj / 'node_modules/x.js').write_text('')
        run_cli(minimal(), self.out)
        names = zipfile.ZipFile(self.out / f'{SCOPE}.zip').namelist()
        self.assertIn(f'{SCOPE}/src/fluent/flows/log-new-incident.now.ts', names)
        self.assertIn(f'{SCOPE}/now.config.json', names)
        self.assertIn(f'{SCOPE}/src/fluent/generated/keys.ts', names)   # moving the project keeps the record keys
        self.assertFalse([n for n in names if '/node_modules/' in n or n.endswith('/generated/other.ts')], names)

    def test_build_install_builds_outside_outputs_and_keeps_keys(self):   # decision 7 (revised)
        """build-install.sh copies to <root>/ServiceNow/<scope>, deletes nothing there, never overwrites the build
        folder's keys.ts from outputs, and copies keys.ts back. npm and npx are stand-ins that log their calls."""
        root = self.out / 'synergyAI'
        out = root / 'outputs' / 'servicenow'
        self.assertEqual(run_cli(fixture('onboarding-en.json'), out, '--vendor', '1234567').returncode, 0)
        src, build = out / SCOPE, root / 'ServiceNow' / SCOPE
        self.assertIn('sh build-install.sh https://<instance>.service-now.com', (src / 'README.md').read_text())
        bin_ = self.out / 'bin'
        bin_.mkdir()
        log = self.out / 'calls.log'
        (bin_ / 'npm').write_text(f'#!/bin/sh\necho "npm $*" >> {log}\nmkdir node_modules\n')
        (bin_ / 'npx').write_text(f'#!/bin/sh\necho "npx $*" >> {log}\n'
                                  'if [ "$2" = build ]; then mkdir -p src/fluent/generated; '
                                  'echo "// built" >> src/fluent/generated/keys.ts; fi\n')   # like the SDK: keeps existing keys
        for f in bin_.iterdir():
            f.chmod(0o755)
        env = dict(os.environ, PATH=f'{bin_}:{os.environ["PATH"]}')

        def run(*args):
            r = subprocess.run(['sh', str(src / 'build-install.sh'), *args], env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)

        run('https://dev1.service-now.com')
        self.assertEqual(log.read_text().splitlines(), [
            'npm install', 'npx now-sdk auth --add https://dev1.service-now.com --type basic',
            'npx now-sdk build', 'npx now-sdk install'])
        self.assertTrue((build / 'src/fluent/flows/new-employee-onboarding.now.ts').is_file())
        self.assertFalse((src / 'node_modules').exists())   # nothing heavy in outputs
        self.assertEqual((src / 'src/fluent/generated/keys.ts').read_text(),
                         (build / 'src/fluent/generated/keys.ts').read_text())   # copied back for the zip

        (build / 'src/fluent/generated/keys.ts').write_text('// only in the build folder\n')
        (build / 'extra.txt').write_text('mine')
        log.write_text('')
        run_cli(minimal(), out)
        run()
        self.assertEqual(log.read_text().splitlines(), ['npx now-sdk build', 'npx now-sdk install'])   # SDK kept
        self.assertTrue((build / 'src/fluent/flows/log-new-incident.now.ts').is_file())
        self.assertTrue((build / 'extra.txt').is_file())   # never deletes
        keys = '// only in the build folder\n// built\n'   # outputs' older copy did not replace it
        self.assertEqual((build / 'src/fluent/generated/keys.ts').read_text(), keys)
        self.assertEqual((src / 'src/fluent/generated/keys.ts').read_text(), keys)
        self.assertIn(f'{SCOPE}/src/fluent/generated/keys.ts', zipfile.ZipFile(out / f'{SCOPE}.zip').namelist())

    def test_second_flow_is_added_not_replacing(self):   # Review Focus 4
        run_cli(fixture('onboarding-en.json'), self.out, '--vendor', '1234567')
        scope_id = json.loads((self.proj / 'now.config.json').read_text())['scopeId']
        first = (self.proj / 'src/fluent/flows/new-employee-onboarding.now.ts').read_text()
        (self.proj / 'src/fluent/generated').mkdir(parents=True)
        (self.proj / 'src/fluent/generated/keys.ts').write_text('// keys from the first build')
        r = run_cli(fixture('all-constructs.json'), self.out)          # vendor remembered
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('(existing; 2 flows)', r.stdout)
        self.assertNotIn('now.config.json', r.stdout)
        self.assertEqual(json.loads((self.proj / 'now.config.json').read_text())['scopeId'], scope_id)
        self.assertEqual((self.proj / 'src/fluent/flows/new-employee-onboarding.now.ts').read_text(), first)
        self.assertEqual((self.proj / 'src/fluent/generated/keys.ts').read_text(), '// keys from the first build')
        flows = (self.proj / 'FLOWS.md').read_text()
        self.assertIn('## New employee onboarding', flows)
        self.assertIn('## All constructs', flows)
        self.assertIn('- Asked during the run (catalog tasks): Resolution notes (`resolution_notes`, step `ask_review`), ', flows)
        second = (self.proj / 'src/fluent/flows/all-constructs.now.ts').read_text()
        self.assertIn("Now.ID['all_constructs__", second)
        self.assertNotIn("Now.ID['new_employee_onboarding", second)

    def test_same_name_replaces_that_flow(self):
        run_cli(minimal(), self.out, '--vendor', '1234567')
        r = run_cli(minimal(title='Log every new incident'), self.out)
        self.assertIn('(existing; 1 flow)', r.stdout)
        self.assertIn("name: 'Log every new incident'", (self.proj / 'src/fluent/flows/log-new-incident.now.ts').read_text())
        flows = (self.proj / 'FLOWS.md').read_text()
        self.assertEqual(flows.count('\n## '), 1, flows)
        self.assertEqual(flows.count('## Log every new incident\n'), 1, flows)
        self.assertNotIn('## Log new incident\n', flows)
        self.assertEqual(flows.count('`src/fluent/flows/log-new-incident.now.ts`'), 1, flows)

    def test_vendor_missing_asks_once_and_writes_nothing(self):   # Review Focus 5
        r = run_cli(minimal(), self.out)
        self.assertEqual(r.returncode, 1)
        self.assertIn('NEED_VENDOR', r.stdout)
        self.assertIn('"--vendor", "<code>"', r.stdout)
        self.assertEqual(list(self.out.iterdir()), [])

    def test_vendor_is_normalised_and_checked(self):   # Review Focus 5
        r = run_cli(minimal(), self.out, '--vendor', ' X_1234567 ')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.proj.is_dir())
        r = run_cli(minimal(), self.out, '--vendor', 'acme-corp-international')
        self.assertEqual(r.returncode, 1)
        self.assertIn('1–8 lowercase letters or digits', r.stderr)

    def test_invalid_spec_writes_nothing(self):
        r = run_cli(minimal(steps=[]), self.out, '--vendor', '1234567')
        self.assertEqual(r.returncode, 1)
        self.assertIn('INVALID — 1 problem(s)', r.stderr)
        self.assertIn('NEXT: fix every problem in one revision', r.stderr)
        self.assertEqual(list(self.out.iterdir()), [])

    def test_unreadable_spec(self):
        bad = self.out / 'bad.json'
        bad.write_text('{not json')
        import subprocess, sys
        from helpers import SCRIPT
        r = subprocess.run([sys.executable, str(SCRIPT), '-i', str(bad), '--out', str(self.out)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn('cannot read the spec JSON', r.stderr)

    def test_schedule_placeholder_carries_the_working_days_note(self):
        """A schedule "day" is 24h of schedule time: FLOWS.md and the README say how to count working days."""
        run_cli(fixture('onboarding-en.json'), self.out, '--vendor', '1234567')
        note = ('a schedule "day" counts as 24h of schedule time — use a Mon–Fri all-day (24h) schedule with holidays '
                'for "N working days", or give the offset in schedule hours (e.g. 16h = 2 days of an 8h schedule)')
        flows = (self.proj / 'FLOWS.md').read_text()
        at = flows.index('  - `working_days_schedule` (schedule, to fill) — ')
        self.assertIn(note, flows[at:].split('\n  - ')[0])
        self.assertIn(note, (self.proj / 'README.md').read_text())
        run_cli(minimal(), self.out)                                  # no schedule placeholder: no note for that flow
        flows = (self.proj / 'FLOWS.md').read_text()
        self.assertEqual(flows.count(note), 1)


if __name__ == '__main__':
    unittest.main()
