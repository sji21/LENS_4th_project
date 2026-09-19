# RunPod 전체 테스트 목록과 설명 — 2026-09-17

## 수치의 의미

이 문서의 2,828개는 판례 데이터 수가 아니라 pytest에서 통과한 테스트 실행 항목 수다. 128개 파일의 1,511개 고유 테스트 함수가 입력 매개변수별로 실행된 결과다. 별도 보고된 678개 subtest는 이 2,828개에 합산하지 않았다.

근거는 실제 RunPod `tmp/runpod-full-tests.xml`과 같은 RunPod 체크아웃의 테스트 소스다. 테스트를 다시 실행한 결과가 아니라 저장 결과를 읽어 작성했다. 원본 XML에는 2,838개 testcase가 있고, 통과 2,828·실패 3·오류 3·건너뜀 4로 구분된다. testsuite의 tests=3,516에는 별도 subtest 수가 포함된다.

실행 기반은 GitHub main `26c5745093cb324951351037cb9fb2fd1cef9dda` + 커밋 예정 변경이다. 후속 수정으로 추가한 `test_base_prepare_checks_pinned_snapshot_without_main_ref`는 이 전체 실행 집계에 없으며, 후속 구축 모듈 검사 42개 통과에 포함된다.

기능 설명은 원본 파일 문서와 테스트 검증 조건을 한국어로 정리했다. 전체 개별 이름·매개변수 ID·검증 조건은 별도 상세 목록에 빠짐없이 기록했다. 파일별로 묶었으며 각 파일 내부의 결과 순서는 XML의 순서를 따른다.

**통과가 실제 Qwen이나 8,377개 판례 전체의 품질 검증을 뜻하지는 않는다.** 생성·검색 테스트 상당수는 Fake LLM, 가짜 dense/임베딩, 메모리 청크, 합성 입력, HTTP 응답 모의를 사용한다. 실제 배포 DB·Linux Chroma·KURE·Qwen·Django 실행 확인은 별도의 [RunPod 배포 검증 기록](runpod-case-release-verification.md)에 있다.

## 기능별 집계

분류는 원본 테스트 파일의 검증 영역에 따른 설명용 묶음이다. 테스트를 재실행하거나 서로 다른 영역의 결과를 합격/불합격으로 다시 판정하지 않았다.

| 영역 | 통과 실행 수 | 통과 항목이 있는 파일 수 |
|---|---:|---:|
| 대화·플래너·상태·라우팅 | 600 | 22 |
| Django·사용자 사건 화면 | 62 | 2 |
| 법령·안내 DB·설치 | 184 | 9 |
| 평가·검색 정책 패치 회귀 | 934 | 38 |
| PDF·OCR·계약서·위험 신호 | 37 | 8 |
| 판례 DB·배포·판례 평가 | 98 | 16 |
| 검색·임베딩·채널 연결 | 163 | 9 |
| 답변 생성·인용·근거 검증 | 657 | 16 |
| 환경·보안·프로젝트 구조 | 52 | 5 |
| 질문 목적·수리·민법 라우팅 | 41 | 3 |
| 합계 | 2,828 | 128 |

## 테스트 파일별 전체 목록

