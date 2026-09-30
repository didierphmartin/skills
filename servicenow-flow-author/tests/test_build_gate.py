"""Dev-only build gate: generated Fluent must pass `npx now-sdk build` (SDK 4.13.3, offline, 0 errors).

Skipped when the SDK lab or Node >= 20.18 is missing. Lab: $SN_SDK_LAB or ~/Documents/synergyAI/servicenow-sdk-lab
(the official flow sample with @servicenow/sdk 4.13.3 installed).
"""
import copy, json, os, re, shutil, subprocess, tempfile, unittest
from pathlib import Path
from helpers import VOCAB, create, fixture, minimal, run_cli

LAB = Path(os.environ.get('SN_SDK_LAB', Path.home() / 'Documents/synergyAI/servicenow-sdk-lab'))
FLOWS = ('onboarding-en.json', 'onboarding-fr.json', 'all-constructs.json', 'p1-escalation.json')
SCHEDULED = minimal(name='daily-check', trigger={'type': 'scheduled_daily', 'time': {'hours': 9, 'minutes': 30, 'seconds': 0, 'timezone': 'UTC'}},
                    steps=[{'id': 'log_it', 'label': 'Log it', 'action': 'log', 'inputs': {'log_level': 'info', 'log_message': 'Daily'}}])


def node_ok() -> bool:
    try:
        v = subprocess.run(['node', '--version'], capture_output=True, text=True).stdout.strip().lstrip('v')
        return tuple(int(x) for x in v.split('.')[:2]) >= (20, 18)
    except (OSError, ValueError):
        return False


def lab_ok() -> bool:
    return (LAB / 'node_modules/@servicenow/sdk/package.json').is_file() and node_ok()


def sdk_build(project: Path) -> tuple[int, str]:
    """Copy the project into a temp dir next to the lab's node_modules and run `npx now-sdk build`."""
    with tempfile.TemporaryDirectory() as td:
        work = Path(td) / project.name
        shutil.copytree(project, work, ignore=shutil.ignore_patterns('node_modules'))
        (work / 'node_modules').symlink_to(LAB / 'node_modules')
        r = subprocess.run(['npx', 'now-sdk', 'build'], cwd=work, capture_output=True, text=True, timeout=600)
        return r.returncode, re.sub(r'\x1b\[[0-9;]*m', '', r.stdout + r.stderr)


@unittest.skipUnless(lab_ok(), f'SDK lab or Node >= 20.18 missing ({LAB})')
class BuildGateTest(unittest.TestCase):
    def assertBuilds(self, project: Path):
        code, log = sdk_build(project)
        self.assertEqual(code, 0, log)
        self.assertIn('Build completed successfully', log)
        self.assertNotIn('ERROR', log)

    def test_rendered_constructs_build(self):
        """Task 3: the renderer's output, in a minimal project, compiles."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'x_1234567_synergy'
            (p / 'src/fluent/flows').mkdir(parents=True)
            (p / 'now.config.json').write_text(json.dumps({'scope': 'x_1234567_synergy', 'scopeId': '0' * 31 + '1'}))
            (p / 'package.json').write_text(create_package())
            specs = {name: fixture(name) for name in FLOWS}
            specs['scheduled'] = SCHEDULED
            for name, spec in specs.items():
                errors, ir = create.check_spec(copy.deepcopy(spec), VOCAB)
                self.assertEqual(errors, [], name)
                (p / f'src/fluent/flows/{ir["name"]}.now.ts').write_text(create.render_flow(ir), encoding='utf-8')
            self.assertBuilds(p)

    def test_project_written_by_the_cli_builds(self):
        """Task 4: four flows added one run at a time to one app (first use, then add) compile together."""
        if not hasattr(create, 'main'):
            self.skipTest('create.main arrives in Task 4')
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            for i, name in enumerate(FLOWS):
                r = run_cli(fixture(name), out, *(['--vendor', '1234567'] if i == 0 else []))
                self.assertEqual(r.returncode, 0, r.stderr)
            self.assertBuilds(out / 'x_1234567_synergy')


def create_package() -> str:
    return json.dumps({'name': 'x-1234567-synergy', 'version': '1.0.0', 'private': True,
                       'devDependencies': {'@servicenow/sdk': '4.13.3', '@servicenow/glide': '27.0.5', 'typescript': '5.8.2'}})


if __name__ == '__main__':
    unittest.main()
