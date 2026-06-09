#!/usr/bin/env python3
"""
compare_audits.py — diff two GEO audit reports and compute deltas for the
geo-compare skill.

Regex-scans both files for score markers and crawler statuses, computes the
numeric deltas, and identifies crawler status changes. Filesystem-only — no
network I/O, no external dependencies.

Usage:
    python compare_audits.py BASELINE_FILE CURRENT_FILE
    python compare_audits.py --list                    # list every report in outputs/
    python compare_audits.py --list stripe             # filter by substring
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path


DEFAULT_OUTPUT_DIR = (
    Path(os.environ.get("SYNERGYAI_OUTPUT_DIR", "/outputs"))
    if sys.platform == "emscripten"
    else Path.home() / "Documents" / "synergyAI" / "outputs"
)

# Numeric score markers. Each entry: label -> regex with a single capture group.
# Order matters: more-specific patterns first.
SCORE_PATTERNS = [
    ("AI Visibility Score",          re.compile(r"AI Visibility Score:\s*\**\s*(\d+)\s*/\s*100", re.I)),
    ("Citability Score",             re.compile(r"Citability Score:?\s*\**\s*(\d+)\s*/\s*100", re.I)),
    ("Content Score",                re.compile(r"Content Score:\s*\**\s*(\d+)\s*/\s*100", re.I)),
    ("Technical Score",              re.compile(r"Technical Score:\s*\**\s*(\d+)\s*/\s*100", re.I)),
    ("Schema Score",                 re.compile(r"Schema Score:\s*\**\s*(\d+)\s*/\s*100", re.I)),
    ("Platform Score (combined)",    re.compile(r"Combined GEO Score:\s*\**\s*(\d+)\s*/\s*100", re.I)),
    ("Overall GEO Score",            re.compile(r"(?:Overall\s+)?GEO Score:\s*\**\s*(\d+)\s*/\s*100", re.I)),
    ("Overall Score",                re.compile(r"Overall Score:\s*\**\s*(\d+)\s*/\s*100", re.I)),
    ("Tier 1 accessible",            re.compile(r"Tier\s*1\s+accessible:\s*\**\s*(\d+)\s*/\s*5", re.I)),
    ("Tier 2 accessible",            re.compile(r"Tier\s*2\s+accessible:\s*\**\s*(\d+)\s*/\s*5", re.I)),
    ("Tier 3 accessible",            re.compile(r"Tier\s*3\s+accessible:\s*\**\s*(\d+)\s*/\s*4", re.I)),
    ("Word count",                   re.compile(r"Word count:\s*\**\s*(\d+)", re.I)),
    ("Total words",                  re.compile(r"Total words:\s*\**\s*(\d+)", re.I)),
    ("H1 count",                     re.compile(r"H1 count:\s*\**\s*(\d+)", re.I)),
    ("Blocks",                       re.compile(r"\*\*Blocks:\*\*\s*(\d+)", re.I)),
    ("Total entries (llms.txt)",     re.compile(r"Total entries:\s*\**\s*(\d+)", re.I)),
    ("JSON-LD entities parsed",      re.compile(r"JSON-LD entities parsed[^:]*:\s*\**\s*(\d+)", re.I)),
    ("Total unique sameAs URLs",     re.compile(r"Total unique sameAs URLs:\s*\**\s*(\d+)", re.I)),
    ("Question-based headings",      re.compile(r"Question-based headings:\s*\**\s*(\d+)", re.I)),
    ("Tables",                       re.compile(r"^\s*-\s*Tables:\s*\**\s*(\d+)", re.I | re.M)),
    ("Statistics detected",          re.compile(r"Statistics detected:\s*\**\s*(\d+)", re.I)),
    ("Definition patterns",          re.compile(r"Definition patterns[^:]*:\s*\**\s*(\d+)", re.I)),
    ("Image alt-coverage %",         re.compile(r"alt coverage:\s*\**\s*(\d+)\s*%", re.I)),
]

# Crawlers — extract status from a row like:
# | GPTBot | OpenAI | 1 | Blocked | … |
# or "GPTBot: Allowed" / "- GPTBot: Allowed".
CRAWLERS = [
    "GPTBot", "OAI-SearchBot", "ChatGPT-User", "ClaudeBot", "PerplexityBot",
    "Google-Extended", "GoogleOther", "Applebot-Extended", "Amazonbot",
    "FacebookBot", "CCBot", "anthropic-ai", "Bytespider", "cohere-ai",
]
STATUS_VALUES = {"allowed", "blocked", "not mentioned"}

TIMESTAMP_RE = re.compile(
    r"(?:\*\*Generated:\*\*|\*\*Fetched:\*\*|Generated:|Fetched:|Date:)\s*"
    r"(\d{4}-\d{2}-\d{2}[T\s][\d:]+(?:Z|[+\-]\d{2}:?\d{2})?|\d{4}-\d{2}-\d{2})",
    re.I,
)
DOMAIN_LINE_RE = re.compile(
    r"^\*\*(?:URL|Site|Domain|Site root):\*\*\s*(\S+)", re.I | re.M,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def resolve_path(arg: str) -> Path:
    p = Path(arg).expanduser()
    if p.is_absolute() or p.exists():
        return p
    return DEFAULT_OUTPUT_DIR / arg


def list_outputs(filter_substr: str | None = None) -> list[dict]:
    """Return a list of files under DEFAULT_OUTPUT_DIR matching common report
    filenames, sorted by mtime descending."""
    if not DEFAULT_OUTPUT_DIR.exists():
        return []
    entries: list[dict] = []
    for p in DEFAULT_OUTPUT_DIR.iterdir():
        if not p.is_file():
            continue
        if p.suffix.lower() not in (".md", ".json"):
            continue
        name_l = p.name.lower()
        # Heuristic: keep files that look like GEO reports or contain a
        # substring the user passed.
        if filter_substr:
            if filter_substr.lower() not in name_l:
                continue
        else:
            if not (name_l.startswith("geo-") or "audit" in name_l
                    or "score" in name_l or "report" in name_l
                    or "extract" in name_l):
                continue
        st = p.stat()
        entries.append({
            "path": str(p),
            "name": p.name,
            "size_bytes": st.st_size,
            "mtime": time.strftime("%Y-%m-%d %H:%M:%S",
                                    time.localtime(st.st_mtime)),
            "mtime_epoch": st.st_mtime,
        })
    entries.sort(key=lambda e: -e["mtime_epoch"])
    return entries


def extract_scores(text: str) -> dict[str, int]:
    """Return label -> int for every numeric score the script recognizes.
    If a label matches multiple times, the FIRST match wins (top-of-report)."""
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


def extract_crawler_statuses(text: str) -> dict[str, str]:
    """Find each crawler's status, looking at table rows and `Crawler: Status`
    lines. Returns {crawler_name: status} where status is the normalized
    'Allowed'/'Blocked'/'Not Mentioned'."""
    out: dict[str, str] = {}
    for crawler in CRAWLERS:
        # Table row: "| GPTBot | OpenAI | 1 | Blocked | …"
        table_re = re.compile(
            rf"\|\s*{re.escape(crawler)}\s*\|[^|]*\|[^|]*\|\s*([A-Za-z ]+?)\s*\|",
            re.I,
        )
        m = table_re.search(text)
        if m:
            status = m.group(1).strip().lower()
            if status in STATUS_VALUES:
                out[crawler] = status.title() if status != "not mentioned" else "Not Mentioned"
                continue
        # Inline: "GPTBot: Allowed" / "- GPTBot - Allowed"
        inline_re = re.compile(
            rf"\b{re.escape(crawler)}\b\s*[:\-]?\s*\**?\s*"
            r"(Allowed|Blocked|Not Mentioned)",
            re.I,
        )
        m = inline_re.search(text)
        if m:
            status = m.group(1).strip().lower()
            out[crawler] = status.title() if status != "not mentioned" else "Not Mentioned"
    return out


def extract_metadata(text: str) -> dict:
    out: dict = {"timestamp": None, "domain": None}
    m = TIMESTAMP_RE.search(text)
    if m:
        out["timestamp"] = m.group(1)
    m = DOMAIN_LINE_RE.search(text)
    if m:
        out["domain"] = m.group(1)
    return out


def trend_symbol(delta: int) -> str:
    if delta >= 5:
        return "▲▲"
    if delta >= 1:
        return "▲"
    if delta == 0:
        return "──"
    if delta >= -4:
        return "▼"
    return "▼▼"


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        print(f"ERROR: could not read {path}: {e}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

def render_diff(baseline: dict, current: dict) -> tuple[str, dict]:
    """Build the stdout report + a structured delta dict."""
    L: list[str] = [
        "# GEO audit delta",
        "",
        "## Files",
        "",
        f"- **Baseline:** `{baseline['path']}`",
        f"  - mtime: {baseline['mtime']}",
        f"  - timestamp in body: {baseline['metadata']['timestamp'] or '(not found)'}",
        f"  - domain: {baseline['metadata']['domain'] or '(not found)'}",
        f"- **Current:** `{current['path']}`",
        f"  - mtime: {current['mtime']}",
        f"  - timestamp in body: {current['metadata']['timestamp'] or '(not found)'}",
        f"  - domain: {current['metadata']['domain'] or '(not found)'}",
        "",
    ]

    # Numeric scores
    base_scores = baseline["scores"]
    cur_scores = current["scores"]
    common = [(label, base_scores[label], cur_scores[label])
              for label in base_scores if label in cur_scores]
    only_base = [(label, base_scores[label]) for label in base_scores
                  if label not in cur_scores]
    only_cur = [(label, cur_scores[label]) for label in cur_scores
                 if label not in base_scores]

    delta_rows: list[dict] = []
    L += ["## Numeric scores — present in BOTH reports", ""]
    if common:
        L.append("| Metric | Baseline | Current | Δ | Trend |")
        L.append("|---|---|---|---|---|")
        for label, b, c in common:
            d = c - b
            L.append(f"| {label} | {b} | {c} | {'+' if d >= 0 else ''}{d} "
                     f"| {trend_symbol(d)} |")
            delta_rows.append({"label": label, "baseline": b, "current": c,
                                "delta": d, "trend": trend_symbol(d)})
    else:
        L.append("(no overlapping numeric scores)")
    L.append("")

    if only_base:
        L += ["## Numeric scores — only in BASELINE", ""]
        for label, v in only_base:
            L.append(f"- {label}: {v}  (no value in current report — was it a "
                     "different audit type, or removed from the new template?)")
        L.append("")
    if only_cur:
        L += ["## Numeric scores — only in CURRENT", ""]
        for label, v in only_cur:
            L.append(f"- {label}: {v}  (new tracking — not present in baseline)")
        L.append("")

    # Crawler statuses
    base_cr = baseline["crawler_statuses"]
    cur_cr = current["crawler_statuses"]
    all_crawlers = sorted(set(base_cr) | set(cur_cr))
    changes: list[dict] = []
    unchanged_count = 0
    for c in all_crawlers:
        b = base_cr.get(c, "(not reported)")
        u = cur_cr.get(c, "(not reported)")
        if b != u:
            changes.append({"crawler": c, "baseline": b, "current": u})
        elif b == u and b != "(not reported)":
            unchanged_count += 1

    L += ["## AI crawler status — changes only", ""]
    if changes:
        L.append("| Crawler | Baseline | Current | Net change |")
        L.append("|---|---|---|---|")
        for ch in changes:
            net = _crawler_net_change(ch["baseline"], ch["current"])
            L.append(f"| {ch['crawler']} | {ch['baseline']} | {ch['current']} "
                     f"| {net} |")
    else:
        L.append("No crawler status changed between baseline and current.")
    L.append("")
    L.append(f"Unchanged: **{unchanged_count}** crawler(s) reported the same "
             "status in both reports.")
    if not (base_cr or cur_cr):
        L.append("")
        L.append("Neither report contained recognizable crawler-status lines "
                 "(neither file is from `geo-crawlers` or a hand-written "
                 "report using its format).")
    L.append("")

    structured = {
        "baseline_path": baseline["path"],
        "current_path": current["path"],
        "baseline_timestamp": baseline["metadata"]["timestamp"],
        "current_timestamp": current["metadata"]["timestamp"],
        "domain": current["metadata"]["domain"] or baseline["metadata"]["domain"],
        "score_deltas": delta_rows,
        "scores_only_baseline": [{"label": l, "value": v} for l, v in only_base],
        "scores_only_current": [{"label": l, "value": v} for l, v in only_cur],
        "crawler_changes": changes,
        "crawler_unchanged_count": unchanged_count,
    }

    return "\n".join(L).rstrip() + "\n", structured


def _crawler_net_change(baseline: str, current: str) -> str:
    accessible = {"Allowed", "Not Mentioned"}
    b_ok = baseline in accessible
    c_ok = current in accessible
    if b_ok and not c_ok:
        return "❌ lost access"
    if not b_ok and c_ok:
        return "✅ gained access"
    return "—"


# ---------------------------------------------------------------------------
# Output paths
# ---------------------------------------------------------------------------

def resolve_output_paths(output: Path | None) -> tuple[Path, Path]:
    if output is None:
        base = DEFAULT_OUTPUT_DIR
        return base / "GEO-COMPARE-DELTA.md", base / "GEO-COMPARE-DELTA.json"
    if output.suffix == "" or output.is_dir():
        return output / "GEO-COMPARE-DELTA.md", output / "GEO-COMPARE-DELTA.json"
    return output, output.with_suffix(".json")


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def parse_file(path_str: str) -> dict:
    path = resolve_path(path_str)
    if not path.is_file():
        print(f"ERROR: file not found: {path} "
              f"(also tried {DEFAULT_OUTPUT_DIR / path_str})", file=sys.stderr)
        sys.exit(1)
    text = read_text(path)
    st = path.stat()
    return {
        "path": str(path),
        "name": path.name,
        "mtime": time.strftime("%Y-%m-%d %H:%M:%S",
                                time.localtime(st.st_mtime)),
        "mtime_epoch": st.st_mtime,
        "scores": extract_scores(text),
        "crawler_statuses": extract_crawler_statuses(text),
        "metadata": extract_metadata(text),
    }


def cmd_list(filter_substr: str | None) -> int:
    entries = list_outputs(filter_substr)
    if not entries:
        print(f"No matching reports in {DEFAULT_OUTPUT_DIR}", file=sys.stderr)
        return 0
    print(f"# Reports in {DEFAULT_OUTPUT_DIR}"
          + (f" matching {filter_substr!r}" if filter_substr else ""))
    print()
    print("| File | Modified | Size |")
    print("|---|---|---|")
    for e in entries:
        print(f"| {e['name']} | {e['mtime']} | {e['size_bytes']:,} bytes |")
    print()
    print("Pick two and call this script again with them as the two "
          "positional args (baseline first, current second).")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare two GEO audit reports and compute deltas.")
    parser.add_argument("baseline", nargs="?", default=None,
                        help="Baseline report file (absolute path, or filename "
                             "in the outputs/ folder).")
    parser.add_argument("current", nargs="?", default=None,
                        help="Current report file.")
    parser.add_argument("--list", dest="list_filter", nargs="?", const="",
                        default=None,
                        help="List report files in outputs/. Pass an optional "
                             "substring to filter (e.g. --list stripe).")
    parser.add_argument("--source", default=None,
                        help="Runner pre-fetch source label (ignored — this "
                             "script does no network I/O).")
    parser.add_argument("-o", "--output", type=Path, default=None,
                        help="Output path. Default: "
                             "~/Documents/synergyAI/outputs/ (/outputs/ in "
                             "Pyodide).")
    args = parser.parse_args(argv)

    if args.list_filter is not None:
        return cmd_list(args.list_filter or None)

    if not args.baseline or not args.current:
        print("ERROR: provide two files (baseline first, current second), or "
              "use --list to see available reports.", file=sys.stderr)
        return 2

    baseline = parse_file(args.baseline)
    current = parse_file(args.current)

    # Heuristic: if mtime of "baseline" is newer than "current", warn — the
    # user may have passed them in the wrong order.
    warnings: list[str] = []
    if baseline["mtime_epoch"] > current["mtime_epoch"]:
        warnings.append(
            "the 'baseline' file is NEWER on disk than the 'current' file. "
            "Did you pass them in the wrong order? (baseline first, "
            "current second)")

    report, structured = render_diff(baseline, current)
    if warnings:
        report = ("# ⚠️ Possible argument-order issue\n\n"
                  + "\n".join(f"- {w}" for w in warnings) + "\n\n" + report)

    md_path, json_path = resolve_output_paths(args.output)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(report, encoding="utf-8")
    json_path.write_text(json.dumps(structured, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")

    # Deltas to stdout — what run_skill_script returns to the model.
    print(report)

    score_count = len(structured["score_deltas"])
    print(f"[geo-compare] {score_count} overlapping score(s), "
          f"{len(structured['crawler_changes'])} crawler change(s), "
          f"{structured['crawler_unchanged_count']} crawler(s) unchanged "
          f"— wrote {md_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
