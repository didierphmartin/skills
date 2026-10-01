import json, tempfile, unittest
from pathlib import Path
from helpers import fixture, run_cli


class OtherCloudTest(unittest.TestCase):
    """Rerun "the same for Azure": the saved spec.json with the other cloud; the region maps through the pairs."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.out = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_same_for_the_other_cloud_from_spec_json(self):
        self.assertEqual(run_cli(fixture('customer-portal.json'), self.out).returncode, 0)
        saved = self.out / 'customer-portal-aws' / 'spec.json'
        self.assertEqual(json.loads(saved.read_text())['target'], {'cloud': 'aws', 'region': 'eu-west-3'})
        r = run_cli(saved, self.out, '--cloud', 'azure')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('note: region francecentral is the azure region nearest to eu-west-3 (catalog region pairs)', r.stdout)
        az = self.out / 'customer-portal-azure'
        self.assertEqual(json.loads((az / 'spec.json').read_text())['target'], {'cloud': 'azure', 'region': 'francecentral'})
        self.assertIn('azurerm_container_app', (az / 'app.tf').read_text())
        self.assertTrue((self.out / 'customer-portal-aws' / 'app.tf').is_file())          # the AWS project is kept


if __name__ == '__main__':
    unittest.main()
