#!/usr/bin/env python3
"""Create a new skill directly in the live skills directory.

This is the gpt-env replacement for the upstream "LLM writes files with the
Write tool, then we package_skill.py" flow. In our env the LLM can't write
arbitrary files — it can only invoke `run_skill_script`. So the LLM stages
the new skill's full file tree under `/scratch/<name>/` via `input_files`
(one entry per file — no nested JSON spec), and this script validates and
copies the tree into `/skills-root/<name>/`, which is mounted to the user's
~/Documents/synergyAI/skills/ folder. The skill is live the moment this
script returns successfully — no zip, no import step.

Invocation shape:

    run_skill_script({
      "dir_name": "skill-creator",
      "script": "scripts/create_skill.py",
      "argv": ["--name", "linkedin-carousel"],   # also "--overwrite" if replacing
      "input_files": {
        "/scratch/linkedin-carousel/SKILL.md":
          "---\\nname: linkedin-carousel\\ndescription: ...\\ndependencies: [pillow]\\n---\\n\\n# ...",
        "/scratch/linkedin-carousel/scripts/generate.py":
          "import argparse\\nfrom PIL import Image\\n...",
        "/scratch/linkedin-carousel/scripts/templates/pro.json":
          "{ \\"primary\\": \\"#0077b5\\", ... }",
        "/scratch/linkedin-carousel/references/spec.md":
          "..."
      }
    })

`/scratch/<name>/SKILL.md` is **required**. Anything else under that tree
(scripts, templates, references, READMEs, etc.) is copied verbatim. All
files must be UTF-8 text — binary isn't supported by this flow yet.

Enforcement before any write to `/skills-root/`:

  - `name` is kebab-case, ≤ 64 chars
  - SKILL.md starts with YAML frontmatter
  - SKILL.md description ≤ 1024 chars
  - SKILL.md declares a `dependencies:` field (even `[]`)
  - Every non-stdlib import across the tree's .py files appears in `dependencies:`
  - No declared dep is on the Pyodide-incompatible list (see PYODIDE_INCOMPATIBLE)

Refuses to overwrite an existing skill folder unless --overwrite is passed.
"""

import argparse
import ast
import json
import re
import sys
from pathlib import Path

SKILLS_ROOT = Path("/skills-root" if sys.platform == "emscripten" else Path.home() / "Documents" / "synergyAI" / "skills")

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_NAME_LEN = 64
MAX_DESC_LEN = 1024

# Top-level package names that are part of the Python stdlib. Imports of
# these don't need to appear in the new skill's `dependencies:` field.
# Sourced from sys.stdlib_module_names where available (Python 3.10+);
# we fall back to a static set for older interpreters.
try:
    _STDLIB_NAMES: set[str] = set(sys.stdlib_module_names)  # type: ignore[attr-defined]
except AttributeError:
    _STDLIB_NAMES = {
        "abc", "argparse", "asyncio", "base64", "binascii", "bisect", "builtins", "calendar",
        "collections", "contextlib", "copy", "csv", "ctypes", "dataclasses", "datetime",
        "decimal", "difflib", "dis", "email", "enum", "errno", "fnmatch", "functools",
        "gc", "getpass", "glob", "gzip", "hashlib", "heapq", "hmac", "html", "http",
        "importlib", "inspect", "io", "ipaddress", "itertools", "json", "keyword", "logging",
        "math", "mimetypes", "multiprocessing", "operator", "os", "pathlib", "pickle",
        "platform", "posixpath", "pprint", "queue", "random", "re", "secrets", "select",
        "shutil", "signal", "socket", "sqlite3", "ssl", "stat", "string", "struct",
        "subprocess", "sys", "tarfile", "tempfile", "textwrap", "threading", "time",
        "timeit", "tkinter", "token", "tokenize", "trace", "traceback", "types", "typing",
        "unicodedata", "unittest", "urllib", "uuid", "warnings", "weakref", "xml", "zipfile",
        "zlib",
    }
