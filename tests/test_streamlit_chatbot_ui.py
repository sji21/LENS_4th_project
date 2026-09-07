from pathlib import Path


APP = Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py"
TEXT = APP.read_text(encoding="utf-8")


def test_streamlit_ui_is_chat_first():
    assert "st.chat_message" in TEXT
    assert "st.chat_input" in TEXT


def test_streamlit_prepares_and_reuses_retrieval_service_in_background():
    assert "answer_question" in TEXT
    assert "get_default_service" in TEXT
    assert "@st.cache_resource(show_spinner=False)" in TEXT
    assert "def load_retrieval_service_loader()" in TEXT
    assert "BackgroundServiceLoader(get_default_service).start()" in TEXT
    assert "retrieval_loader = load_retrieval_service_loader()" in TEXT
    assert '@st.fragment(run_every="1s")' in TEXT
    assert "def render_retrieval_status(" in TEXT
    readiness_body = TEXT[
        TEXT.index("def render_retrieval_readiness("):
        TEXT.index("def render_retrieval_status(")
    ]
    assert "st.rerun()" in readiness_body
    assert "render_retrieval_status(retrieval_loader)" in TEXT
    assert "retrieval_loader.result()" in TEXT
    assert "service=retrieval_service" in TEXT
    assert "검색 모델 준비 완료" in TEXT


def test_ocr_documents_are_attached_through_the_chat_input():
    assert "analyze_uploaded_document" in TEXT
    assert "_infer_document_kind" not in TEXT
    assert 'accept_file="multiple"' in TEXT
    assert 'file_type=("pdf", "jpg", "jpeg", "png")' in TEXT
    assert "def _store_uploaded_documents" in TEXT
    assert "def render_document_manager" in TEXT


def test_upload_question_is_queued_before_ocr_and_tracks_each_file():
    assert 'st.session_state["upload_jobs"] = {}' in TEXT
    assert 'st.session_state["active_upload_job_id"] = None' in TEXT
    assert "def _queue_upload_job" in TEXT
    assert "def process_active_upload_job" in TEXT
    assert '"status": "queued"' in TEXT
    assert 'item["status"] = "processing"' in TEXT
    assert 'item["status"] = "completed"' in TEXT
    assert '"needs_confirmation" if needs_confirmation else "failed"' in TEXT
    assert 'with st.status("첨부 문서 OCR 준비 중..."' in TEXT
    assert "def reconcile_upload_job_state()" in TEXT
    assert "reconcile_upload_job_state()" in TEXT
    assert "disabled=upload_in_progress" not in TEXT
    assert 'submit_mode="disable"' in TEXT
    assert "def _finish_upload_job(" in TEXT
    assert "finally:" in TEXT

    main_body = TEXT.split("def main() -> None:", 1)[1]
    queue_pos = main_body.index("_queue_upload_job(question, uploaded_files)")
    rerun_pos = main_body.index("st.rerun()", queue_pos)
    process_pos = main_body.index("process_active_upload_job(")
    assert process_pos < queue_pos < rerun_pos


def test_user_facing_status_messages_are_descriptive():
    assert '"answered": ("근거를 확인해 답변드렸습니다.", "✅")' in TEXT
    assert '"abstained": ("답변을 바로 제공하기 어렵습니다.", "⚠️")' in TEXT
    assert '"refused": ("이 질문은 LENS의 답변 범위에 포함되지 않습니다.", "🚫")' in TEXT


def test_main_does_not_render_a_sidebar():
    main_body = TEXT.split("def main() -> None:", 1)[1]
    assert "render_sidebar()" not in main_body
    assert "render_document_manager()" in main_body


def test_header_uses_project_title_and_quick_questions_are_removed():
    assert "안전한 부동산 계약을 위한" in TEXT
    assert "챗봇 서비스" in TEXT
    assert "EXAMPLE_QUESTIONS" not in TEXT
    assert "render_quick_questions" not in TEXT
    assert "이런 질문을 해보세요" not in TEXT


def test_intro_card_is_collapsible_and_model_status_precedes_it():
    assert 'st.session_state.setdefault("intro_expanded", True)' in TEXT
    assert "def toggle_intro_card()" in TEXT
    assert "@st.fragment\ndef render_header()" in TEXT
    assert 'key="intro_card"' in TEXT
    assert 'key="intro_toggle"' in TEXT
    assert 'toggle_label = "접기" if expanded else "펼치기"' in TEXT

    main_body = TEXT.split("def main() -> None:", 1)[1]
    readiness_pos = main_body.index(
        'readiness_area = st.container(key="retrieval_readiness_area")'
    )
    header_pos = main_body.index("render_header()")
    assert readiness_pos < header_pos


def test_intro_card_height_changes_chat_viewport_without_covering_input():
    assert "desktop_offset = 500 if expanded else 340" in TEXT
    assert "mobile_offset = 630 if expanded else 405" in TEXT
    assert "min-height: max(9rem, calc(100vh - {desktop_offset}px))" in TEXT
    assert 'submit_mode="disable"' in TEXT


def test_intro_card_spacing_and_toolbar_overlap_are_hardened():
    assert ".st-key-intro_card:has(.hero-copy) .st-key-intro_card_header" in TEXT
    assert "margin-bottom: 1.1rem" in TEXT
    assert "margin: 0" in TEXT
    assert "margin-top: .9rem" in TEXT
    assert '[data-testid="stHeader"] {' in TEXT
    assert "pointer-events: none" in TEXT
    assert '@media (max-width: 900px)' in TEXT
    assert '[data-testid="stAppDeployButton"]' in TEXT
    assert "display: none" in TEXT
    assert ".hero-copy-line" in TEXT
    assert "display: block" in TEXT
    assert "max-width: 46rem" in TEXT


