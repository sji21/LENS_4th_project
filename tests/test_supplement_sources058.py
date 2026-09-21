from pathlib import Path
import pytest

from src.database.relational import connect_database, initialize_relational_database
from src.ingestion.load_cases import load_case_records, export_case_chunks
from src.ingestion.load_guides import load_guide_records, export_guide_chunks
from src.ingestion.retrieval_supplements import case_records, form_records, FORM_ID
from src.retrieval.retriever import load_chunks
from src.retrieval.service import RetrievalService, detect_guide_topics


def test_official_supplements_survive_storage_and_search(tmp_path):
    database = tmp_path / 'knowledge.sqlite3'
    initialize_relational_database(database)
    with connect_database(database) as db:
        assert not load_case_records(case_records(), db).skipped
        assert not load_guide_records(form_records(), db).skipped
        export_case_chunks(db, tmp_path / 'cases.jsonl')
        export_guide_chunks(db, tmp_path / 'guides.jsonl')
    cases = load_chunks(tmp_path / 'cases.jsonl')
    assert len(cases) == 2
    assert all(c['metadata']['corpus_role'] == 'case_supplement' for c in cases)
    assert len({c['metadata']['canonical_case_key'] for c in cases}) == 2
    assert all('【이 유】' in c['text'] for c in cases)
    assert {c['metadata']['case_id'] for c in cases} == {'167313', '197934'}
    guides = load_chunks(tmp_path / 'guides.jsonl')
    assert all(c['metadata']['guide_type'] == 'official_form' for c in guides)
    assert all(c['metadata']['article_id'] == FORM_ID for c in guides)
    service = RetrievalService(guides)
    question = 'LH 국민임대 재계약 시 금융정보 동의서를 다시 제출해야 하나요?'
    result = service.search(question, k_law=0, k_case=0, k_guide=2, k_civil=0)
    assert 1 <= len(result.guides) <= 2
    assert any('추가로 제출하지 않아도' in e.text for e in result.guides)
    assert '## 공식 법정 서식' in result.as_prompt_context()
    assert all('flSeq=168433763' in e.source_url for e in result.guides)
    assert not service.search(question, k_guide=0).guides
    assert not service.search('개인 은행 대출 금융정보 동의서가 필요해요').guides


def test_form_request_requires_public_housing_and_financial_consent():
    assert not detect_guide_topics('LH 국민임대의 전대 허락을 받아야 하나요?')
    assert not detect_guide_topics('은행 금융정보 동의서는 어디서 받나요?')
    assert not detect_guide_topics('공공임대 금융정보 동의서는 제외하고 계약기간만 알려줘')
    assert detect_guide_topics('공공임대 재계약 금융 정보 조회 동의가 필요한가요?')


@pytest.mark.parametrize('question', [
    '공공임대는 아니고 민간 은행의 금융정보 제공 동의서만 묻습니다.',
    '공공임대 금융정보 제공 동의서는 묻지 않습니다. 계약기간만 알려주세요.',
    '공공임대 금융정보 동의서는 필요 없고 계약기간만 알려주세요.',
    '이전 대화: LH 국민임대 금융정보 동의서를 알려줘\n사용자 질문: 은행 대출 서류를 알려줘',
    '"LH 국민임대 금융정보 동의서"라는 예문을 봤어요. 개인 은행 서류를 알려줘',
    '사용자 추가 상황: LH 국민임대에 거주합니다.\n사용자 질문: 공공임대가 아닌 은행 금융정보 동의서를 알려줘',
])
def test_excluded_or_historical_public_consent_does_not_route_form(question):
    assert not detect_guide_topics(question)


def test_current_housing_fact_can_supply_scope_for_consent_request():
    assert detect_guide_topics('사용자 추가 상황: LH 국민임대에 거주합니다.\n사용자 질문: 재계약 때 금융정보 조회 동의서를 다시 제출해야 하나요?')


@pytest.mark.parametrize('question', [
    '공공임대 재계약 때 금융정보 동의서는 필요 없나요?',
    'LH 국민임대 금융정보 제공 동의서는 재계약 때 필요 없는지 궁금해요.',
    'LH가 재계약 금융정보 동의를 요구하는데 다시 제출해야 하나요?',
    '공공임대 금융정보 동의서 제출 의무가 없는 게 아닌가요?',
])
def test_consent_exemption_question_still_requests_the_form(question):
    assert detect_guide_topics(question)
