"""Local ruled-table segmentation and conservative role/label/value binding."""
import io
import re
import subprocess
import time
from dataclasses import dataclass, replace

import numpy as np
from PIL import Image, ImageOps, ImageFilter


@dataclass(frozen=True)
class TableCell:
    box: tuple[int, int, int, int]
    text: str = ""
    confidence: float = 0
    page_number: int = 1
    coordinate_space: str = "table_deskewed"


@dataclass(frozen=True)
class TableValue:
    field_id: str
    value: str
    status: str
    page_number: int
    box: tuple[int, int, int, int]


def _centers(indices):
    groups = []
    for value in indices:
        if not groups or value > groups[-1][-1] + 3:
            groups.append([])
        groups[-1].append(int(value))
    return [round(sum(group) / len(group)) for group in groups]


def _longest_run(row):
    edges = np.diff(np.r_[False, row, False].astype(np.int8))
    starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
    if not len(starts):
        return (0, 0)
    index = np.argmax(ends - starts)
    return int(starts[index]), int(ends[index])


def _ink(image):
    gray = image.convert('L')
    # A shadow is not a table border: compare against the local background.
    background = np.asarray(gray.filter(ImageFilter.GaussianBlur(12)), dtype=float)
    return np.asarray(gray, dtype=float) < background - 25


def rectify_table(image):
    """Deskew a supported table frame without cropping the photographed page."""
    dark = _ink(image)
    height, width = dark.shape
    padded = np.pad(dark, ((15, 15), (0, 0)))
    bands = np.logical_or.reduce([padded[i:i + height] for i in range(31)])
    candidates = [(y, *_longest_run(row)) for y, row in enumerate(bands)]
    ys = _centers([y for y, left, right in candidates if right - left > width * .65])
    if len(ys) < 3 or ys[-1] - ys[0] < height * .4:
        return image
    lines = []
    for y in (ys[0], ys[-1]):
        left, right = _longest_run(bands[y])
        xs = np.arange(left + 4, right - 4, 3)
        best = (0, None)
        for slope in np.arange(-.04, .041, .002):
            for delta in range(-15, 16, 2):
                rows = np.rint(y + delta + slope * (xs - width / 2)).astype(int)
                if min(rows) < 5 or max(rows) >= height - 5:
                    continue
                score = np.logical_or.reduce([dark[rows + d, xs] for d in range(-4, 5)]).mean()
                if score > best[0]:
                    best = (score, (slope, y + delta))
        if best[0] < .8:
            return image
        slope, intercept = best[1]
        full_x = np.arange(width)
        full_y = np.clip(np.rint(intercept + slope * (full_x - width / 2)).astype(int), 5, height - 6)
        support = np.logical_or.reduce([dark[full_y + d, full_x] for d in range(-5, 6)])
        left, right = _longest_run(support)
        if right - left < width * .65:
            return image
        lines.append(((left, intercept + slope * (left - width / 2)),
                      (right - 1, intercept + slope * (right - 1 - width / 2))))
    # Keep the whole page when the paper is curved. A quadrilateral warp can
    # clip cells if a border ends inside the photographed page.
    import math
    (left, right) = lines[-1]
    slope = (right[1] - left[1]) / max(right[0] - left[0], 1)
    return image.rotate(math.degrees(math.atan(slope)), resample=Image.Resampling.BICUBIC, expand=True, fillcolor=255)



