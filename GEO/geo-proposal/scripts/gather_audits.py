#!/usr/bin/env python3
"""
gather_audits.py — collect every existing GEO audit file in the outputs
folder and hand the LLM the consolidated content + score rollup it needs to
write the final client report.

No network I/O, no external deps. Pure filesystem + regex.

Usage:
    python gather_audits.py                           # everything in outputs/
    python gather_audits.py stripe                    # filter by substring
    python gather_audits.py FILE1.md FILE2.md         # specific files
    python gather_audits.py --list                    # quick inventory only
"""

from __future__ import annotations

import os
import argparse
import json
import re
import sys
import time
from pathlib import Path


DEFAULT_OUTPUT_DIR = (
    Path(os.environ.get("SYNERGYAI_OUTPUT_DIR", "/outputs"))
    if sys.platform == "emscripten"
    else Path.home() / "Documents" / "synergyAI" / "outputs"
)

# Cap per-file content embedded in stdout (full content can be 25KB+ for some
# citability extracts). 6000 chars is enough for the LLM to see the scores +
# structure; the file is still on disk for deeper reads if needed.
PER_FILE_CHAR_CAP = 6000

# Score-marker regex (subset of geo-compare's set — kept consistent).
SCORE_PATTERNS = [
    ("AI Visibility Score",       re.compile(r"AI Visibility Score:\s*\**\s*(\d+)\s*/\s*100", re.I)),
    ("Citability Score",          re.compile(r"Citability Score:?\s*\**\s*(\d+)\s*/\s*100", re.I)),
    ("Content Score",             re.compile(r"Content Score:\s*\**\s*(\d+)\s*/\s*100", re.I)),
    ("Technical Score",           re.compile(r"Technical Score:\s*\**\s*(\d+)\s*/\s*100", re.I)),
    ("Schema Score",              re.compile(r"Schema Score:\s*\**\s*(\d+)\s*/\s*100", re.I)),
    ("Combined GEO Score",        re.compile(r"Combined GEO Score:\s*\**\s*(\d+)\s*/\s*100", re.I)),
    ("Overall GEO Score",         re.compile(r"(?:Overall\s+)?GEO Score:\s*\**\s*(\d+)\s*/\s*100", re.I)),
    ("Tier 1 accessible",         re.compile(r"Tier\s*1\s+accessible:\s*\**\s*(\d+)\s*/\s*5", re.I)),
    ("Tier 2 accessible",         re.compile(r"Tier\s*2\s+accessible:\s*\**\s*(\d+)\s*/\s*5", re.I)),
    ("Total unique sameAs URLs",  re.compile(r"Total unique sameAs URLs:\s*\**\s*(\d+)", re.I)),
    ("JSON-LD entities parsed",   re.compile(r"JSON-LD entities parsed[^:]*:\s*\**\s*(\d+)", re.I)),
    ("Word count",                re.compile(r"Word count:\s*\**\s*(\d+)", re.I)),
    ("Question-based headings",   re.compile(r"Question-based headings:\s*\**\s*(\d+)", re.I)),
    ("Image alt-coverage %",      re.compile(r"alt coverage:\s*\**\s*(\d+)\s*%", re.I)),
]

DOMAIN_LINE_RE = re.compile(
    r"^\*\*(?:URL|Site|Domain|Site root):\*\*\s*(\S+)", re.I | re.M,
)

