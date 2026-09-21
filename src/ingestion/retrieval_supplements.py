"""Verified official case and statutory-form sources, independent of evaluation IDs."""
import hashlib
import json
import re
from pathlib import Path

from lxml import html

from src.ingestion.load_cases import CaseRecord
from src.ingestion.load_guides import GuideRecord

ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = ROOT / 'data/sources/retrieval-supplements-v1'
FORM_ID = 'official-form:공공주택특별법시행규칙-별지제4호서식'


def checked_manifest():
    spec = json.loads((SOURCE_DIR / 'manifest.json').read_text(encoding='utf-8'))
    for name, expected in spec['files'].items():
        path = (SOURCE_DIR / name).resolve()
        if not path.is_relative_to(SOURCE_DIR.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Supplement source hash mismatch: ' + name)
    return spec


def element_text(element):
    for br in element.xpath('.//br'):
        br.tail = '\n' + (br.tail or '')
    return re.sub(r'[ \t]+', ' ', ''.join(element.itertext())).strip()


def case_records():
    manifest = checked_manifest()
    records = []
    for spec in manifest['cases']:
        path = SOURCE_DIR / f"case-{spec['case_id']}.html"
        tree = html.fromstring(path.read_text(encoding='utf-8'))
        headings = [re.sub(r'\s+', '', s) for s in tree.xpath('//h2/text()')]
        case_header = tree.xpath('//h2/following-sibling::*[1]')
        identity = ' '.join(''.join(e.itertext()) for e in case_header)
        match = re.search(r'\[(.+?) (\d{4})\.\s*(\d+)\.\s*(\d+)\. 선고 (.+?) 판결\]', identity)
        if not match or (match[1], f'{match[2]}-{int(match[3]):02d}-{int(match[4]):02d}', re.sub(r'\s+', '', match[5])) != (
                spec['court'], spec['date'], re.sub(r'\s+', '', spec['case_number'])):
            raise ValueError('Official case identity mismatch')
        if re.sub(r'\s+', '', spec['name']) not in headings:
            raise ValueError('Official case title mismatch')
        issue = tree.xpath(f"//h4[@id='sa-{spec['case_id']}']/following-sibling::p[1]")
        body = tree.xpath('//div[@class="pgroup"]')
        if len(issue) != 1 or len(body) != 1:
            raise ValueError('Missing official case sections')
        holding = element_text(issue[0])
        full = element_text(body[0])
        if '【전문】' not in full or '【이 유】' not in full:
            raise ValueError('Missing official judgment reasons')
        record = CaseRecord(
            case_id=spec['case_id'], case_number=spec['case_number'], court_name=spec['court'],
            decision_date=spec['date'], case_type='민사', case_name=spec['name'],
            holding=holding, summary=holding, full_text=full,
            source_url=f"https://www.law.go.kr/LSW/precInfoP.do?precSeq={spec['case_id']}",
            collected_at=manifest['collected_at'], file_path=path.relative_to(ROOT).as_posix())
        if record.validate():
            raise ValueError(record.validate())
        records.append(record)
    return records


def form_records():
    manifest = checked_manifest()
    form = manifest['form']
    content = (SOURCE_DIR / 'public-financial-consent.txt').read_text(encoding='utf-8')
    if '[별지 제4호서식]' not in content or '2022. 12. 29.' not in content:
        raise ValueError('Official form identity mismatch')
    return [GuideRecord(
        guide_id=FORM_ID, title=form['title'], agency='국토교통부', guide_type='official_form',
        topic='금융정보등 제공 동의서', source_url=form['url'], published_at=form['revision'],
        content=content, collected_at=manifest['collected_at'], published_at_source='page')]
