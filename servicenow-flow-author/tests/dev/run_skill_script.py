#!/usr/bin/env python3
"""Dev-only stand-in for the app's run_skill_script tool (used by the GREEN test).

Reads one call {"script", "argv", "input_files"} as JSON (file argument or stdin), maps the Pyodide paths
/scratch → <work>/scratch and /outputs → <work>/outputs, writes input_files, runs the skill script with the
local Python, and prints exit code, stdout and stderr exactly as the model would see them; it exits with the
script's exit code.
Usage: run_skill_script.py --work <dir> [call.json]
"""
import argparse, json, subprocess, sys
from pathlib import Path

SKILL = Path(__file__).resolve().parents[2]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', required=True)
    ap.add_argument('call', nargs='?')
    a = ap.parse_args()
    call = json.loads(Path(a.call).read_text(encoding='utf-8') if a.call else sys.stdin.read())
    work = Path(a.work).resolve()

    def local(p: str) -> str:
        for root in ('/scratch', '/outputs'):
            if p == root or p.startswith(root + '/'):
                return str(work / root.strip('/')) + p[len(root):]
        return p

    for path, text in (call.get('input_files') or {}).items():
        f = Path(local(path))
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding='utf-8')
    argv = [local(x) for x in call.get('argv') or []]
    if '--out' not in argv:
        argv += ['--out', str(work / 'outputs' / 'servicenow')]
    r = subprocess.run([sys.executable, str(SKILL / call['script']), *argv], capture_output=True, text=True)
    print(f'exit code: {r.returncode}\n--- stdout ---\n{r.stdout}--- stderr ---\n{r.stderr}')
    return r.returncode   # the stand-in exits with the script's own code, as the app reports it


if __name__ == '__main__':
    sys.exit(main())
