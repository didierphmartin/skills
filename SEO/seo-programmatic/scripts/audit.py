#!/usr/bin/env python3
"""
audit.py — Programmatic-SEO audit.

Crawl a site (or read a URL list), cluster URLs by inferred template pattern,
sample 3-5 pages per cluster, compute pairwise content similarity (Jaccard on
5-gram shingles), and flag clusters at risk of being doorway pages or thin
content.

Quality gates:
  - WARNING at 100+ pages in a cluster
  - HARD STOP at 500+ pages (use --force to override)
  - Content similarity ≥60% across sample → doorway-page flag

Pyodide-safe. Routes fetches through /gpt/backend/api/v1/fetch-url.

Usage:
    python audit.py URL                        # crawl + audit
    python audit.py --from-list urls.txt       # audit explicit list
    python audit.py URL --max-pages 500 --force
"""

from __future__ import annotations

import os
import argparse
import asyncio
import json
import re
import statistics
import sys
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, urljoin, urldefrag

from bs4 import BeautifulSoup


DEFAULT_OUTPUT_DIR = (
    Path(os.environ.get("SYNERGYAI_OUTPUT_DIR", "/outputs"))
    if sys.platform == "emscripten"
    else Path.home() / "Documents" / "synergyAI" / "outputs"
)
PROXY_ENDPOINT = "/gpt/backend/api/v1/fetch-url"
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_SKIP_EXT = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico",
    ".pdf", ".zip", ".tar", ".gz", ".mp4", ".mov", ".webm",
    ".css", ".js", ".json", ".xml", ".rss", ".atom",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".woff", ".woff2", ".ttf", ".eot",
}

SHINGLE_N = 5

# Quality-gate thresholds
WARN_PAGES = 100
HARDSTOP_PAGES = 500


# ---------------------------------------------------------------------------
# Fetch helpers (same pattern as the other audit skills)
# ---------------------------------------------------------------------------

def _is_pyodide() -> bool:
    return sys.platform == "emscripten"


async def _fetch_pyodide(url: str) -> Optional[dict]:
    try:
        import pyodide.http  # type: ignore[import-not-found]
    except ImportError:
        return None
    headers = {"Content-Type": "application/json"}
    try:
        from js import window  # type: ignore[import-not-found]
        tok = getattr(getattr(window, "authManager", None), "token", None)
        if tok:
            headers["Authorization"] = f"Bearer {tok}"
    except Exception:
        pass
    try:
        resp = await pyodide.http.pyfetch(
            PROXY_ENDPOINT, method="POST", headers=headers,
            body=json.dumps({"url": url}),
        )
        text = await resp.string()
        payload = json.loads(text)
    except Exception:
        return None
    if not payload.get("success"):
        return None
    data = payload.get("data", {}) or {}
    return {
        "body": data.get("html", "") or "",
        "status": data.get("status", 0) or 0,
        "final_url": data.get("final_url", "") or url,
    }


def _fetch_urllib(url: str) -> Optional[dict]:
    import ssl
    import urllib.request
    import gzip
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={
        "User-Agent": BROWSER_UA, "Accept": "text/html,*/*", "Accept-Encoding": "gzip",
    })
    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
            body = r.read()
            status = r.status
            final_url = r.url
            encoding = (r.headers.get("Content-Encoding") or "").lower()
    except Exception:
        return None
    if encoding == "gzip":
        try: body = gzip.decompress(body)
        except OSError: pass
    try:
        return {"body": body.decode("utf-8", errors="replace"), "status": status, "final_url": final_url}
    except Exception:
        return {"body": "", "status": status, "final_url": final_url}


async def fetch(url: str) -> Optional[dict]:
    if _is_pyodide():
        return await _fetch_pyodide(url)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch_urllib, url)


# ---------------------------------------------------------------------------
# Crawler — duplicated from sitemap-audit pattern (per the "duplicate per skill"
# decision in INTEGRATION-PLAN section 1.3)
# ---------------------------------------------------------------------------

def _normalize_url(url: str) -> str:
    url, _ = urldefrag(url)
    p = urlparse(url)
    path = p.path
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return f"{p.scheme}://{p.netloc}{path}{('?' + p.query) if p.query else ''}"


