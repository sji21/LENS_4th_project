"""An extractive fallback, never a replacement legal verdict or failed generation."""
from src.document_check.extraction_models import PageExtraction
from src.document_check.risk_rules import detect_risk_signals


def registry_indicator_summary(document):
    if document.get('kind') != 'registry':
        return None
    chunks = document.get('context', {}).get('chunks', [])
    pages = tuple(PageExtraction(c['page_number'], c['text'], c['extraction_method'], len(c['text'])) for c in chunks)
    signals = detect_risk_signals(pages)
    if not signals:
        return None
    paragraphs = [
        '첨부 등기에서 계약 전에 확인할 표시가 발견됐습니다. 아래는 문서의 표시를 정리한 내용이며, 현재 유효한 권리인지나 계약의 안전성을 확정한 결과는 아닙니다.',
        '**문서에서 발견한 표시와 확인사항**',
    ]
    for signal in signals:
        paragraphs.append(f'- **{signal.title} · {signal.page_number}쪽**\n  확인할 사항: ' + ', '.join(signal.checks))
    paragraphs.extend([
        '**계약 전 확인**\n최신 등기 원문에서 해당 항목의 말소 여부와 현재 상태를 확인해 주세요. OCR은 말소선이나 표의 관계를 잘못 읽을 수 있으므로 표시가 있다는 이유만으로 현재 권리가 남아 있다고 단정할 수 없습니다.',
        '이번 생성 답변의 법률적 설명은 근거 검증을 통과하지 못해 포함하지 않았습니다. 위 항목을 원문과 함께 공인중개사 또는 법률 전문가에게 확인해 주세요.',
    ])
    return '\n\n'.join(paragraphs)


def document_readability_request(document, question):
    """Ask for the requested field only; never claim unseen pages lack it."""
    import re
    role = party_name_question(question)
    if document.get('kind') == 'contract' and role:
        title = '임대인' if role == 'landlord_name' else '임차인'
        return (f"첨부 계약서에서 **{title} 성명**을 정확히 확인하지 못했습니다. "
                f"{title} 표시와 성명 칸이 함께 보이도록 표 부분을 선명하게 확대 촬영해 주세요. "
                "다른 당사자의 이름이나 임의의 이름으로 대신 답하지 않겠습니다.")
    if document.get('kind') == 'contract' and move_in_date_question(question):
        return ("첨부 계약서의 **인도일(입주 관련 날짜)**을 정확히 읽지 못했습니다. 날짜가 문서에 없다는 뜻은 아닙니다.\n\n"
                "‘임차인에게 인도’라는 문구와 그 앞의 날짜가 함께 보이도록 해당 부분을 선명하게 확대 촬영해 추가해 주세요. "
                "잔금일·계약 체결일·계약 종료일을 입주일로 대신 안내하지 않겠습니다.")
    if not re.search(r"얼마|언제|몇|적혀|기재|읽|알려|확인", question):
        return None
    topics = {
        'deposit': ('보증금',), 'monthly_rent': ('월세', '차임'),
        'lease_period': ('기간', '만료', '종료일', '입주일'),
        'contract_date': ('계약일', '체결일'), 'property_address': ('주소', '소재지'),
        'leased_area': ('면적',), 'landlord': ('임대인', '집주인'),
        'tenant': ('임차인',), 'payment_schedule': ('잔금', '중도금', '계약금'),
    }
    analysis = document.get('analysis', {})
    fields = analysis.get('fields', [])
    warnings = analysis.get('extraction', {}).get('warnings', [])
    low_quality = any('판독 신뢰도가 낮' in w or '판독 결과' in w for w in warnings)
    requested = [field for field in fields if any(term in question for term in topics.get(field.get('field_id'), ()))]
    uncertain = [field['title'] for field in requested if field.get('status') != 'confirmed' or low_quality]
    if uncertain:
        count = document.get('page_count', 1)
        return (f"현재 첨부된 {count}쪽에서는 요청하신 **{', '.join(uncertain)}**을 정확히 확인하지 못했습니다. "
                "문서에 실제로 없다는 뜻은 아니며 값을 추측해 답하지 않겠습니다.\n\n"
                "해당 항목이 다른 페이지에 있다면 그 페이지를 추가해 주세요. 이미 올린 페이지에 있다면 "
                "문서를 평평하게 놓고 카메라를 정면으로 맞춰, 글자와 표의 항목명·금액·날짜가 함께 보이도록 "
                "반사와 흔들림 없이 확대 촬영해 다시 첨부해 주세요.")
    if low_quality and re.search(r"특약|채권최고액|등기.*금액", question):
        return "질문하신 부분의 판독 신뢰도가 낮아 정확한 내용을 확정하지 못했습니다. 해당 특약이나 금액이 있는 표를 항목명과 함께 선명하게 확대 촬영해 다시 첨부해 주세요."
    return None


