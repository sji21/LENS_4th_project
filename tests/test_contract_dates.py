from dataclasses import asdict, replace

from chat.document_review import contract_handover_answer, document_readability_request
from src.contract_check.dates import extract_clause_dates, reconcile_dates
from src.contract_check.service import analyze_contract_extraction
from src.document_check.extraction_models import ExtractionResult, PageExtraction
from src.document_check import photo_ocr
from tests.test_photo_document_quality import picture


CLAUSE = ('주택 임대차계약서 임대인 임차인 계약 체결일 2020년 1월 3일. '
          '잔금 100원은 2020년 2월 4일에 지불한다. '
          '2020년 2월 5일까지 임차인에게 인도하며, 임대차 기간은 인도일로부터 '
          '2022년 2월 4일(24개월)까지로 한다.')


def test_dates_keep_distinct_roles_and_source_page():
    readings = extract_clause_dates(CLAUSE, 2)
    by_field = {r.field_id: r for r in readings}
    assert by_field['balance_date'].value == '2020-02-04'
    assert by_field['handover_date'].value == '2020-02-05'
    assert by_field['lease_start_date'].value == '2020-02-05'
    assert by_field['lease_end_date'].value == '2022-02-04'
    assert all(r.page_number == 2 for r in readings)


def test_payment_or_expiry_dates_cannot_replace_missing_handover():
    readings = extract_clause_dates('잔금 2020년2월4일에 지불한다. 임대차기간은 인도일로부터 2022년2월4일까지')
    assert not any(r.field_id in {'handover_date', 'lease_start_date'} for r in readings)
    assert not extract_clause_dates('2020년2월30일까지 임차인에게 인도')


def test_conflict_and_insufficient_independent_readings_stay_review():
    a = extract_clause_dates(CLAUSE, method='original')
    b = extract_clause_dates(CLAUSE.replace('2월 5일', '2월 6일'), method='corrected')
    assert all(r.status == 'review' for r in reconcile_dates(a, require_methods=2))
    result = reconcile_dates(a + b, require_methods=2)
    assert all(r.status == 'review' for r in result if r.field_id == 'handover_date')


def test_focused_ocr_agreement_survives_unrelated_amount_warning_and_serialization():
    readings = reconcile_dates(extract_clause_dates(CLAUSE, method='original') +
                               extract_clause_dates(CLAUSE, method='corrected'), require_methods=2)
    extraction = ExtractionResult((PageExtraction(1, '주택 임대차계약서 임대인 임차인', 'tesseract', 20),),
                                  0, ('금액 판독 결과가 일치하지 않습니다',), readings)
    public = analyze_contract_extraction('test.jpg', extraction).to_public_dict()
    assert all(not r['evidence'] for r in public['dates'])
    assert 'date_readings' not in public['extraction']
    answer = contract_handover_answer({'kind': 'contract', 'analysis': public}, '입주일 언제야?')
    assert '2020년 2월 5일' in answer
    assert '실제 입주' in answer


def test_structured_uncertainty_does_not_fall_back_to_other_text():
    item = replace(extract_clause_dates(CLAUSE)[0], field_id='handover_date', status='review')
    doc = {'kind': 'contract', 'analysis': {'dates': [asdict(item)]},
           'context': {'chunks': [{'page_number': 1, 'text': CLAUSE}]}}
    assert contract_handover_answer(doc, '입주일 언제야?') is None
    assert '인도일' in document_readability_request(doc, '입주일 언제야?')
    assert '임대차 기간' not in document_readability_request(doc, '입주일 언제야?')


def test_photo_targets_clause_regions_and_keeps_consensus(monkeypatch):
    calls = []
    def read(data, executable, language, psm, *, regions=None):
        calls.append(psm)
        if regions is not None:
            regions.append((200, 20))
            return ('주택 임대차계약서 임대인 임차인 인도 문장 뒤섞임' * 4, 85)
        return (CLAUSE, 85)
    monkeypatch.setattr(photo_ocr, 'read_layout', read)
    result = photo_ocr.extract_photo(picture(), 'ocr', 'kor')
    assert len(calls) == 5  # three whole-page, two independent cropped reads
    assert all(r.status == 'confirmed' for r in result.date_readings)
    assert all(r.region == (0, 160, 600 if r.method.endswith('0') else 1200, 280) for r in result.date_readings)
    assert '인도 조항 부분 OCR' in result.text
