import io
from unittest.mock import Mock

from PIL import Image
from src.document_check import photo_ocr
from src.document_check.extraction_models import ExtractionResult, PageExtraction
from src.contract_check.service import analyze_contract_extraction
from src.contract_check.rules import check_contract_fields
from chat.document_review import document_readability_request


def picture():
    out = io.BytesIO()
    Image.new('RGB', (600, 800), 'white').save(out, format='PNG')
    return out.getvalue()


def test_photo_compares_layouts_and_requests_recapture_for_conflicting_amounts(monkeypatch):
    reader = Mock(side_effect=[('보증금 100원', 40), ('보증금 700원', 60), ('보증금 100원', 50)])
    monkeypatch.setattr(photo_ocr, 'read_layout', reader)
    result = photo_ocr.extract_photo(picture(), 'ocr', 'kor')
    assert reader.call_count == 3
    assert result.text == '보증금 700원'  # one hypothesis, never concatenated
    assert any('판독 신뢰도가 낮' in w for w in result.warnings)
    assert any('금액 판독 결과' in w for w in result.warnings)


def test_failed_photo_requests_reshoot(monkeypatch):
    monkeypatch.setattr(photo_ocr, 'read_layout', lambda *a, **kw: ('', 0))
    result = photo_ocr.extract_photo(picture(), 'ocr', 'kor')
    assert not result.text
    assert '다시 촬영' in result.warnings[0]


def test_table_adjacent_amount_cannot_confirm_blank_deposit():
    text = '임대차계약서 임대인 임차인 보증금:   월차임: 500000원'
    fields = check_contract_fields((PageExtraction(1, text, 'tesseract', len(text)),))
    assert next(f for f in fields if f.field_id == 'deposit').status == 'review'
    assert next(f for f in fields if f.field_id == 'monthly_rent').status == 'confirmed'


def test_partial_upload_is_not_proof_of_missing_contract_fields():
    text = '주택 임대차계약서 임대인 임차인 보증금 100000원'
    a = analyze_contract_extraction('first.jpg', ExtractionResult((PageExtraction(1, text, 'tesseract', len(text)),), 0))
    assert '1쪽만' in a.summary
    missing = [f for f in a.fields if f.status == 'not_found']
    assert missing and all('실제 누락으로 판단하지 않습니다' in f.guidance for f in missing)


def test_question_checks_requested_field_only():
    doc = {'page_count':1, 'analysis': {'fields':[
        {'field_id':'deposit','title':'보증금','status':'confirmed'},
        {'field_id':'lease_period','title':'임대차 기간','status':'not_found'},
    ]}}
    assert document_readability_request(doc, '보증금 얼마야?') is None
    assert '다른 페이지' in document_readability_request(doc, '임대차 기간이 언제야?')
    assert document_readability_request(doc, '보증금 반환은 어떻게 해?') is None
    doc['analysis']['extraction'] = {'warnings':['금액 판독 결과가 일치하지 않습니다']}
    assert '확인하지 못했습니다' in document_readability_request(doc, '보증금 얼마야?')


def test_uncertain_photo_downgrades_detected_values():
    text = '주택 임대차계약서 임대인 임차인 보증금 100000원'
    a = analyze_contract_extraction('photo.jpg', ExtractionResult((PageExtraction(1, text, 'tesseract', len(text)),), 0, ('금액 판독 결과가 일치하지 않습니다',)))
    assert next(f for f in a.fields if f.field_id == 'deposit').status == 'review'


from tests.test_chatting_routing import runtime, call


def test_unreadable_requested_value_never_reaches_generation(runtime):
    from chat.services import initial_state
    state = initial_state()
    state['documents'] = [{'document_id':'c', 'kind':'contract', 'page_count':1, 'analysis':{'fields':[
        {'field_id':'deposit','title':'보증금','status':'review'}]}}]
    message = call(state, runtime, '이 계약서 보증금은 얼마야?')
    assert message['reason'] == 'document_readability'
    assert '확대 촬영' in message['content']
    runtime.document.assert_not_called()
    runtime.official.assert_not_called()
    assert message['document_ids'] == ['c']
