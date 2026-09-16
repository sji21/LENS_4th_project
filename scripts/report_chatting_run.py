"""Report frozen dialogue gates; passing these is not legal-answer acceptance."""
import argparse
import json
from pathlib import Path


def assess(report, baseline):
    rows = report["rows"]
    results = {}
    for name, threshold in (("action", .85), ("intent", .75), ("facts", .90),
                            ("query_contains", .80), ("query_excludes", .80)):
        required = [r["checks"][name] for r in rows
                    if r["checks"][name]["expected"]
                    and (not name.startswith("query_") or r["observation"].get("action") == "rag")]
        passed = sum(c["passed"] is True for c in required)
        missing = sum(c["passed"] is None for c in required)
        results[name] = {"passed_count": passed, "required_count": len(required),
                         "unobservable_count": missing, "threshold": threshold,
                         "passed": bool(required) and passed / len(required) >= threshold}
    forbidden = [r["checks"]["forbidden_facts"] for r in rows if r["checks"]["forbidden_facts"]["expected"]]
    results["no_forbidden_active_facts"] = {"passed": bool(forbidden) and all(c["passed"] is True for c in forbidden)}
    results["capture_integrity"] = {"passed": report.get("complete") is True}
    results["bounded_model_attempts"] = {"passed": bool(rows) and all(type(r.get("model_calls")) is int and 0 <= r["model_calls"] <= 6 for r in rows)}
    target = baseline["summary"]["latency_seconds"]["p95"] + 30
    actual = report.get("summary", {}).get("latency_seconds", {}).get("p95")
    results["latency"] = {"actual_p95": actual, "target_p95": target,
                          "passed": isinstance(actual, (int, float)) and actual <= target}
    return {"split": report["split"], "gates": results,
            "automated_gates_passed": all(g["passed"] for g in results.values()),
            "manual_review_required": "Compare original queries, negation, active case, evidence and public legal answers; run API/security/browser regressions. These automated gates alone do not authorize acceptance or deployment."}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("--baseline", type=Path, default=Path("data/eval/chatting/results/baseline-dev.json"))
    args = parser.parse_args()
    print(json.dumps(assess(json.loads(args.report.read_text()), json.loads(args.baseline.read_text())), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
