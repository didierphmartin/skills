"""Shared test helpers: the create.py module, fixtures, and a CLI runner."""
import copy, importlib.util, json, subprocess, sys, tempfile
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
SCRIPT = SKILL / 'scripts' / 'create.py'
FIXTURES = Path(__file__).resolve().parent / 'fixtures'

_spec = importlib.util.spec_from_file_location('create', SCRIPT)
create = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(create)
VOCAB = create.load_vocabulary()


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))


def errors_of(spec: dict) -> list[str]:
    return create.check_spec(copy.deepcopy(spec), VOCAB)[0]


def render(spec: dict) -> str:
    errors, ir = create.check_spec(copy.deepcopy(spec), VOCAB)
    assert not errors, errors
    return create.render_flow(ir)


def run_cli(spec: dict, out: Path, *args: str) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as td:
        i = Path(td) / 'spec.json'
        i.write_text(json.dumps(spec, ensure_ascii=False), encoding='utf-8')
        return subprocess.run([sys.executable, str(SCRIPT), '-i', str(i), '--out', str(out), '--today', '2026-09-30', *args],
                              capture_output=True, text=True)


def minimal(**over) -> dict:
    """The smallest valid spec: one log step on incident created."""
    s = {'name': 'log-new-incident', 'title': 'Log new incident', 'description': 'Logs each new incident.',
         'language': 'en', 'trigger': {'type': 'record_created', 'table': 'incident'},
         'steps': [{'id': 'log_it', 'label': 'Log it', 'action': 'log',
                    'inputs': {'log_level': 'info', 'log_message': ['New incident ', {'pill': 'trigger.current.number'}]}}]}
    s.update(over)
    return s


def catalog_min(**over) -> dict:
    """The smallest valid catalog flow: an order form with one variable, one log step reading it."""
    s = {'name': 'badge-request', 'title': 'Badge request', 'description': 'Logs each badge request.', 'language': 'en',
         'trigger': {'type': 'service_catalog'},
         'form': {'submitted_by': 'Any employee who needs a badge.', 'catalog': {'placeholder': 'catalog'},
                  'category': {'placeholder': 'category'},
                  'variables': [{'name': 'badge_holder', 'label': 'Badge holder', 'type': 'reference', 'reference': 'sys_user'}]},
         'placeholders': {'catalog': {'kind': 'catalog', 'description': 'Catalog.'},
                          'category': {'kind': 'category', 'description': 'Category.'}},
         'steps': [{'id': 'log_it', 'label': 'Log it', 'action': 'log',
                    'inputs': {'log_level': 'info', 'log_message': ['Badge for ', {'pill': 'form.badge_holder.name'}]}}]}
    s.update(over)
    return s