| 테스트 파일 | 통과 실행 수 | 설명 |
|---|---:|---|
| ` tests/test_case_features.py ` | 23 | 계약 사건 화면·소유자 권한·사건/채팅 선택·사실·문서·보고서 기능 |
| ` tests/test_chat_leases.py ` | 7 | 채팅 실행 임대의 갱신·만료·소유권·복구 경계 |
| ` tests/test_chatting_api.py ` | 30 | Django 대화 API의 라우팅·후속 질문·기존 응답 계약(모델/검색 모의) |
| ` tests/test_django_web.py ` | 39 | Django HTML·CSRF·세션 격리·오류·기존 RAG 연결 경계 |
| ` tests/test_law_structure.py ` | 16 | 법령 계층 무손실 보존·스냅샷 교체·충돌 거부·기존 DB 이관 |
| ` tests/test_law_watch.py ` | 5 | 법령 변경 알림 중복 방지·기준 유지·코퍼스 기준일 차이 |
| ` tests/test_patch045_review_fixes.py ` | 42 | 회원 대화 보존·암호화 대기 업로드·문서 승격 등 검토 회귀 |
| ` tests/test_analysis_service.py ` | 3 | 문서 분석 결과의 검토 요청·판독 불가·상태 판정 |
| ` tests/test_answer_extras.py ` | 20 | 새 검색 없이 용어 풀이·하이라이트·후속 질문·쉬운 말 표시 |
| ` tests/test_baseline_eval.py ` | 23 | 평가 분모·조문 구분·민법 설정·잘못된 입력 처리 |
| ` tests/test_case_api_recovery.py ` | 13 | 공개 판례 API 복구 도구의 설정·응답·재수집 계약(네트워크 모의) |
| ` tests/test_case_corpus_llm.py ` | 3 | 저장 검색 근거의 기존 생성 프롬프트 전달과 잘못된 근거·빈 답변 거부 |
| ` tests/test_case_database_migration.py ` | 1 | 기존 판례 DB의 사건번호 UNIQUE 제약 마이그레이션 |
| ` tests/test_case_holdout.py ` | 2 | 판례 고정 평가의 사건 ID 점수와 누락된 정답 ID 거부 |
| ` tests/test_case_ingestion.py ` | 4 | 판례 JSONL 왕복·SQLite 적재·공통 청크 및 관계 연결 |
| ` tests/test_case_internal.py ` | 17 | 내부 판례 검색의 사건 중복 제거·임계값·K·출처·프로필 계약 |
| ` tests/test_case_internal_build_profile.py ` | 2 | 상대 입력 경로로 생성한 프로필의 절대 경로 정규화·로딩 |
| ` tests/test_case_internal_final_score.py ` | 3 | 사건 ID 일치와 본문 지지를 구분하고 오류·빈 검색·코퍼스 누락을 평가에 반영 |
| ` tests/test_case_internal_fresh_eval_gate.py ` | 10 | 새 평가에서 재사용된 질문·ID·변형 질문을 탐지하고 평가 경계 보호 |
| ` tests/test_case_internal_metrics.py ` | 3 | 다중 근거 그룹·중복·검색 오류·코퍼스 부재를 반영한 판례 지표 |
| ` tests/test_case_only_evaluation.py ` | 2 | 판례 전용 청크 스키마·보류 응답 제외 등 평가 계약 |
| ` tests/test_case_parsing.py ` | 11 | 공식 판례 원천의 본문·요지·날짜·식별자 안전 파싱 |
| ` tests/test_case_profile.py ` | 11 | 판례 배포 프로필·후보 깊이·반환 K·RRF·재정렬·기본 채널 연결 경계 |
| ` tests/test_case_review_queue.py ` | 1 | 검토 대상 판례만 수동 검토표로 추출 |
| ` tests/test_chatting_acceptance_regressions.py ` | 12 | 최종 대화/UI 개발 과정에서 발견된 상태 보존·문서 선택 회귀 |
| ` tests/test_chatting_acceptance_report.py ` | 3 | 대화 수용 평가의 필수 의도 분모·사실 변경·지연·품질 판정 |
| ` tests/test_chatting_clarification.py ` | 27 | 확인 질문의 짧은 응답을 대기 중인 사실에만 연결 |
| ` tests/test_chatting_contract.py ` | 112 | 플래너 출력의 불변·유효성·실행 허용 계약과 사실 처리 |
| ` tests/test_chatting_dialogue_run.py ` | 1 | 대화 평가 경계에서 실제 법률 생성 백엔드가 실행되지 않음 |
| ` tests/test_chatting_documents.py ` | 10 | 소유한 문서 메타데이터·명시적 참조·문서 선택 연결 |
| ` tests/test_chatting_normalization.py ` | 54 | 기존 상태 반복을 새 근거로 취급하지 않고 사실 출처·원문 보존 |
| ` tests/test_chatting_plan_run.py ` | 19 | 합성 입력 기반 플래너 평가·턴 기록·정답 누출 방지 |
| ` tests/test_chatting_planner.py ` | 106 | 메모리 모델 모의 객체를 사용한 플래너 호출·현재 의도·사실 업데이트 |
| ` tests/test_chatting_provenance.py ` | 35 | 데이터·검색 산출물·소스·실행 해시와 출처 추적 |
| ` tests/test_chatting_query.py ` | 24 | 현재 질문·조건·최신 수정 보존 및 비공개 기억의 검색어 누출 방지 |
| ` tests/test_chatting_recovery.py ` | 20 | 대화 실패 시 선택 정보 추출·상태·법률 응답 복구 |
| ` tests/test_chatting_reliability.py ` | 7 | 선택 컨텍스트 축소·필수 입력 초과·모델 호출 한계 |
| ` tests/test_chatting_routing.py ` | 28 | 법률/일상/확인 라우팅과 기존 법률 그래프·대화 기록 경계 |
| ` tests/test_chatting_run.py ` | 11 | 대화 평가 입력·관측·시드·정답 독립성 |
| ` tests/test_chatting_scenarios.py ` | 8 | 다중 턴 시나리오의 분리·구조·잘못된 묶음 거부 |
| ` tests/test_chatting_scoring.py ` | 18 | 대화 평가의 누락 관측·필수/금지 사실과 점수 계산 |
| ` tests/test_chatting_state.py ` | 25 | 세션 대화 상태 기본값·버전·손상 상태·독립성 |
| ` tests/test_chroma_index.py ` | 24 | Chroma 메타데이터·적재·조회·점수 변환(결정론적 가짜 임베딩) |
| ` tests/test_citation_display_consistency.py ` | 33 | 프롬프트·인용 검증·화면 표시의 일치와 주장 보존 |
| ` tests/test_citation_grounding.py ` | 209 | 검색한 조문과 본문 속 참조 조문 구분·인용 출처·형식 회귀 |
| ` tests/test_civil_notice_boundary.py ` | 11 | 금전 표현을 균열 통지 의도로 오인하지 않는 경계 |
| ` tests/test_civil_result_channel.py ` | 7 | 민법 전용 결과·프롬프트 출처·단계형 검색 채널 보존 |
| ` tests/test_contract_check.py ` | 5 | 계약서 핵심 항목·특약·빈 항목·원본 확인 규칙 |
| ` tests/test_dense_model_loading.py ` | 3 | 임베딩 로컬 캐시 우선 로딩과 캐시 부재 시 다운로드 계약(모의) |
| ` tests/test_dev100_v2.py ` | 28 | DEV v2 공개 평가 묶음의 범위·무결성·고정 결과 재현 |
| ` tests/test_document_citation_identity.py ` | 69 | 업로드 문서 인용을 표시 문자열이 아닌 법령 식별자로 비교 |
| ` tests/test_document_kind.py ` | 5 | 파일명 대신 OCR 구조로 등기·계약 문서 종류 판정 |
| ` tests/test_environment.py ` | 1 | 프로젝트 환경 설정이 셸 환경 변수를 덮어쓰지 않음 |
| ` tests/test_evaluation.py ` | 8 | 평가 지표·보고서 비교·빈 입력 안전성 |
| ` tests/test_generation_abstention.py ` | 19 | 답변 허용 범위와 보류 판단 |
| ` tests/test_generation_chain.py ` | 41 | 답변/보류/검토 흐름·근거·출처·면책문구(가짜 LLM·메모리 검색) |
| ` tests/test_generation_citation.py ` | 37 | 법원·기관·법령 등 생성 답변의 인용 검증 |
| ` tests/test_generation_conversation.py ` | 8 | 독립 질문과 후속 질문의 구분·대화 기반 질의 재작성 |
| ` tests/test_generation_eval_entrypoint.py ` | 2 | 생성 평가가 운영 그래프 진입점과 환경 로딩 순서를 사용 |
| ` tests/test_generation_eval_resume.py ` | 4 | 코드 버전이 바뀌면 과거 체크포인트를 섞어 재사용하지 않음 |
| ` tests/test_generation_evidence_routing.py ` | 9 | 질문 종류별 판례·안내 등 단계형 근거 라우팅 |
| ` tests/test_generation_graph.py ` | 24 | LangGraph 실행 순서·보안·검색·생성·검증 분기(실제 Ollama/Chroma 호출 없음) |
| ` tests/test_generation_llm.py ` | 55 | Ollama 환경 설정·요청·시간/토큰 제한·추론문 제거(실제 서버 호출 없음) |
| ` tests/test_generation_prompt.py ` | 37 | 생성 프롬프트의 정확성·안전·조건·시점 등 필수 규칙 보존 |
| ` tests/test_generation_validation.py ` | 30 | 생성 답변의 조건·시점·역할·인용 등 검증 규칙 |
| ` tests/test_guidance_first.py ` | 12 | 검증된 안내를 먼저 주고 알려진 사실을 반복 질문하지 않음 |
| ` tests/test_guide_ingestion.py ` | 18 | 저장 HTML에서 공식 안내 본문 경계·푸터 제거·적재(네트워크 없음) |
| ` tests/test_guided_dialogue.py ` | 31 | 검증 안내와 저장 확인 질문의 표시·다음 응답·사건 경계 |
| ` tests/test_hybrid.py ` | 15 | BM25/dense 순위의 RRF 결합 공식·중복·순서(가짜 검색기) |
| ` tests/test_knowledge_databases.py ` | 7 | 법령·판례 관계형 스키마 초기화·연결·벡터 DB 초기화 |
| ` tests/test_knowledge_release.py ` | 13 | 배포 DB/JSONL 식별 연결·해시·변조 거부·프로필·재출력 계약 |
| ` tests/test_langsmith_connection.py ` | 6 | LangSmith 추적 설정·비활성 상태 처리(실제 접속 항목은 별도 건너뜀) |
| ` tests/test_law_ingestion.py ` | 22 | 법령 필수 필드·JSONL 왕복·SQLite 적재·청크 추출 |
| ` tests/test_manage_retrieval_data.py ` | 45 | 검색 데이터 설치 잠금·해시·중복·실패 복구·활성화·보존 |
| ` tests/test_merge_chunks.py ` | 2 | 청크 묶음의 입력 순서 유지·중복 ID 거부 |
| ` tests/test_paragraph_ownership.py ` | 60 | 조문 항·문단의 법령/조문 귀속 경계 |
| ` tests/test_patch015_baseline.py ` | 6 | 초기 검색 평가의 분모·조문 식별·실패 단계 구분 |
| ` tests/test_patch016_candidates.py ` | 16 | 후보 근거 범위와 최종 답변 정확성 구분·고정 결과 재현 |
| ` tests/test_patch017_selection.py ` | 9 | 고정 순위 결합·선택 결정론·근거 손실·캡처 무결성 |
| ` tests/test_patch018_separate.py ` | 17 | 법령/판례 채널 분리·원문 해시·누락 정답 처리 |
| ` tests/test_patch020_budget.py ` | 8 | 근거 예산 확대에서 기존 선택 보존·후보 중복 제거 |
| ` tests/test_patch021_provenance.py ` | 4 | 생성 평가의 커밋 소스·줄바꿈·변경 작업트리 감사 |
| ` tests/test_patch023_report.py ` | 7 | 데이터 부재/검색 실패 구분·민법 노출·보고서 계약 |
| ` tests/test_patch024_expansion.py ` | 3 | 법령 확장에서 기존 7개 보존·추가 3개 선택·버전 검증 |
| ` tests/test_patch024_report.py ` | 2 | 과거 보고서의 파일/매니페스트 누락 거부(재현 실패 항목은 별도) |
| ` tests/test_patch025_ranking.py ` | 9 | 선택된 순위 구간만 변경·연속 순위·작은 K 보존 |
| ` tests/test_patch027_context_tuning.py ` | 48 | 현재 질문의 통지 목적·전달 행위·대화 문맥 확장 경계 |
| ` tests/test_patch027_expansion_report.py ` | 2 | 확장 후보 보고서 재현·원천/매니페스트 누락 거부 |
| ` tests/test_patch027_final_test.py ` | 18 | 현재 연결 문서·기록 대상·최종 검색 평가 경계 |
| ` tests/test_patch027_full.py ` | 16 | 승인 원천의 조문·버전·식별자·전체 구축 계약 |
| ` tests/test_patch027_full_ranking.py ` | 21 | 일반 법령 순위의 제한·중복·요청된 접두 결과 보존 |
| ` tests/test_patch027_loss_analysis.py ` | 8 | 반사실 선택 분석·시드/참조 선택 보존·손실 구분 |
| ` tests/test_patch027_paths.py ` | 5 | 과거 고정 경로 해석·캡처 바이트 보존·별칭 범위 |
| ` tests/test_patch027_product.py ` | 34 | 운영 검색 정책과 고정 시험의 일치·출처 관계·가역 활성화 |
| ` tests/test_patch027_report.py ` | 6 | Top3 손실/Top5 보존 구분·트레이스/매니페스트 검증 |
| ` tests/test_patch027_sources.py ` | 6 | 공식 원천의 단일 버전·조문 식별·검증 |
| ` tests/test_patch027_tuning.py ` | 20 | 조문 ID를 하드코딩하지 않는 개념 확장·튜닝 계약 |
| ` tests/test_patch041_retrieval_eval.py ` | 196 | 재구축 검색 평가의 고정 235개 입력·식별/해시·지표·트레이스 계약 |
| ` tests/test_patch042_gap_analysis.py ` | 2 | 과거 격차 분석의 소스 누락·변조 거부(재현 실패/오류 항목은 별도) |
| ` tests/test_patch046_dialogue_integration.py ` | 7 | 실제 대화 직렬화의 현재 세금 범위 선택 |
| ` tests/test_patch046_improvement_eval.py ` | 1 | 평가 질문 순서가 바뀌어도 층화·비교 식별 유지 |
| ` tests/test_patch046_multi_evidence.py ` | 66 | 현재 의도에 따른 복수 법령 근거·문서 조회 범위·근거 예산 |
| ` tests/test_patch046_review_regressions.py ` | 38 | 운영 선택 경계에서 검토 반례의 현재 요청 근거만 선택 |
| ` tests/test_patch051_adversarial.py ` | 30 | 보완 근거 선택의 적대적 반례·현재 효력·본문 지지 |
| ` tests/test_patch051_companion_evidence.py ` | 99 | 요건/효과 질문의 보완 법령 근거 선택 계약 |
| ` tests/test_patch051_evaluation_contract.py ` | 3 | 메타데이터 사건 식별·법령/판례/안내 평가 범위 구분 |
| ` tests/test_patch051_generation_delivery.py ` | 6 | 핵심 조문·항 본문의 그래프/프롬프트/native 요청 전달(HTTP 응답 모의) |
| ` tests/test_patch053_retrieval_independent.py ` | 91 | 현재 의도·생성 입력의 독립 반례(실제 KURE/생성 API 없음) |
| ` tests/test_patch053_retrieval_intent.py ` | 25 | 현재 질문의 직접 주제·권한·기한·효력·신청 방법 우선순위 |
| ` tests/test_pdf_extraction.py ` | 11 | PDF 업로드 유효성·추출 전략·판독 실패 |
| ` tests/test_pdfium_thread_safety.py ` | 1 | PDF 렌더링 후 병렬 OCR 실행의 스레드 경계 |
| ` tests/test_portable_index.py ` | 2 | 봉인 Chroma의 격리 복사·캐시 재사용·원본 변경 구분 |
| ` tests/test_procedure_partition.py ` | 9 | 절차 검색의 기존 DB/통계 보존·후보 필터·깊이·동시 질의 |
| ` tests/test_progress_js.py ` | 0 | 화면 진행 타이머 제어 시계 검사(Node.js 부재로 건너뜀) |
| ` tests/test_project_structure.py ` | 2 | 문서 구조·계획 파일·README의 디렉터리/산출물 기록 |
| ` tests/test_prompt_injection.py ` | 27 | 역할 변경·우회 명령·모호한 공격의 차단/의미 검토 |
| ` tests/test_question_purpose.py ` | 9 | 질문 목적을 주제·사용자 사실과 분리하고 검색/생성에 전달 |
| ` tests/test_rag_handoff.py ` | 3 | 위험 신호별 검색 질의·LangGraph 초기 상태·판독 불가 보류 |
| ` tests/test_registry_integration.py ` | 0 | 개인 등기 PDF 선택 통합 검사(개인 PDF 미지정으로 건너뜀) |
| ` tests/test_repair_public_regression.py ` | 4 | 공개 수리 회귀 평가 CLI·동일 프로토콜의 손실/지표 차이 |
| ` tests/test_repair_reimbursement.py ` | 21 | 수리 문맥과 비용 반환 의도가 있을 때만 비용상환 라우팅 |
| ` tests/test_retrieval_readiness.py ` | 2 | 검색 서비스의 비차단 준비·단일 로더·실패 후 재시도 |
| ` tests/test_retrieval_service.py ` | 75 | 법령/판례/민법/안내 채널 분리·출처·K·검색 진입점(가짜 dense) |
| ` tests/test_retriever.py ` | 15 | 토큰화·BM25·조문 참조·메타데이터 매핑 |
| ` tests/test_risk_signals.py ` | 7 | 개인정보 마스킹·갑구/을구 위험 신호·오탐 경계 |
| ` tests/test_secret_filter.py ` | 16 | 비밀값 탐지·치환·빈 템플릿 안전성 |
| ` tests/test_server_build.py ` | 41 | GitHub 승인 원천의 기본 DB 구축·벡터 재사용·검증·실패 복구 |
| ` tests/test_session_retrieval.py ` | 13 | 명시적 업로드 문서 질문의 세션 OCR 라우팅·문서 참조 경계 |
| ` tests/test_setup_data.py ` | 28 | 설치/검사/준비 옵션·환경/모델 준비·고정 revision·배포 자동 연결 |
| ` tests/test_tesseract_discovery.py ` | 2 | 명시적 설정과 Windows/macOS OCR 실행 파일 탐색 |
| **합계** | **2,828** | 통과 실행 항목; 실패·오류·건너뜀 제외 |

