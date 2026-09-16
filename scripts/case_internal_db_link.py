"""Restore full DB-to-chunk provenance from 8,366 through all supplements."""
import argparse
import json
import sqlite3
from pathlib import Path
from case_internal_run import sha,write


def rows(path):
    db=sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True);db.row_factory=sqlite3.Row
    result=[dict(r) for r in db.execute('SELECT c.*,d.source_url FROM cases c JOIN documents d ON c.document_id=d.document_id WHERE c.corpus_active=1')]
    db.close();return result


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--original',type=Path,required=True)
    ap.add_argument('--baseline',type=Path,required=True);ap.add_argument('--data',type=Path,required=True)
    args=ap.parse_args()
    original=rows(args.original.resolve());baseline=rows(args.baseline.resolve())
    assert len(original)==8366 and len(baseline)==8377
    first={r['canonical_case_key']:r for r in original};second={r['canonical_case_key']:r for r in baseline}
    assert set(first).issubset(second)
    assert all(first[k]==second[k] for k in first), 'original case rows changed'
    db=sqlite3.connect(args.data/'database/cases.sqlite3')
    for r in baseline:
        db.execute('INSERT OR IGNORE INTO case_sources VALUES(?,?,?,?,?,?,?,?,?)',
            (r['case_id'],r['canonical_case_key'],r['court_name'],r['decision_date'],r['case_number'],
             r['case_name'],r['source_url'],__import__('hashlib').sha256(r['full_text'].encode()).hexdigest(),r['full_text']))
    db.commit()
    chunks=[json.loads(l) for l in (args.data/'chunks/cases.jsonl').read_text('utf-8').splitlines() if l.strip()]
    exported={c['chunk_id']:(str(c['metadata']['case_id']),c['metadata']['canonical_case_key'],c['text']) for c in chunks}
    stored={r[0]:(r[1],r[2],r[3]) for r in db.execute('SELECT chunk_id,case_id,canonical_case_key,text FROM case_chunks')}
    missing=db.execute('SELECT count(*) FROM case_chunks c LEFT JOIN case_sources s ON c.case_id=s.case_id WHERE s.case_id IS NULL OR c.canonical_case_key<>s.canonical_case_key').fetchone()[0]
    total=db.execute('SELECT count(*) FROM case_sources').fetchone()[0]
    empty=db.execute("SELECT count(*) FROM case_sources WHERE trim(full_text)='' OR trim(source_url)=''").fetchone()[0]
    db.close()
    assert stored==exported and missing==0 and empty==0
    corpus=json.loads((args.data/'CORPUS_MANIFEST.json').read_text('utf-8'))
    corpus['files']['database/cases.sqlite3']=sha(args.data/'database/cases.sqlite3')
    write(args.data/'CORPUS_MANIFEST.json',corpus)
    write(args.data/'DB_CONNECTION_AUDIT.json',{'pass':True,
        'baseline_8366':{'path':str(args.original.resolve()),'sha256':sha(args.original),'cases':8366},
        'previous_supplement_8377':{'path':str(args.baseline.resolve()),'sha256':sha(args.baseline),'cases':8377},
        'relationship':'8366 preserved original cases + 11 previously reviewed supplements + 18 new cases; 6 existing cases receive full-text supplements',
        'new_case_count':total,'chunk_count':len(chunks),'original_8366_rows_unchanged':True,
        'db_chunk_text_identity_exact':stored==exported,'orphan_or_identity_mismatch_chunks':missing,
        'empty_case_sources':empty,'data_db_sha256':sha(args.data/'database/cases.sqlite3'),
        'note':'Current draft corpus DB completes full source links; final index/application connection must be checked separately.'})
    print(json.dumps({'case_count':total,'chunks':len(chunks),'connection_pass':True}))


if __name__=='__main__':main()
