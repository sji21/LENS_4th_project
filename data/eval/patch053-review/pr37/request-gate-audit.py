"""Compare fixed request gates without rerunning an unchanged search backend."""
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import types


ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from src.retrieval.retrieval_intent import requested_law_intents

BASE = "0b202fa73ea7a765bff9cc5e24328bde893ccfd5"
SOURCE = "src/retrieval/retrieval_intent.py"
CAPTURE = ROOT / "data/eval/patch053-review/after"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalized_sha(path):
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")).hexdigest()


def top_level(code):
    result = {}
    for node in ast.parse(code).body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            key = node.name
        elif isinstance(node, ast.Assign):
            key = ast.unparse(node.targets[0])
        else:
            key = ast.dump(node, include_attributes=False)
        result[key] = ast.dump(node, include_attributes=False)
    return result


old_code = subprocess.check_output(
    ["git", "-c", "safe.directory=" + ROOT.as_posix(), "show", BASE + ":" + SOURCE],
    cwd=ROOT, text=True, encoding="utf-8")
old = types.ModuleType("baseline_intent")
exec(compile(old_code, BASE + ":" + SOURCE, "exec"), old.__dict__)
rows = json.loads((CAPTURE / "rows.json").read_text(encoding="utf-8"))
manifest = json.loads((CAPTURE / "manifest.json").read_text(encoding="utf-8"))
for name, expected in manifest.items():
    assert sha(CAPTURE / name) == expected, ("Previous capture changed", name)
differences = []
for row in rows:
    before, after = old.requested_law_intents(row["query"]), requested_law_intents(row["query"])
    if before != after:
        differences.append({"qid": row["qid"], "mode": row["mode"],
                            "query": row["query"], "before": before, "after": after})

before_ast = top_level(old_code)
after_ast = top_level((ROOT / SOURCE).read_text(encoding="utf-8"))
changed_nodes = sorted(key for key in before_ast.keys() | after_ast.keys()
                       if before_ast.get(key) != after_ast.get(key))
audit = json.loads((CAPTURE / "audit.json").read_text(encoding="utf-8"))
changed_sources = [name for name, expected in audit["after"]["sources"].items()
                   if normalized_sha(ROOT / name) != expected]
result = {
    "baseline_commit": BASE,
    "previous_actual_capture": str(CAPTURE.relative_to(ROOT)).replace("\\", "/"),
    "capture_rows_sha256": sha(CAPTURE / "rows.json"),
    "source_sha256": normalized_sha(ROOT / SOURCE),
    "input_count": len(rows), "differences": differences,
    "changed_module_nodes": changed_nodes,
    "captured_source_count": len(audit["after"]["sources"]),
    "changed_sources_since_actual_capture": changed_sources,
    "kure_rerun": False,
    "note": "Fixed-input gate equivalence plus unchanged downstream source is not a fresh retrieval measurement or a guarantee for unseen wording.",
}
out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("gate-comparison.json")
out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(result, ensure_ascii=False, indent=2))
assert len(rows) == 313 and not differences
assert changed_sources == [SOURCE]
assert set(changed_nodes) <= {
    "_LEASE_ACTION", "_LEASE_PAPERWORK", "_TOPIC_JOIN", "_private_form_requested",
    "_topic_tail", "requested_law_intents",
}
