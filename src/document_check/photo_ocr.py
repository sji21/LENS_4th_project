"""Bounded, local photo OCR alternatives. Confidence is a heuristic, not accuracy."""
import csv
import io
import re
import subprocess
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageOps, ImageFilter


@dataclass(frozen=True)
class PhotoResult:
    text: str
    warnings: tuple[str, ...]
    date_readings: tuple = ()
    table_cells: tuple = ()
    table_values: tuple = ()


def corrected_photo(image_bytes, *, keep_grid=False):
    image = Image.open(io.BytesIO(image_bytes)).convert('L')
    # Bound both CPU cost and memory; enlarging cannot restore lost characters.
    scale = min(2.0, 2400 / max(image.size))
    image = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))))
    image = ImageOps.autocontrast(image, cutoff=1)
    sample = image.copy()
    sample.thumbnail((700, 700))
    scores = []
    for angle in range(-5, 6):
        rotated = np.asarray(sample.rotate(angle, fillcolor=255))
        projection = (rotated < 150).sum(axis=1)
        scores.append((float(np.var(projection)), angle))
    best, angle = max(scores)
    baseline = next(score for score, a in scores if a == 0)
    if angle and best > baseline * 1.15:
        image = image.rotate(angle, expand=True, fillcolor=255)
    image = image.filter(ImageFilter.UnsharpMask(radius=1, percent=110, threshold=4))
    if keep_grid:
        out = io.BytesIO()
        image.save(out, format='PNG')
        return out.getvalue()
    # Remove long straight grid strokes in this alternative only. Original
    # pixels remain a separate OCR candidate to avoid destructive correction.
    pixels = np.asarray(image).copy()
    for axis, minimum in ((0, max(60, image.width // 7)), (1, max(80, image.height // 8))):
        view = pixels if axis == 0 else pixels.T
        for row in view:
            dark = row < 115
            edges = np.diff(np.r_[False, dark, False].astype(np.int8))
            starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
            for start, end in zip(starts, ends):
                if end - start >= minimum:
                    row[start:end] = 255
    image = Image.fromarray(pixels)
    out = io.BytesIO()
    image.save(out, format='PNG')
    return out.getvalue()


def read_layout(data, executable, language, psm, *, regions=None, word_boxes=None):
    result = subprocess.run([executable, 'stdin', 'stdout', '-l', language,
                             '--psm', str(psm), 'tsv'], input=data,
                            capture_output=True, check=True, timeout=25)
    lines, confidences, words, bounds = {}, [], [], {}
    for row in csv.DictReader(io.StringIO(result.stdout.decode('utf-8', errors='replace')), delimiter='\t', quoting=csv.QUOTE_NONE):
        text = (row.get('text') or '').strip()
        if row.get('level') != '5' or not text:
            continue
        key = tuple(row[k] for k in ('page_num', 'block_num', 'par_num', 'line_num'))
        lines.setdefault(key, []).append(text)
        bounds.setdefault(key, []).append((int(row['top']), int(row['height'])))
        confidences.append(float(row['conf']))
        words.append((int(row['top']), int(row['left']), int(row['height']), text))
        if word_boxes is not None:
            word_boxes.append({'text': text, 'left': int(row['left']), 'top': int(row['top']),
                               'width': int(row['width']), 'height': int(row['height']), 'confidence': float(row['conf'])})
    if psm == 11:
        # Sparse blocks are reordered into geometric rows so table cell labels
        # precede their neighbouring values instead of following block order.
        rows = []
        for top, left, height, word in sorted(words):
            if not rows or abs(top - rows[-1][0]) > max(4, height * .55):
                rows.append((top, []))
            rows[-1][1].append((left, word))
        text = '\n'.join(' '.join(word for _, word in sorted(row)) for _, row in rows)
    else:
        text = '\n'.join(' '.join(words) for words in lines.values())
    score = float(np.mean(confidences)) if confidences else 0.0
    if regions is not None:
        for key, line in lines.items():
            if re.search(r'인도|존속기간', ''.join(line)):
                top = min(y for y, _ in bounds[key])
                height = max(h for _, h in bounds[key])
                if not any(abs(top - y) < max(height, h) * 2 for y, h in regions):
                    regions.append((top, height))
    return text, score


def read_date_regions(data, regions, executable, language, method):
    from src.contract_check.dates import extract_clause_dates
    from dataclasses import replace
    image = Image.open(io.BytesIO(data))
    readings = []
    for top, height in regions[:3]:
        box = (0, max(0, top - height * 2), image.width, min(image.height, top + height * 4))
        output = io.BytesIO()
        image.crop(box).save(output, format='PNG')
        try:
            text, confidence = read_layout(output.getvalue(), executable, language, 6)
        except (subprocess.SubprocessError, ValueError, KeyError):
            continue
        readings.extend(replace(item, status='confirmed' if confidence >= 75 else 'review')
                        for item in extract_clause_dates(text, method=method, region=box))
    return readings


def extract_photo(image_bytes, executable, language):
    corrected = corrected_photo(image_bytes)
    candidates = []
    date_readings = []
    date_regions = []
    for index, (data, psm) in enumerate(((image_bytes, 6), (corrected, 6), (corrected, 11))):
        try:
            regions = []
            text, confidence = read_layout(data, executable, language, psm, regions=regions if psm == 6 else None)
            if text:
                candidates.append((text, confidence))
                if regions:
                    date_regions.append((data, regions, f'photo_region_{index}'))
        except (subprocess.SubprocessError, ValueError, KeyError):
            continue
    if not candidates:
        return PhotoResult('', ('사진을 읽지 못했습니다. 문서를 평평하게 놓고 네 모서리와 글자가 선명하게 나오도록 다시 촬영해 주세요.',))
    # Do not merge competing readings: doing so can duplicate or invent values.
    from src.contract_check.rules import FIELD_RULES
    def score(item):
        compact = re.sub(r"\s+", "", item[0])
        anchors = sum(any(re.sub(r"\s+", "", label) in compact for label in rule.labels) for rule in FIELD_RULES)
        return item[1] + anchors * 5 + min(len(compact), 4000) / 120
    text, confidence = max(candidates, key=score)
    warnings = ['촬영본의 명암·작은 기울기를 보정하고 표/문단 배치를 비교했습니다. 표의 셀 관계와 숫자는 원본 확인이 필요합니다.']
    if confidence < 75 or sum(c.isalnum() for c in text) < 80:
        warnings.append('사진의 판독 신뢰도가 낮습니다. 금액·날짜·특약 등 질문할 부분을 정면에서 흔들림·반사 없이 확대 촬영해 다시 첨부해 주세요.')
    amounts = [{re.sub(r"[ ,]", "", value) for value in re.findall(r"[0-9][0-9, ]*원", candidate)} for candidate, _ in candidates]
    if len({tuple(sorted(values)) for values in amounts}) > 1:
        warnings.append('금액 판독 결과가 OCR 방식에 따라 일치하지 않습니다. 보증금·차임·채권최고액 부분을 선명하게 확대 촬영해 다시 첨부해 주세요.')
    for data, regions, method in date_regions:
        date_readings.extend(read_date_regions(data, regions, executable, language, method))
    from src.contract_check.dates import reconcile_dates
    dates = reconcile_dates(date_readings, require_methods=2)
    if any(item.status == 'review' for item in dates):
        warnings.append('인도 조항의 날짜를 두 OCR 방식에서 동일하게 확인하지 못했습니다. 날짜와 인도 문구가 함께 보이는 확대 사진을 확인해 주세요.')
    confirmed_clauses = list(dict.fromkeys(item.evidence for item in dates if item.status == 'confirmed'))
    if confirmed_clauses:
        text += '\n[인도 조항 부분 OCR: 원본·보정본 일치]\n' + '\n'.join(confirmed_clauses)
    from .table_ocr import read_tables, table_evidence_text
    try:
        cells, values = read_tables(corrected_photo(image_bytes, keep_grid=True), executable, language, read_layout)
    except (subprocess.SubprocessError, ValueError, KeyError):
        cells, values = (), ()
        warnings.append('표의 셀 구조를 판독하지 못했습니다. 필요한 항목의 표를 선명하게 다시 첨부해 주세요.')
    table_text = table_evidence_text(cells)
    if table_text:
        text += '\n[표 셀별 OCR: |는 셀 경계, 병합된 당사자 표시는 행 앞에 표시]\n' + table_text
    return PhotoResult(text, tuple(warnings), dates, cells, values)
