"""Standalone tests (no pytest needed): python3 tests/test_compile_output.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import compile as c  # workflow-compile/scripts/compile.py


def wf(output=None):
    w = {
        "name": "T", "start": {"prompt": "go"},
        "agents": [{"id": "a", "name": "A", "provider": "openai", "instructions": "x"}],
        "flow": [["start", "a"], ["a", "output"]],
    }
    if output is not None:
        w["output"] = output
    return w


def agent_node(dsl):
    return next(n for n in dsl["definition"]["nodes"] if n["node_type"] == "agent")


d = c.compile_workflow(wf({"store": True, "folder": "reports", "format": "pdf"}))
assert d["output_storage_enabled"] == 1, d["output_storage_enabled"]
assert d["output_folder"] == "reports", d["output_folder"]
assert agent_node(d)["config"]["bound_skill"]["dir_name"] == "report-pdf"
print("ok storage+format")

d = c.compile_workflow(wf())
assert d["output_storage_enabled"] == 0 and d["output_folder"] is None
assert agent_node(d)["config"]["bound_skill"] is None
print("ok defaults")

w = wf({"format": "pdf"}); w["agents"][0]["skill_binding"] = "html"
assert agent_node(c.compile_workflow(w))["config"]["bound_skill"]["dir_name"] == "html"
print("ok no-override")

try:
    c.compile_workflow(wf({"format": "xlsx"})); assert False, "expected ValidationError"
except c.ValidationError as e:
    assert any("format" in x for x in e.errors), e.errors
print("ok bad-format")

try:
    c.compile_workflow(wf({"folder": "../etc"})); assert False, "expected ValidationError"
except c.ValidationError as e:
    assert any("folder" in x for x in e.errors), e.errors
print("ok unsafe-folder")

def wf62():
    return {
        "name": "Job", "start": {"option": "ServiceNow: get new job",
                                 "args": {"tag": "okta-reset", "version": 1}},
        "agents": [
            {"id": "pb", "kind": "playbook", "playbook": "okta-reset", "name": "Okta reset"},
            {"id": "close", "kind": "component", "component": "ServiceNow: complete job"},
            {"id": "back", "kind": "component", "component": "ServiceNow: hand back",
             "args": {"group": "Service Desk"}},
        ],
        "flow": [["start", "pb"], ["pb.done", "close"], ["pb.failed", "back"],
                 ["close", "output"], ["back", "output"]],
    }


d = c.compile_workflow(wf62())
nodes = {n["id"]: n for n in d["definition"]["nodes"]}
assert nodes["1"]["config"]["start_option_ref"] == "ServiceNow: get new job"
assert nodes["1"]["config"]["start_args"] == {"tag": "okta-reset", "version": 1}
assert nodes["1"]["config"]["start_args"]["version"] == 1
pb = nodes["2"]
assert pb["node_type"] == "playbook" and pb["config"]["playbook_ref"] == "okta-reset"
assert pb["config"]["exits"] == ["done", "failed"] and pb["config"]["writes_enabled"] is True
assert pb["config"]["agent_provider"] == "claude" and pb["config"]["mcp_servers"] == []
assert pb["config"]["type"] == "playbook" and pb["config"]["node_type"] == "playbook"
assert pb["config"]["name"] == "Okta reset"
assert nodes["3"]["node_type"] == "code"
assert nodes["3"]["config"]["code_component_ref"] == "ServiceNow: complete job"
assert nodes["3"]["config"]["args"] == {}
assert nodes["4"]["config"]["args"] == {"group": "Service Desk"}
edges = d["definition"]["edges"]
assert {"from": "2", "to": "3", "from_port_name": "done"} in edges, edges
assert {"from": "2", "to": "4", "from_port_name": "failed"} in edges, edges
assert {"from": "1", "to": "2", "from_port": "output_1"} in edges, edges
assert nodes["3"]["position"]["x"] == nodes["4"]["position"]["x"]
print("ok workflow-62")

w = wf62(); w["flow"][1] = ["pb.maybe", "close"]
try:
    c.compile_workflow(w); assert False, "expected ValidationError"
except c.ValidationError as e:
    assert any("maybe" in x for x in e.errors), e.errors
print("ok bad-exit")

w = wf62(); w["agents"][0]["tools"] = ["mcp_x"]
try:
    c.compile_workflow(w); assert False, "expected ValidationError"
except c.ValidationError as e:
    assert any("tools" in x for x in e.errors), e.errors
print("ok playbook-tools")

import json, tempfile, os
with tempfile.TemporaryDirectory() as t:
    i, o = os.path.join(t, "i.json"), os.path.join(t, "o.json")
    json.dump(wf62(), open(i, "w"))
    assert c.main(["-i", i, "-o", o]) == 0 and os.path.exists(o)
print("ok main(argv)")

print("ALL PASS")
