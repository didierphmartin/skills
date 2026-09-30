"""Project writer + CLI: first use vs add flow, FLOWS.md, the zip and the remembered vendor code."""
import json, tempfile, unittest, zipfile
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
        self.assertIn('NEXT: reply in 3–5 lines', r.stdout)
        flows = (self.proj / 'FLOWS.md').read_text()
        self.assertIn('## New employee onboarding', flows)
        self.assertIn('- Trigger: service_catalog — ordering the catalog item "New employee onboarding"', flows)
        self.assertIn("- Who submits this form and when: HR submits this form when it creates a new user record for an "
                      "employee who hasn't started yet.", flows)
        self.assertIn('- Order form: New hire (`new_hire`, reference), Manager (`manager`, reference), ', flows)
        self.assertIn('  - `hr_rejection_notification` (notification, to fill) — ', flows)

    def test_zip_holds_the_project_without_build_outputs(self):
        run_cli(fixture('onboarding-en.json'), self.out, '--vendor', '1234567')
        (self.proj / 'src/fluent/generated').mkdir(parents=True)
        (self.proj / 'src/fluent/generated/keys.ts').write_text('// keys')
        (self.proj / 'node_modules').mkdir()
        (self.proj / 'node_modules/x.js').write_text('')
        run_cli(minimal(), self.out)
        names = zipfile.ZipFile(self.out / f'{SCOPE}.zip').namelist()
        self.assertIn(f'{SCOPE}/src/fluent/flows/log-new-incident.now.ts', names)
        self.assertIn(f'{SCOPE}/now.config.json', names)
        self.assertFalse([n for n in names if '/node_modules/' in n or '/generated/' in n], names)

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
