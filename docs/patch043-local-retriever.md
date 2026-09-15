# PATCH-043 팀원 로컬 리트리버 실행 조건

## 결론

팀원은 저장소 코드로 로컬에서 검색 서비스를 호출할 수 있다. 기본 검색은 `src.retrieval.service.RetrievalService.from_index()` 또는 앱과 같은 `src.generation.chain.get_default_service()`를 사용한다. PATCH-043의 판례 전용 후보도 같은 앱 팩토리에서 `LENS_CASE_RETRIEVAL_PROFILE`로 선택할 수 있다. 이 앱 경로는 기존 제품 서비스의 법령·민법·안내 채널을 로드한 뒤 판례 채널만 후보로 교체한다. `scripts/case_internal_query.py`는 별도의 판례 전용 로더로 질문 하나의 근거를 파일에 저장하므로 제품의 다른 검색 자료 없이 실행할 수 있다. 이 단독 명령은 답변 생성이나 Ollama를 호출하지 않는다.

**Git 체크아웃만으로 실제 KURE-v1 판례 검색을 실행할 수는 없다.** 생성 데이터와 모델은 Git에 포함되지 않는다. 로컬 데이터가 없는 상태에서 기본 앱은 청크가 있으면 어휘 검색으로 내려갈 수 있지만, 고정 판례 프로필은 이 대체를 허용하지 않고 초기화 오류를 반환한다. 판례 전용 후보의 성능이나 실제 DB 실행은 아직 검증 완료로 기록하지 않는다.

## 기본 제품 검색 준비

Python 3.11과 `requirements.txt`의 패키지를 준비하고, 저장소 루트에서 `setup_data.py`를 실행한다. Windows는 `py -3.11 setup_data.py`, macOS/Linux는 `python3.11 setup_data.py`를 사용한다. 이 원천 기반 설치기는 승인 원천에서 검색용 청크, SQLite 관계 DB, Chroma KURE-v1 인덱스를 구축하고 확인한다. 환경·다운로드 없는 점검만 하려면 `setup_data.py --check`를 사용한다. Django 웹 실행은 별도로 `.env`의 `DJANGO_SECRET_KEY`와 웹 DB 마이그레이션이 필요하다. 검색 함수만 호출할 때 Ollama는 필요하지 않다. 상세 순서는 [서버 데이터 설치](server-data-setup.md)와 [README](../README.md)의 6절을 따른다.

기본 검색의 SQLite는 원문과 청크 관계를 보존한다. 검색 호출은 준비된 JSONL 청크와 Chroma 벡터 인덱스를 읽는다. 따라서 검색을 위해 **Chroma 인덱스와 청크가 반드시 필요**하고, 동일한 검색 자료를 재구축·출처 추적하려면 SQLite도 필요하다. 웹 계정·대화용 `data/database/web.sqlite3`는 별도 DB이며 리트리버만 실행할 때 필요하지 않다.

## PATCH-043 판례 전용 후보 준비

PATCH-043은 기본 제품 DB를 자동으로 이 후보 DB로 바꾸지 않는다. 담당자로부터 동일한 버전의 자료 묶음과 선택 정책을 받아 별도 로컬 경로에 둔다. 다음 파일은 프로필 로더가 직접 검사하거나 참조한다.

| 자료 | 역할 |
| --- | --- |
| `database/cases.sqlite3` | 판례 원천·청크·출처 관계의 일치 확인; **필수** |
| `chunks/cases.jsonl` | BM25 대상과 반환할 판례 본문; **필수** |
| `index/chroma_kurev1_1024/` | KURE-v1 1,024차원 판례 벡터; **필수** |
| `CORPUS_MANIFEST.json`, `DB_CONNECTION_AUDIT.json`, `INDEX_MANIFEST.json` | 코퍼스·DB 연결·인덱스 검증 기록; **필수** |
| 고정 KURE-v1 로컬 모델 복사본과 `KURE_LOCAL_COPY.json` | 모델 파일·revision 확인; 생성된 후보 프로필이 경로와 해시를 고정함 |
| 선택한 정책의 로컬 cross-encoder | 정책이 `cross_encoder` 재정렬을 선택한 경우에만 필요 |
| `lens-case-internal-v1` 프로필 JSON | 데이터 절대 경로, 파일 해시, 인덱스 논리 해시, 정책과 모델 revision을 연결함 |

