"""Query the Git-delivered corpus without rebuilding vectors or calling an LLM."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv=None):
    from src.retrieval.case_profile import DEFAULT_PROFILE, load_case_profile
    parser = argparse.ArgumentParser(description="배포 판례 리트리버 단독 검사; Ollama 호출 없음")
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--question", required=True)
    parser.add_argument("--k", type=int, default=2)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.k < 1 or args.k > 80:
        parser.error("--k는 1~80 범위여야 합니다.")
    if args.output and args.output.exists():
        parser.error("기존 출력 파일을 덮어쓰지 않습니다.")
    service = load_case_profile(args.profile, attach_base=False)
    result = service.search(args.question, k_law=0, k_case=args.k, k_guide=0, k_civil=0)
    payload = service.evidence_payload(result)
    payload["requested_k"] = args.k
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as handle:
            handle.write(text)
        print(args.output.resolve())
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