async def crawl(seed_url: str, max_pages: int = 200, concurrency: int = 5) -> list[str]:
    """BFS same-host crawl; return list of discovered URLs (no page content)."""
    parsed = urlparse(seed_url)
    start_host = parsed.netloc.lower()
    seen: set[str] = set()
    discovered: list[str] = []
    queue: deque[str] = deque([_normalize_url(seed_url)])

    while queue and len(discovered) < max_pages:
        batch: list[str] = []
        while queue and len(batch) < concurrency and len(discovered) + len(batch) < max_pages:
            url = queue.popleft()
            if url in seen:
                continue
            seen.add(url)
            batch.append(url)
        if not batch:
            break

        results = await asyncio.gather(*(fetch(u) for u in batch))
        for url, res in zip(batch, results):
            if not res or not res.get("body"):
                continue
            discovered.append(url)
            try:
                soup = BeautifulSoup(res["body"], "html.parser")
            except Exception:
                continue
            for a in soup.find_all("a", href=True):
                href = (a.get("href") or "").strip()
                if not href or href.startswith(("javascript:", "mailto:", "tel:")):
                    continue
                absolute, _ = urldefrag(urljoin(url, href))
                p = urlparse(absolute)
                if p.scheme not in ("http", "https"):
                    continue
                if p.netloc.lower() != start_host:
                    continue
                if any(p.path.lower().endswith(ext) for ext in _SKIP_EXT):
                    continue
                n = _normalize_url(absolute)
                if n not in seen:
                    queue.append(n)
        print(f"  crawl: {len(discovered)}/{max_pages}", file=sys.stderr)
    return discovered


# ---------------------------------------------------------------------------
# URL pattern clustering — infer template from path segments
# ---------------------------------------------------------------------------

def url_to_pattern(url: str) -> str:
    """Convert a URL into a generic pattern. Numeric segments → [num];
    long path segments → [slug]; short single-word segments stay literal.

    Examples:
      /locations/seattle/        → /locations/[slug]/
      /tools/keyword-research/   → /tools/[slug]/
      /blog/2024/05/19/post-title → /blog/[num]/[num]/[num]/[slug]
      /                          → /
    """
    parsed = urlparse(url)
    parts = parsed.path.strip("/").split("/") if parsed.path.strip("/") else []
    out = []
    for part in parts:
        if not part:
            continue
        if part.isdigit():
            out.append("[num]")
        elif len(part) > 12 and "-" in part:
            out.append("[slug]")   # long hyphenated → almost certainly a slug
        elif len(part) > 20:
            out.append("[slug]")   # very long → slug regardless
        else:
            out.append(part)
    return "/" + "/".join(out) + ("/" if parsed.path.endswith("/") else "")


def cluster_urls(urls: list[str]) -> dict[str, list[str]]:
    """Group URLs by their inferred pattern."""
    clusters: dict[str, list[str]] = defaultdict(list)
    for url in urls:
        clusters[url_to_pattern(url)].append(url)
    return clusters


# ---------------------------------------------------------------------------
# Content extraction + similarity
# ---------------------------------------------------------------------------

def extract_main_text(html: str) -> str:
    """Strip nav/header/footer/aside/script/style; return remaining text."""
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return ""
    for el in soup(["script", "style", "nav", "header", "footer", "aside", "noscript"]):
        el.decompose()
    return soup.get_text(" ", strip=True)


def shingles(text: str, n: int = SHINGLE_N) -> set[tuple[str, ...]]:
    """Return the set of word n-grams (shingles) in `text`."""
    words = re.findall(r"\w+", text.lower())
    if len(words) < n:
        return {tuple(words)} if words else set()
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


def jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


# ---------------------------------------------------------------------------
# Per-cluster analysis
# ---------------------------------------------------------------------------

@dataclass
class ClusterReport:
    pattern: str
    urls: list[str] = field(default_factory=list)
    sample_urls: list[str] = field(default_factory=list)
    sample_text_lengths: list[int] = field(default_factory=list)
    pairwise_similarities: list[float] = field(default_factory=list)
    mean_similarity: float = 0.0
    max_similarity: float = 0.0
    severity: str = "ok"     # ok | warn | doorway | hardstop


SEVERITY_WEIGHT = {"hardstop": 30, "doorway": 15, "warn": 5, "ok": 0}


async def analyze_cluster(pattern: str, urls: list[str], sample_size: int,
                            similarity_threshold: float) -> ClusterReport:
    """Sample N pages from the cluster, fetch them, compute pairwise similarity."""
    report = ClusterReport(pattern=pattern, urls=urls)

    if len(urls) < 2:
        report.severity = "ok"
        return report

    # Pick sample (random-ish but deterministic across runs)
    import random
    rng = random.Random(hash(pattern) & 0xFFFFFFFF)
    sample = rng.sample(urls, min(sample_size, len(urls)))
    report.sample_urls = sample

    fetched = await asyncio.gather(*(fetch(u) for u in sample))
    texts = []
    for url, res in zip(sample, fetched):
        if not res or not res.get("body"):
            continue
        text = extract_main_text(res["body"])
        texts.append(text)
        report.sample_text_lengths.append(len(re.findall(r"\w+", text)))

    if len(texts) < 2:
        report.severity = "ok"
        return report

    # Pairwise Jaccard on shingles
    shingled = [shingles(t) for t in texts]
    sims = []
    for i in range(len(shingled)):
        for j in range(i + 1, len(shingled)):
            sims.append(jaccard(shingled[i], shingled[j]))
    report.pairwise_similarities = sims
    if sims:
        report.mean_similarity = round(statistics.mean(sims), 3)
        report.max_similarity = round(max(sims), 3)

    # Severity rules
    n = len(urls)
    if report.mean_similarity >= similarity_threshold:
        report.severity = "doorway"
    elif n >= HARDSTOP_PAGES:
        report.severity = "hardstop"
    elif n >= WARN_PAGES:
        report.severity = "warn"
    else:
        report.severity = "ok"

    return report


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