프로필은 `scripts/case_internal_build_profile.py --root <모델과 입력의 루트> --data <판례 자료 루트> --selection <정책 JSON> --output <새 프로필 JSON>`으로 생성한다. 필요한 입력과 선택 정책이 제공되지 않았다면 임의의 경로·모델·정책으로 프로필을 만들지 않는다. 프로필에 적힌 경로와 해시가 실제 파일과 다르면 로더가 중단한다. `setup_data.py`는 기본 제품 데이터 구축용이며 PATCH-043의 별도 승인 원천·선택 정책·봉인 자료를 자동으로 만들어 주지 않는다.

준비된 프로필이 있을 때 저장소 루트에서 다음 명령으로 질문 한 개의 판례 Top-2와 본문·출처·점수를 확인한다. 이 **단독 판례 검색**에는 제품의 법령·민법·안내 DB가 필요하지 않다. `<...>`는 자신의 절대 경로로 바꾼다. 출력 파일은 이미 있으면 덮어쓰지 않는다.

```powershell
$env:PYTHONUTF8 = "1"
python scripts/case_internal_query.py --profile "<프로필 절대 경로>" --question "임대차 종료 뒤 보증금 반환 판례는?" --k 2 --output "<새 결과 JSON 절대 경로>"
```

제품 앱에서 LLM에 판례와 기존 법령·민법·안내 근거를 함께 전달하려면 기본 제품 DB·청크·인덱스도 준비해야 한다. 코드에서 앱 팩토리를 직접 호출할 때는 **서비스 생성 전에** 프로필 환경변수를 지정한다.

```python
import os
os.environ["LENS_CASE_RETRIEVAL_PROFILE"] = "/absolute/path/case-profile.json"

from src.generation.chain import get_default_service
service = get_default_service()
result = service.search("임대차 종료 뒤 보증금 반환 판례는?", k_law=3, k_case=2, k_guide=2)
payload = service.evidence_payload(result)
```

`result.cases`의 순위·본문·인용·출처를 검토한 뒤 정답 판례를 지정한다. 앱의 생성 단계는 법령·안내를 먼저 검색하고, 판례 직접 요청 또는 1차 근거 부족 시에만 판례 Top-2를 추가한다. 검색된 판례는 LLM의 참고 자료로 전달되지만 최종 인용은 답변 생성·검증 결과에 달려 있다. Top-2 반환은 판례의 법적 타당성이나 새 독립 평가 통과를 뜻하지 않는다. 기존 프로필과 PATCH-043 후보의 역할·차이는 [별도 판례 코퍼스 검색 프로필](case-corpus-retriever.md)을 참고한다.

## 확인 범위

2026-09-15 기준 최신 원격 main(`44078ce`)에서 새 PATCH-043 브랜치를 만들고 Windows 전체 테스트를 실행했다. 기존 PATCH-024 봉인 보고서 재계산 테스트 한 건을 제외하고 1,777개 통과, 3개 생략, 1개 선택 제외였다. 관련 검색·생성·인용 테스트는 218개 통과했다. PATCH-024의 부동소수점 마지막 자리 차이는 이전 변경 없는 main에서도 재현됐다. 앞선 RunPod 1,767개 통과·4개 생략·172 subtests 통과 기록은 PATCH-042 이름을 사용하던 이전 기준 체크아웃의 결과이며, 이번 최신 main 재검증 수치로 사용하지 않는다. Django 설정 검사와 WSGI 로딩은 필수 DJANGO_SECRET_KEY를 임시로 넣은 로컬 검사에서 통과했다. 이 결과는 PATCH-043의 실제 판례 DB·모델 파일을 이용한 로컬 검색, 새 독립 Holdout 채점 또는 운영 서버 전체 기동을 증명하지 않는다.

마지막으로 저장된 LENS-HO2 `FINAL_ONE_SHOT_EVALUATION`은 frozen Git commit `462e80a2bf98696747ec48e04b345776dd0e8f7e`, profile SHA-256 `448352c6faabf33ed6fd887370c89ac1f07fa9d27441c58d6d3f469364890455`, 판례 반환 계약 20건에 대한 결과다. PATCH-043 새 구현 commit b63f263과 판례 명시적 Top-2의 독립 실행 결과로 승격하지 않는다. 기존 봉인 점수와 입력은 유지한다.
