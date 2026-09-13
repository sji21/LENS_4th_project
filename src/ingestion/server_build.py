"""Build server retrieval data from approved sources; no frozen DB is required."""
from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import asdict
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid

from src.ingestion.server_sources import ROOT, source_records, fingerprint, digest
from src.retrieval.profile import CHUNKS, FILES, INDEXES, PROFILE, index_hash
from src.retrieval.expanded import CIVIL_IDS, POLICY
from scripts.manage_retrieval_data import installation_lock, check_duplicates
from scripts.patch027_rollout import child

BUILD = "index/server-build.json"
SCOPES = tuple(sorted(FILES)) + INDEXES + (PROFILE, BUILD, "parsed/server-build")
MODEL = "nlpai-lab/KURE-v1"


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def payload_hashes(data):
    result = {}
    for rel in SCOPES:
        path = child(data, rel)
        result[rel] = ({p.relative_to(path).as_posix(): digest(p) for p in sorted(path.rglob("*")) if p.is_file()}
                       if path.is_dir() else digest(path) if path.exists() else None)
    return result


def recipe():
    # Parser/index/schema edits must invalidate a previous build's no-op decision.
    files = [p for directory in ("src/ingestion", "src/database", "src/retrieval")
             for p in sorted((ROOT / directory).glob("*.py"))]
    files += [ROOT / name for name in ("setup_data.py", "scripts/load_case_only_demo_corpus.py",
              "scripts/patch027_sources.py", "scripts/patch027_full_sources.py", "scripts/patch027_paths.py")]
    return {p.relative_to(ROOT).as_posix(): digest(p) for p in files}


def model_identity():
    return read(ROOT / "data/eval/patch027-full/capture/audit.json")["model_files"]


def load_databases(records, data):
    from src.database.relational import initialize_relational_database, connect_database
    from src.ingestion.load_laws import load_records, export_chunks
    from src.ingestion.load_cases import load_case_records, export_case_chunks
    from src.ingestion.load_guides import load_guide_records, export_guide_chunks
    laws, cases, guides = records
    for name, group in zip(("laws", "cases", "guides"), records):
        path = data / f"parsed/server-build/{name}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(asdict(row), ensure_ascii=False) + "\n" for row in group), encoding="utf-8")
    for civil in (False, True):
        database = data / "database" / ("civil.sqlite3" if civil else "knowledge.sqlite3")
        initialize_relational_database(database)
        with closing(connect_database(database)) as db:
            summary = load_records([r for r in laws if not civil or r.law_name == "민법"], db)
            if summary.skipped:
                raise ValueError(summary.skipped)
            export_chunks(db, data / "chunks" / ("civil.jsonl" if civil else "chunks.jsonl"))
            if not civil:
                for loader, rows in ((load_case_records, cases), (load_guide_records, guides)):
                    if loader(rows, db).skipped:
                        raise ValueError("판례·안내 적재 누락")
                db.commit()
                export_case_chunks(db, data / "chunks/cases.jsonl")
                export_guide_chunks(db, data / "chunks/guides.jsonl")
    counts = check_duplicates(data)
    if counts != {"laws": 178, "civil_laws": 26, "cases": 26, "guides": 6}:
        raise ValueError(f"재구축 건수 불일치: {counts}")
    return counts


def sync_index(chunks, path, backend_factory):
    """Reuse vectors for identical text; update metadata without re-embedding."""
    import chromadb
    from src.retrieval.index import clean_metadata
    client = chromadb.PersistentClient(path=str(path))
    collection = client.get_or_create_collection("knowledge_chunks", metadata={"hnsw:space": "cosine"})
    old = collection.get(include=["documents", "metadatas", "embeddings"])
    existing = {cid: (old["documents"][i], old["metadatas"][i], old["embeddings"][i].tolist())
                for i, cid in enumerate(old["ids"])}
    changed = [c for c in chunks if c["chunk_id"] not in existing or existing[c["chunk_id"]][0] != c["text"]]
    vectors = {}
    if changed:
        backend = backend_factory()
        for start in range(0, len(changed), 16):
            window = changed[start:start + 16]
            vectors.update(zip([c["chunk_id"] for c in window], backend.embed([c["text"] for c in window]), strict=True))
            print(f"임베딩 {min(start + 16, len(changed))}/{len(changed)}", flush=True)
    for c in chunks:
        cid, metadata = c["chunk_id"], clean_metadata(c["metadata"])
        if cid not in vectors and existing[cid][1] == metadata:
            continue
        vector = vectors[cid] if cid in vectors else existing[cid][2]
        collection.upsert(ids=[cid], documents=[c["text"]], metadatas=[metadata], embeddings=[vector])
    stale = sorted(set(existing) - {c["chunk_id"] for c in chunks})
    if stale:
        collection.delete(ids=stale)
    return {"embedded": len(changed), "reused": len(chunks) - len(changed), "removed": len(stale)}


