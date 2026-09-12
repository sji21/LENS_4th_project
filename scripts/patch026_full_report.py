"""Replay the full-corpus capture and locate missing targets without an LLM/model."""
import json
from collections import Counter
from pathlib import Path

from scripts.patch015_baseline import ROOT, read, sha, norm
from scripts.patch025_ranking import close
from scripts.patch026_expand import score
from scripts.patch026_full_eval import rank_changes
from scripts.patch026_full_sources import OUT, parse_verified

REQUIRED={'specs.json','records.jsonl',*(f'capture/{n}.json' for n in
          ('before','after','report','traces','audit','new-chunks','new-units'))}


def analyze(bundle=OUT):
    manifest=read(bundle/'manifest.json')
    if not REQUIRED <= manifest.keys():
        raise ValueError('Incomplete full-corpus bundle')
    for rel,digest in manifest.items():
        path=(bundle/rel).resolve()
        if not path.is_relative_to(bundle.resolve()) or sha(path)!=digest:
            raise ValueError('Bundle path/hash mismatch')
    specs=read(bundle/'specs.json')
    records=[]
    for spec in specs:
        rel=Path(spec['path']).relative_to(OUT.relative_to(ROOT)).as_posix()
        if rel not in manifest or sha(bundle/rel)!=spec['sha256']:
            raise ValueError('Missing official source')
        records.append(parse_verified((bundle/rel).read_text(encoding='utf-8'),spec))
    from dataclasses import asdict
    persisted=[json.loads(line) for line in (bundle/'records.jsonl').read_text(encoding='utf-8').splitlines()]
    if [asdict(r) for r in records]!=persisted:
        raise ValueError('Records differ from verified official source')
    plan=read(ROOT/'data/eval/patch026-scope/plan.json')
    anchors={norm(r.law_name+'-'+r.article_number) for r in records}
    if len(records)!=56 or len(anchors)!=56 or not {r['article_anchor'] for r in plan['missing_articles']} <= anchors:
        raise ValueError('Missing reviewed article scope')
    capture=bundle/'capture'
    before,after,traces,audit=[read(capture/f'{n}.json') for n in ('before','after','traces','audit')]
    queryrows=read(ROOT/'data/eval/patch015-baseline/capture/results.json')
    key=lambda r:(r['qid'],r['mode'])
    keys=[key(q) for q in queryrows]
    for rows in (before,after,traces):
        if len(rows)!=235 or [key(r) for r in rows]!=keys or len(set(map(key,rows)))!=235:
            raise ValueError('Input identity mismatch')
    if [r['query_sha256'] for r in traces]!=[r['query_sha256'] for r in queryrows]:
        raise ValueError('Query content mismatch')
    if before!=read(ROOT/'data/eval/patch026-expansion/before.json'):
        raise ValueError('Baseline drift')
    for rel,digest in audit['source_hashes'].items():
        if rel.startswith('data/eval/') and not rel.startswith('data/eval/patch026-full/'):
            if sha(ROOT/rel)!=digest:
                raise ValueError('Frozen evaluation dependency changed')
    if not (audit['clean'] and audit['baseline_matches']==235 and audit['candidate_only'] and
            audit['old_articles_and_vectors_preserved'] and audit['cases_guides_preserved']):
        raise ValueError('Capture audit contract mismatch')
    if any(a[ch]!=b[ch] for a,b in zip(before,after) for ch in ('cases','guides')):
        raise ValueError('Case/guide output changed')
    available=set(audit['available_after']); prior_available=set(audit['available_before'])
    chunks=read(capture/'new-chunks.json')
    newanchors={norm(c['metadata']['article_id']) for c in chunks}
    if len(chunks)!=61 or len(available)!=204 or len(prior_available)!=143 or available-prior_available!=newanchors:
        raise ValueError('Ingestion coverage mismatch')
    if not anchors<=newanchors or sum(c['metadata']['title']=='민법' for c in chunks)!=16:
        raise ValueError('Civil/new article coverage mismatch')
    from src.ingestion.load_laws import read_records,chunk_body,chunk_id_of,article_row_id_of
    allrecords=read_records(ROOT/'data/eval/patch026-expansion/records.jsonl')+records
    by_chunk={chunk_id_of(r,0):r for r in allrecords}
    if set(by_chunk)!={c['chunk_id'] for c in chunks}:
        raise ValueError('Chunk identifiers differ from source records')
    for chunk in chunks:
        record=by_chunk[chunk['chunk_id']];meta=chunk['metadata']
        if chunk['text']!=chunk_body(record) or any(meta[k]!=v for k,v in {
            'article_id':record.law_name+'-'+record.article_number,'title':record.law_name,
            'source_url':record.source_url,'effective_date':record.effective_from,
            'version':record.proclamation_number,'doc_type':record.document_type}.items()):
            raise ValueError('Chunk body/metadata differs from source record')
    units=read(capture/'new-units.json');by_article={article_row_id_of(r):r for r in allrecords}
    roots={}
    for unit in units:
        record=by_article.get(unit['article_id'])
        if record is None or record.content[unit['start_offset']:unit['end_offset']]!=unit['content']:
            raise ValueError('Structure offset/content mismatch')
        if unit['unit_key']=='root':
            if unit['article_id'] in roots or unit['content']!=record.content:
                raise ValueError('Invalid structure root')
            roots[unit['article_id']]=True
    if set(roots)!=set(by_article):
        raise ValueError('Missing article structure')
    for row in after:
        if (len(row['laws'])>5 or len(row['civil_laws'])>3 or
            len(set(row['laws']))!=len(row['laws']) or len(set(row['civil_laws']))!=len(row['civil_laws']) or
            not set(row['laws']+row['civil_laws'])<=available or
            any(x.startswith('민법-') for x in row['laws']) or
            any(not x.startswith('민법-') for x in row['civil_laws'])):
            raise ValueError('Returned channel/coverage contract mismatch')
    expected_civil=sorted({a for a in prior_available if a.startswith('민법-')}|
                          {c['metadata']['article_id'] for c in chunks if c['metadata']['title']=='민법'})
    if sorted(audit['candidate_settings']['corpora']['civil']['include_ids'])!=expected_civil:
        raise ValueError('Civil articles missing from search allowlist')
    result=score(after,available,before)
    result['baseline']=score(before,prior_available,before)['groups']
    result['rank_changes']=rank_changes(before,after,result['details'])
    if not close(result,read(capture/'report.json')):
        raise ValueError('Stored metrics differ from replay')
    traces_by_key={key(t):t for t in traces}
    failure_rows=[]
    for detail in result['details']:
        if detail['category']!='retrieval_miss':continue
        trace=traces_by_key[key(detail)]
        for target in detail['held_not_returned']:
            channel='civil' if target.startswith('민법-') else 'general'
            ranks=trace[channel]
            member_ranks={name:(ids.index(target)+1 if target in ids else None) for name,ids in ranks['members'].items()}
            rrf_rank=ranks['rrf'].index(target)+1 if target in ranks['rrf'] else None
            # Civil diagnostics query every allowed article: presence alone is not recall quality.
            stage=('final_selection' if channel=='civil' and rrf_rank is not None and rrf_rank<=3 else
                   'ranking' if any(v is not None for v in member_ranks.values()) else 'outside_member_candidates')
            failure_rows.append({'qid':detail['qid'],'mode':detail['mode'],'track':detail['track'],
                'target':target,'channel':channel,'new_article':target in newanchors,
                'stage':stage,'member_ranks':member_ranks,'rrf_rank_if_captured':rrf_rank})
    stages={}
    for mode in ('question_only','context_diagnostic'):
        stages[mode]={ch:dict(Counter(x['stage'] for x in failure_rows if x['track']=='dev100' and x['mode']==mode and x['channel']==ch)) for ch in ('general','civil')}
    olddiag={key(d):d for d in score(before,prior_available,before)['details']}
    success_changes={}
    for mode in ('question_only','context_diagnostic'):
        selected=[d for d in result['details'] if d['track']=='dev100' and d['mode']==mode]
        success_changes[mode]={
            'gained':[d['qid'] for d in selected if d['all_required_law5_civil3'] is True and olddiag[key(d)]['all_required_law5_civil3'] is False],
            'lost':[d['qid'] for d in selected if d['all_required_law5_civil3'] is False and olddiag[key(d)]['all_required_law5_civil3'] is True]}
    return {'unit':'fixed required articles; not LLM answer correctness or legal applicability',
        'capture_commit':audit['commit'],'baseline':result['baseline'],'groups':result['groups'],
        'data_counts':{'before':143,'candidate':204,'added':61,'civil_before':10,'civil_candidate':26},
        'rank_changes':result['rank_changes'],'lost_prior_required':result['losses'],
        'success_changes':success_changes,'failure_stage_counts_target_instances':stages,'failures':failure_rows,
        'cases_guides_preserved':True,'operating_data_adopted':False,
        'adoption_gate_passed':not result['losses'] and not any(x['lost'] for x in result['rank_changes']['laws']['3']),
        'limitations':['civil member search depth is 26, so candidate presence alone does not establish useful recall',
                      'general RRF capture contains TOP5 only; absent RRF rank is not a precise rank beyond 5',
                      'fixed-gold absence does not prove retrieved supplementary evidence is irrelevant',
                      'current reviewed article corpus only; historical versions/appendices/cases/guides not completed',
                      'public development set; no independent holdout or LLM answer assessment']}


if __name__=='__main__':
    result=analyze()
    print(json.dumps({k:v for k,v in result.items() if k in ('data_counts','success_changes','failure_stage_counts_target_instances','adoption_gate_passed')},ensure_ascii=False,indent=2))
