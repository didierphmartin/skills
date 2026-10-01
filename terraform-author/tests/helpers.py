"""Shared test helpers: the create.py module, the catalog, fixtures, a spec checker, a file generator, a CLI runner."""
import copy, importlib.util, json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
SCRIPT = SKILL / 'scripts' / 'create.py'
FIXTURES = Path(__file__).resolve().parent / 'fixtures'
LAB = Path(os.environ.get('TF_LAB', Path.home() / 'Documents/synergyAI/terraform-lab'))

_spec = importlib.util.spec_from_file_location('create', SCRIPT)
create = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(create)
CATALOG = create.load_catalog()


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))


def spec_of(raw: dict, cloud: str | None = None, region: str | None = None):
    return create.Spec(copy.deepcopy(raw), CATALOG, cloud, region)


def errors_of(raw: dict, cloud: str | None = None, region: str | None = None) -> list[str]:
    return spec_of(raw, cloud, region).errors


def minimal(**over) -> dict:
    """The smallest valid spec with a target: one private bucket on AWS."""
    s = {'name': 'notes', 'title': 'Notes', 'target': {'cloud': 'aws', 'region': 'eu-west-3'},
         'components': [{'type': 'object_storage', 'name': 'files'}]}
    s.update(over)
    return s


def run_cli(raw, out: Path, *args: str) -> subprocess.CompletedProcess:
    """Run create.py on a spec dict (written to a temp file) or on an existing spec path."""
    with tempfile.TemporaryDirectory() as td:
        if isinstance(raw, dict):
            i = Path(td) / 'spec.json'
            i.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
        else:
            i = raw
        return subprocess.run([sys.executable, str(SCRIPT), '-i', str(i), '--out', str(out), *args],
                              capture_output=True, text=True)


def lab_ready() -> bool:
    return (LAB / 'bin' / 'terraform').is_file() and (LAB / 'plugin-cache').is_dir() and shutil.which('sh') is not None
