import shutil, subprocess, unittest
from helpers import FIXTURES, LAB, SKILL, lab_ready

SPECS = sorted(p.stem for p in FIXTURES.glob('*.json') if not p.stem.startswith('invalid-'))


@unittest.skipUnless(lab_ready(), f'terraform lab not found at {LAB} (set TF_LAB)')
class LabGateTest(unittest.TestCase):
    """dev/validate_all.sh: every fixture spec → init (shared plugin cache) → validate → fmt -check → tflint, 0 errors."""

    def gate(self, cloud: str) -> str:
        free_mb = shutil.disk_usage(LAB).free // 2**20
        if free_mb < 400:
            self.skipTest(f'only {free_mb} MB free on disk')
        r = subprocess.run(['sh', str(SKILL / 'dev' / 'validate_all.sh'), '--cloud', cloud],
                           capture_output=True, text=True, timeout=1200)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn('FAIL', r.stdout)
        return r.stdout

    def test_aws_projects_pass_the_lab_gate(self):
        out = self.gate('aws')
        expected = [s for s in SPECS if not s.startswith('azure-')]
        self.assertEqual(sorted(l.split()[1] for l in out.splitlines() if l.startswith('PASS')), expected)
        self.assertNotRegex(out, r'PASS \S+ aws \(validate, fmt, tflint: 0 errors, [1-9]')   # aws: no warnings either

    def test_azure_projects_pass_the_lab_gate(self):
        out = self.gate('azure')
        expected = [s for s in SPECS if not s.startswith('aws-')]
        self.assertEqual(sorted(l.split()[1] for l in out.splitlines() if l.startswith('PASS')), expected)


if __name__ == '__main__':
    unittest.main()