# Heuristic mapping: filename → source skill.
SOURCE_SKILL_FROM_NAME = [
    (re.compile(r"CRAWLER", re.I),    "geo-crawlers"),
    (re.compile(r"CITABILITY", re.I), "geo-citability"),
    (re.compile(r"CONTENT",   re.I),  "geo-content"),
    (re.compile(r"TECHNICAL", re.I),  "geo-technical"),
    (re.compile(r"PLATFORM",  re.I),  "geo-platform-optimizer"),
    (re.compile(r"LLMSTXT",   re.I),  "geo-llmstxt"),
    (re.compile(r"SCHEMA",    re.I),  "geo-schema"),
    (re.compile(r"COMPARE|DELTA|MONTHLY", re.I), "geo-compare"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def classify(name: str) -> str:
    for pattern, skill in SOURCE_SKILL_FROM_NAME:
        if pattern.search(name):
            return skill
    return "(unknown)"


def extract_scores(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for label, pattern in SCORE_PATTERNS:
        if label in out:
            continue
        m = pattern.search(text)
        if m:
            try:
                out[label] = int(m.group(1))
            except ValueError:
                pass
    return out


def detect_domain(text: str) -> str | None:
    m = DOMAIN_LINE_RE.search(text)
    if m:
        return m.group(1)
    return None


def is_report_filename(name: str) -> bool:
    nl = name.lower()
    if not (nl.endswith(".md") or nl.endswith(".json")):
        return False
    return any(token in nl for token in (
        "geo-", "audit", "extract", "report", "score", "analysis",
        "monthly", "delta", "client",
    ))


def list_outputs(filter_substr: str | None,
                  explicit_paths: list[str]) -> list[dict]:
    """If `explicit_paths` is non-empty, those are the files (resolved against
    DEFAULT_OUTPUT_DIR for non-absolute paths). Otherwise scan the outputs
    directory, filtering by substring if given."""
    entries: list[dict] = []

    if explicit_paths:
        for p_str in explicit_paths:
            p = Path(p_str).expanduser()
            if not p.is_absolute() and not p.exists():
                p = DEFAULT_OUTPUT_DIR / p_str
            if not p.is_file():
                entries.append({"path": str(p), "name": p.name,
                                "missing": True})
                continue
            st = p.stat()
            entries.append({
                "path": str(p), "name": p.name,
                "size_bytes": st.st_size,
                "mtime": time.strftime("%Y-%m-%d %H:%M:%S",
                                        time.localtime(st.st_mtime)),
                "mtime_epoch": st.st_mtime,
                "missing": False,
            })
        return entries

    if not DEFAULT_OUTPUT_DIR.exists():
        return []
    for p in DEFAULT_OUTPUT_DIR.iterdir():
        if not p.is_file():
            continue
        if not is_report_filename(p.name):
            continue
        if filter_substr and filter_substr.lower() not in p.name.lower():
            # Content match as a fallback (the filter may be a domain that
            # appears in the report body but not the filename).
            try:
                head = p.read_text(encoding="utf-8", errors="ignore")[:4000]
            except Exception:
                head = ""
            if filter_substr.lower() not in head.lower():
                continue
        st = p.stat()
        entries.append({
            "path": str(p), "name": p.name,
            "size_bytes": st.st_size,
            "mtime": time.strftime("%Y-%m-%d %H:%M:%S",
                                    time.localtime(st.st_mtime)),
            "mtime_epoch": st.st_mtime,
            "missing": False,
        })
    entries.sort(key=lambda e: -e["mtime_epoch"])
    return entries


def read_metadata(entry: dict) -> dict:
    """Read a report and pull scores + domain + skill classification."""
    path = Path(entry["path"])
    text = ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        pass
    entry["source_skill"] = classify(entry["name"])
    entry["domain"] = detect_domain(text)
    entry["scores"] = extract_scores(text)
    entry["content"] = text
    entry["truncated"] = len(text) > PER_FILE_CHAR_CAP
    if entry["truncated"]:
        entry["content_for_stdout"] = text[:PER_FILE_CHAR_CAP] + \
            f"\n\n…[truncated — full file is {len(text):,} chars on disk]"
    else:
        entry["content_for_stdout"] = text
    return entry


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

def render(entries: list[dict]) -> tuple[str, dict]:
    """Build the stdout aggregate + a structured summary dict."""
    L: list[str] = [
        "# GEO audit inventory + content",
        "",
        f"**Output folder:** `{DEFAULT_OUTPUT_DIR}`",
        f"**Files found:** {len(entries)}",
        "",
    ]
    if not entries:
        L += ["No GEO audit files found in the outputs folder. Run "
              "`geo-crawlers`, `geo-citability`, `geo-content`, "
              "`geo-technical`, `geo-platform-optimizer`, `geo-llmstxt`, or "
              "`geo-schema` first, then call geo-report again.", ""]
        return "\n".join(L), {"entries": [], "score_rollup": {},
                                "skills_present": [], "domains_present": []}

    L += [
        "## Inventory",
        "",
        "| File | Source skill | Domain | Modified | Size | Key scores |",
        "|---|---|---|---|---|---|",
    ]
    for e in entries:
        if e.get("missing"):
            L.append(f"| {e['name']} | (file not found) | — | — | — | — |")
            continue
        scores = e.get("scores", {})
        score_summary = (
            ", ".join(f"{k}={v}" for k, v in list(scores.items())[:3])
            if scores else "(no numeric scores matched)"
        )
        L.append(
            f"| {e['name']} | {e.get('source_skill', '?')} | "
            f"{e.get('domain') or '—'} | {e['mtime']} | "
            f"{e['size_bytes']:,} bytes | {score_summary} |"
        )
    L.append("")

    # Score rollup by source skill (latest file per skill wins).
    rollup: dict[str, dict] = {}
    for e in sorted(entries, key=lambda x: x.get("mtime_epoch") or 0):
        if e.get("missing"):
            continue
        skill = e.get("source_skill", "(unknown)")
        if e.get("scores"):
            rollup[skill] = {"file": e["name"], "scores": e["scores"]}
    if rollup:
        L += ["## Score rollup (latest file per source skill)", ""]
        L.append("| Source skill | File | Scores |")
        L.append("|---|---|---|")
        for skill, info in rollup.items():
            sc = ", ".join(f"{k}={v}" for k, v in info["scores"].items())
            L.append(f"| {skill} | {info['file']} | {sc} |")
        L.append("")

    # Per-file content (capped).
    L += ["## File contents", ""]
    for e in entries:
        if e.get("missing"):
            continue
        L.append(f"### {e['name']}  ({e.get('source_skill', '?')})")
        L.append("")
        L.append(e.get("content_for_stdout") or "(empty)")
        L.append("")

    # Coverage diagnostics for the LLM.
    skills_present = sorted({e.get("source_skill") for e in entries
                              if e.get("source_skill") and not e.get("missing")})
    domains_present = sorted({e.get("domain") for e in entries
                                if e.get("domain") and not e.get("missing")})
    L += [
        "## Coverage diagnostics",
        "",
        f"- Source skills represented: {skills_present}",
        f"- Domains represented: {domains_present}",
        "",
    ]
    expected = {"geo-crawlers", "geo-citability", "geo-content",
                "geo-technical", "geo-platform-optimizer", "geo-llmstxt",
                "geo-schema"}
    missing_skills = sorted(expected - set(skills_present))
    if missing_skills:
        L.append("- ⚠️ Missing audits (run these for a complete client "
                 f"report): {missing_skills}")
    else:
        L.append("- ✅ All seven core GEO audits are present.")
    L.append("")

    return "\n".join(L).rstrip() + "\n", {
        "entries": [{k: v for k, v in e.items()
                      if k not in ("content", "content_for_stdout")}
                     for e in entries],
        "score_rollup": rollup,
        "skills_present": skills_present,
        "domains_present": domains_present,
        "missing_skills": missing_skills,
    }


# ---------------------------------------------------------------------------
# Output paths
# ---------------------------------------------------------------------------

def resolve_output_paths(output: Path | None) -> tuple[Path, Path]:
    if output is None:
        base = DEFAULT_OUTPUT_DIR
        return base / "GEO-REPORT-INVENTORY.md", base / "GEO-REPORT-INVENTORY.json"
    if output.suffix == "" or output.is_dir():
        return output / "GEO-REPORT-INVENTORY.md", output / "GEO-REPORT-INVENTORY.json"
    return output, output.with_suffix(".json")


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def cmd_list(filter_substr: str | None) -> int:
    entries = list_outputs(filter_substr, [])
    print(f"# Reports in {DEFAULT_OUTPUT_DIR}"
          + (f" matching {filter_substr!r}" if filter_substr else ""))
    print()
    if not entries:
        print("(none)")
        return 0
    print("| File | Source skill | Modified | Size |")
    print("|---|---|---|---|")
    for e in entries:
        print(f"| {e['name']} | {classify(e['name'])} | {e['mtime']} | "
              f"{e['size_bytes']:,} bytes |")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Gather GEO audit files for the geo-report skill.")
    parser.add_argument("inputs", nargs="*",
                        help="Either a substring filter (single arg matching no "
                             "file), or specific filenames (resolved against "
                             "the outputs folder).")
    parser.add_argument("--list", action="store_true",
                        help="Inventory only — no content reading.")
    parser.add_argument("--source", default=None,
                        help="Runner pre-fetch source label (ignored).")
    parser.add_argument("-o", "--output", type=Path, default=None,
                        help="Output path. Default: "
                             "~/Documents/synergyAI/outputs/ (/outputs/ in "
                             "Pyodide).")
    args = parser.parse_args(argv)

    if args.list:
        substr = args.inputs[0] if len(args.inputs) == 1 else None
        return cmd_list(substr)

    # Decide between substring-filter vs explicit-files: a single arg that
    # doesn't end in .md/.json is treated as a filter; multiple args (or
    # explicit .md/.json names) are treated as files.
    inputs = args.inputs or []
    explicit: list[str] = []
    substr: str | None = None
    if len(inputs) == 1:
        only = inputs[0]
        looks_like_file = only.endswith((".md", ".json")) or "/" in only
        if looks_like_file:
            explicit = [only]
        else:
            substr = only
    elif len(inputs) > 1:
        explicit = inputs

    entries = list_outputs(substr, explicit)
    entries = [read_metadata(e) for e in entries if not e.get("missing")] + \
              [e for e in entries if e.get("missing")]

    report, structured = render(entries)

    md_path, json_path = resolve_output_paths(args.output)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(report, encoding="utf-8")
    json_path.write_text(json.dumps(structured, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")

    # Inventory + content + rollup to stdout.
    print(report)

    print(f"[geo-report] {len(entries)} file(s) gathered, "
          f"{len(structured['skills_present'])} skill(s) represented, "
          f"{len(structured['missing_skills'])} missing — wrote {md_path}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
