"""Compare fixed general-law partitions and civil selection on the complete corpus."""
import argparse
from dataclasses import replace
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess

from scripts.patch015_baseline import ROOT, read, sha, norm, write
from scripts.patch025_ranking import index_digest
from scripts.patch026_expand import score
from scripts.patch026_full_eval import rank_changes
from src.retrieval.service import RetrievalService, LAW, CIVIL, route_law_corpus, detect_civil_topics
from src.retrieval.hybrid import HybridRetriever
from src.retrieval.retriever import load_chunks
from src.evaluation.baseline import SEARCH_K, settings

CORE_TITLES=('주택임대차보호법','주택임대차보호법 시행령','상가건물 임대차보호법','상가건물 임대차보호법 시행령')
GENERAL=('full_current','stats_pool','split_rrf','guard1_pool','guard2_pool','guard1_split','guard2_split')
CIVIL_POLICIES=('current','plain_rrf','dense2_rrf','seed_plain','seed_dense2')
DEPENDENCIES=('data/eval/patch026-full/manifest.json','data/eval/patch026-full/capture/after.json',
              'data/eval/patch026-full/capture/before.json','data/eval/patch026-full/capture/audit.json',
              'data/eval/patch024-expansion/report.json','data/eval/patch015-baseline/capture/results.json')


def fuse(bm25,dense,dense_weight=1):
    scores={}
    for ids,weight in ((bm25,1),(dense,dense_weight)):
        for rank,cid in enumerate(ids,1):scores[cid]=scores.get(cid,0)+weight/(5+rank)
    return sorted(scores,key=lambda c:(-scores[c],c))


def general_select(row,policy):
    if policy=='full_current':return row['current_laws']
    if policy not in GENERAL:raise ValueError('Unknown general policy')
    if policy=='stats_pool' or policy.endswith('_pool'):
        pooled=sorted(row['core_bm25']+row['extra_bm25'],key=lambda h:(-h[1],h[0]))
        ranked=fuse([c for c,_ in pooled[:20]],[c for c,_ in row['global_dense']])
    else:
        ranked=[c for c,_ in sorted(row['core']+row['extra'],key=lambda h:(-h[1],h[0]))]
    keep=1 if policy.startswith('guard1') else 2 if policy.startswith('guard2') else 0
    return list(dict.fromkeys([c for c,_ in row['core'][:keep]]+ranked))[:5]


def civil_select(row,policy):
    if policy=='current':return row['current_civil']
    if policy not in CIVIL_POLICIES:raise ValueError('Unknown civil policy')
    ranked=fuse(row['civil_bm25'],row['civil_dense'],2 if 'dense2' in policy else 1)
    return list(dict.fromkeys((row['civil_seed'] if policy.startswith('seed_') else [])+ranked))[:3]


