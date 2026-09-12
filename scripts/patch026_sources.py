"""Collect five reviewed procedure articles from version-checked official pages."""
import json
import re
from pathlib import Path
from urllib.request import Request, urlopen
from lxml import html

from scripts.patch015_baseline import ROOT, read, write, sha
from src.ingestion.fetch_law_mock import html_to_text, parse_law_header, parse_articles
from src.ingestion.load_laws import LawArticleRecord, write_records

SPECS = (
    ('RR16','주민등록법','제16조','2025-07-22','법률 제20677호','law'),
    ('TAX109','국세징수법','제109조','2026-06-02','법률 제21713호','law'),
    ('TAX97','국세징수법 시행령','제97조','2026-02-27','대통령령 제36126호','decree'),
    ('LT6','지방세징수법','제6조','2026-02-05','법률 제21327호','law'),
    ('LT8','지방세징수법 시행령','제8조','2026-02-05','대통령령 제36076호','decree'),
)


def parse_page(raw, spec, url, file_path):
    sid,name,number,date,proclamation,kind=spec
    root=html.fromstring(raw)
    text=html_to_text(raw)
    header=parse_law_header(text)
    if header['effective_from']!=date or header['proclamation_number']!=proclamation:
        raise ValueError('Unexpected version: '+sid)
    titles=root.xpath('//h2/text()')
    if not any(re.sub(r'\s+','',name)==re.sub(r'\s+','',t) for t in titles):
        raise ValueError('Unexpected law title: '+sid)
    blocks=root.xpath('//div[contains(concat(" ", normalize-space(@class), " "), " lawcon ")]')
    if not blocks: raise ValueError('No article body: '+sid)
    body='\n'.join(html_to_text(html.tostring(b,encoding='unicode')) for b in blocks)
    articles=parse_articles(body)
    matches=[(n,t,b) for n,t,b in articles if n==number]
    if len(matches)!=1 or len(articles)!=1: raise ValueError('Unexpected article set: '+sid)
    n,title,content=matches[0]
    seqs=re.findall(r'lsiSeq=(\d+)',url+' '+raw)
    if not seqs: raise ValueError('Missing official version identifier: '+sid)
    seq=seqs[0]
    return LawArticleRecord(law_name=name,law_type='법률' if kind=='law' else '시행령',
        ministry=header['ministry'] or ('재정경제부' if sid.startswith('TAX') else '행정안전부'),law_code=seq,
        proclamation_number=proclamation,proclaimed_at=header['proclaimed_at'],effective_from=date,
        content=content,source_url=url,collected_at='2026-09-12',article_number=n,article_title=title,
        document_type=kind,file_path=file_path,source_version_id=seq)
    # source_text deliberately empty: these are article pages, not full laws/addenda.


def collect():
    out=ROOT/'data/eval/patch026-expansion'
    (out/'sources').mkdir(parents=True,exist_ok=True)
    registry={r['source_id']:r for r in read(ROOT/'data/eval/dev100-v2/source-registry.json')}
    records=[]; sources=[]
    for spec in SPECS:
        sid=spec[0]; url=registry[sid]['url']; path=out/'sources'/f'{sid}.html'
        raw=urlopen(Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=30).read()
        path.write_bytes(raw)
        record=parse_page(raw.decode('utf-8'),spec,url,path.relative_to(ROOT).as_posix())
        records.append(record)
        sources.append({'source_id':sid,'url':url,'sha256':sha(path),'version':record.source_version_id,
                        'article':record.law_name+'-'+record.article_number,'scope':'single article; no full-law/addenda claim'})
    write_records(records,out/'records.jsonl')
    write(out/'source-manifest.json',sources)
    print('Collected',len(records),'verified article pages')


if __name__=='__main__': collect()
