# RunPod README 설치 환경의 GPU 확인

확인일: 2026-09-15. PATCH-040은 실측 결과를 반영하는 문서 정리이며 제품 코드·의존성·모델 설정을 변경하지 않는다.

## 결론

새 RunPod에서 README 6절의 환경 준비 경로를 실행한 뒤, **LENS 가상환경의 CUDA 연산과 KURE GPU 임베딩이 정상 동작함을 확인했다.** 현재 확인한 Pod에는 CUDA 문제 해결을 위한 코드 수정이나 PATCH-029 적용이 필요하지 않다.

과거 Pod의 `CUDA initialization ... driver ... 12080` 경고는 이번 환경에서 재현되지 않았다. 과거 전체 로그·정확한 드라이버·torch 버전을 확보하지 못했으므로, 원인을 Pod 환경 차이로 확정하거나 과거 문제가 해결됐다고 판정하지 않는다.

## 설치 기준과 환경

| 항목 | 실측값 |
| --- | --- |
| 코드 | main `b3cb8ac711e2f80732dc9384987cbd82fb88b397`, 설치 전 clean |
| OS / glibc | Ubuntu 22.04.3 LTS / 2.35 |
| GPU | NVIDIA RTX A4000, 16GB, compute capability 8.6 |
| NVIDIA 드라이버 | 595.91.07 |
| `nvidia-smi`의 드라이버 지원 CUDA | 13.2 |
| 설치 전 시스템 환경 | Python 3.10.12 / torch 2.1.0+cu118, CUDA 행렬 연산 통과 |
| LENS 가상환경 | `/opt/lens-venv`, Python 3.11.15 |
| 설치 후 torch / CUDA 런타임 | 2.14.0+cu130 / 13.0 |
| sentence-transformers / transformers | 6.0.1 / 5.17.0 |
| huggingface-hub / chromadb | 1.31.0 / 1.5.9 |
| numpy / safetensors | 2.4.6 / 0.8.0 |
| KURE revision | `4ed4540949c70b7da2c74004a915e1f2d5e46e4f` |

`nvidia-smi`의 CUDA 값과 `torch.version.cuda`는 의미가 다르다. 전자는 드라이버의 지원 수준이며 후자는 설치된 PyTorch의 CUDA 런타임이다. 시스템 Python의 성공만으로 별도 가상환경의 GPU 동작을 판단하지 않았다.

Python 3.11은 해당 Pod에 이미 설정된 deadsnakes 저장소의 `3.11.15-1+jammy1` 패키지와 venv 모듈로 준비했다. 새 PPA를 추가하거나 시스템 Python 3.10을 교체하지 않았다. 이는 이번 실측 환경의 준비 이력이며 모든 Pod에 같은 패키지 버전이 존재한다는 설치 지침은 아니다.

## 실행한 설치 경로

빈 `/workspace`에 위 main을 받은 뒤 실행했다.

```bash
cd /workspace/LENS_4th_project
export HF_HOME=/workspace/huggingface
export PIP_NO_CACHE_DIR=1
export PIP_PROGRESS_BAR=off
python3.11 setup_data.py --venv-dir /opt/lens-venv --prepare-only
```

`--prepare-only`는 README 기본 설치와 같은 가상환경·requirements·KURE 모델 준비를 실행하고 DB 구축 전에 종료한다. pip 다운로드 캐시와 진행 막대만 비활성화했다. torch 버전이나 CUDA 프로필을 지정하지 않았으며 PATCH-029 코드도 적용하지 않았다.

설치 종료코드는 0이었다. `pip check`, DB 적재 모듈 import, 저장소 audit에 지정된 KURE revision·파일 SHA256·main 참조 검증을 통과했다. 가상환경은 약 6.6GB, 모델 캐시는 약 2.2GB를 사용했다.

## GPU 실측 결과

같은 `/opt/lens-venv/bin/python`으로 다음을 검사했다.