def test_welcome_message_uses_two_readable_paragraphs():
    assert "질문해 주세요.\\n\\n" in TEXT
    assert '"확인 가능한 근거와 함께 안내해 드릴게요."' in TEXT


def test_chat_area_uses_the_original_page_scroll_and_bottom_alignment():
    assert 'st.container(key="chat_area")' in TEXT
    assert ".st-key-chat_area" in TEXT
    assert "overflow-y: auto" not in TEXT
    assert "overscroll-behavior-y: contain" not in TEXT
    assert "min-height: max(9rem" in TEXT
    assert "justify-content: flex-end" in TEXT
    assert "@media (max-width: 640px)" in TEXT


def test_chat_messages_and_statuses_have_distinct_styles():
    assert '[data-testid="stChatMessage"]' in TEXT
    assert "background: #DDEFFD" in TEXT
    assert "background: #FFFFFF" in TEXT
    assert "margin-left: 3.5rem" in TEXT
    assert "margin-right: 3.5rem" in TEXT
    assert ".status-answered" in TEXT
    assert ".status-abstained" in TEXT
    assert ".status-refused" in TEXT


def test_processing_state_has_live_elapsed_timer_without_internal_exception_type():
    assert "streamlit.components.v1 as components" not in TEXT
    assert "st.iframe(" in TEXT
    assert "def render_live_elapsed_timer()" in TEXT
    assert 'id="jeonse-elapsed"' in TEXT
    assert "setInterval(updateElapsed, 100)" in TEXT
    assert "response_slot.empty()" in TEXT
    assert "오류 유형" not in TEXT


def test_streamlit_wires_multiturn_before_existing_answer_chain():
    assert "from src.generation.conversation import resolve_question" in TEXT
    assert 'previous_messages = list(st.session_state["chat_messages"])' in TEXT
    assert "resolve_question(question, previous_messages)" in TEXT
    assert "answer_question(" in TEXT
    assert "resolved.standalone" in TEXT
    assert "service=retrieval_service" in TEXT
    assert "answer_document_question(" in TEXT
    assert "question_references_uploaded_document(" in TEXT
    assert "_available_document_kinds()," in TEXT
    assert "normalize_document_review_question(" in TEXT
    assert "answer_question_text" in TEXT
    assert "if use_uploaded_document:" in TEXT


def test_answer_raw_text_is_kept_for_followup_context():
    assert '"context_content": answer.raw_text if answer.status == "answered" else ""' in TEXT
    assert "이전 대화 맥락을 반영해 질문을 해석했습니다." in TEXT


def test_sources_are_grouped_by_user_facing_categories():
    assert 'render_source_group("업로드 문서 근거", document_sources)' in TEXT
    assert 'render_source_group("관련 법령", law_sources)' in TEXT
    assert 'render_source_group("관련 판례", case_sources)' in TEXT
    assert 'render_source_group("관련 기관 안내", guide_sources)' in TEXT
    assert '답변에 사용한 출처' in TEXT
    assert '· `{doc_type}` ·' not in TEXT


def test_assistant_answer_text_has_explicit_visible_color():
    assert 'AvatarAssistant"]) p' in TEXT
    assert "color: #172B3A !important" in TEXT


def test_answer_render_does_not_expose_raw_text_as_fallback():
    assert "def visible_answer_text(answer: Answer)" in TEXT
    assert '"content": visible_answer_text(answer)' in TEXT
    assert "raw_text = (answer.raw_text" not in TEXT
    assert "검증 전 생성 원문일 수 있으므로" in TEXT


def test_final_answer_replaces_timer_without_completion_rerun():
    answer_body = TEXT[TEXT.index("def _answer_visible_question("):TEXT.index("def process_question(")]
    process_body = TEXT[TEXT.index("def process_question("):TEXT.index("def process_active_upload_job(")]
    upload_body = TEXT[TEXT.index("def process_active_upload_job("):TEXT.index("def main() -> None:")]

    assert "render_assistant_meta(" not in answer_body
    assert "st.markdown(visible_answer_text(answer))" not in answer_body
    assert "response_slot = st.empty()" in answer_body
    assert "response_slot.empty()" in answer_body
    assert "render_assistant_content(assistant_message)" in answer_body
    assert "_answer_visible_question(" in process_body
    assert "st.rerun()" not in process_body
    assert "st.rerun()" not in upload_body


def test_answer_elapsed_time_is_shown_in_chat_meta():
    assert "import time" in TEXT
    assert "started_at = time.perf_counter()" in TEXT
    assert "elapsed_seconds = time.perf_counter() - started_at" in TEXT
    assert '"elapsed_seconds": elapsed_seconds' in TEXT
    assert '⏱ {elapsed_seconds:.1f}초' in TEXT
    assert ".answer-meta-row" in TEXT


def test_streamlit_logs_server_exception_without_showing_details():
    assert "logger = logging.getLogger(__name__)" in TEXT
    assert 'logger.exception("Streamlit 질문 처리 중 예외가 발생했습니다.")' in TEXT
    assert "오류 유형" not in TEXT


def test_live_timer_runs_during_blocking_answer_call():
    timer_pos = TEXT.index("render_live_elapsed_timer()")
    answer_pos = TEXT.index("answer = answer_question(")
    clear_pos = TEXT.index("response_slot.empty()")
    assert timer_pos < answer_pos < clear_pos
