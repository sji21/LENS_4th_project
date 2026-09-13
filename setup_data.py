"""Prepare the local Python environment and KURE cache before DB management."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
MODEL = "nlpai-lab/KURE-v1"


def environment_python(root=ROOT):
    return root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def run(command):
    subprocess.run([str(value) for value in command], cwd=ROOT, check=True,
                   env={**os.environ, "PYTHONUTF8": "1"})


def prepare_environment(check=False):
    if sys.version_info[:2] != (3, 11):
        raise ValueError("Python 3.11로 실행하세요: Windows py -3.11 / macOS python3.11")
    python = environment_python()
    if not python.is_file():
        if check:
            raise ValueError(".venv가 없습니다. --check 없이 실행하면 생성합니다.")
        print("[준비] 프로젝트 .venv 생성", flush=True)
        run([sys.executable, "-m", "venv", ROOT / ".venv"])
    # A copied Windows venv or an older interpreter must not be reused silently.
    run([python, "-c", "import sys; assert sys.version_info[:2] == (3, 11), 'Python 3.11 required'"])
    if not check:
        print("[준비] requirements.txt 모듈 확인·필요한 패키지 다운로드", flush=True)
        run([python, "-m", "pip", "install", "-r", ROOT / "requirements.txt"])
    run([python, "-m", "pip", "check"])
    run([python, "-c", "import chromadb, sentence_transformers, huggingface_hub, dotenv; "
         "from scripts import manage_retrieval_data; print('DB 적재 모듈 로딩 확인 완료')"])
    return python


def prepare_model(check=False):
    """Executed with the project interpreter, after dependency installation."""
    audit = json.loads((ROOT / "data/eval/patch027-full/capture/audit.json").read_text(encoding="utf-8"))
    expected = audit["model_files"]
    revisions = {Path(name).parts[1] for name in expected}
    if len(revisions) != 1:
        raise ValueError("검증된 모델 버전을 특정할 수 없습니다.")
    revision = revisions.pop()
    hub = Path.home() / ".cache/huggingface/hub"
    cache = hub / "models--nlpai-lab--KURE-v1"
    # The retrieval verifier currently uses this exact cache location.
    from huggingface_hub.constants import HF_HUB_CACHE
    if Path(HF_HUB_CACHE).resolve() != hub.resolve():
        raise ValueError("현재 검증기는 기본 Hugging Face 캐시를 사용합니다. 사용자 지정 HF 캐시 설정을 해제하세요.")
    ref = cache / "refs/main"
    if ref.exists() and ref.read_text().strip() != revision:
        raise ValueError("KURE 캐시의 main 버전이 검증 버전과 다릅니다. 기존 모델을 자동 교체하지 않습니다.")
    if not check:
        print("[준비] 검증된 KURE 모델 확인·없는 파일 다운로드", flush=True)
        from huggingface_hub import snapshot_download
        snapshot_download(MODEL, revision=revision, cache_dir=str(hub))
    for name, digest in expected.items():
        path = cache / name
        if not path.is_file():
            raise ValueError("KURE 모델 파일이 없습니다. --check 없이 실행해 다운로드하세요.")
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                raise ValueError(f"KURE 모델 파일 검증 실패: {name}")
    if not ref.exists():
        if check:
            raise ValueError("KURE main 참조가 없습니다. --check 없이 모델 준비를 실행하세요.")
        ref.parent.mkdir(parents=True, exist_ok=True)
        with ref.open("x", encoding="utf-8") as stream:
            stream.write(revision)
    print("[준비] KURE 모델 버전·파일 확인 완료", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="모듈·모델 준비 후 DB 체크→적용→확인")
    parser.add_argument("--check", action="store_true", help="모듈·모델 확인만; 다운로드·DB 변경 없음")
    parser.add_argument("--prepare-only", action="store_true", help="모듈·모델만 준비하고 DB는 변경하지 않음")
    parser.add_argument("--source", type=Path, help="검증된 확대 데이터 폴더")
    parser.add_argument("--model-worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.model_worker:
            prepare_model(args.check)
            return 0
        python = prepare_environment(args.check)
        command = [python, "-X", "utf8", ROOT / "setup_data.py", "--model-worker"]
        if args.check:
            command.append("--check")
        run(command)
        if not args.check and not args.prepare_only:
            command = [python, "-X", "utf8", "-m", "scripts.manage_retrieval_data"]
            if args.source:
                command.extend(["--source", args.source.resolve()])
            run(command)
        return 0
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"준비/적용 중단: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
