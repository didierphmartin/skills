#!/usr/bin/env python3
"""
crawler_check.py — AI crawler access audit.

Fetches a site's robots.txt, homepage, and AI-discovery files, resolves the
effective Allowed/Blocked/Not-Mentioned status of 14 known AI crawlers, parses
the IETF Content-Signal directive, scans the homepage for noai/noindex meta
tags, and computes a 0-100 AI Visibility Score.

Pyodide-safe: routes fetches through /gpt/backend/api/v1/fetch-url when running
under Pyodide; uses urllib directly under native CPython.

Usage:
    python crawler_check.py https://example.com
    python crawler_check.py https://example.com/any/deep/page   # root is derived
"""

from __future__ import annotations

import os
import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse, urlunparse

try:
    from bs4 import BeautifulSoup  # type: ignore
except ImportError:
    BeautifulSoup = None  # homepage meta-tag scan is skipped gracefully


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

# 14 known AI crawlers -> (operator, tier).
CRAWLERS: dict[str, tuple[str, int]] = {
    "GPTBot":            ("OpenAI",       1),
    "OAI-SearchBot":     ("OpenAI",       1),
    "ChatGPT-User":      ("OpenAI",       1),
    "ClaudeBot":         ("Anthropic",    1),
    "PerplexityBot":     ("Perplexity",   1),
    "Google-Extended":   ("Google",       2),
    "GoogleOther":       ("Google",       2),
    "Applebot-Extended": ("Apple",        2),
    "Amazonbot":         ("Amazon",       2),
    "FacebookBot":       ("Meta",         2),
    "CCBot":             ("Common Crawl", 3),
    "anthropic-ai":      ("Anthropic",    3),
    "Bytespider":        ("ByteDance",    3),
    "cohere-ai":         ("Cohere",       3),
}
TIER1 = [c for c, (_, t) in CRAWLERS.items() if t == 1]
TIER2 = [c for c, (_, t) in CRAWLERS.items() if t == 2]
TIER3 = [c for c, (_, t) in CRAWLERS.items() if t == 3]

CONTENT_SIGNAL_KEYS = {"ai-train", "search", "ai-personalization", "ai-retrieval"}
CONTENT_SIGNAL_MEANING = {
    "ai-train": "use for AI model training",
    "search": "use in AI-powered search results",
    "ai-personalization": "use for AI personalization",
    "ai-retrieval": "use for AI retrieval / RAG grounding",
}

RECOMMENDED_ROBOTS = """\
# AI crawlers - ALLOWED for AI search visibility
User-agent: GPTBot
User-agent: OAI-SearchBot
User-agent: ChatGPT-User
User-agent: ClaudeBot
User-agent: PerplexityBot
User-agent: Google-Extended
User-agent: GoogleOther
User-agent: Applebot-Extended
User-agent: Amazonbot
User-agent: FacebookBot
Allow: /

# AI crawlers - BLOCKED (aggressive / low value)
User-agent: Bytespider
Disallow: /

# Declare AI-usage preferences explicitly (IETF draft)
Content-Signal: ai-train=yes, search=yes, ai-retrieval=yes
"""


# ---------------------------------------------------------------------------
# Fetch helpers (same dual Pyodide/CPython pattern as schema-generate)
# ---------------------------------------------------------------------------

def _is_pyodide() -> bool:
    return sys.platform == "emscripten"


async def _fetch_pyodide(url: str) -> dict | None:
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
        payload = json.loads(await resp.string())
    except Exception:
        return None
    if not payload.get("success"):
        # A non-success payload may still carry the upstream HTTP status
        # (e.g. a 404 robots.txt), which UrlFetchController puts under
        # data.upstream_status rather than data.status.
        data = payload.get("data", {}) or {}
        status = data.get("status") or data.get("upstream_status") or 0
        return {"body": "", "status": status, "final_url": data.get("final_url") or url}
    data = payload.get("data", {}) or {}
    return {
        "body": data.get("html", "") or "",
        "status": data.get("status", 0) or 0,
        "final_url": data.get("final_url", "") or url,
    }


