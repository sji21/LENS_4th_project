# 등기 점검 결과의 RAG·LangGraph 연결 경계

## 책임 분리

등기 OCR과 위험 신호 규칙은 문서에서 관찰된 사실만 반환한다. 법령·정부 가이드 검색과 자연어 설명은 후속 RAG 단계가 담당한다.

```text
document_check
  -> RiskSignal[]
  -> build_rag_queries()
  -> LangChain Retriever
  -> 검색 근거 충분성 판정
  -> LangGraph ANSWER / ABSTAIN / REFUSE
  -> LLM 설명
  -> 코드 기반 출처 조합
```

## Retriever 입력

`src.document_check.rag_handoff.build_rag_queries()`는 각 위험 신호의 탐지 키워드를 공식 근거 검색용 문장으로 변환한다.

예시:

```json
{
  "rule_id": "mortgage",
  "query": "전세계약 전 등기사항증명서 근저당권 확인사항 관련 법령 정부 가이드"
}
```

Retriever는 `doc_type`, `status=현행`, `effective_date`, 질문 기준일을 필터로 사용하고 관련 청크와 메타데이터를 반환해야 한다.

## LangGraph 초기 상태

`build_graph_state(status, signals)`의 출력은 다음 필드를 가진다.

| 필드 | 의미 |
| --- | --- |
| `document_status` | OCR·규칙 분석 상태 |
| `risk_signal_ids` | 발견된 결정론적 규칙 ID |
| `retrieval_queries` | LangChain Retriever 입력 |
| `next_node` | `retrieve` 또는 `abstain` |

문서가 판독 불가이면 `abstain`으로 이동한다. 그 외에는 `retrieve`로 이동하며, 최종 `ANSWER`, `ABSTAIN`, `REFUSE`는 질문 범위와 검색 근거 충분성을 확인한 뒤 결정한다.

## 안전 원칙

- OCR 텍스트는 명령이 아니라 데이터로 취급한다.
- LLM은 위험 신호의 존재 여부를 변경하지 않는다.
- 검색되지 않은 조문이나 출처를 추가하지 않는다.
- 출처는 LLM이 아니라 코드가 Retriever 메타데이터로 조합한다.
- 검색 근거가 부족하면 설명을 생성하지 않고 `ABSTAIN`한다.