def party_name_question(question):
    import re
    text = re.sub(r'\s+', '', question)
    if re.search(r'누구에게|누구한테|누구를|변경|바뀌|다르|틀리|일치|소유자|확인방법', text):
        return None
    if not re.search(r'누구|(?:이름|성명).*(?:뭐|무엇|알려|읽|기재|적혀|확인|[?？]|$)', text):
        return None
    if '임대인' in text or '집주인' in text:
        return 'landlord_name'
    if '임차인' in text or '세입자' in text:
        return 'tenant_name'
    return None


def contract_party_answer(document, question):
    role = party_name_question(question)
    if document.get('kind') != 'contract' or not role:
        return None
    values = [v for v in document.get('context', {}).get('table_values', []) if v['field_id'] == role]
    if len(values) != 1 or values[0]['status'] != 'confirmed' or not values[0]['value']:
        return None
    item = values[0]
    title = '임대인' if role == 'landlord_name' else '임차인'
    return (f"첨부 계약서 **{item['page_number']}쪽의 {title} 성명 칸에서 읽은 이름은 {item['value']}**입니다. "
            "OCR 판독 결과이므로 계약서 원문과 대조해 주세요. 이 이름만으로 소유자 일치 여부나 신원을 확인한 것은 아닙니다.")


def move_in_date_question(question):
    import re
    text = re.sub(r'\s+', '', question)
    return bool(re.search(r'(?:입주일|입주는|입주가|인도일|계약시작일|입주시기).*(?:언제|몇|알려|확인|날짜|[?？])|언제(?:부터)?(?:입주|들어가)', text)) and not bool(re.search(r'입주일(?:전|후)|인도일(?:전|후)', text))


def contract_handover_answer(document, question):
    """Extract a dated handover clause; never substitute the signature/expiry date."""
    import re
    from datetime import date
    if document.get('kind') != 'contract' or not move_in_date_question(question):
        return None
    matches = {}
    analysis = document.get('analysis', {})
    if 'dates' in analysis:
        readings = [item for item in analysis['dates'] if item['field_id'] == 'handover_date']
        if any(item['status'] != 'confirmed' for item in readings):
            return None
        for item in readings:
            try:
                matches[date.fromisoformat(item['value'])] = item['page_number']
            except (ValueError, KeyError):
                return None
    else:
        # Older saved uploads have no structured dates; preserve strict parsing.
        warnings = analysis.get('extraction', {}).get('warnings', [])
        if any('판독 신뢰도가 낮' in w or '날짜 판독 결과' in w for w in warnings):
            return None
        from src.contract_check.dates import extract_clause_dates
        for chunk in document.get('context', {}).get('chunks', []):
            for item in extract_clause_dates(chunk.get('text', ''), chunk.get('page_number', 1)):
                if item.field_id == 'handover_date':
                    matches[date.fromisoformat(item.value)] = item.page_number
    if len(matches) != 1:
        return None
    value, page = next(iter(matches.items()))
    return (f"첨부 계약서 **{page}쪽에 기재된 인도기한은 {value.year}년 {value.month}월 {value.day}일**입니다. "
            "입주 관련 날짜를 물으신 것이므로 계약서의 이 날짜를 먼저 안내드립니다.\n\n"
            "문서에 ‘임대차 기간은 인도일로부터’라고 적혀 있다면 해당 문구도 함께 확인해야 하며, 계약 체결일이나 임대차 종료일과는 구분합니다. "
            "다만 ‘해당 날짜까지 인도한다’는 문구만으로 실제 입주한 날짜까지 확정할 수는 없습니다. OCR로 읽은 날짜는 계약서 원문과 대조해 주세요.")