def _fetch_urllib(url: str) -> dict | None:
    import gzip
    import ssl
    import urllib.error
    import urllib.request

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={
        "User-Agent": BROWSER_UA,
        "Accept": "text/html,application/xhtml+xml,text/plain,*/*;q=0.8",
        "Accept-Encoding": "gzip",
    })
    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
            body_bytes = r.read()
            status = r.status
            final_url = r.url
            encoding = (r.headers.get("Content-Encoding") or "").lower()
    except urllib.error.HTTPError as e:
        # 404 etc. — a real answer, not a transport failure.
        return {"body": "", "status": e.code, "final_url": url}
    except Exception:
        return None
    if encoding == "gzip":
        try:
            body_bytes = gzip.decompress(body_bytes)
        except OSError:
            pass
    return {
        "body": body_bytes.decode("utf-8", errors="replace"),
        "status": status,
        "final_url": final_url,
    }


async def fetch(url: str) -> dict | None:
    if _is_pyodide():
        return await _fetch_pyodide(url)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch_urllib, url)


def _ok(resp: dict | None) -> bool:
    """True when a fetch returned a 200 with a non-empty body."""
    return bool(resp) and resp.get("status", 0) == 200 and bool((resp.get("body") or "").strip())


# ---------------------------------------------------------------------------
# robots.txt parsing
# ---------------------------------------------------------------------------

def parse_robots(text: str) -> tuple[dict[str, dict], list[str], list[str]]:
    """Parse robots.txt into (groups, sitemaps, content_signal_lines).

    groups: {agent_lower: {"allow": [...], "disallow": [...], "crawl_delay": str|None}}
    Consecutive User-agent lines share the rule block that follows them.
    """
    groups: dict[str, dict] = {}
    sitemaps: list[str] = []
    content_signals: list[str] = []
    current: list[str] = []
    expecting_agent = False  # inside a run of consecutive User-agent lines

    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, _, value = line.partition(":")
        field = field.strip().lower()
        value = value.strip()

        if field == "user-agent":
            if not expecting_agent:
                current = []
                expecting_agent = True
            current.append(value.lower())
            groups.setdefault(value.lower(),
                              {"allow": [], "disallow": [], "crawl_delay": None})
        elif field in ("allow", "disallow", "crawl-delay"):
            expecting_agent = False
            for agent in current:
                g = groups.setdefault(
                    agent, {"allow": [], "disallow": [], "crawl_delay": None})
                if field == "allow":
                    g["allow"].append(value)
                elif field == "disallow":
                    g["disallow"].append(value)
                else:
                    g["crawl_delay"] = value
        elif field == "sitemap":
            sitemaps.append(value)
        elif field == "content-signal":
            content_signals.append(value)

    return groups, sitemaps, content_signals


def _group_blocks_all(g: dict) -> bool:
    """True if the group bars the whole site (Disallow: /) with no Allow: / override."""
    if not any(d.strip() == "/" for d in g["disallow"]):
        return False
    return not any(a.strip() == "/" for a in g["allow"])


def crawler_status(agent: str, groups: dict[str, dict]) -> tuple[str, str]:
    """Resolve effective access for one crawler -> (status, reason)."""
    a = agent.lower()
    if a in groups:
        if _group_blocks_all(groups[a]):
            return "Blocked", "explicit Disallow: / for this crawler"
        return "Allowed", "explicit User-agent group, not blocked"
    if "*" in groups:
        if _group_blocks_all(groups["*"]):
            return "Blocked", "blanket User-agent: * Disallow: /"
        return "Not Mentioned", "inherits User-agent: * (not blocked)"
    return "Not Mentioned", "no robots.txt rule applies (allowed by default)"


def parse_content_signals(lines: list[str]) -> tuple[dict[str, str], list[str]]:
    """Parse Content-Signal directive lines -> (parsed, warnings)."""
    parsed: dict[str, str] = {}
    warnings: list[str] = []
    for line in lines:
        for token in line.split(","):
            token = token.strip()
            if not token:
                continue
            if "=" not in token:
                warnings.append(f"malformed token (no '='): {token!r}")
                continue
            key, _, val = token.partition("=")
            key, val = key.strip().lower(), val.strip().lower()
            parsed[key] = val
            if key not in CONTENT_SIGNAL_KEYS:
                warnings.append(f"unknown key {key!r} (spec is an IETF draft)")
            if val not in ("yes", "no"):
                warnings.append(f"invalid value for {key!r}: {val!r} (expected yes/no)")
    return parsed, warnings


