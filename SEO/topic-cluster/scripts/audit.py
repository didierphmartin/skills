#!/usr/bin/env python3
"""
audit.py — SERP-overlap-driven topic clustering.

Pipeline:
  1. Expand a seed keyword into 20-30 variants (heuristic).
  2. Fetch Google top-10 organic results for each variant via SerpAPI.
  3. Cluster by pairwise top-10 URL overlap (threshold rules).
  4. Classify intent per keyword (rule-based).
  5. Pick pillar (highest aggregate overlap + broadest intent).
  6. Design hub-and-spoke: 2-5 clusters × 2-4 spokes each.
  7. Generate internal link matrix.
  8. Emit cluster-plan.json, cluster-plan.md, cluster-map.html.

Pyodide-safe: routes SerpAPI calls through /gpt/backend/api/v1/fetch-url.
SerpAPI key read from <skill>/.env (SERPAPI_API_KEY=...).

Usage:
    python audit.py "seed keyword"                          # default (20 keywords)
    python audit.py "seed keyword" --max-keywords 30
    python audit.py "seed keyword" --gl fr --hl fr
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode, urlparse


DEFAULT_OUTPUT_DIR = (
    Path(os.environ.get("SYNERGYAI_OUTPUT_DIR", "/outputs"))
    if sys.platform == "emscripten"
    else Path.home() / "Documents" / "synergyAI" / "outputs"
)
PROXY_ENDPOINT = "/gpt/backend/api/v1/fetch-url"
SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent

# Hard cap on SerpAPI calls per run — protects against accidental cost spikes.
MAX_KEYWORDS_CAP = 50


# ---------------------------------------------------------------------------
# Seed expansion (heuristic, no API cost)
# ---------------------------------------------------------------------------

# Modifiers to combine with the seed. Order matters slightly — front-loaded
# modifiers are more likely to be added; trailing ones fill in as needed.
_PREFIX_MODIFIERS = [
    "best", "top", "how to", "why", "what is",
]
_SUFFIX_MODIFIERS = [
    "guide", "tutorial", "template", "checklist", "examples",
    "vs", "alternatives", "for beginners", "for small business",
    "free", "tools",
]
_QUESTION_MODIFIERS = [
    "what is {0}", "how to use {0}", "why {0} matters",
    "when to use {0}", "where to learn {0}", "who needs {0}",
    "{0} vs", "{0} alternatives", "{0} pricing", "{0} review",
]
_INTENT_MODIFIERS = [
    "best {0}", "{0} comparison", "{0} pricing", "{0} review",
]


def expand_seed(seed: str, target: int = 20, cap: int = MAX_KEYWORDS_CAP) -> list[str]:
    """Expand a seed keyword into up to `cap` variants.

    Variants are derived deterministically — same seed yields same expansion.
    Includes the bare seed as the first entry so it's always represented.
    """
    seed = seed.strip()
    variants: list[str] = [seed]
    seen: set[str] = {seed.lower()}

    def add(v: str) -> None:
        v = re.sub(r"\s+", " ", v.strip())
        key = v.lower()
        if key not in seen and v:
            seen.add(key)
            variants.append(v)

    # Question forms (often have distinct SERPs)
    for tmpl in _QUESTION_MODIFIERS:
        add(tmpl.format(seed))
        if len(variants) >= cap:
            return variants[:cap]

    # Prefix modifiers
    for mod in _PREFIX_MODIFIERS:
        add(f"{mod} {seed}")
        if len(variants) >= cap:
            return variants[:cap]

    # Suffix modifiers
    for mod in _SUFFIX_MODIFIERS:
        add(f"{seed} {mod}")
        if len(variants) >= cap:
            return variants[:cap]

    # Intent variants
    for tmpl in _INTENT_MODIFIERS:
        add(tmpl.format(seed))
        if len(variants) >= cap:
            return variants[:cap]

    return variants[:max(min(target, cap), 1)]


# ---------------------------------------------------------------------------
# Intent classifier (rule-based)
# ---------------------------------------------------------------------------

_INFO_TOKENS  = {"how", "what", "why", "when", "where", "who", "guide", "tutorial",
                  "learn", "examples", "explain", "explained", "definition", "meaning"}
_COMM_TOKENS  = {"best", "top", "review", "reviews", "comparison", "compare",
                  "vs", "versus", "alternative", "alternatives", "rated", "ranking"}
_TRANS_TOKENS = {"buy", "price", "pricing", "cost", "discount", "coupon", "order",
                  "subscribe", "sign", "demo", "free", "trial", "download"}
_NAV_PATTERNS = re.compile(r"\b(login|signin|sign in|account|dashboard)\b", re.I)


def classify_intent(keyword: str) -> str:
    """Return one of: 'informational', 'commercial', 'transactional', 'navigational'.

    Rule-based. Picks the highest-scoring intent; ties broken in priority:
    transactional > commercial > navigational > informational.
    """
    if _NAV_PATTERNS.search(keyword):
        return "navigational"

    tokens = set(re.findall(r"\w+", keyword.lower()))
    info_score = len(tokens & _INFO_TOKENS)
    comm_score = len(tokens & _COMM_TOKENS)
    trans_score = len(tokens & _TRANS_TOKENS)

    # Tie-break order: transactional > commercial > informational
    if trans_score > 0 and trans_score >= comm_score and trans_score >= info_score:
        return "transactional"
    if comm_score > 0 and comm_score >= info_score:
        return "commercial"
    if info_score > 0:
        return "informational"
    # Bare seed or unknown → informational default
    return "informational"


# ---------------------------------------------------------------------------
# SerpAPI client (same pattern as serp-shape)
# ---------------------------------------------------------------------------

def load_env_key() -> str:
    """Read SERPAPI_API_KEY from <skill>/.env or environment."""
    env_path = SKILL_DIR / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line.startswith("SERPAPI_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("SERPAPI_API_KEY", "")


def _is_pyodide() -> bool:
    return sys.platform == "emscripten"


async def _fetch_pyodide(url: str) -> Optional[str]:
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
    return (payload.get("data", {}) or {}).get("html", "") or ""


def _fetch_urllib(url: str) -> Optional[str]:
    import ssl
    import urllib.request
    import gzip
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={
        "User-Agent": "topic-cluster/1.0",
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
    })
    try:
        with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
            body_bytes = r.read()
            encoding = (r.headers.get("Content-Encoding") or "").lower()
    except Exception:
        return None
    if encoding == "gzip":
        try:
            body_bytes = gzip.decompress(body_bytes)
        except OSError:
            pass
    try:
        return body_bytes.decode("utf-8", errors="replace")
    except Exception:
        return None


async def fetch_text(url: str) -> Optional[str]:
    if _is_pyodide():
        return await _fetch_pyodide(url)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch_urllib, url)


async def serpapi_top10(keyword: str, api_key: str,
                          gl: str = "us", hl: str = "en",
                          location: Optional[str] = None) -> tuple[list[str], dict]:
    """Fetch top-10 organic URLs for a keyword via SerpAPI.

    Returns (urls, raw_response_subset). On API failure: empty list + error dict.
    """
    params = {
        "engine": "google",
        "q": keyword,
        "num": 10,
        "gl": gl,
        "hl": hl,
        "google_domain": "google.com",
        "api_key": api_key,
    }
    if location:
        params["location"] = location
    url = "https://serpapi.com/search?" + urlencode(params)
    body = await fetch_text(url)
    if not body:
        return ([], {"error": "fetch_failed", "keyword": keyword})
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return ([], {"error": "non_json_response", "keyword": keyword, "preview": body[:200]})
    if data.get("error"):
        return ([], {"error": "serpapi_error", "keyword": keyword, "message": data["error"]})
    results = data.get("organic_results") or []
    urls = []
    for r in results[:10]:
        link = r.get("link")
        if link:
            urls.append(link)
    related = data.get("related_searches") or []
    paa = data.get("related_questions") or []
    return (urls, {
        "keyword": keyword,
        "urls": urls,
        "related_searches": [r.get("query") for r in related if r.get("query")][:5],
        "paa": [q.get("question") for q in paa if q.get("question")][:5],
    })


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------

# Threshold rules per the spec
_OVERLAP_MERGE = 7       # 7-10 shared URLs → same post
_OVERLAP_CLUSTER = 4     # 4-6 shared → same cluster
_OVERLAP_INTERLINK = 2   # 2-3 shared → adjacent clusters, cross-links
# Below 2: separate


@dataclass
class KeywordRecord:
    keyword: str
    intent: str = "informational"
    top10: list[str] = field(default_factory=list)
    related_searches: list[str] = field(default_factory=list)
    paa: list[str] = field(default_factory=list)
    cluster_id: int = -1   # filled in by clustering


def pairwise_overlaps(records: list[KeywordRecord]) -> dict[tuple[int, int], int]:
    """Return {(i, j): shared_count} for every pair where i < j."""
    overlaps: dict[tuple[int, int], int] = {}
    n = len(records)
    sets = [set(r.top10) for r in records]
    for i in range(n):
        si = sets[i]
        for j in range(i + 1, n):
            sj = sets[j]
            shared = len(si & sj)
            if shared > 0:
                overlaps[(i, j)] = shared
    return overlaps


def cluster_by_overlap(records: list[KeywordRecord],
                        overlaps: dict[tuple[int, int], int]) -> tuple[list[list[int]], list[tuple[int, int, int]]]:
    """Greedy union-find clustering by SERP overlap.

    Returns:
      clusters: list of lists, each inner list is keyword indices in one cluster
      interlinks: list of (i, j, shared) for cross-cluster pairs (overlap 2-3)
    """
    n = len(records)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # Sort pairs by overlap (highest first) so strongest signals win when greedy.
    sorted_pairs = sorted(overlaps.items(), key=lambda kv: -kv[1])

    interlinks: list[tuple[int, int, int]] = []
    for (i, j), shared in sorted_pairs:
        if shared >= _OVERLAP_CLUSTER:
            # Belongs in the same cluster (either same post or same cluster)
            union(i, j)
        elif shared >= _OVERLAP_INTERLINK:
            interlinks.append((i, j, shared))
        # else: separate — no action

    # Build clusters by root
    groups: dict[int, list[int]] = defaultdict(list)
    for idx in range(n):
        groups[find(idx)].append(idx)

    clusters = sorted(groups.values(), key=lambda g: -len(g))

    # Tag each record with cluster_id (1-based; 0 reserved for pillar)
    for cid, members in enumerate(clusters, start=1):
        for idx in members:
            records[idx].cluster_id = cid

    return (clusters, interlinks)


def pick_pillar(records: list[KeywordRecord],
                 overlaps: dict[tuple[int, int], int]) -> int:
    """Return the index of the keyword to use as the pillar.

    Heuristic: highest aggregate overlap with all other keywords + informational
    intent preferred (pillar pages are typically broad informational).
    """
    n = len(records)
    score = [0] * n
    for (i, j), shared in overlaps.items():
        score[i] += shared
        score[j] += shared

    # Boost informational intent slightly so pillar tilts toward broad guides
    INFO_BOOST = 5
    for i, r in enumerate(records):
        if r.intent == "informational":
            score[i] += INFO_BOOST

    # Pick max. Ties broken by shortest keyword (broader-sounding).
    best_idx = 0
    best_score = -1
    for i in range(n):
        s = score[i]
        if s > best_score or (s == best_score and len(records[i].keyword) < len(records[best_idx].keyword)):
            best_score = s
            best_idx = i
    return best_idx


# ---------------------------------------------------------------------------
# Hub-and-spoke architecture
# ---------------------------------------------------------------------------

# Map intent → recommended template type
INTENT_TEMPLATE = {
    "informational": "ultimate-guide",
    "commercial":    "comparison",
    "transactional": "landing-page",
}


@dataclass
class Post:
    keyword: str
    intent: str
    template: str
    word_count: int
    cluster: int  # 0 = pillar; 1..N = spokes
    is_pillar: bool = False
    paa: list[str] = field(default_factory=list)


@dataclass
class Link:
    """Internal link recommendation (anchor text omitted — author fills it)."""
    from_post: str
    to_post: str
    type: str  # 'mandatory' | 'cross-cluster' | 'sibling'


def design_hub_spoke(records: list[KeywordRecord], pillar_idx: int,
                      clusters: list[list[int]]) -> tuple[list[Post], list[Link]]:
    """Build the post list + link matrix.

    Returns (posts, links). Pillar is post 0 with cluster=0; spokes follow.
    """
    pillar = records[pillar_idx]
    posts: list[Post] = [Post(
        keyword=pillar.keyword,
        intent=pillar.intent,
        template="ultimate-guide",
        word_count=3000,  # pillar target
        cluster=0,
        is_pillar=True,
        paa=list(pillar.paa[:5]),
    )]
    keyword_to_post_idx: dict[str, int] = {pillar.keyword: 0}

    # Filter out navigational keywords and the pillar from the clusters
    excluded_keywords = {records[pillar_idx].keyword.lower()}
    for cluster in clusters:
        for idx in cluster:
            r = records[idx]
            if r.intent == "navigational":
                excluded_keywords.add(r.keyword.lower())
                continue
            if r.keyword.lower() in excluded_keywords:
                continue
            posts.append(Post(
                keyword=r.keyword,
                intent=r.intent,
                template=INTENT_TEMPLATE.get(r.intent, "ultimate-guide"),
                word_count=1500,
                cluster=r.cluster_id,
                is_pillar=False,
                paa=list(r.paa[:3]),
            ))
            keyword_to_post_idx[r.keyword] = len(posts) - 1
            excluded_keywords.add(r.keyword.lower())

    # Build link matrix
    links: list[Link] = []
    pillar_kw = pillar.keyword
    by_cluster: dict[int, list[str]] = defaultdict(list)
    for p in posts:
        if not p.is_pillar:
            by_cluster[p.cluster].append(p.keyword)

    # Mandatory: every spoke ↔ pillar
    for spokes in by_cluster.values():
        for spoke_kw in spokes:
            links.append(Link(from_post=spoke_kw, to_post=pillar_kw, type="mandatory"))
            links.append(Link(from_post=pillar_kw, to_post=spoke_kw, type="mandatory"))

    # Sibling: spoke ↔ spoke within cluster (up to 2 per spoke)
    for spokes in by_cluster.values():
        for i, a in enumerate(spokes):
            for b in spokes[i + 1:min(i + 3, len(spokes))]:
                links.append(Link(from_post=a, to_post=b, type="sibling"))
                links.append(Link(from_post=b, to_post=a, type="sibling"))

    return (posts, links)


# ---------------------------------------------------------------------------
# Output emitters
# ---------------------------------------------------------------------------

def emit_plan_json(records: list[KeywordRecord], posts: list[Post],
                    links: list[Link], pillar_kw: str, seed: str,
                    meta: dict) -> str:
    payload = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "seed": seed,
        "pillar": pillar_kw,
        "meta": meta,
        "keywords": [
            {
                "keyword": r.keyword,
                "intent": r.intent,
                "cluster_id": r.cluster_id,
                "top10": r.top10,
                "related_searches": r.related_searches,
                "paa": r.paa,
            }
            for r in records
        ],
        "posts": [asdict(p) for p in posts],
        "links": [asdict(l) for l in links],
    }
    return json.dumps(payload, indent=2)


def emit_plan_md(records: list[KeywordRecord], posts: list[Post],
                  links: list[Link], pillar_kw: str, seed: str,
                  interlinks_kw: list[tuple[str, str, int]]) -> str:
    pillar_post = next(p for p in posts if p.is_pillar)
    by_cluster: dict[int, list[Post]] = defaultdict(list)
    for p in posts:
        if not p.is_pillar:
            by_cluster[p.cluster].append(p)

    lines = [
        f"# Topic Cluster: {seed}",
        "",
        f"**Pillar:** {pillar_kw}",
        f"**Total posts:** {len(posts)} (1 pillar + {len(posts) - 1} spoke(s))",
        f"**Clusters:** {len(by_cluster)}",
        f"**Internal links:** {len(links)}",
        f"**Generated:** {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Pillar Page",
        "",
        f"### {pillar_post.keyword}",
        f"- **Intent:** {pillar_post.intent}",
        f"- **Template:** {pillar_post.template}",
        f"- **Word count target:** {pillar_post.word_count}",
        "",
    ]
    if pillar_post.paa:
        lines.append("**People Also Ask (cover these):**")
        for q in pillar_post.paa:
            lines.append(f"- {q}")
        lines.append("")

    for cid, cluster_posts in sorted(by_cluster.items()):
        lines.append(f"## Cluster {cid} ({len(cluster_posts)} spoke(s))")
        lines.append("")
        for p in cluster_posts:
            lines.append(f"### {p.keyword}")
            lines.append(f"- **Intent:** {p.intent}")
            lines.append(f"- **Template:** {p.template}")
            lines.append(f"- **Word count target:** {p.word_count}")
            if p.paa:
                lines.append("- **PAA to cover:**")
                for q in p.paa:
                    lines.append(f"    - {q}")
            lines.append("")

    if interlinks_kw:
        lines.append("## Cross-cluster interlinks (recommended)")
        lines.append("")
        lines.append("| From | To | Shared SERP URLs |")
        lines.append("|---|---|---|")
        for a, b, shared in interlinks_kw[:20]:
            lines.append(f"| {a} | {b} | {shared} |")
        lines.append("")

    lines.append("## Internal link matrix")
    lines.append("")
    lines.append(f"Total mandatory links: {sum(1 for l in links if l.type == 'mandatory')}")
    lines.append(f"Total sibling links: {sum(1 for l in links if l.type == 'sibling')}")
    lines.append("")
    lines.append("Every spoke must link to the pillar and vice-versa. Sibling links (spoke↔spoke within a cluster) reinforce topical authority. Cross-cluster links are optional but help readers navigate adjacent topics.")
    lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def emit_cluster_map_html(records: list[KeywordRecord], posts: list[Post],
                           pillar_kw: str, seed: str) -> str:
    """Self-contained HTML visualization. No JS dependencies; pure CSS + SVG."""
    pillar_post = next(p for p in posts if p.is_pillar)
    by_cluster: dict[int, list[Post]] = defaultdict(list)
    for p in posts:
        if not p.is_pillar:
            by_cluster[p.cluster].append(p)

    # Color palette per cluster (cycled)
    palette = ["#4f8cff", "#ff6b6b", "#22c55e", "#f59e0b", "#8b5cf6",
                "#06b6d4", "#ec4899", "#84cc16"]

    cluster_blocks = []
    for i, (cid, cluster_posts) in enumerate(sorted(by_cluster.items())):
        color = palette[i % len(palette)]
        posts_html = "".join(
            f'<li><span class="intent intent-{p.intent}">{p.intent[0:3]}</span> {p.keyword}</li>'
            for p in cluster_posts
        )
        cluster_blocks.append(
            f'<section class="cluster" style="--cluster-color: {color}">'
            f'<h3>Cluster {cid} ({len(cluster_posts)} spoke{"s" if len(cluster_posts) > 1 else ""})</h3>'
            f'<ul>{posts_html}</ul>'
            f'</section>'
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<title>Topic Cluster: {_html_escape(seed)}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #fafafa; color: #1a1a1a; padding: 2rem;
  }}
  h1 {{ font-size: 1.6rem; margin-bottom: 0.25rem; }}
  .meta {{ color: #6b7280; margin-bottom: 2rem; font-size: 0.9rem; }}
  .pillar {{
    background: white; border: 3px solid #1e3a5f;
    padding: 1.5rem; border-radius: 12px; max-width: 600px;
    margin: 0 auto 2rem; text-align: center; box-shadow: 0 4px 6px rgba(0,0,0,0.05);
  }}
  .pillar-label {{ font-size: 0.75rem; color: #1e3a5f; font-weight: 600; letter-spacing: 0.1em; text-transform: uppercase; }}
  .pillar h2 {{ font-size: 1.4rem; margin: 0.5rem 0; }}
  .pillar .stats {{ color: #6b7280; font-size: 0.85rem; }}
  .clusters {{
    display: grid; gap: 1.5rem;
    grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  }}
  .cluster {{
    background: white; padding: 1.25rem;
    border-left: 4px solid var(--cluster-color);
    border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.04);
  }}
  .cluster h3 {{ font-size: 1rem; margin-bottom: 0.75rem; color: var(--cluster-color); }}
  .cluster ul {{ list-style: none; }}
  .cluster li {{
    padding: 0.4rem 0; border-bottom: 1px solid #f3f4f6;
    font-size: 0.9rem; display: flex; align-items: center; gap: 0.5rem;
  }}
  .cluster li:last-child {{ border-bottom: none; }}
  .intent {{
    display: inline-block; padding: 0.15rem 0.4rem;
    font-size: 0.65rem; font-weight: 700; letter-spacing: 0.05em;
    text-transform: uppercase; border-radius: 3px;
  }}
  .intent-informational {{ background: #dbeafe; color: #1e40af; }}
  .intent-commercial {{ background: #fef3c7; color: #92400e; }}
  .intent-transactional {{ background: #dcfce7; color: #166534; }}
  .intent-navigational {{ background: #fee2e2; color: #991b1b; }}
</style>
</head>
<body>
  <h1>Topic Cluster: {_html_escape(seed)}</h1>
  <div class="meta">
    {len(posts)} posts · {len(by_cluster)} cluster{"s" if len(by_cluster) > 1 else ""} ·
    Generated {time.strftime('%Y-%m-%d %H:%M:%S')}
  </div>
  <div class="pillar">
    <div class="pillar-label">Pillar Page</div>
    <h2>{_html_escape(pillar_post.keyword)}</h2>
    <div class="stats">{pillar_post.template} · {pillar_post.word_count} words · {pillar_post.intent}</div>
  </div>
  <div class="clusters">
    {"".join(cluster_blocks)}
  </div>
</body>
</html>
"""


