"""Append reviewed official case text in a new case-only corpus version."""
import argparse
import hashlib
import json
import re
import shutil
import sqlite3
from pathlib import Path
from datetime import datetime, timezone


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--baseline', type=Path, required=True)
    ap.add_argument('--sources', type=Path, required=True)
    ap.add_argument('--approval', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    approval = json.loads(args.approval.read_text('utf-8-sig'))
    records = json.loads(args.sources.read_text('utf-8-sig'))
    # An explicit author/reviewer source seal is mandatory before membership work.
    if not approval.get('pass') or approval.get('source_manifest_sha256') != sha(args.sources):
        raise ValueError('official source cross-review seal mismatch')
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output/'chunks').mkdir()
    (args.output/'sources').mkdir()
    (args.output/'database').mkdir()
    original = args.baseline/'chunks/cases.jsonl'
    shutil.copy2(original, args.output/'chunks/cases.jsonl')
    chunks = [json.loads(l) for l in original.read_text('utf-8').splitlines() if l.strip()]
    bycase = {}
    for c in chunks:
        bycase.setdefault(str(c['metadata']['case_id']), []).append(c)
    # New case-specific DB contains original case chunks and reviewed full sources.
    db = sqlite3.connect(args.output/'database/cases.sqlite3')
    db.executescript('CREATE TABLE case_sources(case_id TEXT PRIMARY KEY, canonical_case_key TEXT, court TEXT, decision_date TEXT, case_number TEXT, case_name TEXT, source_url TEXT, source_sha256 TEXT, full_text TEXT); CREATE TABLE case_chunks(chunk_id TEXT PRIMARY KEY, case_id TEXT, canonical_case_key TEXT, text TEXT, metadata_json TEXT);')
    for c in chunks:
        m = c['metadata']
        db.execute('INSERT INTO case_chunks VALUES(?,?,?,?,?)', (c['chunk_id'],str(m['case_id']),m['canonical_case_key'],c['text'],json.dumps(m, ensure_ascii=False)))
    added, links = [], []
    now = datetime.now(timezone.utc).isoformat()
    for record in records:
        if not record['identity_matches']:
            raise ValueError('unresolved case identity')
        source = Path(record['source_path'])
        if sha(source) != record['source_sha256']:
            raise ValueError('source hash changed')
        text = source.read_text('utf-8')
        case_id = record['precSeq'] or 'official-'+digest(record['official_url'])[:20]
        saved = args.output/'sources'/f'{case_id}.txt'
        shutil.copy2(source, saved)
        existing = bycase.get(case_id, [])
        if existing:
            key = existing[0]['metadata']['canonical_case_key']
            meta = dict(existing[0]['metadata'])
        else:
            normalize = lambda s: re.sub(r'\s+', '', s).replace('고등법원', '고법').replace('지방법원', '지법')
            identity = [normalize(record['court']), normalize(record['case_number']), record['decision_date'], normalize(record['case_name'])]
            key = digest(json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(',', ':')))
            meta = {'doc_type':'case', 'status':'current', 'case_id':case_id, 'canonical_case_key':key,
                'court_name':record['court'], 'court_level':0 if record['court']=='대법원' else 2,
                'decision_date':record['decision_date'], 'case_number':record['case_number'],
                'title':record['case_name'], 'case_name':record['case_name'],
                'source_name':'대한민국 법원' if 'scourt.go.kr' in record['official_url'] else '국가법령정보센터', 'source_url':record['official_url'],
                'summary_type':'official', 'scope_tier':'lease_related'}
        db.execute('INSERT INTO case_sources VALUES(?,?,?,?,?,?,?,?,?)',
            (case_id,key,record['court'],record['decision_date'],record['case_number'],record['case_name'],record['official_url'],record['source_sha256'],text))
        # Source-based continuous windows include every character of all opinions.
        # No question, answer, reviewed proposition, or expected ID enters text.
        start = text.index('【판시사항】') if '【판시사항】' in text else record['reasoning_start']
        stop = record['reasoning_end']
        # Whitespace after the last judicial line is retained if review includes it.
        stop = max(stop, record.get('reviewed_proposition_passage', {}).get('end', stop))
        index = 0
        coverage = []
        while start < stop:
            end = min(stop, start+1800)
            if end < stop:
                paragraph = text.rfind('\n', start+1000, end)
                if paragraph > start:
                    end = paragraph+1
            header = f"[{record['court']} {record['decision_date']} {record['case_number']} {record['case_name']}]\n"
            body = header+text[start:end]
            metadata = dict(meta, canonical_case_key=key, source_url=record['official_url'],
                source_relative_path=f'sources/{case_id}.txt', source_sha256=record['source_sha256'],
                source_start=start, source_end=end, source_offset_unit='unicode_codepoint_newlines_normalized',
                indexed_official_section='full_text_window', corpus_version='case-internal-v1',
                collected_at=now, text_sha256=digest(body))
            cid = f'case:{case_id}#full-v1-{index}'
            chunk = {'chunk_id':cid, 'doc_id':f'case-document:{case_id}', 'chunk_index':index,
                     'text':body, 'metadata':metadata}
            added.append(chunk)
            db.execute('INSERT INTO case_chunks VALUES(?,?,?,?,?)', (cid,case_id,key,body,json.dumps(metadata,ensure_ascii=False)))
            coverage.append([start,end])
            if end == stop:
                break
            start = max(start+1, end-300)
            index += 1
        links.append(dict(case_id=case_id, canonical_case_key=key, source_sha256=record['source_sha256'],
            existing_case=bool(existing), windows=coverage, source_end=stop, all_opinions_preserved=True))
    with (args.output/'chunks/cases.jsonl').open('a', encoding='utf-8') as stream:
        for c in added:
            stream.write(json.dumps(c,ensure_ascii=False)+'\n')
    (args.output/'chunks/additions.jsonl').write_text(''.join(json.dumps(c,ensure_ascii=False)+'\n' for c in added), 'utf-8')
    db.commit(); db.close()
    manifest = {'version':'case-internal-v1', 'source_manifest_sha256':sha(args.sources),
        'source_approval_sha256':sha(args.approval), 'baseline_chunks_sha256':sha(original),
        'original_chunks_preserved_as_prefix':(args.output/'chunks/cases.jsonl').read_bytes().startswith(original.read_bytes()),
        'original_case_chunks':len(chunks), 'added_chunks':len(added),
        'unique_cases':len({c['metadata']['canonical_case_key'] for c in chunks+added}),
        'links':links, 'files':{str(p.relative_to(args.output)):sha(p) for p in args.output.rglob('*') if p.is_file()}}
    (args.output/'CORPUS_MANIFEST.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps({'original_chunks':len(chunks),'added_chunks':len(added),'unique_cases':manifest['unique_cases']}))


if __name__ == '__main__':
    main()
