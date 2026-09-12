"""Snapshot reviewed official pages; fail closed before producing ingest records."""
import concurrent.futures
import json
import re
from dataclasses import asdict
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from scripts.patch015_baseline import ROOT, read
from scripts.patch015_baseline import norm, sha
from lxml import html
from src.ingestion.fetch_law_mock import html_to_text, parse_law_header
from src.ingestion.fetch_law_mock import parse_articles
from src.ingestion.load_laws import LawArticleRecord

OUT = ROOT / 'data/eval/patch026-full'


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2)+'\n').encode('utf-8'))


def download(item):
    sid, url = item
    path = OUT/'sources'/f'{sid}.html'
    if not path.exists():
        parts = urlsplit(url)
        if parts.hostname not in {'law.go.kr', 'www.law.go.kr', 'www.korea.kr'}:
            raise ValueError('Unexpected source domain')
        encoded = urlunsplit((parts.scheme, parts.netloc, quote(parts.path), parts.query, parts.fragment))
        with urlopen(Request(encoded, headers={'User-Agent':'Mozilla/5.0'}), timeout=30) as response:
            raw = response.read()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    text = html_to_text(path.read_text(encoding='utf-8'))
    return {'source_id':sid, 'url':url, 'bytes':path.stat().st_size,
            'header':parse_law_header(text), 'preview':text[:350]}


def collect():
    plan = read(ROOT/'data/eval/patch026-scope/plan.json')
    items = {r['source_id']:r['review_record']['url'] for r in plan['missing_articles']}
    items.update({r['source_id']:r['review_record'].get('url') for r in plan['unresolved_law_tagged_sources']})
    items.update(PRIVATE_RULE18='https://law.go.kr/LSW/lsLinkCommonInfo.do?chrClsCd=010202&lspttninfSeq=150799',
                 PUBLIC_RULE13_25='https://law.go.kr/lsLinkCommonInfo.do?lspttninfSeq=124639')
    result=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures={pool.submit(download, item):item for item in items.items() if item[1]}
        for future in concurrent.futures.as_completed(futures):
            sid,url=futures[future]
            try: row=future.result()
            except Exception as exc: row={'source_id':sid,'url':url,'error':str(exc)}
            result.append(row)
            print(json.dumps(row,ensure_ascii=False),flush=True)
    write(OUT/'fetch-status.json',sorted(result,key=lambda r:r['source_id']))


def parse_verified(raw, spec):
    """Extract a single exact article from an explicitly pinned page/version."""
    root = html.fromstring(raw)
    header = parse_law_header(html_to_text(raw))
    for field in ('effective_from', 'proclamation_number', 'proclaimed_at'):
        if header[field] != spec[field]:
            raise ValueError(f'Version mismatch: {spec["source_id"]} {field}')
    if header['effective_from'] > '2026-09-08':
        raise ValueError('Future version relative to evaluation date')
    titles = [norm(t) for t in root.xpath('//h2/text()')]
    if norm(spec['law_name']) not in titles:
        raise ValueError('Law identity mismatch')
    matches = []
    for block in root.xpath('//div[contains(concat(" ", normalize-space(@class), " "), " lawcon ")]'):
        articles = parse_articles(html_to_text(html.tostring(block, encoding='unicode')))
        for article in articles:
            if article[0] == spec['article_number']:
                if len(articles) != 1:
                    raise ValueError('Mixed article block')
                matches.append(article)
    if len(matches) != 1:
        raise ValueError(f'Article missing/duplicated: {spec["source_id"]} {spec["article_number"]}')
    number, title, content = matches[0]
    if re.search(r'위로\n아래로|검색어 입력|파일형식|javascript:', content):
        raise ValueError('Page chrome in article body')
    seq = spec['source_version_id']
    if not re.search(r'lsiSeq='+re.escape(seq)+r'(?:\D|$)', spec['url']+' '+raw):
        raise ValueError('Official sequence not present in source')
    record = LawArticleRecord(
        law_name=spec['law_name'], law_type=spec['proclamation_number'].split()[0],
        ministry=header['ministry'] or '미상', law_code=seq,
        proclamation_number=header['proclamation_number'], proclaimed_at=header['proclaimed_at'],
        effective_from=header['effective_from'], content=content, source_url=spec['url'],
        collected_at='2026-09-13', article_number=number, article_title=title,
        document_type=spec['document_type'], file_path=spec['path'], source_version_id=seq)
    if record.validate():
        raise ValueError(record.validate())
    return record


def compile_records():
    specs = read(OUT/'specs.json')
    records = []
    anchors = set()
    for spec in specs:
        path = ROOT/spec['path']
        if sha(path) != spec['sha256']:
            raise ValueError('Source hash mismatch')
        record = parse_verified(path.read_text(encoding='utf-8'), spec)
        anchor = norm(record.law_name+'-'+record.article_number)
        if anchor in anchors:
            raise ValueError('Duplicate article')
        anchors.add(anchor)
        records.append(record)
    plan=read(ROOT/'data/eval/patch026-scope/plan.json')
    planned={r['article_anchor'] for r in plan['missing_articles']}
    if not planned <= anchors or len(records)!=56:
        raise ValueError('Full reviewed current-article scope not covered')
    path=OUT/'records.jsonl'
    path.write_bytes(''.join(json.dumps(asdict(r),ensure_ascii=False)+'\n' for r in records).encode('utf-8'))
    return records


if __name__=='__main__': collect()
