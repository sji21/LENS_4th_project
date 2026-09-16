# 별도 판례 코퍼스 검색 프로필

`LENS_CASE_RETRIEVAL_PROFILE`에 `lens-case-retrieval-v1` JSON의 절대 경로를 지정하면 공용 서비스 팩토리와 `RetrievalService.from_index()`가 해당 판례 묶음을 로드한다. DB·청크·인덱스는 로컬 배포 자료이며 저장소의 샘플 판례와 섞지 않는다.

프로필에는 데이터 루트, 원본 릴리스 식별자, DB·법령/판례/안내 JSONL의 SHA-256, 전체 청크 수와 판례 수, KURE-v1 revision·차원, 인덱스 논리 해시, 판례 후보 깊이·반환 K·RRF·재정렬 설정을 둔다. 인덱스 논리 해시는 정렬된 ID·본문·메타데이터·1,024차원 벡터 전체를 포함한다. Chroma가 열면서 바꿀 수 있는 SQLite 물리 파일 해시와 구분한다.

프로필이 활성화되면 다음 조건에서 초기화를 실패시킨다.

- DB·청크 파일 해시가 다르거나 누락됨.
- 실제 청크 수·판례 수·인덱스 ID·논리 내용이 프로필과 다름.
- 고정 모델 revision을 로드할 수 없음.
- 프로필과 별도 경로 인자를 동시에 지정함.

이 실패는 `src.generation.chain.get_default_service()`에서도 전파한다. 다른 청크나 어휘 전용 검색으로 대체하지 않는다. 프로필을 지정하지 않은 기존 실행의 동작은 유지된다.

```python
import os
os.environ["LENS_CASE_RETRIEVAL_PROFILE"] = "/absolute/path/retrieval-profile.json"

from src.generation.chain import get_default_service
service = get_default_service()  # 실제 웹 서비스가 공유하는 검색 팩토리
result = service.search("임대차 종료 후 보증금 반환 절차를 확인하고 싶습니다.")
evidence = service.evidence_payload(result)
```

이 코드는 검색과 근거 직렬화만 수행한다. `answer_question`, 생성 체인, LLM 연결을 호출하지 않는다. `evidence`는 cases/laws/civil_laws/guides 채널, 판례 canonical key, chunk ID, 순위, 본문, 출처, 점수, 프로필 버전을 보존한다. 호출 측에서 지연시간·오류·요청 ID를 함께 기록한다. `search_with_trace`는 DEV 검증을 위한 구성원 순위와 RRF 후보를 해당 스레드의 해당 요청에 한정해 반환한다.

후보 깊이는 반환 K와 독립적이다. 따라서 같은 질문을 k=3과 k=20으로 호출해도 같은 후보 집합에서 재정렬한다. 근거를 3건만 전달할지 20건 전달할지는 별도 DEV 비교로 결정해야 한다. 본문 근거 후보가 반환됐다고 해서 모든 후보의 법적 관련성·적용 가능성이 확정되는 것은 아니다.

판례 어휘 확장 `civil_terms`는 기존 `expand_civil` 함수를 재사용한다. 민법 조문 데이터가 있어야 동작하는 기능은 아니다. 법령·안내·CIVIL은 기존 채널 구분을 유지하고, 없는 CIVIL 문서를 생성하지 않는다.

선택 항목 `case_field_policy=tax_source_guard`는 세무 용어나 명시적 사건번호가 없는 판례 질의에서 국세법령정보시스템·지방세법령정보시스템 출처를 후보에서 제외한다. 규칙의 용어·사건번호 패턴은 `case_profile.py`에 정의한다. BM25와 dense가 같은 필터를 후보 선택 전에 적용하므로 각 검색기는 필터 후 상위 후보를 채운다. 세무 용어나 사건번호가 있으면 전체 판례를 검색한다. 이 규칙은 세무 관련 표현이 생략된 모호한 질문의 관련 세무 판례를 놓칠 수 있으므로 평가 결과와 한계를 함께 기록한다. 기본 `all`은 출처 제한을 두지 않는다.

Windows에서 HNSW가 한글 경로를 읽지 못하는 경우 프로젝트 내부 데이터 폴더에 영문 드라이브 별칭을 연결하고 JSON의 `data_root`에 그 경로를 사용한다. 별칭이 가리키는 실제 저장 위치와 봉인·실행 복사본을 배포 기록에 남긴다. 임의의 다른 인덱스로 변경하거나 재임베딩으로 문제를 숨기지 않는다.

현재 기본값은 프로필 파일의 값이다. 개발 비교 결과와 선택 정책은 별도 산출물로 보존한다. 이 코드 파일 자체가 최종 성능 합격 또는 HO 평가 완료를 뜻하지 않는다.