def render_report(reports: list[ClusterReport], seed: str,
                   total_urls: int, similarity_threshold: float,
                   force_used: bool) -> str:
    deductions = sum(SEVERITY_WEIGHT[r.severity] for r in reports)
    score = max(0, 100 - deductions)

    lines = [
        "# Programmatic-SEO Audit",
        "",
        f"**Source:** {seed}",
        f"**Generated:** {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Total URLs analyzed:** {total_urls}",
        f"**Clusters detected:** {len(reports)}",
        f"**Similarity threshold:** {similarity_threshold} (sample-pairwise Jaccard on 5-gram shingles)",
        "",
        f"## Score: {score}/100",
        "",
    ]

    counts = Counter(r.severity for r in reports)
    lines.append(f"- Doorway-page risk clusters: {counts.get('doorway', 0)}")
    lines.append(f"- Hard-stop clusters (500+ pages, no --force): {counts.get('hardstop', 0)}")
    lines.append(f"- Warning clusters (100+ pages): {counts.get('warn', 0)}")
    lines.append(f"- OK clusters: {counts.get('ok', 0)}")
    if force_used:
        lines.append("")
        lines.append("⚠️ `--force` was passed; hard-stop clusters were audited anyway. Verify uniqueness manually.")
    lines.append("")

    # Cluster summary table
    lines.append("## Cluster summary")
    lines.append("")
    lines.append("| Pattern | Count | Sampled | Mean Jaccard | Max Jaccard | Mean words | Severity |")
    lines.append("|---|---|---|---|---|---|---|")
    sev_order = {"doorway": 0, "hardstop": 1, "warn": 2, "ok": 3}
    for r in sorted(reports, key=lambda x: (sev_order.get(x.severity, 9), -len(x.urls))):
        mean_wc = round(statistics.mean(r.sample_text_lengths), 0) if r.sample_text_lengths else "—"
        sev_emoji = {"doorway": "❌", "hardstop": "🛑", "warn": "⚠️", "ok": "✅"}[r.severity]
        lines.append(
            f"| `{r.pattern}` | {len(r.urls)} | {len(r.sample_urls)} | "
            f"{r.mean_similarity if r.pairwise_similarities else '—'} | "
            f"{r.max_similarity if r.pairwise_similarities else '—'} | "
            f"{mean_wc} | {sev_emoji} {r.severity} |"
        )
    lines.append("")

    # Per-flagged-cluster detail
    flagged = [r for r in reports if r.severity in ("doorway", "hardstop", "warn")]
    if flagged:
        lines.append("## Flagged clusters (detail)")
        lines.append("")
        for r in flagged:
            lines.append(f"### `{r.pattern}` — {r.severity}")
            lines.append("")
            lines.append(f"- URLs in cluster: {len(r.urls)}")
            lines.append(f"- Pages sampled: {len(r.sample_urls)}")
            if r.pairwise_similarities:
                lines.append(f"- Mean pairwise Jaccard: {r.mean_similarity}")
                lines.append(f"- Max pairwise Jaccard: {r.max_similarity}")
            if r.sample_urls:
                lines.append("- Sample URLs:")
                for u in r.sample_urls[:5]:
                    lines.append(f"  - {u}")

            # Recommendation per severity
            if r.severity == "doorway":
                lines.append("")
                lines.append(f"**Action:** mean content similarity is ≥{similarity_threshold}. "
                              "These pages are at high risk of being treated as doorway pages by Google. "
                              "Add genuine per-page differentiation: unique testimonials, photos, "
                              "location-specific copy, distinct facts. Same-template + same-content = "
                              "thin-content penalty risk.")
            elif r.severity == "hardstop":
                lines.append("")
                lines.append(f"**Action:** {len(r.urls)} pages in this cluster exceed the 500-page hard-stop "
                              "threshold. Audit a larger sample (50+ pages) manually for uniqueness before "
                              "treating as safe. Re-run with `--force` only after content review.")
            elif r.severity == "warn":
                lines.append("")
                lines.append(f"**Action:** {len(r.urls)} pages in this cluster trip the 100-page warning. "
                              "Verify each is genuinely differentiated. Common pitfall: only the city/product "
                              "name changes between pages.")
            lines.append("")

    # Top clusters by size (regardless of severity) for situational awareness
    lines.append("## All clusters by size")
    lines.append("")
    for r in sorted(reports, key=lambda x: -len(x.urls))[:20]:
        lines.append(f"- `{r.pattern}` — {len(r.urls)} URL(s) — {r.severity}")
    if len(reports) > 20:
        lines.append(f"- _… {len(reports) - 20} more clusters_")
    lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# CLI / main