def detect_cells(image):
    """Require four supported edges. Preserve vertically merged cells."""
    dark = _ink(image)
    height, width = dark.shape
    horizontal = []
    for y, row in enumerate(dark):
        edges = np.diff(np.r_[False, row, False].astype(np.int8))
        starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
        if len(starts) and max(ends - starts) >= max(40, width * .15):
            horizontal.append(y)
    ys = _centers(horizontal)[:100]
    boxes = set()
    for i, top in enumerate(ys):
        for bottom in ys[i + 1:i + 9]:
            if bottom - top < 10:
                continue
            strip = dark[top + 2:bottom - 1]
            padded = np.pad(strip, ((0, 0), (3, 3)))
            vertical = np.logical_or.reduce([padded[:, j:j + width] for j in range(7)])
            xs = _centers(np.flatnonzero(vertical.mean(axis=0) >= .85))
            for left, right in zip(xs, xs[1:]):
                if right - left < 12:
                    continue
                if all(dark[max(0, y - 6):min(height, y + 7), left:right + 1].any(axis=0).mean() >= .85
                       for y in (top, bottom)):
                    boxes.add((left, top, right, bottom))
    # Larger rectangles spanning already closed cells are not individual cells.
    return [TableCell(box) for box in sorted(boxes, key=lambda b: (b[1], b[0]))
            if not any(box != other and box[0] <= other[0] and box[1] <= other[1]
                       and box[2] >= other[2] and box[3] >= other[3] for other in boxes)][:160]


def name_targets(cells):
    """A merged party-role cell must cover the name label's own row."""
    targets = []
    for role, field_id in (('임대인', 'landlord_name'), ('임차인', 'tenant_name')):
        roles = [c for c in cells if re.sub(r'[^가-힣]', '', c.text) == role and c.confidence >= 60]
        for label in cells:
            if re.sub(r'[^가-힣]', '', label.text) != '성명' or label.confidence < 60:
                continue
            x, y, right, bottom = label.box
            parents = [c for c in roles if c.box[2] <= x + 3 and c.box[1] <= y + 3 and c.box[3] >= bottom - 3]
            # Nearest containing role only; never carry a role into a later row/table.
            if len(parents) != 1:
                continue
            values = [c for c in cells if abs(c.box[0] - right) <= 4 and abs(c.box[1] - y) <= 4 and abs(c.box[3] - bottom) <= 4]
            if len(values) == 1:
                targets.append((field_id, values[0]))
    return targets


def inline_vertical_label(patch):
    if patch.height < patch.width * 1.3:
        return patch
    ink = np.asarray(ImageOps.autocontrast(patch, cutoff=2)) < 110
    margin = max(3, round(patch.width * .2))
    ink[:, :margin] = False
    ink[:, -margin:] = False
    rows = np.flatnonzero(ink.sum(axis=1) > max(2, patch.width * .1))
    groups = []
    for y in rows:
        if not groups or y > groups[-1][-1] + 4:
            groups.append([])
        groups[-1].append(int(y))
    if not 2 <= len(groups) <= 6 or any(g[-1] - g[0] < 8 for g in groups):
        return patch
    pixels = np.asarray(patch).copy()
    pixels[:, :margin] = 255
    pixels[:, -margin:] = 255
    patch = Image.fromarray(pixels)
    canvas = Image.new('L', (len(groups) * (patch.width + 8), max(g[-1] - g[0] + 5 for g in groups)), 255)
    for i, group in enumerate(groups):
        canvas.paste(patch.crop((0, max(0, group[0] - 2), patch.width, group[-1] + 3)), (i * (patch.width + 8), 0))
    return canvas


