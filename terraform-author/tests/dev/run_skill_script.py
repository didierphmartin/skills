#!/usr/bin/env python3
"""Dev-only stand-in for the app's run_skill_script tool (used by the GREEN test).

Reads one call {"script", "argv", "input_files"} as JSON (file argument or stdin), maps the Pyodide paths
/scratch → <work>/scratch and /outputs → <work>/outputs, writes input_files, runs the skill script with the
local Python, and prints exit code, stdout and stderr exactly as the model would see them; it exits with the
script's exit code.
Usage: run_skill_script.py --work <dir> [call.json]
"""
import argparse, json, os, subprocess, sys
from pathlib import Path

SKILL = Path(__file__).resolve().parents[2]


class Refused(Exception):
    pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', required=True)
    ap.add_argument('call', nargs='?')
    a = ap.parse_args()
    try:
        return run(a)
    except Refused as e:
        print(f'run_skill_script: {e}', file=sys.stderr)
        return 2


def run(a) -> int:
    try:
        call = json.loads(Path(a.call).read_text(encoding='utf-8') if a.call else sys.stdin.read())
    except (OSError, ValueError) as e:
        raise Refused(f'cannot read the call: {e}')
    if not isinstance(call, dict) or not isinstance(call.get('script'), str):
        raise Refused('the call needs a "script" key (a string)')
    work = Path(a.work).resolve()

    def local(p: str) -> str:
        for root in ('/scratch', '/outputs'):
            if p == root or p.startswith(root + '/'):
                q = work / root.strip('/') / p[len(root):].lstrip('/')
                break
        else:
            q = Path(p) if p.startswith('/') else work / p
        q = Path(os.path.normpath(q)).resolve()
        if q != work and work not in q.parents:
            raise Refused(f'path outside the work folder refused: {p}')
        return str(q)

    def arg(x: str) -> str:
        if x.startswith('--') and '=' in x:
            k, v = x.split('=', 1)
            return f'{k}={local(v)}' if '/' in v else x
        return local(x) if '/' in x else x

    script = (SKILL / call['script']).resolve()
    if SKILL not in script.parents or not script.is_file():
        raise Refused(f'unknown script: {call["script"]}')
    files = call.get('input_files') or {}
    if not isinstance(files, dict):
        raise Refused('"input_files" must be an object')
    argv = [arg(x) for x in call.get('argv') or []]
    dests = {path: Path(local(path)) for path in files}
    for path, text in files.items():
        dests[path].parent.mkdir(parents=True, exist_ok=True)
        dests[path].write_text(text, encoding='utf-8')
    if not any(x == '--out' or x.startswith('--out=') for x in argv):
        argv += ['--out', str(work / 'outputs' / 'terraform')]
    r = subprocess.run([sys.executable, str(script), *argv], capture_output=True, text=True)
    print(f'exit code: {r.returncode}\n--- stdout ---\n{r.stdout}--- stderr ---\n{r.stderr}')
    return r.returncode   # the stand-in exits with the script's own code, as the app reports it


if __name__ == '__main__':
    sys.exit(main())