# ---------------------------------------------------------------------------
# Homepage meta-tag scan
# ---------------------------------------------------------------------------

def parse_meta_robots(html: str) -> dict:
    """Best-effort scan of homepage <meta> tags for AI-relevant directives."""
    result = {
        "robots": None, "noindex": False, "noai": False, "noimageai": False,
        "bot_specific": {}, "checked": False,
    }
    if not html or BeautifulSoup is None:
        return result
    result["checked"] = True
    soup = BeautifulSoup(html, "html.parser")
    crawler_names_lower = {c.lower() for c in CRAWLERS}
    for meta in soup.find_all("meta"):
        name = (meta.get("name") or "").strip()
        content = (meta.get("content") or "").strip()
        if not name or not content:
            continue
        lname, lcontent = name.lower(), content.lower()
        if lname == "robots":
            result["robots"] = content
            result["noindex"] = result["noindex"] or "noindex" in lcontent
            result["noai"] = result["noai"] or "noai" in lcontent
            result["noimageai"] = result["noimageai"] or "noimageai" in lcontent
        elif lname == "noai":
            result["noai"] = True
        elif lname == "noimageai":
            result["noimageai"] = True
        elif lname in crawler_names_lower:
            result["bot_specific"][name] = content
    return result


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def compute_score(statuses: dict[str, tuple[str, str]], has_blanket_block: bool,
                  meta: dict, has_llmstxt: bool, has_sitemap: bool) -> dict:
    def accessible(c: str) -> bool:
        return statuses[c][0] in ("Allowed", "Not Mentioned")

    t1 = sum(1 for c in TIER1 if accessible(c))
    t2 = sum(1 for c in TIER2 if accessible(c))
    s_t1 = t1 / len(TIER1) * 50
    s_t2 = t2 / len(TIER2) * 25
    s_blocks = 0 if (has_blanket_block or meta.get("noai")) else 15
    s_files = (5 if has_llmstxt else 0) + (5 if has_sitemap else 0)
    total = round(min(100, s_t1 + s_t2 + s_blocks + s_files))
    return {
        "score": total,
        "tier1_accessible": t1, "tier1_total": len(TIER1),
        "tier2_accessible": t2, "tier2_total": len(TIER2),
        "tier3_accessible": sum(1 for c in TIER3 if accessible(c)),
        "tier3_total": len(TIER3),
        "components": {
            "tier1_crawlers": round(s_t1, 1),
            "tier2_crawlers": round(s_t2, 1),
            "no_blanket_blocks": s_blocks,
            "ai_discovery_files": s_files,
        },
    }


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

