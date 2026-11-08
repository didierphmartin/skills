import json, subprocess, sys, tempfile, unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
STAND_IN = SKILL / 'tests' / 'dev' / 'run_skill_script.py'
SPEC = (SKILL / 'tests' / 'fixtures' / 'customer-portal.json').read_text(encoding='utf-8')


class StandInTest(unittest.TestCase):
    def call(self, argv, input_files=None, extra=None):
        work = tempfile.mkdtemp()
        c = {'script': 'scripts/create.py', 'argv': argv, 'input_files': input_files or {}}
        c.update(extra or {})
        r = subprocess.run([sys.executable, str(STAND_IN), '--work', work], input=json.dumps(c),
                           capture_output=True, text=True)
        return r, Path(work)

    def test_normal_call_works(self):
        r, work = self.call(['-i', '/scratch/p.json'], {'/scratch/p.json': SPEC})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((work / 'outputs' / 'terraform' / 'customer-portal-aws' / 'network.tf').is_file())

    def test_dotdot_escape_refused(self):
        r, _ = self.call(['-i', '/scratch/../../x.json'])
        self.assertEqual(r.returncode, 2)
        self.assertIn('outside the work folder', r.stderr)

    def test_absolute_outside_refused(self):
        r, _ = self.call(['-i', '/etc/hosts'])
        self.assertEqual(r.returncode, 2)

    def test_input_files_destination_outside_refused(self):
        r, _ = self.call([], {'/scratch/../../evil.txt': 'x'})
        self.assertEqual(r.returncode, 2)

    def test_out_equals_form_refused(self):
        r, _ = self.call(['-i', '/scratch/p.json', '--out=/tmp/x'], {'/scratch/p.json': SPEC})
        self.assertEqual(r.returncode, 2)

    def test_out_inside_work_allowed(self):
        r, _ = self.call(['-i', '/scratch/p.json', '--out=/outputs/t'], {'/scratch/p.json': SPEC})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_missing_script_and_bad_json(self):
        work = tempfile.mkdtemp()
        for text in ('{"argv": []}', 'not json'):
            r = subprocess.run([sys.executable, str(STAND_IN), '--work', work], input=text, capture_output=True, text=True)
            self.assertEqual(r.returncode, 2)
            self.assertNotIn('Traceback', r.stderr)


if __name__ == '__main__':
    unittest.main()