def capture(out,candidate_path):
    from scripts.patch026_full_report import analyze
    from src.retrieval.dense import SentenceTransformerEmbedding,ChromaRetriever
    from src.retrieval.index import clean_metadata
    if out.exists() or not out.resolve().is_relative_to(ROOT/'tmp'):raise ValueError('Use a fresh tmp output')
    if subprocess.check_output(['git','status','--porcelain'],text=True).strip():raise ValueError('Commit source first')
    analyze()
    previous=read(ROOT/'data/eval/patch026-full/capture/after.json')
    baseline=read(ROOT/'data/eval/patch026-full/capture/before.json')
    prior=read(ROOT/'data/eval/patch026-full/capture/audit.json')
    for rel,digest in prior['candidate_files'].items():
        if sha(candidate_path/rel)!=digest:raise ValueError('Candidate data changed')
    chunks=[c for name in ('chunks','cases','guides') for c in load_chunks(candidate_path/f'chunks/{name}.jsonl')]
    backend=SentenceTransformerEmbedding('nlpai-lab/KURE-v1')
    modelroot=Path.home()/'.cache/huggingface/hub/models--nlpai-lab--KURE-v1'
    assert all(sha(modelroot/p)==v for p,v in prior['model_files'].items())
    original=backend.embed;cache={}
    def cached(texts):
        key=tuple(texts)
        if key not in cache:cache[key]=original(texts)
        return cache[key]
    backend.embed=cached
    dense=ChromaRetriever(backend,candidate_path/'index/chroma_kurev1_1024')
    civil_dense=ChromaRetriever(backend,candidate_path/'index/chroma_civil_kurev1_1024')
    digests=[index_digest(r) for r in (dense,civil_dense)]
    assert digests==prior['candidate_index_hashes']
    civil_ids=tuple(sorted(c['metadata']['article_id'] for c in chunks if c['metadata']['title']=='민법'))
    service=RetrievalService(chunks,dense,civil=replace(CIVIL,include_ids=civil_ids),civil_dense=civil_dense)
    assert json.loads(json.dumps(settings(service)))==prior['candidate_settings']
    for retriever in (dense,civil_dense):
        data=retriever.collection.get(include=['documents','metadatas'])
        for cid,body,meta in zip(data['ids'],data['documents'],data['metadatas']):
            assert body==service._chunks[cid]['text'] and meta==clean_metadata(service._chunks[cid]['metadata'])
    laws=[c for c in chunks if c['metadata']['doc_type'] in ('law','decree','rule') and c['metadata']['title']!='민법']
    core_chunks=[c for c in laws if c['metadata']['title'] in CORE_TITLES]
    extra_chunks=[c for c in laws if c['metadata']['title'] not in CORE_TITLES]
    assert len(core_chunks)==133 and len(extra_chunks)==45 and len(civil_ids)==26
    core=service._build(replace(LAW,name='core-full'),core_chunks)
    extra=service._build(replace(LAW,name='extra-full'),extra_chunks)
    core_ids=[c['metadata']['article_id'] for c in core_chunks]
    extra_ids=[c['metadata']['article_id'] for c in extra_chunks]
    anchors={c['chunk_id']:norm(c['metadata']['article_id']) for c in chunks if c['metadata']['doc_type'] in ('law','decree','rule')}
    rows=[]
    for i,q in enumerate(read(ROOT/'data/eval/patch015-baseline/capture/results.json')):
        assert hashlib.sha256(q['query'].encode()).hexdigest()==q['query_sha256']
        result=service.search(q['query'],**SEARCH_K)
        actual={'qid':q['qid'],'mode':q['mode'],'laws':[anchors[e.chunk_id] for e in result.laws],
                'civil_laws':[anchors[e.chunk_id] for e in result.civil_laws],
                'cases':[e.chunk_id for e in result.cases],'guides':[e.chunk_id for e in result.guides]}
        assert actual==previous[i],('Full corpus drift',q['qid'])
        where=route_law_corpus(q['query']).where()
        cw={'$and':[where,{'article_id':{'$in':core_ids}}]}
        ew={'$and':[where,{'article_id':{'$in':extra_ids}}]}
        row={'qid':q['qid'],'mode':q['mode'],'query_sha256':q['query_sha256'],
             'current_laws':[e.chunk_id for e in result.laws],'current_civil':[e.chunk_id for e in result.civil_laws],
             'core':core.search(q['query'],20,cw),'extra':extra.search(q['query'],20,ew),
             'core_bm25':HybridRetriever._ask(core.members[0],q['query'],20,cw),
             'extra_bm25':HybridRetriever._ask(extra.members[0],q['query'],20,ew),
             'global_dense':HybridRetriever._ask(core.members[1],q['query'],20,where)}
        assert [anchors[c] for c,_ in row['core'][:5]]==baseline[i]['laws'],('Core baseline drift',q['qid'])
        _,members=service._search_one_with_member_hits(service.civil,q['query'],len(civil_ids))
        row['civil_bm25']=members['민법-bm25'];row['civil_dense']=members['민법-dense']
        row['civil_seed']=[e.chunk_id for e in service._search_civil(q['query'],detect_civil_topics(q['query']),2)]
        rows.append(row)
        if len(rows)%25==0:print(f'{len(rows)}/235 full-corpus policy traces',flush=True)
    assert digests==[index_digest(r) for r in (dense,civil_dense)]
    assert all(sha(candidate_path/p)==v for p,v in prior['candidate_files'].items())
    assert not subprocess.check_output(['git','status','--porcelain'],text=True).strip()
    out.mkdir(parents=True)
    write(out/'traces.json',rows)
    write(out/'audit.json',{'patch':'PATCH-027','commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'clean':True,'anchors':anchors,'core_ids':[c['chunk_id'] for c in core_chunks],
        'extra_ids':[c['chunk_id'] for c in extra_chunks],'civil_ids':[c['chunk_id'] for c in chunks if c['metadata']['title']=='민법'],
        'general_policies':GENERAL,'civil_policies':CIVIL_POLICIES,'settings':settings(service),
        'source_hashes':{p:sha(ROOT/p) for p in DEPENDENCIES},'traces_sha256':sha(out/'traces.json'),
        'index_hashes':digests,'candidate_unchanged':True,'full_and_core_matches':235,'cases_guides_preserved':True})
    print('Complete: 235 current and core baselines reproduced',flush=True)


