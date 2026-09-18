# 신규 원문·수정·삭제와 재청킹

`src.ingestion.mysql_ingest`는 공용 MySQL의 부모 스냅샷에 명시한 문서 변경을 적용해 새 스냅샷을 만든다. 부모 원문·청크·메타데이터를 MySQL에서 읽고 기존 청킹 함수를 호출한 뒤, 전체 관계와 검색 출력을 검증한 결과를 MySQL에 한 트랜잭션으로 저장한다. 기존 SQLite 원본이나 JSONL 입력은 필요하지 않다. 기존 파서를 재사용하기 위한 임시 SQLite 작업 파일은 실행 중 생성했다가 닫고 삭제한다.

## 입력과 적용

입력은 공식 원문을 수집·파싱한 레코드다. 법령은 `LawArticleRecord`, 판례는 `CaseRecord`, 안내는 `GuideRecord`의 필드를 사용한다. 수집 사이트가 추가되면 그 사이트의 파서가 이 레코드를 만들어 전달한다. 이 명령 자체가 웹을 수집하지는 않는다.

변경 파일의 공통 구조:

```json
{
  "schema": "lens-mysql-document-changes-v1",
  "parent_snapshot": "부모 스냅샷 SHA256",
  "corpus": "base",
  "version": "base-source-v2",
  "observed_at": "2026-09-18T03:00:00+00:00",
  "documents": [
    {
      "operation": "replace",
      "document_id": "guide-document:YOUR_GUIDE_ID",
      "doc_type": "guide",
      "records": [{
        "guide_id": "YOUR_GUIDE_ID",
        "title": "원문의 제목",
        "agency": "발행 기관명",
        "guide_type": "공식 안내",
        "topic": "자료의 주제",
        "source_url": "공식 원문 URL",
        "published_at": "",
        "published_at_source": "unknown",
        "content": "수집한 원문 전체",
        "collected_at": "2026-09-18",
        "status": "current"
      }]
    }
  ]
}
```

위 예시의 식별자·출처·본문을 실제 자료로 채운다. `document_id`는 기존 파서가 생성하는 ID와 일치해야 한다. 새 문서도 `replace`를 사용한다. `version`은 부모와 다른 새 이름이며 동일 코퍼스의 이미 사용한 버전을 다른 내용으로 덮어쓸 수 없다.

```powershell
python -m src.ingestion.mysql_ingest --env-file tmp/mysql-shared.env inspect --changes changes.json
python -m src.ingestion.mysql_ingest --env-file tmp/mysql-shared.env apply --changes changes.json --export tmp/mysql-export/base-v2
```

`inspect`는 MySQL을 읽어 변경 결과를 검증하고 후보 스냅샷 ID·건수·해시를 출력한다. `apply`는 검증한 데이터를 새 스냅샷으로 저장한다. 같은 입력·관찰 시각·파서 코드·버전으로 재실행하면 같은 스냅샷을 검증하고 `created: false`를 반환한다. 파서 코드나 입력이 달라졌다면 새 버전을 사용한다. 저장 후 내보내기에 실패해도 검증된 DB 스냅샷은 남으므로 출력된 ID로 `mysql_transfer export`를 재실행할 수 있다.

`documents`에 없는 문서는 그대로 보존한다. **`records`에는 교체 대상 문서의 검색 대상 전체 레코드를 넣는다.** 법령 조문 일부만 보내면 나머지 조문은 새 스냅샷에서 제외된다. 삭제는 `operation`, `document_id`, `doc_type`만 있는 명시적 `delete` 항목으로 요청한다. 이전 스냅샷은 삭제되지 않는다.

관련 원문 관계 행은 문서와 함께 교체된다. 이전 내용에 대한 법령 인용·평가 근거 등 연결을 새 내용의 검증 근거로 자동 재사용하지 않는다. 관계의 반대편 문서·평가 질문·규칙 자체는 삭제하지 않는다. 판례 교체에서는 기존 버전·관찰·별칭 이력을 복원하고 새 관찰을 추가한다. 판례 삭제 시 새 스냅샷에서 해당 이력도 제외되지만 부모 스냅샷으로 계속 조회할 수 있다.

## 자료별 청킹 규칙