# Pyodide-specific runtime modules — present at runtime, treated as stdlib
# for our purposes so the LLM doesn't have to declare them.
_PYODIDE_RUNTIME = {"pyodide", "js", "micropip"}
_STDLIB_NAMES |= _PYODIDE_RUNTIME

# Packages known to be unusable in Pyodide. Trying them either fails at
# install time (no wasm wheel) or at import (C extensions that don't load
# in the browser sandbox). Each entry maps the package name to a short
# suggested alternative so the error message is actionable. Keep this
# list narrow — false positives block legitimate skill creation.
PYODIDE_INCOMPATIBLE: dict[str, str] = {
    "reportlab": "fpdf2 or pypdf for PDF generation/manipulation",
    "psutil": "(no good alternative — system info isn't accessible in the browser sandbox)",
    "selenium": "(not usable — browser automation requires a separate WebDriver process)",
    "pyautogui": "(not usable — needs OS-level access)",
    "pywin32": "(Windows-only; not applicable in Pyodide)",
    "win32api": "(Windows-only; not applicable in Pyodide)",
    "win32com": "(Windows-only; not applicable in Pyodide)",
    "tk": "(GUI toolkit; not usable in a browser sandbox)",
    "tkinter": "(GUI toolkit; not usable in a browser sandbox)",
    "pyqt5": "(GUI toolkit; not usable in a browser sandbox)",
    "pyqt6": "(GUI toolkit; not usable in a browser sandbox)",
    "pyside2": "(GUI toolkit; not usable in a browser sandbox)",
    "pyside6": "(GUI toolkit; not usable in a browser sandbox)",
}

# Map of import-name → install-name where they differ. `dependencies:`
# uses install names (what micropip would receive); `import` uses module
# names. Add entries here as we encounter mismatches.
_INSTALL_NAME_FOR_IMPORT = {
    "yaml": "pyyaml",
    "bs4": "beautifulsoup4",
    "PIL": "pillow",
    "docx": "python-docx",
    "pptx": "python-pptx",
    "fitz": "pymupdf",
    "cv2": "opencv-python",
    "sklearn": "scikit-learn",
    "fpdf": "fpdf2",
    "dateutil": "python-dateutil",
    "magic": "python-magic",
    "Crypto": "pycryptodome",
}


def _fail(msg: str) -> int:
    print(f"❌ Error: {msg}", file=sys.stderr)
    return 1


def _validate_name(name: str) -> str | None:
    if not isinstance(name, str) or not name:
        return "spec.name must be a non-empty string"
    if len(name) > MAX_NAME_LEN:
        return f"spec.name is {len(name)} chars (max {MAX_NAME_LEN})"
    if not NAME_RE.match(name):
        return f"spec.name must be kebab-case (lowercase letters, digits, hyphens, no leading/trailing/double hyphens): got {name!r}"
    return None


def _validate_skill_md(text: str) -> str | None:
    """Light validation: frontmatter present, description length bounded.

    Heavy validation lives in scripts/quick_validate.py. We just catch the
    obvious shape problems so we don't write a garbage SKILL.md that would
    then fail the deeper validator.
    """
    if not isinstance(text, str) or not text.strip():
        return "spec.skill_md must be a non-empty string"
    if not text.lstrip().startswith("---"):
        return "spec.skill_md must begin with YAML frontmatter (---)"
    # Quick length check on the description line if we can find it.
    m = re.search(r"^description:\s*(.+?)$", text, re.MULTILINE)
    if m:
        desc = m.group(1).strip().strip('"').strip("'")
        if len(desc) > MAX_DESC_LEN:
            return f"description is {len(desc)} chars (max {MAX_DESC_LEN})"
    return None


def _sanitize_rel_path(rel: str) -> str | None:
    """Reject anything that could escape the skill folder."""
    if not isinstance(rel, str) or not rel.strip():
        return None
    rel = rel.strip().lstrip("/")
    if ".." in Path(rel).parts:
        return None
    if rel == "SKILL.md":
        # Caller should use spec.skill_md, not include SKILL.md in scripts.
        return None
    return rel


