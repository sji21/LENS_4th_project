"""One-command installation of the reviewed PATCH-027 retrieval data bundle."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import uuid

from scripts.patch027_paths import ROOT, read, sha
from scripts.patch027_rollout import SCOPES, child, copy_scope, expected_profile, payload_hashes
from src.retrieval.profile import CHUNKS, FILES, INDEXES, PROFILE


@contextmanager
def installation_lock(root=None, *, data_root=None):
    """Lock the resolved data target across checkouts; never unlink this file."""
    target = Path(data_root) if data_root is not None else Path(root or ROOT) / "data"
    path = target.resolve() / ".retrieval-data.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if path.stat().st_size == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ValueError("다른 데이터 적용/복구 작업이 실행 중입니다.") from error
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def check_duplicates(data):
    """Civil chunks intentionally exist in the combined and civil-only files."""
    loaded = {}
    for name in (*CHUNKS, "chunks/civil.jsonl"):
        rows = [json.loads(line) for line in (data / name).read_text(encoding="utf-8").splitlines() if line.strip()]
        ids = [row["chunk_id"] for row in rows]
        if len(ids) != len(set(ids)):
            raise ValueError(f"중복 청크 ID: {name}")
        if name in {"chunks/chunks.jsonl", "chunks/civil.jsonl"}:
            keys = [(row["metadata"].get("title"), row["metadata"].get("article_id"),
                     row["metadata"].get("version"), row["metadata"].get("effective_date")) for row in rows]
            if len(keys) != len(set(keys)):
                raise ValueError(f"같은 법령·조문·판본이 중복됐습니다: {name}")
        loaded[name] = rows
    combined = {row["chunk_id"]: row for name in CHUNKS for row in loaded[name]}
    if len(combined) != sum(len(loaded[name]) for name in CHUNKS):
        raise ValueError("법령·판례·안내 파일 사이에 청크 ID가 중복됐습니다.")
    civil = {row["chunk_id"]: row for row in loaded["chunks/chunks.jsonl"] if row["metadata"].get("title") == "민법"}
    if civil != {row["chunk_id"]: row for row in loaded["chunks/civil.jsonl"]}:
        raise ValueError("전체 법령과 민법 전용 파일이 일치하지 않습니다.")
    for name in ("knowledge.sqlite3", "civil.sqlite3"):
        path = data / "database" / name
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
            if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise ValueError(f"SQLite 무결성 오류: {name}")
            if db.execute("PRAGMA foreign_key_check").fetchone():
                raise ValueError(f"SQLite 참조 오류: {name}")
            if db.execute("""SELECT 1 FROM law_articles
                    GROUP BY law_version_id, article_number, paragraph_number, item_number
                    HAVING COUNT(*) > 1 LIMIT 1""").fetchone():
                raise ValueError(f"SQLite 조문 중복: {name}")
    return {"laws": len(loaded["chunks/chunks.jsonl"]) - len(civil), "civil_laws": len(civil),
            "cases": len(loaded["chunks/cases.jsonl"]), "guides": len(loaded["chunks/guides.jsonl"])}


def reject_source_build(data):
    if child(data, "index/server-build.json").exists():
        raise ValueError("원천 구축 DB가 감지됐습니다. 고정 검증 묶음과 해시가 다르며 이 도구의 직접 전환/복원은 지원하지 않습니다. "
                         "기존 data를 삭제하지 말고 별도 체크아웃의 빈 DB에 --validation-bundle --source로 설치하세요. "
                         "원천 DB 확인·복구는 setup_data.py / --rebuild를 사용하세요. docs/local-retrieval-data.md 참고.")


def inspect_data(data, kind):
    """Run in a subprocess so Chroma handles close before any directory swap."""
    reject_source_build(data)
    from src.retrieval.dense import ChromaRetriever
    from src.retrieval.profile import index_hash

    counts = check_duplicates(data)
    audit = read(ROOT / "data/eval/patch027-full/capture/audit.json")
    profile = expected_profile()
    expected = profile["files"] if kind == "expanded" else {
        Path(name).relative_to("data").as_posix(): digest for name, digest in audit["data_hashes"].items()}
    if set(expected) != FILES or any(sha(data / name) != digest for name, digest in expected.items()):
        raise ValueError("검증된 데이터 묶음과 파일 해시가 다릅니다. 부분 적재/다른 판본은 덮어쓰지 않습니다.")
    if (data / PROFILE).exists():
        if kind != "expanded" or read(data / PROFILE) != profile:
            raise ValueError("검색 프로필이 검증된 데이터와 다릅니다.")
    if any(not (data / name / "chroma.sqlite3").is_file() for name in INDEXES):
        raise ValueError("기본/민법 Chroma 인덱스가 없습니다. 빈 인덱스를 자동 생성하지 않습니다.")
    expected_indexes = profile["index_hashes"] if kind == "expanded" else audit["operating_index_hashes"]
    actual_indexes = [index_hash(ChromaRetriever(None, data / name)) for name in INDEXES]
    if actual_indexes != expected_indexes:
        raise ValueError("Chroma 문서·메타데이터·벡터가 검증된 인덱스와 다릅니다.")
    return counts


def inspect_in_process(data, kind):
    reject_source_build(data)
    # Chroma can rewrite physical index files even on get(). Inspect a copy;
    # the child exits before TemporaryDirectory removes Windows-locked files.
    parent = ROOT / "tmp"
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="retrieval-inspect-", dir=parent) as directory:
        snapshot = Path(directory).resolve()
        if not snapshot.is_relative_to(parent.resolve()):
            raise ValueError("검사 임시 폴더가 작업 경로를 벗어났습니다.")
        copy_scope(data, snapshot / "data")
        result = subprocess.run([sys.executable, "-X", "utf8", "-m", "scripts.manage_retrieval_data",
                                 "_inspect", "--source", str(snapshot / "data"), "--kind", kind], cwd=ROOT,
                                capture_output=True, text=True, encoding="utf-8")
        if result.returncode:
            raise ValueError(result.stderr.strip() or "데이터 검증에 실패했습니다.")
        return json.loads(result.stdout.strip().splitlines()[-1])


def data_status(data):
    reject_source_build(data)
    if not any(child(data, name).exists() for name in SCOPES):
        return {"state": "empty", "counts": {"laws": 0, "civil_laws": 0, "cases": 0, "guides": 0}}
    if not all((data / name).is_file() for name in FILES):
        raise ValueError("기본 데이터 파일 일부가 없습니다. 부분 적재 상태는 덮어쓰지 않습니다.")
    kind = "expanded" if (data / PROFILE).exists() else "baseline"
    counts = inspect_in_process(data, kind)
    return {"state": "installed" if kind == "expanded" else "baseline", "counts": counts}


def run_step(action, **paths):
    command = [sys.executable, "-X", "utf8", "-m", "scripts.patch027_rollout", action]
    for name, path in paths.items():
        command.extend(["--" + name, str(path)])
    env = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
           "HF_HUB_DISABLE_TELEMETRY": "1", "ANONYMIZED_TELEMETRY": "False"}
    log = paths["out"].parent / (paths["out"].name + "-" + action + ".log")
    with log.open("wb") as stream:
        # Mutating children must finish their transaction before parent rollback.
        options = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
                   else {"start_new_session": True})
        with subprocess.Popen(command, cwd=ROOT, env=env, stdout=stream,
                              stderr=subprocess.STDOUT, **options) as process:
            try:
                returncode = process.wait()
            except KeyboardInterrupt:
                if action in {"apply", "restore"}:
                    print("중단 요청: 데이터 교체가 종료된 후 복구 처리합니다.", flush=True)
                else:
                    process.kill()
                while True:
                    try:
                        process.wait()
                        break
                    except KeyboardInterrupt:
                        continue
                raise
    if returncode:
        raise ValueError(f"{action} 단계 실패. 상세 기록: {log}")


def new_run():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = ROOT / "tmp/retrieval-data" / (stamp + "-" + uuid.uuid4().hex[:8])
    path.mkdir(parents=True, exist_ok=False)
    return path


def save_result(path, result):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def require_clean_code():
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT).strip():
        raise ValueError("검증할 코드를 먼저 커밋하고 작업 트리를 정리하세요.")


def show_counts(result):
    counts = result.get("counts")
    if counts:
        print(f"일반 법령 {counts['laws']}개 · 민법 {counts['civil_laws']}개 · "
              f"판례 {counts['cases']}개 · 안내 {counts['guides']}개", flush=True)


def request_source():
    if not sys.stdin.isatty():
        raise ValueError("추가할 데이터 폴더가 필요합니다. -Source 또는 --source로 지정하세요.")
    try:
        value = input("추가할 검증된 데이터 폴더를 입력하세요: ").strip().strip('"')
    except EOFError as error:
        raise ValueError("데이터 폴더 입력이 종료되어 적용하지 않았습니다.") from error
    if not value:
        raise ValueError("데이터 폴더를 입력하지 않아 적용하지 않았습니다.")
    return Path(value)


def apply_data(source):
    with installation_lock():
        print("[1/3] DB 체크 — 현재 데이터·중복·손상 확인 중...", flush=True)
        status = data_status(ROOT / "data")
        print("[1/3] DB 체크 완료", flush=True)
        show_counts(status)
        if status["state"] == "installed":
            print("[2/3] DB 적용 — 이미 같은 데이터가 있어 추가 불필요", flush=True)
            print("[3/3] 확인 완료 — 중복 없음, 데이터 변경 없음", flush=True)
            return status
        if source is None:
            source = request_source()
        source = source.resolve()
        if source == (ROOT / "data").resolve():
            raise ValueError("적재 원본과 대상은 다른 폴더여야 합니다.")
        require_clean_code()
        counts = inspect_in_process(source, "expanded")
        run = new_run()
        operation = "최초 설치" if status["state"] == "empty" else "백업·적용"
        print(f"[2/3] DB 적용 — 사전 검사 후 {operation}합니다. 잠시 기다려 주세요.", flush=True)
        run_step("stage", data=source, out=run / "stage")
        run_step("verify", data=run / "stage/data", out=run / "preflight")
        if status["state"] == "empty":
            return install_empty(run, counts)
        applied = False
        try:
            run_step("apply", data=run / "stage/data", verification=run / "preflight", out=run / "backup")
            applied = True
            print("[3/3] 결과 확인 — 적용된 DB의 검색 결과 확인 중...", flush=True)
            run_step("verify", data=ROOT / "data", out=run / "postflight")
            result = {"state": "installed", "counts": counts, "backup": str(run / "backup"),
                      "preflight": str(run / "preflight"), "postflight": str(run / "postflight")}
            save_result(run / "result.json", result)
        except BaseException:
            if applied or (run / "backup/receipt.json").is_file():
                run_step("restore", data=run / "backup", out=run / "rollback")
                print("사후 확인 실패로 기존 데이터를 복구했습니다. 기록:", run, flush=True)
            raise
        print("[3/3] 확인 완료 — 235입력 일치, 중복 없음", flush=True)
        show_counts(result)
        print("기록·백업:", run, flush=True)
        return result


def install_empty(run, counts):
    """Install only absent payload paths; preserve unrelated data/eval assets."""
    target, staged = ROOT / "data", run / "stage/data"
    if not target.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError("설치 대상이 저장소 밖으로 연결되어 있습니다.")
    if any(child(target, rel).exists() for rel in SCOPES):
        raise ValueError("최초 설치 중 대상 파일이 생겼습니다. 기존 자료를 덮어쓰지 않습니다.")
    incoming = run / "incoming"
    copy_scope(staged, incoming)
    if payload_hashes(incoming) != payload_hashes(staged):
        raise ValueError("최초 설치 사본이 원본과 다릅니다.")
    touched = []
    try:
        for rel in SCOPES:
            destination = child(target, rel)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                raise ValueError("최초 설치 대상이 변경됐습니다.")
            touched.append(rel)
            child(incoming, rel).rename(destination)
        print("[3/3] 결과 확인 — 최초 설치 DB 검색 검증 중...", flush=True)
        run_step("verify", data=target, out=run / "postflight")
        result = {"state": "installed", "installation": "fresh", "counts": counts,
                  "preflight": str(run / "preflight"), "postflight": str(run / "postflight")}
        save_result(run / "result.json", result)
    except BaseException:
        # Move only files installed by this invocation into its failure archive.
        for rel in reversed(touched):
            failed = child(run / "failed", rel)
            failed.parent.mkdir(parents=True, exist_ok=True)
            if child(target, rel).exists():
                child(target, rel).rename(failed)
        raise
    print("[3/3] 확인 완료 — 최초 설치·235입력 일치, 중복 없음", flush=True)
    show_counts(result)
    print("설치 기록:", run, flush=True)
    return result


def restore_data(backup):
    with installation_lock():
        require_clean_code()
        status = data_status(ROOT / "data")
        if status["state"] == "empty":
            raise ValueError("설치된 데이터가 없어 복구할 수 없습니다.")
        if status["state"] == "baseline":
            print("이미 기존 데이터입니다. 복구할 변경이 없습니다.")
            return status
        # A modified/foreign data installation must not be silently overwritten.
        inspect_in_process(backup.resolve() / "snapshot", "baseline")
        run = new_run()
        run_step("restore", data=backup.resolve(), out=run / "restored-from")
        result = data_status(ROOT / "data")
        if result["state"] != "baseline":
            raise ValueError("복구 후 기존 데이터와 일치하지 않습니다.")
        save_result(run / "result.json", result)
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="로컬 검색 DB 상태 확인·안전 적용·복구")
    parser.add_argument("action", nargs="?", default="apply", choices=("status", "apply", "restore", "_inspect"))
    parser.add_argument("--source", type=Path, help="검증된204조문 데이터 폴더")
    parser.add_argument("--backup", type=Path, help="적용 완료 시 안내된 백업 폴더")
    parser.add_argument("--kind", choices=("baseline", "expanded"), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.action == "_inspect":
            if args.source is None or args.kind is None:
                parser.error("internal inspection needs --source and --kind")
            result = inspect_data(args.source, args.kind)
        elif args.action == "status":
            with installation_lock():
                result = data_status(ROOT / "data")
        elif args.action == "apply":
            result = apply_data(args.source)
        else:
            if args.backup is None:
                parser.error("restore에는 --backup이 필요합니다.")
            result = restore_data(args.backup)
        if args.action == "_inspect":
            print(json.dumps(result, ensure_ascii=False))
        elif args.action != "apply":
            print("DB 상태:", {"installed": "확대 데이터 적용 완료", "baseline": "기존 데이터", "empty": "미설치"}[result["state"]])
            show_counts(result)
        return 0
    except (ValueError, OSError, KeyError, sqlite3.Error, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
