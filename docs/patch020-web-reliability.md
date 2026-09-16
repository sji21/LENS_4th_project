# 웹 안정화 및 법령 감시

검증: 전체 테스트 2회 각각 797 passed, 3 skipped, 172 subtests passed (13.50초 / 13.11초).
실제 인덱스로 수리비 질문에 민법 제626조·제623조 반환 확인.
법령 API는 인증값 미설정으로 실조회 미실시. 판례 검색 사이트의 라이브 응답 확인은 제한됨.

- 요청 잠금은 30초 유효하며 살아 있는 작업이 10초마다 갱신한다. 만료된 작업은 결과를 저장하지 못한다.
- 실제 경과 시간은 유지한다. 답변 예상 게이지는 최근 성공 답변 최대 5개의 중앙값(첫 요청 30초)을 사용하며, 초과하면 진행 중 표시로 전환한다.
- 쉬운 설명은 숫자 보존 검사와 별도 의미 판정을 통과해야 저장된다. 판정 실패 시 원래 답변을 유지한다. LLM 판정은 정확성을 완전히 보장하지 않는다.
- 검색된 인용만 원문과 연결한다. 현행 조문은 검색에 사용한 판본과 다를 수 있다. 판례 사건번호 링크는 종합법률정보 검색이며 특정 판결문을 확정하는 링크가 아니다.
- 개정 감시는 본문을 저장하거나 코퍼스를 바꾸지 않는다. 법령ID·일련번호·공포일·공포번호·시행일·제개정구분을 비교한다. 최초 조회는 기준 설정이며, 코퍼스 시행일 불일치도 알린다.

## 실행

```bash
python manage.py migrate
python manage.py check_law_updates
python manage.py check_law_updates --watch --interval 86400
```

`.env`의 `LAW_OPEN_API_OC`가 필요하다. 지속 감시는 위 프로세스가 실행 중이어야 하며 서버 재부팅 후 다시 시작해야 한다.
관리자는 `/admin/chat/lawalert/`에서 변경 알림을 보고 검토 후 resolved를 체크한다.
`/admin/chat/lawwatch/`에서 마지막 조회와 실패 상태를 확인한다. 일반 사용자에게 공개되지 않는다.
이메일·외부 메시지는 보내지 않는다. 외부 API 장애 시 기준값을 유지하고 다음 주기에 재시도한다.

공식 API: https://open.law.go.kr/LSO/openApi/guideResult.do?htmlName=lsNwListGuide

## 민법 데이터

민법 전용 인덱스와 통합 DB 복구는 `docs/patch006-civil-routing.md` 절차를 사용한다.
이번 복구 전 지식 DB와 법령 청크는 `knowledge.before-civil.sqlite3`, `chunks.before-civil.jsonl`로 보관했다.
기본 인덱스는 재색인하지 않는다. 실행 중인 검색 서비스는 자료 복구 후 재시작해야 다시 읽는다.