def read_cells(image, cells, executable, language, reader):
    """Batch isolated cells with whitespace; map OCR coordinates back to cells."""
    result = []
    for offset in range(0, len(cells), 16):
        batch = cells[offset:offset + 16]
        patches, cursor = [], 20
        for cell in batch:
            x, y, right, bottom = cell.box
            patch = image.crop((x + 4, y + 4, right - 4, bottom - 4))
            patch = ImageOps.autocontrast(patch, cutoff=2)
            patch = inline_vertical_label(patch)
            scale = min(3, 1600 / patch.width, 400 / patch.height)
            patch = patch.resize((max(1, round(patch.width * scale)), max(1, round(patch.height * scale))))
            patches.append((cell, patch, cursor))
            cursor += patch.height + 40
        sheet = Image.new('L', (1640, cursor), 255)
        for _, patch, top in patches:
            sheet.paste(patch, (20, top))
        output = io.BytesIO(); sheet.save(output, format='PNG')
        words = []
        reader(output.getvalue(), executable, language, 11, word_boxes=words)
        for cell, patch, top in patches:
            owned = [w for w in words if top <= w['top'] + w['height'] / 2 < top + patch.height]
            owned.sort(key=lambda w: (w['top'], w['left']))
            text = ' '.join(w['text'] for w in owned)
            confidence = min((w['confidence'] for w in owned), default=0)
            # Small header cells need a single-line pass; do not reinterpret
            # larger address/value cells as party labels.
            if cell.box[2] - cell.box[0] < image.width * .12 and patch.height < 350:
                out = io.BytesIO(); ImageOps.expand(patch, border=12, fill=255).save(out, format='PNG')
                local_text, local_confidence = reader(out.getvalue(), executable, 'kor' if 'kor' in language else language, 6)
                known_label = re.sub(r'[^가-힣]', '', local_text) in {'임대인', '임차인', '성명', '주소', '전화', '보증금', '계약금', '잔금'}
                if local_confidence > confidence or (known_label and local_confidence >= 60):
                    text, confidence = local_text, local_confidence
            result.append(replace(cell, text=text, confidence=confidence))
    return result


def read_tables(data, executable, language, reader):
    original_reader = reader
    deadline = time.monotonic() + 45
    def reader(*args, **kwargs):
        if time.monotonic() >= deadline:
            raise subprocess.TimeoutExpired('local table OCR', 45)
        return original_reader(*args, **kwargs)
    image = rectify_table(Image.open(io.BytesIO(data)).convert('L'))
    cells = detect_cells(image)
    if not cells:
        return (), ()
    cells = read_cells(image, cells, executable, language, reader)
    values = []
    for field_id, cell in name_targets(cells)[:4]:
        x, y, right, bottom = cell.box
        crop = image.crop((x + 3, y + 3, right - 2, bottom - 2))
        crop = ImageOps.expand(crop.resize((crop.width * 2, crop.height * 2)), border=12, fill=255)
        output = io.BytesIO(); crop.save(output, format='PNG')
        hypotheses = []
        for source in (crop, ImageOps.autocontrast(crop, cutoff=1)):
            output = io.BytesIO(); source.save(output, format='PNG')
            text, confidence = reader(output.getvalue(), executable, language, 6)
            name = re.sub(r'\s+', '', text).strip('|:：;')
            if re.fullmatch(r'[가-힣]{2,5}', name) and confidence >= 75:
                hypotheses.append(name)
        confirmed = len(hypotheses) == 2 and len(set(hypotheses)) == 1
        values.append(TableValue(field_id, hypotheses[0] if confirmed else '',
                                 'confirmed' if confirmed else 'review', 1, cell.box))
    # Duplicate roles (e.g. co-lessors) cannot silently collapse to one person.
    values = [replace(item, value='', status='review') if sum(v.field_id == item.field_id for v in values) > 1 else item
              for item in values]
    return tuple(cells), tuple(values)


def table_evidence_text(cells):
    """Expose cell boundaries to retrieval; unclear cells remain explicit gaps."""
    rows = []
    for top in sorted({c.box[1] for c in cells}):
        row = sorted((c for c in cells if c.box[1] == top), key=lambda c: c.box[0])
        if not any(c.text and c.confidence >= 75 for c in row):
            continue
        parents = [c for c in cells if c.box[1] < top < c.box[3] and c.box[2] <= row[0].box[0] + 4
                   and re.sub(r'[^가-힣]', '', c.text) in {'임대인', '임차인'} and c.confidence >= 60]
        prefix = (re.sub(r'\s+', '', parents[0].text) + ' / ') if len(parents) == 1 else ''
        rows.append(prefix + ' | '.join(re.sub(r'\s+', ' ', c.text).strip() if c.confidence >= 75 else '[셀 판독 확인 필요]' for c in row))
    return '\n'.join(rows)
