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

print("ALL PASS")