def verify(data, smoke=False):
    from src.retrieval.retriever import load_chunks
    from src.retrieval.dense import ChromaRetriever
    from src.retrieval.profile import read_profile
    paths = tuple(data / rel for rel in CHUNKS)
    chunks = [c for path in paths for c in load_chunks(path)]
    counts = check_duplicates(data)
    if counts != {"laws": 178, "civil_laws": 26, "cases": 26, "guides": 6}:
        raise ValueError("승인 검색 자료 건수 불일치")
    if any(not (data / rel / "chroma.sqlite3").is_file() for rel in INDEXES):
        raise ValueError("검색 인덱스 누락")
    indexes = [ChromaRetriever(None, data / rel) for rel in INDEXES]
    profile = read_profile(data, chunks, paths, index_paths=tuple(data / rel for rel in INDEXES), model=MODEL)
    if profile is None or profile["index_hashes"] != [index_hash(index) for index in indexes]:
        raise ValueError("검색 프로필·인덱스 불일치")
    expected = {c["chunk_id"]: c for c in chunks}
    found = set()
    from src.retrieval.index import clean_metadata
    for civil, index in enumerate(indexes):
        rows = index.collection.get(include=["documents", "metadatas"])
        for cid, body, metadata in zip(rows["ids"], rows["documents"], rows["metadatas"], strict=True):
            c = expected.get(cid)
            if (c is None or cid in found or body != c["text"] or metadata != clean_metadata(c["metadata"])
                    or (metadata.get("title") == "민법") != bool(civil)):
                raise ValueError("검색 청크·인덱스 채널 불일치")
            found.add(cid)
    if found != set(expected):
        raise ValueError("검색 인덱스 문서 누락")
    if smoke:
        from src.retrieval.service import RetrievalService
        service = RetrievalService.from_index(chunk_paths=paths, index_path=data / INDEXES[0], civil_index_path=data / INDEXES[1])
        results = [service.search(q) for q in ("전세 계약 갱신", "집주인이 보일러 수리를 거부해요", "보증금 반환 판례", "전세보증금 반환보증")]
        if not all(any(getattr(r, name) for r in results) for name in ("laws", "civil_laws", "cases", "guides")):
            raise ValueError("기본 검색 연결 확인 실패")
    return counts


def worker(action, data, previous=None):
    if action == "verify":
        return verify(data, smoke=True)
    if action == "inspect":
        return verify(data)
    from setup_data import prepare_model
    prepare_model(check=True)
    records = source_records()
    counts = load_databases(records, data)
    from src.retrieval.retriever import load_chunks
    from src.retrieval.dense import SentenceTransformerEmbedding, ChromaRetriever
    chunks = [c for rel in CHUNKS for c in load_chunks(data / rel)]
    cached = []
    def backend():
        if not cached:
            cached.append(SentenceTransformerEmbedding(MODEL))
        return cached[0]
    indexing = []
    for civil, name in enumerate(INDEXES):
        if previous:
            shutil.copytree(previous / name, data / name)
        selected = [c for c in chunks if (c["metadata"].get("title") == "민법") == bool(civil)]
        indexing.append(sync_index(selected, data / name, backend))
    profile = {"version": 1, "policy": POLICY, "model": MODEL, "civil_ids": list(CIVIL_IDS),
               "files": {rel: digest(data / rel) for rel in FILES},
               "index_hashes": [index_hash(ChromaRetriever(None, data / rel)) for rel in INDEXES]}
    write(data / PROFILE, profile)
    write(data / BUILD, {"version": 1, "source_fingerprint": fingerprint(records), "recipe": recipe(),
                        "model": MODEL, "model_identity": model_identity()})
    verify(data, smoke=True)
    return {"counts": counts, "indexing": indexing}


def run_worker(action, data, run, previous=None):
    output = run / f"{action}-result.json"
    command = [sys.executable, "-X", "utf8", "-m", "src.ingestion.server_build", "--worker", action,
               "--data-root", str(data), "--result", str(output)]
    if previous:
        command += ["--previous", str(previous)]
    with (run / f"{action}.log").open("wb") as log:
        process = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    if process.returncode:
        # Inspection snapshots are temporary; keep diagnostics after cleanup.
        failure = ROOT / "tmp/server-build/errors" / f"{action}-{uuid.uuid4().hex}.log"
        failure.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(run / f"{action}.log", failure)
        raise ValueError(f"{action} 실패: {failure}")
    return read(output)