def render_report(result: dict) -> str:
    score = result["score"]
    L: list[str] = [
        f"# AI Crawler Access Report: {result['domain']}",
        "",
        f"**Site:** {result['site_root']}",
        f"**Generated:** {result['generated']}",
        f"**robots.txt:** {result['robots']['status_label']}",
        "",
        f"## AI Visibility Score: {score['score']}/100",
        "",
        f"- Tier 1 accessible: {score['tier1_accessible']}/{score['tier1_total']} "
        f"({score['components']['tier1_crawlers']}/50 pts)",
        f"- Tier 2 accessible: {score['tier2_accessible']}/{score['tier2_total']} "
        f"({score['components']['tier2_crawlers']}/25 pts)",
        f"- No blanket block: {score['components']['no_blanket_blocks']}/15 pts",
        f"- AI discovery files: {score['components']['ai_discovery_files']}/10 pts",
        "",
        "## Crawler Access Summary",
        "",
        "| Crawler | Operator | Tier | Status | Basis |",
        "|---|---|---|---|---|",
    ]
    for name in TIER1 + TIER2 + TIER3:
        operator, tier = CRAWLERS[name]
        status, reason = result["crawlers"][name]
        L.append(f"| {name} | {operator} | {tier} | {status} | {reason} |")
    L.append("")

    crit = result["critical_issues"]
    L.append("## Critical Issues")
    L.append("")
    L.extend([f"- ⚠️ {c}" for c in crit] if crit else ["None — no Tier 1 AI crawler is blocked."])
    L.append("")

    rb = result["robots"]
    L.append("## robots.txt Findings")
    L.append("")
    L.append(f"- Blanket block (`User-agent: * Disallow: /`): "
             f"{'YES' if rb['blanket_block'] else 'no'}")
    L.append(f"- `Sitemap:` directives: "
             f"{', '.join(rb['sitemaps']) if rb['sitemaps'] else 'none'}")
    if rb["crawl_delays"]:
        L.append(f"- `Crawl-delay` set for: {', '.join(rb['crawl_delays'])}")
    L.append("")

    cs = result["content_signal"]
    L.append("## Content-Signal (IETF draft)")
    L.append("")
    if cs["present"]:
        L.append("| Key | Value | Meaning |")
        L.append("|---|---|---|")
        for k, v in cs["values"].items():
            meaning = CONTENT_SIGNAL_MEANING.get(k, "(unknown key)")
            verdict = "permits" if v == "yes" else "opts out of" if v == "no" else "?"
            L.append(f"| {k} | {v} | {verdict} {meaning} |")
        if cs["warnings"]:
            L.append("")
            L.extend([f"- ⚠️ {w}" for w in cs["warnings"]])
    else:
        L.append("**Absent.** The site has not declared AI-usage preferences. "
                 "Recommend adding a `Content-Signal:` directive to robots.txt — "
                 "e.g. `Content-Signal: ai-train=yes, search=yes, ai-retrieval=yes`. "
                 "See https://contentsignals.org/")
    L.append("")

    L.append("## AI Discovery Files")
    L.append("")
    for label, key in (("/llms.txt", "llmstxt"), ("/ai.txt", "aitxt"),
                        ("/.well-known/ai-plugin.json", "aiplugin")):
        L.append(f"- `{label}`: {'present' if result['ai_files'][key] else 'absent'}")
    L.append("")

    meta = result["meta"]
    L.append("## Homepage Meta Tags")
    L.append("")
    if not meta["checked"]:
        L.append("- Not checked (homepage unavailable or HTML parser missing).")
    else:
        L.append(f"- `<meta name=\"robots\">`: {meta['robots'] or 'not set'}")
        L.append(f"- `noindex`: {'YES' if meta['noindex'] else 'no'}")
        L.append(f"- `noai`: {'YES' if meta['noai'] else 'no'}")
        L.append(f"- `noimageai`: {'YES' if meta['noimageai'] else 'no'}")
        if meta["bot_specific"]:
            for bot, val in meta["bot_specific"].items():
                L.append(f"- bot-specific `<meta name=\"{bot}\">`: {val}")
    L.append("")

    L.append("## Recommended robots.txt for Maximum AI Visibility")
    L.append("")
    L.append("```")
    L.append(RECOMMENDED_ROBOTS.rstrip())
    L.append("```")
    L.append("")
    return "\n".join(L).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def site_root(url: str) -> str:
    p = urlparse(url if "://" in url else "https://" + url)
    return urlunparse((p.scheme or "https", p.netloc, "", "", "", "")).rstrip("/")


def resolve_output_paths(output: Path | None) -> tuple[Path, Path]:
    """Return (md_path, json_path).

    The Pyodide runner injects `-o /outputs/<stem>-audit.md` — a FILE path — so a
    path with a suffix is treated as the markdown report and the JSON is its
    `.json` sibling. A suffix-less path (or an existing directory) is treated as
    a directory and gets the default file names.
    """
    if output is None:
        base = DEFAULT_OUTPUT_DIR
        return base / "GEO-CRAWLER-ACCESS.md", base / "GEO-CRAWLER-ACCESS.json"
    if output.suffix == "" or output.is_dir():
        return output / "GEO-CRAWLER-ACCESS.md", output / "GEO-CRAWLER-ACCESS.json"
    return output, output.with_suffix(".json")