## 실패·오류·건너뜀 전체 목록

아래 항목은 통과한 2,828개에 포함되지 않는다. 실패 3건과 오류 3건은 변경 없는 GitHub 원본에서도 동일하게 재현됐다. 기대값이나 과거 평가 파일을 바꿔 통과시키지 않았다.

- **skipped** ` tests.test_langsmith_connection::test_langsmith_environment_is_configured ` — LANGSMITH_TRACING이 활성화되어 있지 않습니다.
- **skipped** ` tests.test_langsmith_connection::test_langsmith_client_connection ` — LANGSMITH_TRACING이 활성화되어 있지 않습니다.
- **failure** ` tests.test_patch024_report::test_published_results_replay ` — ValueError: Report does not match frozen targets and results
- **failure** ` tests.test_patch042_gap_analysis::test_historical_source_does_not_hide_other_dependency_changes ` — AssertionError: Regex pattern did not match.   Expected regex: 'Analysis does not replay'   Actual message: 'Saved report does not replay'
- **failure** ` tests.test_patch042_gap_analysis::test_new_analysis_records_current_generation_source ` — ValueError: Saved report does not replay
- **error** ` tests.test_patch042_gap_analysis::test_exclusive_failure_labels_follow_question_and_context ` — failed on setup with "ValueError: Saved report does not replay"
- **error** ` tests.test_patch042_gap_analysis::test_return_capacity_and_consumption_capacity_are_distinct ` — failed on setup with "ValueError: Saved report does not replay"
- **error** ` tests.test_patch042_gap_analysis::test_missing_trace_covers_exact_dev_targets_without_civil_review ` — failed on setup with "ValueError: Saved report does not replay"
- **skipped** ` tests.test_progress_js::test_estimate_keeps_elapsed_and_becomes_indeterminate ` — Node.js required for frontend timer check
- **skipped** ` tests.test_registry_integration::test_registry_pdf_end_to_end ` — REGISTRY_SAMPLE_PDF를 지정하면 로컬 등기 PDF 통합 테스트를 실행합니다.

## 2,828개 이름과 개별 검증 조건

[전체 상세 목록](runpod-test-catalog-2828.md)을 참조한다. 매개변수로 나뉜 실행을 생략하거나 하나로 합치지 않았다. 같은 검증 함수를 공유하는 항목은 함수별 검증 설명 아래 각각 나열한다. 원본 assertion·예외·mock 호출 조건도 함께 기록해 설명의 근거를 확인할 수 있다.