def promote(staged, target, run):
    touched = []
    try:
        for rel in SCOPES:
            src, dst, backup = child(staged, rel), child(target, rel), child(run / "backup", rel)
            backup.parent.mkdir(parents=True, exist_ok=True)
            existed = dst.exists()
            if existed:
                dst.rename(backup)
            touched.append((rel, existed))
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.rename(dst)
        result = run_worker("verify", target, run)
        write(run / "result.json", {"state": "ready", "counts": result, "backup": str(run / "backup")})
    except Exception:
        for rel, existed in reversed(touched):
            dst, failed = child(target, rel), child(run / "failed", rel)
            failed.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                dst.rename(failed)
            if existed:
                child(run / "backup", rel).rename(dst)
        raise


def prepare(data_root=None, rebuild=False):
    target = Path(data_root or ROOT / "data").resolve()
    if data_root is None and not target.is_relative_to(ROOT.resolve()):
        raise ValueError("기본 data 경로가 저장소 밖으로 연결되어 있습니다. --data-root로 명시하세요.")
    if target == ROOT or ROOT.is_relative_to(target):
        raise ValueError("저장소 루트나 상위 폴더를 데이터 대상으로 사용할 수 없습니다.")
    # Same lock as validation-bundle operations; Django web DB is never in SCOPES.
    with installation_lock(ROOT):
        print("[1/3] DB 체크 — 원천 자료·현재 구축 상태 확인", flush=True)
        from setup_data import prepare_model
        prepare_model(check=True)
        records = source_records()
        expected = {"version": 1, "source_fingerprint": fingerprint(records), "recipe": recipe(),
                    "model": MODEL, "model_identity": model_identity()}
        existing = any(child(target, rel).exists() for rel in SCOPES)
        before = payload_hashes(target)
        owned = (target / BUILD).is_file()
        if existing and not owned and not rebuild:
            raise ValueError("기존 DB가 있습니다. 원천부터 재구축하려면 --rebuild를 지정하세요. 기존 파일은 백업합니다.")
        work = ROOT / "tmp/server-build"
        work.mkdir(parents=True, exist_ok=True)
        # Validate on a copy: Chroma reads may rewrite index storage.
        previous = None
        with tempfile.TemporaryDirectory(dir=work, prefix="inspect-") as temporary:
            snapshot = Path(temporary) / "data"
            if owned:
                for rel in SCOPES:
                    src, dst = child(target, rel), child(snapshot, rel)
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    if src.is_dir():
                        shutil.copytree(src, dst)
                    else:
                        shutil.copy2(src, dst)
                run_worker("inspect", snapshot, Path(temporary))
                prior = read(snapshot / BUILD)
                if not rebuild and prior == expected:
                    print("[2/3] 구축 생략 — 원천·코드·모델 동일, 재임베딩 0건", flush=True)
                    print("[3/3] 확인 완료 — 기존 DB·검색 인덱스 정상", flush=True)
                    return {"state": "unchanged", "embedded": 0}
                if prior.get("model") == MODEL and prior.get("model_identity") == model_identity():
                    previous = snapshot
            run = work / uuid.uuid4().hex
            run.mkdir()
            print("[2/3] 원천 파싱 → SQLite 저장 → 변경 청크 임베딩·인덱스 구축", flush=True)
            result = run_worker("build", run / "data", run, previous)
            if read(run / "data" / BUILD) != expected or payload_hashes(target) != before:
                raise ValueError("구축 중 원천·코드·모델 또는 대상 DB가 변경됐습니다. 적용하지 않습니다.")
            print("[3/3] 기본 검색 확인 후 적용·확인", flush=True)
            promote(run / "data", target, run)
            print(f"완료: {result} · 기록/백업: {run}", flush=True)
            return {"state": "ready", **result, "run": str(run)}


def main(argv=None):
    parser = argparse.ArgumentParser(description="서버 원천 파싱·DB·검색 인덱스 구축")
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--worker", choices=("build", "verify", "inspect"), help=argparse.SUPPRESS)
    parser.add_argument("--previous", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--result", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.worker:
            write(args.result, worker(args.worker, args.data_root, args.previous))
        else:
            prepare(args.data_root, args.rebuild)
        return 0
    except (ValueError, OSError) as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