async def amain(args) -> int:
    if not args.input:
        print("ERROR: a site URL is required.", file=sys.stderr)
        return 2
    root = site_root(args.input)
    domain = urlparse(root).netloc
    if not domain:
        print(f"ERROR: could not parse a host from {args.input!r}", file=sys.stderr)
        return 2

    print(f"Auditing AI crawler access for {root}…", file=sys.stderr)

    robots_resp, homepage, llmstxt, aitxt, aiplugin = await asyncio.gather(
        fetch(f"{root}/robots.txt"),
        fetch(root + "/"),
        fetch(f"{root}/llms.txt"),
        fetch(f"{root}/ai.txt"),
        fetch(f"{root}/.well-known/ai-plugin.json"),
    )

    robots_found = _ok(robots_resp)
    robots_text = (robots_resp or {}).get("body", "") if robots_found else ""
    if robots_found:
        status_label = "found"
    elif robots_resp is None:
        status_label = "fetch failed (treated as no robots.txt)"
    else:
        status_label = f"not found (HTTP {robots_resp.get('status', 0)}) — crawlers allowed by default"

    groups, sitemaps, cs_lines = parse_robots(robots_text)
    statuses = {c: crawler_status(c, groups) for c in CRAWLERS}
    has_blanket_block = "*" in groups and _group_blocks_all(groups["*"])
    crawl_delays = [a for a, g in groups.items() if g["crawl_delay"] is not None]
    cs_values, cs_warnings = parse_content_signals(cs_lines)
    meta = parse_meta_robots((homepage or {}).get("body", ""))

    has_llmstxt = _ok(llmstxt)
    has_sitemap = bool(sitemaps)
    score = compute_score(statuses, has_blanket_block, meta, has_llmstxt, has_sitemap)

    critical = [
        f"{c} (Tier 1) is BLOCKED — {statuses[c][1]}"
        for c in TIER1 if statuses[c][0] == "Blocked"
    ]
    if has_blanket_block:
        critical.append("robots.txt has a blanket `User-agent: * Disallow: /` "
                         "blocking all crawlers without their own group.")
    if meta.get("noai"):
        critical.append("Homepage sets a `noai` meta tag, signalling AI engines to skip it.")

    result = {
        "domain": domain,
        "site_root": root,
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "score": score,
        "crawlers": statuses,
        "critical_issues": critical,
        "robots": {
            "found": robots_found,
            "status_label": status_label,
            "blanket_block": has_blanket_block,
            "sitemaps": sitemaps,
            "crawl_delays": crawl_delays,
        },
        "content_signal": {
            "present": bool(cs_lines),
            "values": cs_values,
            "warnings": cs_warnings,
        },
        "ai_files": {
            "llmstxt": has_llmstxt,
            "aitxt": _ok(aitxt),
            "aiplugin": _ok(aiplugin),
        },
        "meta": meta,
    }

    report_md = render_report(result)

    md_path, json_path = resolve_output_paths(args.output)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(report_md, encoding="utf-8")
    json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")

    # The complete report goes to STDOUT — this is what run_skill_script returns
    # to the model. The model must present THIS report; it must not rebuild the
    # crawler table, tiers, or statuses from memory.
    print(report_md)

    print(f"[geo-crawlers] score {score['score']}/100 — wrote {md_path}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit which AI crawlers can access a website.")
    parser.add_argument("input", nargs="?",
                        help="Site URL (positional). Any page URL works — the root is derived.")
    parser.add_argument("--url", dest="url_alias", default=None,
                        help="Alias for the positional URL argument.")
    parser.add_argument("--source", default=None,
                        help="Source URL label when the runner pre-fetches a local file.")
    parser.add_argument("-o", "--output", type=Path, default=None,
                        help="Output directory. Default: ~/Documents/synergyAI/outputs/ "
                             "(/outputs/ in Pyodide).")
    args = parser.parse_args(argv)

    if not args.input and args.url_alias:
        args.input = args.url_alias
    # Bridge pre-fetch case: a local file path arrives as input and the real URL
    # as --source. This script fetches on its own, so prefer the real URL.
    if args.source and re.match(r"^https?://", args.source, re.I):
        args.input = args.source

    return asyncio.run(amain(args))


if __name__ == "__main__":
    sys.exit(main())
