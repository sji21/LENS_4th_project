from dataclasses import asdict
import io
import pytest

from PIL import Image, ImageDraw

from src.document_check.table_ocr import TableCell, TableValue, detect_cells, name_targets, read_tables, table_evidence_text
from chat.document_review import contract_party_answer, document_readability_request
from chat import services
from tests.test_chatting_routing import runtime, call


def ruled_table():
    im = Image.new('L', (500, 220), 255)
    draw = ImageDraw.Draw(im)
    for y in (10, 110, 210):
        draw.line((10, y, 490, y), fill=0, width=2)
    for y in (60, 160):
        draw.line((90, y, 490, y), fill=0, width=2)
    for x in (10, 90, 230, 490):
        draw.line((x, 10, x, 210), fill=0, width=2)
    return im


def test_detects_merged_role_cell_without_crossing_adjacent_rows():
    cells = detect_cells(ruled_table())
    assert len(cells) == 10
    assert sum(c.box[3] - c.box[1] >= 95 for c in cells) == 2


def labelled():
    return [TableCell((10, 10, 90, 110), '임 대 인', 95),
            TableCell((90, 60, 230, 110), '성명', 95),
            TableCell((230, 60, 490, 110), '', 0),
            TableCell((10, 110, 90, 210), '임차인', 95),
            TableCell((90, 160, 230, 210), '성명', 95),
            TableCell((230, 160, 490, 210), '', 0)]


def test_role_and_label_bind_only_same_merged_row():
    targets = name_targets(labelled())
    assert [(f, c.box[1]) for f, c in targets] == [('landlord_name', 60), ('tenant_name', 160)]
    assert name_targets(labelled()[1:3]) == []
    assert name_targets([labelled()[0], *labelled()[4:]]) == []


def test_blank_image_has_no_table_or_ocr_calls():
    image = Image.new('L', (500, 300), 255)
    out = io.BytesIO(); image.save(out, format='PNG')
    def reader(*args, **kwargs):
        raise AssertionError('no table')
    assert read_tables(out.getvalue(), 'ocr', 'kor', reader) == ((), ())


def document(values=()):
    return {'document_id': 'c', 'kind': 'contract', 'filename': 'test.jpg',
            'context': {'chunks': [], 'table_values': [asdict(v) for v in values]}}


def test_name_answer_uses_owned_verified_value_not_raw_ocr(runtime):
    d = document([TableValue('landlord_name', '김예시', 'confirmed', 1, (0, 0, 100, 30)),
                  TableValue('tenant_name', '이예시', 'confirmed', 1, (0, 40, 100, 70))])
    state = services.initial_state(); state['documents'] = [d]
    runtime.evidences.return_value = ()
    result = call(state, runtime, '임대인이 누구야?')
    assert result['reason'] == 'document_field_extracted'
    assert '김예시' in result['content'] and '이예시' not in result['content']
    runtime.planner.assert_not_called()
    runtime.document.assert_not_called()


def test_unverified_names_never_generate_placeholder(runtime):
    d = document([TableValue('landlord_name', '', 'review', 1, (0, 0, 100, 30))])
    state = services.initial_state(); state['documents'] = [d]
    result = call(state, runtime, '임대인 이름은 뭐야?')
    assert result['reason'] == 'document_readability'
    assert '임대인 성명' in result['content']
    runtime.document.assert_not_called()


def test_multiple_owners_or_missing_metadata_are_not_guessed():
    item = TableValue('landlord_name', '김예시', 'confirmed', 1, (0, 0, 100, 30))
    assert contract_party_answer(document([item, item]), '집주인 이름은?') is None
    assert contract_party_answer(document(), '임대인이 누구야?') is None
    assert '성명' in document_readability_request(document(), '임대인이 누구야?')


def test_table_text_keeps_row_boundaries_and_uncertainty():
    text = table_evidence_text(labelled())
    assert '임대인 / 성명 | [셀 판독 확인 필요]' in text
    assert '임차인 / 성명 | [셀 판독 확인 필요]' in text


def test_table_private_text_not_exposed_in_public_analysis():
    from src.contract_check.service import analyze_contract_extraction
    from src.document_check.extraction_models import ExtractionResult, PageExtraction
    extraction = ExtractionResult((PageExtraction(1, '임대차계약서 임대인 임차인', 'tesseract', 20),), 0,
                                  table_cells=tuple(labelled()),
                                  table_values=(TableValue('landlord_name', '김예시', 'confirmed', 1, (0, 0, 100, 30)),))
    payload = analyze_contract_extraction('test.jpg', extraction).to_public_dict()
    assert 'table_cells' not in payload['extraction']
    assert 'table_values' not in payload['extraction']
    assert '김예시' not in str(payload)
    assert next(f for f in payload['fields'] if f['field_id'] == 'landlord_name')['status'] == 'confirmed'


def test_party_legal_questions_are_not_identity_lookups():
    from chat.document_review import party_name_question
    assert party_name_question('임대인은 누구에게 통보해야 해?') is None
    assert party_name_question('임대인 이름이 바뀌었는데 계약은 유효해?') is None


@pytest.mark.parametrize('second,expected', [('김예시', 'confirmed'), ('김다름', 'review')])
def test_independent_cell_readings_must_agree(monkeypatch, second, expected):
    from src.document_check import table_ocr
    monkeypatch.setattr(table_ocr, 'rectify_table', lambda image: image)
    monkeypatch.setattr(table_ocr, 'read_cells', lambda *args: labelled()[:3])
    readings = iter([('김예시', 95), (second, 95)])
    def reader(*args, **kwargs):
        return next(readings)
    out = io.BytesIO(); ruled_table().save(out, format='PNG')
    cells, values = read_tables(out.getvalue(), 'ocr', 'kor', reader)
    assert values[0].status == expected
    if expected == 'review':
        assert values[0].value == ''