def _html_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;").replace("'", "&#39;"))


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

async def amain(args) -> int:
    DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_dir = args.output if args.output else DEFAULT_OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    api_key = load_env_key()
    if not api_key:
        print("ERROR: SERPAPI_API_KEY not found. Set it in <skill>/.env or env var.",
              file=sys.stderr)
        return 1

    # Step 1: seed expansion
    target = max(2, min(args.max_keywords, MAX_KEYWORDS_CAP))
    keywords = expand_seed(args.seed, target=target, cap=target)
    print(f"Expanded seed into {len(keywords)} keyword(s):", file=sys.stderr)
    for kw in keywords:
        print(f"  - {kw}", file=sys.stderr)

    # Step 2: SerpAPI fetches (concurrent, capped concurrency to be polite)
    print(f"\nFetching SerpAPI top-10 for {len(keywords)} keyword(s) "
          f"(concurrency={args.concurrency})…", file=sys.stderr)
    sem = asyncio.Semaphore(args.concurrency)

    async def fetch_one(kw: str) -> dict:
        async with sem:
            urls, meta = await serpapi_top10(kw, api_key,
                                              gl=args.gl, hl=args.hl,
                                              location=args.location)
            return meta

    serp_results = await asyncio.gather(*(fetch_one(k) for k in keywords))

    # Build keyword records
    records: list[KeywordRecord] = []
    failed = []
    for kw, meta in zip(keywords, serp_results):
        if meta.get("error"):
            failed.append((kw, meta["error"]))
            continue
        records.append(KeywordRecord(
            keyword=kw,
            intent=classify_intent(kw),
            top10=meta.get("urls", []),
            related_searches=meta.get("related_searches", []),
            paa=meta.get("paa", []),
        ))

    if failed:
        print(f"\nWarning: {len(failed)} SerpAPI fetch(es) failed:", file=sys.stderr)
        for kw, err in failed:
            print(f"  - {kw}: {err}", file=sys.stderr)

    if len(records) < 2:
        print("ERROR: not enough successful SerpAPI fetches to cluster (need ≥2).",
              file=sys.stderr)
        return 1

    print(f"\nClustering {len(records)} keyword(s) by SERP overlap…", file=sys.stderr)

    # Step 3 + 4: clustering + intent (intent already done above)
    overlaps = pairwise_overlaps(records)
    clusters, interlinks = cluster_by_overlap(records, overlaps)
    print(f"  → {len(clusters)} cluster(s); {len(interlinks)} cross-cluster interlink(s)",
          file=sys.stderr)

    # Step 5: pick pillar
    pillar_idx = pick_pillar(records, overlaps)
    pillar_kw = records[pillar_idx].keyword
    print(f"  → pillar: {pillar_kw!r}", file=sys.stderr)

    # Step 6 + 7: hub-and-spoke + link matrix
    posts, links = design_hub_spoke(records, pillar_idx, clusters)

    # Convert interlinks indices → keyword pairs for the markdown report
    interlinks_kw = [
        (records[i].keyword, records[j].keyword, shared)
        for (i, j, shared) in interlinks
    ]

    # Step 8: emit outputs
    meta = {
        "max_keywords": args.max_keywords,
        "concurrency": args.concurrency,
        "gl": args.gl, "hl": args.hl, "location": args.location,
        "serpapi_calls": len(keywords),
        "serpapi_failed": len(failed),
        "cluster_count": len(clusters),
        "interlink_count": len(interlinks),
    }

    plan_json = emit_plan_json(records, posts, links, pillar_kw, args.seed, meta)
    plan_md = emit_plan_md(records, posts, links, pillar_kw, args.seed, interlinks_kw)
    plan_html = emit_cluster_map_html(records, posts, pillar_kw, args.seed)

    json_path = out_dir / "cluster-plan.json"
    md_path = out_dir / "cluster-plan.md"
    html_path = out_dir / "cluster-map.html"

    json_path.write_text(plan_json, encoding="utf-8")
    md_path.write_text(plan_md, encoding="utf-8")
    html_path.write_text(plan_html, encoding="utf-8")

    print(f"\nWrote cluster plan:", file=sys.stderr)
    print(f"  {json_path}", file=sys.stderr)
    print(f"  {md_path}", file=sys.stderr)
    print(f"  {html_path}", file=sys.stderr)
    print(f"\nSummary: {len(posts)} post(s) across {len(clusters)} cluster(s); "
          f"pillar={pillar_kw!r}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="SERP-overlap-driven topic clustering."
    )
    parser.add_argument("seed", nargs="?",
        help="Seed keyword. Required unless --seed is passed. Accepts the seed positionally — no flag needed.")
    parser.add_argument("--seed", dest="seed_alias", default=None,
        help="Alias for the positional seed argument.")
    parser.add_argument("--max-keywords", type=int, default=20,
        help=f"Number of keyword variants to fetch (default: 20, max: {MAX_KEYWORDS_CAP}). "
             "Each variant = one SerpAPI call.")
    parser.add_argument("--gl", default="us",
        help="SerpAPI gl param (country code, default: us).")
    parser.add_argument("--hl", default="en",
        help="SerpAPI hl param (language code, default: en).")
    parser.add_argument("--location", default=None,
        help="SerpAPI location param (e.g. 'New York, NY'). Omit for the broadest SERP.")
    parser.add_argument("--concurrency", type=int, default=4,
        help="Concurrent SerpAPI calls (default: 4).")
    parser.add_argument("-o", "--output", type=Path, default=None,
        help="Output directory (default: ~/Documents/synergyAI/outputs/ or /outputs/ in Pyodide).")
    parser.add_argument("--source", default=None,
        help="Source URL label (bridge pre-fetch case — typically unused here).")
    args = parser.parse_args(argv)

    if not args.seed and args.seed_alias:
        args.seed = args.seed_alias
    if not args.seed:
        parser.print_help(sys.stderr)
        return 2

    return asyncio.run(amain(args))


if __name__ == "__main__":
    sys.exit(main())