| 검사 | 결과 |
| --- | --- |
| CUDA 사용 가능 | `True` |
| CUDA 행렬 연산 | 16×16 tensor 생성·행렬곱·동기화·결과값 검증 통과 |
| KURE 기본 장치 선택 | 제품 `SentenceTransformerEmbedding(MODEL)`이 `cuda:0` 선택 |
| KURE 명시적 CUDA | 별도 프로세스의 `device="cuda"` 호출도 `cuda:0` 선택 |
| 실제 임베딩 | 두 검사 각각 2문항·1024차원·모든 원소 유한값 확인, 종료코드 0 |
| 활성화 후 `python` | `/opt/lens-venv/bin/python`, `2.14.0+cu130 / 13.0 / True` 확인 |

KURE 검사는 `HF_HOME=/workspace/huggingface`, `HF_HUB_OFFLINE=1`을 지정하고 모델 파일 검증 후 실행했다. 사용한 문장은 “전세 보증금 반환 절차”, “임대차 계약 갱신 요구권”이다. 장치가 CPU이면 성공 처리하지 않았다.

원격 원본 증거는 해당 Pod의 `/workspace/lens-runpod-logs`에 보존했다. 이 경로는 Git에 포함된 공용 자료가 아니므로 Pod·볼륨이 없어지면 직접 열람하지 못할 수 있다.

| 파일 | 내용 |
| --- | --- |
| `git-head.txt` | 설치 기준 커밋 |
| `readme-prepare-only.log`, `.exit` | 설치 로그와 종료코드 |
| `venv-freeze.txt` | 실제 전체 패키지 목록 |
| `gpu_probe.py` | 실제 실행한 CUDA·KURE 검사 |
| `kure-default.log`, `.exit` | 자동 장치 선택 결과와 종료코드 |
| `kure-cuda.log`, `.exit` | 명시적 CUDA 결과와 종료코드 |

증거 파일 SHA256:

```text
gpu_probe.py    b1aeb267c5b0ab7ffe4b21d496731fbbaab1714a3b4b7bdbe396a41c61346047
venv-freeze.txt 9031749021eb93ef414fcdfe8004350f4dc4aba2983be9c0dd830cc818a40f4f
```

## 과거 경고와 PATCH-029의 상태

현재 기본 설치가 CUDA 13.0 PyTorch를 선택했다는 점은 실측했다. 과거 `12080`이 CUDA 드라이버 API 버전 값이었다면 CUDA 12.8 지원을 뜻한다. 당시에도 CUDA 13 빌드가 설치됐다면 드라이버·런타임 불일치 설명과 부합하지만, 과거 환경 자료가 부족하므로 가설로 유지한다. [NVIDIA 버전 표기](https://docs.nvidia.com/cuda/cuda-driver-api/group__CUDA__VERSION.html), [CUDA 호환성 기준](https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html)을 참고한다.

PATCH-029는 로컬 `fix/patch-029-runpod-torch`의 CUDA 12.8 고정 설치 후보다(구현 `093d4a4`, 브랜치 HEAD `d71864a`). 이번 확인 시 main과 원격 브랜치에는 적용되지 않았다. 현재 Pod에서 필요하지 않아 추가 비교 설치를 하지 않았으며, **PATCH-029 설치 흐름을 실검증하거나 완료 처리한 결과가 아니다.** 향후 다른 드라이버 환경에서 실패가 재현되면 해당 환경의 로그·버전을 보존하고 적용 여부를 다시 검토한다.

## 남은 작업

- 이번 실행은 환경·GPU 임베딩 확인이다. 새 Pod의 DB 구축, Django 서버 프로세스, 검색 재평가와 LLM 답변 평가는 실행하지 않았다.
- 이전 Pod에서 완료한 DB 구축·기본 검색 확인은 별도 이력이다. 이번 결과와 합쳐 최신 main·새 DB의 검색 평가 완료로 표현하지 않는다.
- 다음 검색 작업은 DB 구축 후 코드·데이터·모델·설정을 고정하고 DEV100 v2·민법35·기존 회귀셋을 평가하는 것이다. 일반 법령·민법 채널, 반환 개수·채점 분모·근거 손실을 구분한다.
- GPU 확인은 KURE 임베딩에 한정한다. BM25·검색 전체·LLM 품질 개선을 의미하지 않는다. 비고정 requirements의 향후 설치 결과도 이번과 같다고 보장하지 않는다.
