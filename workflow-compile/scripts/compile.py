#!/usr/bin/env python3
"""Compile a simplified workflow JSON into the full Workflow DSL.

Usage:
    python compile.py -i /scratch/<name>.simple.json -o /outputs/<name>.json

Input (simplified language):
    {
      "name": "...",
      "description": "...",
      "start":  { "prompt": "...", "documents": [] },
      "agents": [
        { "id": "crypto", "name": "...", "provider": "kimi",
          "tools": [...], "instructions": "...", "skill": "...",
          "agent_template_id": null,
          "max_tokens": 4096, "temperature": 0.7 }
      ],
      "flow": [["start","crypto"], ["crypto","publisher"], ["publisher","output"]]
    }

Output: the full Workflow DSL JSON written to the path given by -o.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from collections import defaultdict, deque
from pathlib import Path

ALLOWED_PROVIDERS = {
    "openai", "anthropic", "kimi", "grok", "deepseek", "gemini",
}

# Path to the MCP catalog the app refreshes at startup. The file lists every
# MCP function this user can call, with the `mcp_` prefix already applied.
# Strict mode: any `mcp_*` reference in an agent's `tools` array that doesn't
# appear here is a compile error. This trains the LLM (via the retry loop)
# to only pick real functions instead of inventing plausible-looking names.
MCP_CATALOG_PATH = Path(__file__).resolve().parent.parent / "references" / "mcp-catalog.md"
_MCP_FUNC_RE = re.compile(r"`(mcp_[A-Za-z0-9_]+)`")

DEFAULT_MAX_TOKENS = 4096
DEFAULT_TEMPERATURE = 0.7

BASE_X = 61
COL_WIDTH = 330
BASE_Y = 250
ROW_HEIGHT = 175


class ValidationError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


def _is_nonempty_str(v) -> bool:
    return isinstance(v, str) and v.strip() != ""


def load_mcp_catalog() -> set[str]:
    """Parse references/mcp-catalog.md for valid mcp_* function names.

    The catalog is auto-generated at app startup by StartupTasksRunner.
    Returns the set of every backtick-wrapped `mcp_*` identifier found in
    the file. An empty result is legal (no MCP servers configured for this
    user); in that case any `mcp_*` reference in an agent's tools array
    will be rejected with a clear "no MCPs configured" error.
    """
    if not MCP_CATALOG_PATH.exists():
        # Catalog must exist for strict validation. The app refreshes it
        # post-login; if it's missing the user needs to reload to trigger
        # the StartupTasksRunner's refresh-workflow-compile-catalog task.
        raise FileNotFoundError(
            f"MCP catalog not found at {MCP_CATALOG_PATH}. "
            "The app should auto-generate this at startup (post-login). "
            "Reload the app to trigger the catalog refresh."
        )
    text = MCP_CATALOG_PATH.read_text()
    return set(_MCP_FUNC_RE.findall(text))


def validate_mcp_tools(agents: list[dict], catalog: set[str]) -> list[str]:
    """Strict-all validation: every entry in any agent's `tools` array
    must appear verbatim in the MCP catalog — with OR without the
    `mcp_` prefix. The catalog is the sole source of truth; inventing
    names (e.g. `web_search`, `api_call`, `data_retrieval`) is rejected
    regardless of whether the model uses the prefix.

    Errors include up to 3 closest-match suggestions so the LLM can
    correct on the retry without re-reading the catalog file.
    """
    errors: list[str] = []
    catalog_sorted = sorted(catalog)
    for i, a in enumerate(agents):
        if not isinstance(a, dict):
            continue
        aid = a.get("id", f"<no-id-{i}>")
        tools = a.get("tools", [])
        if not isinstance(tools, list):
            continue
        for t in tools:
            if not isinstance(t, str):
                continue
            if t in catalog:
                continue
            if not catalog:
                errors.append(
                    f"agents[{i}] {aid!r}: tool {t!r} is not a valid function — "
                    f"no MCP servers are configured for this user. "
                    f"Leave tools empty: \"tools\": []."
                )
                continue
            # Closest-match suggestions. Try direct fuzzy first; if nothing
            # matches (e.g. model dropped the prefix entirely), also try
            # adding `mcp_` and fuzzy-matching against catalog stems.
            suggestions = difflib.get_close_matches(t, catalog_sorted, n=3, cutoff=0.5)
            if not suggestions:
                suggestions = difflib.get_close_matches(
                    f"mcp_{t}", catalog_sorted, n=3, cutoff=0.4
                )
            if not suggestions:
                # Fallback: substring match anywhere in catalog entries.
                needle = t.lower().lstrip("mcp_")
                suggestions = [c for c in catalog_sorted if needle and needle in c.lower()][:3]
            hint = (
                f"Did you mean: {', '.join(suggestions)}?"
                if suggestions
                else f"See references/mcp-catalog.md for the full list ({len(catalog)} available)."
            )
            errors.append(
                f"agents[{i}] {aid!r}: tool {t!r} is not in the MCP catalog. {hint}"
            )
    return errors


def validate_schema(simple: dict) -> tuple[list[str], list[dict], list[list[str]]]:
    """Return (errors, agents, flow). Never raises — errors collected for aggregation."""
    errors: list[str] = []

    if not isinstance(simple, dict):
        return ["root: workflow must be a JSON object"], [], []

    if not _is_nonempty_str(simple.get("name")):
        errors.append('root: missing required field "name" (non-empty string)')

    start = simple.get("start")
    if not isinstance(start, dict) or not _is_nonempty_str(start.get("prompt")):
        errors.append('root: missing required field "start.prompt" (non-empty string)')

    agents = simple.get("agents")
    if not isinstance(agents, list) or len(agents) == 0:
        errors.append('root: "agents" must be a non-empty array')
        agents = []

    flow = simple.get("flow")
    if not isinstance(flow, list):
        errors.append('root: "flow" must be an array of [from, to] pairs')
        flow = []

    seen_ids: set[str] = set()
    for i, a in enumerate(agents):
        if not isinstance(a, dict):
            errors.append(f"agents[{i}]: entry must be an object")
            continue
        aid = a.get("id")
        if not _is_nonempty_str(aid):
            errors.append(f'agents[{i}]: missing required field "id" (non-empty string)')
        elif aid in ("start", "output"):
            errors.append(f'agents[{i}]: id {aid!r} is reserved')
        elif aid in seen_ids:
            errors.append(f"agents[{i}]: duplicate id {aid!r}")
        else:
            seen_ids.add(aid)
        if not _is_nonempty_str(a.get("name")):
            errors.append(f'agents[{i}] {aid!r}: missing required field "name"')
        provider = a.get("provider")
        if not _is_nonempty_str(provider):
            errors.append(f'agents[{i}] {aid!r}: missing required field "provider"')
        elif provider not in ALLOWED_PROVIDERS:
            allowed = ", ".join(sorted(ALLOWED_PROVIDERS))
            errors.append(
                f"agents[{i}] {aid!r}: provider {provider!r} is not in allowed set ({allowed})"
            )
        if not _is_nonempty_str(a.get("instructions")):
            errors.append(f'agents[{i}] {aid!r}: missing required field "instructions"')
        tools = a.get("tools", [])
        if not isinstance(tools, list) or any(not isinstance(t, str) for t in tools):
            errors.append(f"agents[{i}] {aid!r}: \"tools\" must be a list of strings")
        sb = a.get("skill_binding")
        if sb is not None and not isinstance(sb, (str, dict)):
            errors.append(
                f"agents[{i}] {aid!r}: \"skill_binding\" must be a string (skill dir_name) "
                f"or an object with name/dir_name/source"
            )

    valid_targets = seen_ids | {"start", "output"}
    for i, edge in enumerate(flow):
        if not (isinstance(edge, list) and len(edge) == 2):
            errors.append(f"flow[{i}]: must be a [from, to] pair, got {edge!r}")
            continue
        f, t = edge
        if not _is_nonempty_str(f) or not _is_nonempty_str(t):
            errors.append(f"flow[{i}]: both endpoints must be non-empty strings")
            continue
        if f == t:
            errors.append(f"flow[{i}]: self-loop {f!r} -> {t!r} not allowed")
        if f not in valid_targets:
            known = ", ".join(sorted(valid_targets))
            errors.append(f"flow[{i}]: unknown source id {f!r} — known ids: {known}")
        if t not in valid_targets:
            known = ", ".join(sorted(valid_targets))
            errors.append(f"flow[{i}]: unknown target id {t!r} — known ids: {known}")
        if f == "output":
            errors.append(f"flow[{i}]: 'output' cannot be a source")
        if t == "start":
            errors.append(f"flow[{i}]: 'start' cannot be a target")

    return errors, agents, flow


def validate_graph(agent_ids: list[str], flow: list[list[str]]) -> list[str]:
    """Structural graph checks. Returns aggregated errors; never raises."""
    errors: list[str] = []

    valid_nodes = set(agent_ids) | {"start", "output"}
    out_edges: dict[str, list[str]] = defaultdict(list)
    in_edges: dict[str, list[str]] = defaultdict(list)
    clean_flow: list[tuple[str, str]] = []
    for edge in flow:
        if not (isinstance(edge, list) and len(edge) == 2):
            continue
        f, t = edge
        if not (isinstance(f, str) and isinstance(t, str)):
            continue
        if f not in valid_nodes or t not in valid_nodes:
            continue
        clean_flow.append((f, t))
        out_edges[f].append(t)
        in_edges[t].append(f)

    if not any(t == "output" for _, t in clean_flow):
        errors.append('graph: no edge ends at "output" — workflow must have a terminus')

    # Cycle detection via Kahn topological sort
    nodes = set(agent_ids) | {"start", "output"}
    in_deg = {n: len(in_edges[n]) for n in nodes}
    queue = deque(n for n in nodes if in_deg[n] == 0)
    visited = 0
    topo: list[str] = []
    while queue:
        n = queue.popleft()
        topo.append(n)
        visited += 1
        for m in out_edges[n]:
            in_deg[m] -= 1
            if in_deg[m] == 0:
                queue.append(m)
    if visited != len(nodes):
        cycle_nodes = [n for n in nodes if in_deg[n] > 0]
        errors.append(
            'graph: cycle detected involving nodes: ' + ', '.join(sorted(cycle_nodes))
            + ' — workflow must be a DAG (no loops)'
        )

    # Reachability from start
    reachable_from_start: set[str] = set()
    stack = ["start"]
    while stack:
        n = stack.pop()
        if n in reachable_from_start:
            continue
        reachable_from_start.add(n)
        stack.extend(out_edges[n])
    for aid in agent_ids:
        if aid not in reachable_from_start:
            errors.append(
                f'graph: agent {aid!r} is not reachable from "start" — '
                f'add an edge that leads to it'
            )

    # Path to output (reverse reachability)
    reaches_output: set[str] = set()
    stack = ["output"]
    while stack:
        n = stack.pop()
        if n in reaches_output:
            continue
        reaches_output.add(n)
        stack.extend(in_edges[n])
    for aid in agent_ids:
        if aid not in reaches_output:
            errors.append(
                f'graph: agent {aid!r} has no path to "output" — '
                f'add an edge from {aid!r} (directly or via other agents) to "output"'
            )

    return errors


def compute_levels(node_ids: list[str], flow: list[list[str]]) -> dict[str, int]:
    """Longest path from any source = column for left-to-right layout."""
    out_edges: dict[str, list[str]] = defaultdict(list)
    in_edges: dict[str, list[str]] = defaultdict(list)
    for f, t in flow:
        out_edges[f].append(t)
        in_edges[t].append(f)

    in_deg = {n: len(in_edges[n]) for n in node_ids}
    queue = deque(n for n in node_ids if in_deg[n] == 0)
    topo: list[str] = []
    while queue:
        n = queue.popleft()
        topo.append(n)
        for m in out_edges[n]:
            in_deg[m] -= 1
            if in_deg[m] == 0:
                queue.append(m)

    level = {n: 0 for n in node_ids}
    for n in topo:
        for m in out_edges[n]:
            if level[n] + 1 > level[m]:
                level[m] = level[n] + 1
    return level


def compute_positions(
    node_ids: list[str], levels: dict[str, int], declaration_order: list[str]
) -> dict[str, dict[str, int]]:
    """Place by column (level) and distribute siblings vertically.

    Within a column, siblings keep their declaration order (top to bottom).
    """
    order_index = {n: i for i, n in enumerate(declaration_order)}
    by_level: dict[int, list[str]] = defaultdict(list)
    for n, lvl in levels.items():
        by_level[lvl].append(n)

    positions: dict[str, dict[str, int]] = {}
    for lvl, ns in by_level.items():
        ns_sorted = sorted(ns, key=lambda n: order_index.get(n, 0))
        x = BASE_X + lvl * COL_WIDTH
        n_rows = len(ns_sorted)
        for i, n in enumerate(ns_sorted):
            offset = i - (n_rows - 1) / 2.0
            y = int(round(BASE_Y + offset * ROW_HEIGHT))
            positions[n] = {"x": x, "y": y}
    return positions


def build_start_node(node_id: str, simple: dict, position: dict) -> dict:
    s = simple["start"]
    return {
        "id": node_id,
        "node_type": "start",
        "type": "start",
        "agent_id": None,
        "agent_name": None,
        "config": {
            "agent_type": None,
            "type": "start",
            "prompt": s.get("prompt", ""),
            "documents": s.get("documents", []),
        },
        "position": position,
        "pos_x": position["x"],
        "pos_y": position["y"],
    }


def build_output_node(node_id: str, position: dict) -> dict:
    return {
        "id": node_id,
        "node_type": "output",
        "type": "output",
        "agent_id": None,
        "agent_name": None,
        "config": {
            "agent_type": None,
            "type": "output",
        },
        "position": position,
        "pos_x": position["x"],
        "pos_y": position["y"],
    }


def _expand_skill_binding(sb) -> dict | None:
    """Accept string (dir_name) or full object; emit canonical bound_skill or None."""
    if sb is None:
        return None
    if isinstance(sb, str):
        name = sb.strip()
        if not name:
            return None
        return {"id": f"local:{name}", "name": name, "source": "local", "dir_name": name}
    if isinstance(sb, dict):
        name = sb.get("name") or sb.get("dir_name")
        source = sb.get("source", "local")
        dir_name = sb.get("dir_name") or name
        sb_id = sb.get("id") or f"{source}:{name}"
        return {"id": sb_id, "name": name, "source": source, "dir_name": dir_name}
    return None


def build_agent_node(node_id: str, agent: dict, position: dict) -> dict:
    template_id = agent.get("agent_template_id")
    bound = _expand_skill_binding(agent.get("skill_binding"))
    return {
        "id": node_id,
        "node_type": "agent",
        "type": "agent",
        "agent_id": template_id,
        "agent_name": agent.get("name", ""),
        "config": {
            "agent_type": "worker",
            "type": "agent",
            "model": agent.get("model", ""),
            "tools": agent.get("tools", []),
            "agent_id": template_id,
            "settings": {
                "max_tokens": agent.get("max_tokens", DEFAULT_MAX_TOKENS),
                "temperature": agent.get("temperature", DEFAULT_TEMPERATURE),
            },
            "agent_name": agent.get("name", ""),
            "isTemplate": False,
            "bound_skill": bound,
            "description": agent.get("description", ""),
            "instructions": agent.get("instructions", ""),
            "output_schema": None,
            "skill_content": agent.get("skill", ""),
            "agent_provider": agent.get("provider", ""),
            "merge_strategy": "labeled",
            "output_schema_id": agent.get("output_schema_id"),
        },
        "position": position,
        "pos_x": position["x"],
        "pos_y": position["y"],
    }


def compile_workflow(simple: dict) -> dict:
    schema_errors, agents, flow = validate_schema(simple)

    # Graph validation runs on whatever made it through schema, ignoring malformed
    # entries. Errors from both phases are aggregated so the LLM sees the full picture.
    valid_agent_ids = [
        a["id"] for a in agents
        if isinstance(a, dict) and _is_nonempty_str(a.get("id"))
        and a.get("id") not in ("start", "output")
    ]
    # Drop duplicates (already flagged by schema validator).
    seen = set()
    agent_ids: list[str] = []
    for aid in valid_agent_ids:
        if aid not in seen:
            seen.add(aid)
            agent_ids.append(aid)

    graph_errors = validate_graph(agent_ids, flow)

    # Strict MCP function-name validation. Catalog missing → hard fail
    # (the app refreshes it at startup; if it's gone, something upstream
    # is broken). Catalog present but empty → fine, but any mcp_* in
    # tools will be rejected with a "no MCPs configured" error.
    try:
        mcp_catalog = load_mcp_catalog()
        mcp_errors = validate_mcp_tools(agents, mcp_catalog)
    except FileNotFoundError as e:
        mcp_errors = [f"catalog: {e}"]

    all_errors = schema_errors + graph_errors + mcp_errors
    if all_errors:
        raise ValidationError(all_errors)

    declaration_order = ["start", *agent_ids, "output"]
    node_ids = list(declaration_order)
    levels = compute_levels(node_ids, flow)
    positions = compute_positions(node_ids, levels, declaration_order)

    # Numeric DSL ids in declaration order: 1=start, 2..N+1=agents, N+2=output.
    dsl_id = {"start": "1"}
    for i, aid in enumerate(agent_ids, start=2):
        dsl_id[aid] = str(i)
    dsl_id["output"] = str(len(agent_ids) + 2)

    nodes = [build_start_node(dsl_id["start"], simple, positions["start"])]
    for a in agents:
        nodes.append(build_agent_node(dsl_id[a["id"]], a, positions[a["id"]]))
    nodes.append(build_output_node(dsl_id["output"], positions["output"]))

    edges = [
        {"from": dsl_id[f], "to": dsl_id[t], "from_port": "output_1"}
        for f, t in flow
    ]

    return {
        "name": simple["name"],
        "description": simple.get("description", ""),
        "definition": {
            "runtime_mode": "batch",
            "nodes": nodes,
            "edges": edges,
        },
        "triggers": {"schedule": {"enabled": False}},
        "output_storage_enabled": 0,
        "output_folder": None,
    }


def main() -> int:
    p = argparse.ArgumentParser(
        description="Compile a simplified workflow JSON into the full Workflow DSL."
    )
    p.add_argument("-i", "--input", required=True, help="path to simplified workflow JSON")
    p.add_argument("-o", "--output", required=True, help="path to write the DSL JSON")
    args = p.parse_args()

    try:
        simple = json.loads(Path(args.input).read_text())
    except FileNotFoundError:
        print(f"error: input file not found: {args.input}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as e:
        print(f"error: input is not valid JSON: {e}", file=sys.stderr)
        return 2

    try:
        dsl = compile_workflow(simple)
    except ValidationError as e:
        print(
            f"compile error: {len(e.errors)} problem(s) found\n\n  - "
            + "\n  - ".join(e.errors),
            file=sys.stderr,
        )
        return 1

    Path(args.output).write_text(json.dumps(dsl, indent=2))
    n_nodes = len(dsl["definition"]["nodes"])
    n_edges = len(dsl["definition"]["edges"])
    print(f"wrote {args.output} ({n_nodes} nodes, {n_edges} edges)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
