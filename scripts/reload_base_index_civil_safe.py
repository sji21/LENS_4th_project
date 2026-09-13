"""민법 오염 없이 기본 Chroma 인덱스를 (재)생성한다.

배경
----
`data/chunks/chunks.jsonl` 에는 설정된 민법 조문이 함께
export 되어 있다. BM25 코퍼스와 `_warn_if_civil_missing` 누락 감지에는 이
파일이 그대로(민법 포함) 필요하지만, 기본 Chroma 인덱스
(`data/index/chroma_kurev1_1024`)에 민법이 섞여 들어가면
`RetrievalService.from_index()` 가 시작 시 즉시

    ValueError("기본 인덱스에 민법이 섞여 있습니다 ...")

를 던진다 (src/retrieval/service.py). README.md 6.5 절의 원래 명령은
`chunks.jsonl` 을 여과 없이 그대로 인덱싱하므로, 민법이 이미 병합된
환경(현재 상태)에서 그 명령을 그대로 재실행하면 이 예외가 실제로
발생한다.

이 스크립트는 `chunks.jsonl` 에서 민법 조문(title == "민법")을 제외한
사본(`chunks.base-only.jsonl`)을 만든 뒤 그 사본으로만 기본 인덱스를
(재)생성하고, 마지막에 결과를 검증한다. 원본 `chunks.jsonl` 과 민법
전용 인덱스(`chroma_civil_kurev1_1024`)는 건드리지 않는다.

`src/retrieval/index.py::build_index` 는 입력에 들어있는 doc_type 범위
안에서 "이번 입력에 없는 기존 문서"를 stale 로 지우기 때문에(코드 주석
참고), 과거 실수로 기본 인덱스에 민법이 섞여 들어간 상태였더라도 이
스크립트를 한 번 실행하면 그 민법 벡터는 자동으로 정리된다.

사용
----
    python scripts/reload_base_index_civil_safe.py
    python scripts/reload_base_index_civil_safe.py --skip-index   # 필터링/검증만
    python scripts/reload_base_index_civil_safe.py --skip-verify  # 색인만 하고 검증 생략
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CIVIL_TITLE = "민법"

DEFAULT_CHUNKS = ROOT / "data/chunks/chunks.jsonl"
DEFAULT_BASE_ONLY = ROOT / "data/chunks/chunks.base-only.jsonl"
DEFAULT_CASES = ROOT / "data/chunks/cases.jsonl"
DEFAULT_GUIDES = ROOT / "data/chunks/guides.jsonl"
DEFAULT_BASE_INDEX = ROOT / "data/index/chroma_kurev1_1024"
DEFAULT_CIVIL_INDEX = ROOT / "data/index/chroma_civil_kurev1_1024"


def filter_civil(src: Path, dst: Path) -> tuple[int, int, int]:
    """chunks.jsonl 에서 민법 조문을 제외한 사본을 만든다.

    Returns (입력 건수, 출력 건수, 제외된 민법 건수).
    """
    n_in = n_out = n_civil = 0
    with src.open(encoding="utf-8") as f_src, dst.open("w", encoding="utf-8") as f_dst:
        for line in f_src:
            line = line.rstrip("\n")
            if not line:
                continue
            n_in += 1
            chunk = json.loads(line)
            if chunk.get("metadata", {}).get("title") == CIVIL_TITLE:
                n_civil += 1
                continue
            f_dst.write(line + "\n")
            n_out += 1
    return n_in, n_out, n_civil


def run_index(chunks: Path, path: Path) -> None:
    cmd = [sys.executable, "-m", "src.retrieval.index", "--chunks", str(chunks), "--path", str(path)]
    print(f"  $ {' '.join(str(c) for c in cmd)}")
    subprocess.run(cmd, check=True, cwd=ROOT)


def verify(
    base_index: Path,
    civil_index: Path,
    chunks: Path,
    cases: Path,
    guides: Path,
    expected_civil_count: int | None = None,
) -> bool:
    import chromadb
    from src.retrieval.service import CIVIL_ARTICLE_IDS

    if expected_civil_count is None:
        expected_civil_count = len(CIVIL_ARTICLE_IDS)

    ok = True

    client = chromadb.PersistentClient(path=str(base_index))
    collection = client.get_or_create_collection(name="knowledge_chunks")
    total = collection.count()
    civil_hits = collection.get(where={"title": CIVIL_TITLE}, include=[])["ids"]
    print(f"  기본 인덱스 총 벡터 수: {total}")
    print(f"  기본 인덱스 내 민법 벡터 수: {len(civil_hits)} (0 이어야 정상)")
    if civil_hits:
        ok = False

    civil_client = chromadb.PersistentClient(path=str(civil_index))
    civil_collection = civil_client.get_or_create_collection(name="knowledge_chunks")
    civil_count = civil_collection.count()
    print(f"  민법 전용 인덱스 총 벡터 수: {civil_count} ({expected_civil_count} 이어야 정상)")
    if civil_count != expected_civil_count:
        ok = False

    try:
        from src.retrieval.service import RetrievalService

        # 사용자가 --base-index/--civil-index/--chunks 등으로 기본 경로가 아닌
        # 다른 경로를 지정했을 수 있으므로, 이번 실행에 실제로 쓴 경로 그대로
        # 넘겨서 검증한다. 인자를 생략하면 클래스 기본 경로를 검사하게 되어
        # 방금 색인한 결과와 무관한 판정이 나올 수 있다.
        RetrievalService.from_index(
            chunk_paths=(chunks, cases, guides),
            index_path=base_index,
            civil_index_path=civil_index,
        )
        print("  RetrievalService.from_index() 정상 구동 확인")
    except Exception as exc:  # noqa: BLE001
        print(f"  RetrievalService.from_index() 실패: {exc!r}")
        ok = False

    return ok


def main() -> int:
    ap = argparse.ArgumentParser(
        description="민법 오염 없이 기본 Chroma 인덱스를 (재)생성한다.",
    )
    ap.add_argument("--chunks", type=Path, default=DEFAULT_CHUNKS)
    ap.add_argument("--base-only-output", type=Path, default=DEFAULT_BASE_ONLY)
    ap.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    ap.add_argument("--guides", type=Path, default=DEFAULT_GUIDES)
    ap.add_argument("--base-index", type=Path, default=DEFAULT_BASE_INDEX)
    ap.add_argument("--civil-index", type=Path, default=DEFAULT_CIVIL_INDEX)
    ap.add_argument(
        "--skip-index", action="store_true",
        help="필터링/검증만 하고 색인 명령(python -m src.retrieval.index)은 실행하지 않는다",
    )
    ap.add_argument("--skip-verify", action="store_true", help="검증 단계를 생략한다")
    args = ap.parse_args()

    print("1) 민법 조문 제외 사본 생성")
    n_in, n_out, n_civil = filter_civil(args.chunks, args.base_only_output)
    print(
        f"   {args.chunks} {n_in}건 -> {args.base_only_output} {n_out}건 "
        f"(민법 {n_civil}건 제외)"
    )

    if not args.skip_index:
        print("2) 기본 인덱스 재생성 (법령 -> 판례 -> 안내 순서로 실행)")
        run_index(args.base_only_output, args.base_index)
        run_index(args.cases, args.base_index)
        run_index(args.guides, args.base_index)
    else:
        print("2) --skip-index 지정: 색인 명령 생략")

    if not args.skip_verify:
        print("3) 검증")
        ok = verify(
            args.base_index,
            args.civil_index,
            args.chunks,
            args.cases,
            args.guides,
        )
        if not ok:
            print("   검증 실패: 위 로그를 확인하세요.")
            return 1
        print("   검증 통과.")
    else:
        print("3) --skip-verify 지정: 검증 생략")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