def _parse_deps_field(skill_md: str) -> list[str] | None:
    """Read the `dependencies:` line from frontmatter.

    Returns the list of declared dependency names (install-name form, what
    micropip receives), normalized to lowercase. Returns None if the field
    is entirely absent — caller treats absence as a hard error.

    Accepts the two YAML forms used elsewhere in the project:
      dependencies: [pyyaml, pillow]
      dependencies: pyyaml
    Also accepts an empty list:
      dependencies: []
    """
    # Frontmatter only — stop at the closing ---.
    end_idx = skill_md.find("\n---", 3)
    frontmatter = skill_md[: end_idx if end_idx != -1 else len(skill_md)]
    m = re.search(r"^dependencies:\s*(.*)$", frontmatter, re.MULTILINE)
    if not m:
        return None
    raw = m.group(1).strip()
    if raw == "" or raw == "[]":
        return []
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    return [
        p.strip().strip("'\"").lower()
        for p in raw.split(",")
        if p.strip()
    ]


def _extract_imports(source: str) -> set[str]:
    """Top-level package names a Python source file imports.

    Uses ast so we ignore imports inside strings/comments. Returns the
    first dotted component only (e.g. `from reportlab.pdfgen import canvas`
    contributes `reportlab`). Relative imports (`from . import ...`) and
    syntax-error files are silently skipped.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue  # relative import, intra-skill
            if node.module:
                names.add(node.module.split(".", 1)[0])
    return names


def _normalize_dep_name(name: str) -> str:
    """Map an import name to its install-name for dependency comparison."""
    return _INSTALL_NAME_FOR_IMPORT.get(name, name).lower()


def _validate_dependencies_and_imports(
    skill_md: str,
    scripts: dict,
) -> str | None:
    """Enforce the two hard rules:

    1. `dependencies:` field MUST be declared in frontmatter (even as []).
    2. Every non-stdlib import across all .py scripts must appear in
       `dependencies`. No declared dep may be on the Pyodide-incompatible
       list.

    Returns None on success, an error string on the first failure.
    """
    declared = _parse_deps_field(skill_md)
    if declared is None:
        return (
            "the new skill's SKILL.md frontmatter MUST declare a `dependencies:` field, "
            "even if it's an empty list. Examples: `dependencies: []`, "
            "`dependencies: pyyaml`, or `dependencies: [beautifulsoup4, pillow]`. "
            "This isn't optional in gpt env — Pyodide needs to know what to install "
            "before any non-stdlib import will work."
        )

    # Reject incompatible declared deps up front.
    bad = [d for d in declared if d in PYODIDE_INCOMPATIBLE]
    if bad:
        lines = [f"  - {d}: {PYODIDE_INCOMPATIBLE[d]}" for d in bad]
        return (
            "the following declared dependencies are NOT usable in Pyodide. "
            "Pick alternatives:\n" + "\n".join(lines)
        )

    declared_set = set(declared)
    declared_set.add("scripts")  # intra-skill `from scripts.utils import X` style

    # Walk every .py file in the spec and check its imports.
    issues: list[str] = []
    py_pattern = re.compile(r"\.py$", re.IGNORECASE)
    for rel, content in scripts.items():
        if not isinstance(rel, str) or not py_pattern.search(rel):
            continue
        if not isinstance(content, str):
            continue
        imports = _extract_imports(content)
        for imp in imports:
            if imp in _STDLIB_NAMES:
                continue
            normalized = _normalize_dep_name(imp)
            if normalized in PYODIDE_INCOMPATIBLE:
                issues.append(
                    f"{rel}: imports `{imp}` which is NOT usable in Pyodide "
                    f"({PYODIDE_INCOMPATIBLE[normalized]})"
                )
                continue
            if normalized not in declared_set and imp not in declared_set:
                issues.append(
                    f"{rel}: imports `{imp}` but the new skill's dependencies "
                    f"doesn't list `{normalized}`. Add it to the frontmatter "
                    f"or remove the import."
                )
    if issues:
        return "import / dependency mismatches:\n  - " + "\n  - ".join(issues)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", required=True, help="Kebab-case name of the new skill. The source tree must already be at /scratch/<name>/.")
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing skill folder of the same name")
    args = parser.parse_args()

    name = args.name.strip()
    err = _validate_name(name)
    if err:
        return _fail(err)

    # Source tree: the LLM writes the new skill's files via input_files
    # under /scratch/<name>/<rel-path>. Each file is its own top-level
    # input_files entry — no nested JSON spec to escape twice. We pick up
    # the whole tree here.
    source_root = Path("/scratch") if sys.platform == "emscripten" else Path("/tmp/scratch")
    source_dir = source_root / name
    if not source_dir.is_dir():
        return _fail(
            f"source tree not found at {source_dir}. The new skill's files must be passed "
            f"via input_files keyed under /scratch/{name}/, with at minimum a SKILL.md at "
            f"/scratch/{name}/SKILL.md and any scripts under /scratch/{name}/scripts/."
        )

    skill_md_path = source_dir / "SKILL.md"
    if not skill_md_path.is_file():
        return _fail(f"required file missing: {skill_md_path}. Place the skill's SKILL.md there via input_files.")

    skill_md = skill_md_path.read_text()
    err = _validate_skill_md(skill_md)
    if err:
        return _fail(err)

    # Soft check for the SLOW_UPDATE marker block. Skills without it still
    # work today; future skill-optimizer passes (epoch-boundary slow updates,
    # per the SkillOpt paper §3.6) can only write into skills that have the
    # reserved region. Warn the author so they can add it now — adding it
    # later is a one-line edit but easier when the file is fresh.
    if "<!-- SLOW_UPDATE_START -->" not in skill_md or "<!-- SLOW_UPDATE_END -->" not in skill_md:
        print(
            "⚠️  WARNING: SKILL.md is missing the SLOW_UPDATE marker block.\n"
            "   Insert these two lines right after the YAML frontmatter:\n"
            "       <!-- SLOW_UPDATE_START -->\n"
            "       <!-- SLOW_UPDATE_END -->\n"
            "   See skill-creator/SKILL.md → 'SLOW_UPDATE Convention'. Skill\n"
            "   still installed; only future optimizer passes need the markers.",
            file=sys.stderr,
        )

    # Collect every other file in the tree (any depth). The validator
    # downstream filters for .py files; non-py files (templates, refs,
    # READMEs, JSON, MD) ride along verbatim.
    other_files: dict[str, str] = {}
    for f in sorted(source_dir.rglob("*")):
        if not f.is_file() or f == skill_md_path:
            continue
        rel = str(f.relative_to(source_dir))
        try:
            other_files[rel] = f.read_text()
        except UnicodeDecodeError:
            return _fail(
                f"file {rel} is not UTF-8 text. Only text files are supported by this flow "
                f"(no binary). Re-encode or drop the file."
            )

    # Enforce: dependencies field present in SKILL.md + all non-stdlib
    # imports across the tree declared + no Pyodide-incompatible package
    # used. Runs BEFORE we touch /skills-root/, so a rejected spec
    # produces zero on-disk state in the live skills folder.
    err = _validate_dependencies_and_imports(skill_md, other_files)
    if err:
        return _fail(err)

    if not SKILLS_ROOT.exists():
        return _fail(
            f"skills root not mounted at {SKILLS_ROOT}. "
            "In gpt env this is mounted automatically by pyodide-runner; outside of it, ensure the directory exists."
        )

    target = SKILLS_ROOT / name
    if target.exists():
        if not args.overwrite:
            return _fail(
                f"skill {name!r} already exists at {target}. Pass --overwrite to replace it."
            )
        import shutil
        shutil.rmtree(target)

    # Copy the validated tree wholesale. shutil.copytree preserves
    # structure and we already validated every file's content above.
    import shutil
    shutil.copytree(source_dir, target)

    written = ["SKILL.md", *sorted(other_files)]
    print(f"✅ Created skill {name!r} at {target}")
    print(f"   Files written: {len(written)}")
    for f in written:
        print(f"     - {f}")
    print()
    print(f"The skill is live. Reload the sidebar to see it appear in the Skills list.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