| 자료 | 검색 본문과 분할 | ID·버전 |
| --- | --- | --- |
| 법령·시행령·시행규칙 | 조문 또는 입력 항·호 한 레코드에 출처 헤더를 붙여 한 청크. 임의 길이 분할·중첩 없음. 항호 구조와 수집 원문도 저장 | 기존 `document_id#조문번호#조문 안 순번`, `law-ingest-1` |
| 판례 | 전문은 원문 테이블에 저장하고 공식 `holding`에 법원·사건번호·사건명 헤더를 붙여 한 청크. 전문을 자동 분할하거나 LLM으로 요약하지 않음 | `case:case_id#0`, `case-ingest-1` |
| 기관 안내 | 줄의 공백을 정리한 뒤 문단 경계로 약 600자씩 묶고 제목 헤더를 추가. 중첩 없음 | `guide_id#순번`, `guide-ingest-1` |

안내의 600자는 목표 길이다. 600자를 넘는 한 문단은 통째로 유지한다. 검색 배포 빌더는 실제 고정 모델 토크나이저로 모든 청크를 검사하고, 특수 토큰을 포함해 입력 한도를 넘으면 배포를 중단한다. 현재 KURE 한도는 8,192토큰이다. 한도 초과 자료는 원문을 보존하면서 새 청킹 정책·버전과 검색 평가를 준비해야 한다. 저장된 `token_count`는 기존 글자 수 기반 추정값이므로 이 검사에 사용하지 않는다.

법령에 기존 수집 전문이 있으면 교체 레코드에도 새 `source_text`와 `source_document_url`을 제공한다. 안내 게시일은 공식 페이지에서 확인한 경우 `published_at_source: page`로 지정하고, 불명확하면 빈 날짜와 `unknown`을 사용한다.

## 판례 출처·이력과 추가 메타데이터

판례 `replace`에는 `case_provenance`도 필요하다.

```json
{
  "canonical_case_key": "검증된 사건 식별자",
  "source_name": "원문 제공 기관",
  "scope_tier": "core",
  "court_level": 0,
  "corpus_active": true,
  "official_id": "공식 사이트 판례 ID",
  "raw_response": "수집한 원응답"
}
```

같은 `case_id`의 사건 식별자를 다른 사건으로 바꾸거나, 이미 존재하는 사건·공식 ID를 다른 `case_id`에 연결하면 실패한다. 새 `case_versions.record_json`에는 전달한 원문 레코드와 출처 정보를 저장한다. 내용 해시, 관찰 시각, 별칭이 연결되며 검색 메타데이터의 `version_checksum`도 같은 버전을 가리킨다.

기존 청크에 JSONL 전용 필드가 있으면 변경 항목의 `metadata`에 `chunk_id → 추가 메타데이터 객체`로 새 값을 제공한다. `source_page_sha256`처럼 원문이 바뀌면 함께 바뀌어야 하는 값은 자동 복사하지 않는다. 기존 추가 필드를 누락하거나 원문에서 생성한 ID·본문 체크섬·출처 값을 덮어쓰면 실패한다. 추가 최상위 JSON 필드는 같은 방식의 `fields`에 제공한다. 수정하지 않은 문서의 필드 없음·NULL·false·추가 객체는 그대로 보존한다.

## 검색 데이터 갱신

기본·민법·판례의 사용할 스냅샷을 `EXPORT_ROOT/base`, `civil`, `cases`로 내보낸다. 민법을 수정하면 기본 스냅샷에 포함된 민법과 별도 민법 스냅샷도 함께 갱신해야 한다. 검색 빌더가 두 자료의 일치를 검사한다.

```powershell
python -m src.ingestion.mysql_search build --export-root EXPORT_ROOT --output data/mysql-search/releases/VERSION --model-dir MODEL_DIR --previous-release data/mysql-search/releases/PREVIOUS/release.json
python -m src.ingestion.mysql_search verify --release data/mysql-search/releases/VERSION/release.json
python -m src.ingestion.mysql_search activate --release data/mysql-search/releases/VERSION/release.json --pointer data/mysql-search/active.json
```

`--previous-release`는 이전 MySQL 검색 배포본을 검증해 별도 폴더로 복사한다. 같은 모델·본문의 벡터는 재사용하고 신규·변경 본문만 임베딩하며, 삭제된 청크는 새 색인에서 제거한다. 메타데이터만 바뀐 경우 벡터를 재사용하면서 메타데이터를 갱신한다. 이전 검색 배포본과 MySQL 내보내기만 사용하므로 최초 판례 SQLite 배포는 다시 필요하지 않다. BM25는 새 JSONL로 구성하고, 검증된 새 Chroma와 같은 버전으로 활성화된다. 앱 프로세스는 활성화 후 재시작한다.

되돌릴 때는 `activate`에 이전 배포본의 `release.json`을 지정한 뒤 앱을 재시작한다. 변경 실패나 부분 다운로드는 기존 활성 포인터에 반영되지 않는다.