def report(run):
    audit=read(run/'audit.json');rows=read(run/'traces.json')
    if sha(run/'traces.json')!=audit['traces_sha256']:raise ValueError('Trace changed')
    if tuple(audit['general_policies'])!=GENERAL or tuple(audit['civil_policies'])!=CIVIL_POLICIES:raise ValueError('Policies changed')
    if set(audit['source_hashes'])!=set(DEPENDENCIES):raise ValueError('Incomplete dependencies')
    for p,digest in audit['source_hashes'].items():
        if sha(ROOT/p)!=digest:raise ValueError('Dependency changed')
    queries=read(ROOT/'data/eval/patch015-baseline/capture/results.json')
    keys=lambda rs:[(r['qid'],r['mode']) for r in rs]
    if len(rows)!=235 or keys(rows)!=keys(queries) or len(set(keys(rows)))!=235:raise ValueError('Input identity mismatch')
    if [r['query_sha256'] for r in rows]!=[q['query_sha256'] for q in queries]:raise ValueError('Input text changed')
    prior=read(ROOT/'data/eval/patch026-full/capture/before.json')
    current=read(ROOT/'data/eval/patch026-full/capture/after.json')
    available=read(ROOT/'data/eval/patch026-full/capture/audit.json')['available_after']
    anchors=audit['anchors'];core=set(audit['core_ids']);extra=set(audit['extra_ids']);civil=set(audit['civil_ids'])
    if (len(core),len(extra),len(civil))!=(133,45,26) or core&extra or core&civil or extra&civil or core|extra|civil!=set(anchors):raise ValueError('Invalid inventory')
    if len(set(anchors.values()))!=204 or set(anchors.values())!=set(available):raise ValueError('Anchor coverage mismatch')
    for i,row in enumerate(rows):
        if [anchors[c] for c in row['current_laws']]!=current[i]['laws'] or [anchors[c] for c in row['current_civil']]!=current[i]['civil_laws']:raise ValueError('Current baseline changed')
        if [anchors[c] for c,_ in row['core'][:5]]!=prior[i]['laws']:raise ValueError('Core baseline changed')
        for name,allowed in [('core',core),('extra',extra),('core_bm25',core),('extra_bm25',extra),('global_dense',core|extra)]:
            hits=row[name]
            if len(hits)>20 or len({c for c,_ in hits})!=len(hits) or any(c not in allowed or not math.isfinite(s) for c,s in hits):raise ValueError('Invalid general trace')
        for name,limit in (('civil_bm25',26),('civil_dense',26),('civil_seed',2),('current_civil',3)):
            if len(row[name])>limit or not set(row[name])<=civil or len(set(row[name]))!=len(row[name]):raise ValueError('Invalid civil trace')
    def assess(general,civil_policy):
        actual=[{**current[i],'laws':[anchors[c] for c in general_select(row,general)],
                 'civil_laws':[anchors[c] for c in civil_select(row,civil_policy)]} for i,row in enumerate(rows)]
        result=score(actual,available,prior);changes=rank_changes(prior,actual,result['details'])
        return {'groups':result['groups'],'losses':result['losses'],'rank_changes':changes,
                'top3_general_loss_inputs':sum(bool(r['lost']) for r in changes['laws']['3']),
                'new_loss_vs_full':score(actual,available,current)['losses']}
    return {'general':{p:assess(p,'current') for p in GENERAL},
            'civil':{p:assess('full_current',p) for p in CIVIL_POLICIES}}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture',type=Path);parser.add_argument('--candidate',type=Path)
    parser.add_argument('--report',type=Path);args=parser.parse_args()
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',LANGSMITH_TRACING='false',ANONYMIZED_TELEMETRY='False')
    if args.capture:capture(args.capture,args.candidate)
    if args.report:
        result=report(args.report);write(args.report/'comparison.json',result)
        for channel,policies in result.items():
            for name,r in policies.items():print(channel,name,'loss',len(r['losses']),'top3lawloss',r['top3_general_loss_inputs'],
                {m:r['groups'][m]['union_all_required']['hits'] for m in ('question_only','context_diagnostic')})