# ---------------------------------------------------------------------------

def read_url_list(path: Path) -> list[str]:
    urls = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            urls.append(line)
    return urls


async def amain(args) -> int:
    DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.from_list:
        if not args.from_list.exists():
            print(f"ERROR: URL list not found: {args.from_list}", file=sys.stderr)
            return 1
        urls = read_url_list(args.from_list)
        if not urls:
            print(f"ERROR: URL list at {args.from_list} is empty.", file=sys.stderr)
            return 1
        seed_label = f"list:{args.from_list}"
        print(f"Loaded {len(urls)} URL(s) from {args.from_list}", file=sys.stderr)
    else:
        if not args.input:
            print("ERROR: provide a URL or --from-list", file=sys.stderr)
            return 2
        seed_label = args.input
        print(f"Crawling {args.input} (max {args.max_pages})…", file=sys.stderr)
        urls = await crawl(args.input, max_pages=args.max_pages, concurrency=args.concurrency)
        if not urls:
            print(f"ERROR: crawl from {args.input} discovered no pages.", file=sys.stderr)
            return 1

    clusters = cluster_urls(urls)
    print(f"Detected {len(clusters)} URL pattern(s) across {len(urls)} URL(s).", file=sys.stderr)

    # Hard-stop gate: any cluster ≥500 pages requires --force
    hardstop_clusters = [p for p, members in clusters.items() if len(members) >= HARDSTOP_PAGES]
    if hardstop_clusters and not args.force:
        print(f"\nHARD STOP: {len(hardstop_clusters)} cluster(s) have 500+ pages:",
              file=sys.stderr)
        for p in hardstop_clusters:
            print(f"  {p}: {len(clusters[p])} pages", file=sys.stderr)
        print("\nRe-run with --force after auditing a 50+ page sample manually for uniqueness.",
              file=sys.stderr)
        return 2

    # Analyze each cluster
    print(f"Sampling pages per cluster (sample-size={args.sample_size})…", file=sys.stderr)
    reports = []
    for pattern, members in clusters.items():
        r = await analyze_cluster(pattern, members,
                                    sample_size=args.sample_size,
                                    similarity_threshold=args.similarity_threshold)
        reports.append(r)
        if r.severity != "ok":
            print(f"  [{r.severity}] {pattern} — {len(members)} URLs, "
                  f"mean Jaccard {r.mean_similarity}", file=sys.stderr)

    # Emit report
    out = args.output if args.output else (DEFAULT_OUTPUT_DIR / "PROGRAMMATIC-REPORT.md")
    if out.is_dir():
        out = out / "PROGRAMMATIC-REPORT.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    report_md = render_report(reports, seed_label, len(urls),
                                args.similarity_threshold, args.force)
    if args.stdout:
        sys.stdout.write(report_md)
    else:
        out.write_text(report_md, encoding="utf-8")
        print(f"\nWrote {out}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Programmatic-SEO audit (doorway-page / thin-content detector).")
    parser.add_argument("input", nargs="?", help="Seed URL to crawl. Required unless --from-list.")
    parser.add_argument("--url", dest="url_alias", default=None, help="Alias for the positional URL.")
    parser.add_argument("--from-list", type=Path, default=None,
        help="Audit an explicit list of URLs from a text file (one per line, # comments).")
    parser.add_argument("--max-pages", type=int, default=200, help="Crawl cap (default: 200).")
    parser.add_argument("--concurrency", type=int, default=5, help="Crawl + sample-fetch concurrency.")
    parser.add_argument("--sample-size", type=int, default=4,
        help="Pages to sample per cluster for similarity check (default: 4).")
    parser.add_argument("--similarity-threshold", type=float, default=0.6,
        help="Mean pairwise Jaccard ≥ this triggers a doorway-page flag (default: 0.6).")
    parser.add_argument("--force", action="store_true",
        help="Override the 500+ pages hard stop. Use after manual uniqueness review.")
    parser.add_argument("-o", "--output", type=Path, default=None)
    parser.add_argument("--stdout", action="store_true")
    parser.add_argument("--source", default=None, help="Source URL label for bridge pre-fetch case.")
    args = parser.parse_args(argv)

    if not args.input and args.url_alias:
        args.input = args.url_alias
    if args.source and re.match(r"^https?://", args.source, re.I):
        args.input = args.source

    return asyncio.run(amain(args))


if __name__ == "__main__":
    sys.exit(main())
