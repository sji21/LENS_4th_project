"""Development-only concept expansion for BM25+KURE; never selects a gold ID."""
import argparse
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from scripts.patch015_baseline import ROOT, read, write, sha, norm
from scripts.patch025_ranking import index_digest, close
from scripts.patch026_expand import score
from scripts.patch026_full_eval import rank_changes
from scripts.patch027_full_ranking import BUNDLE as PREVIOUS, check_bundle, fuse, adoption_check
from src.retrieval.service import RetrievalService, CIVIL, route_law_corpus
from src.retrieval.terms import expand_law, expand_civil
from src.retrieval.retriever import load_chunks
from src.retrieval.hybrid import HybridRetriever
from src.evaluation.baseline import SEARCH_K, settings

BUNDLE=ROOT/'data/eval/patch027-concept-tuning'
GENERAL=('current','lexical','both','blend','core3_blend')
CIVILS=('current','lexical','both','plain_both','seed1_both')
# A pattern names a search concept, not a legal conclusion or a destination article.
CIVIL_RULES=(
    (r'공동\s*(?:명의|소유)|공유자|지분', '공유물의 관리 보존 공유자 지분 과반수'),
    (r'대리|위임|대신.{0,15}계약', '대리행위 대리권의 범위 본인 권한'),
    (r'등기|소유자|소유권', '부동산 물권변동 등기 물권취득'),
    (r'내용증명|통보|통지|우편(?!번호)|문자', '의사표시 도달 효력발생시기'),
    (r'반송|(?:우편|편지|내용증명|서류).{0,40}돌아왔|소재.{0,8}모르|주소.{0,8}모르|행방', '의사표시 공시송달 상대방 소재'),
    (r'원상\s*(?:복구|회복)|벽지|장판|도배', '원상회복의무 철거권 임대차 준용규정'),
    (r'실수|과실|깨뜨|파손', '채무불이행 손해배상 고의 과실'),
    (r'특약|구두\s*합의|약정', '임의규정 당사자 의사표시'),
)
LAW_RULES=(
    (r'체납|세금', '미납국세 미납지방세 열람 납세증명'),
    (r'전입|주민등록', '주민등록 전입신고 주택 인도 대항력'),
    (r'임대차\s*신고|계약\s*신고', '주택 임대차 계약 신고 확정일자 부여'),
    (r'공공\s*임대|국민\s*임대|행복\s*주택', '공공임대주택 입주자 자격 임대차계약'),
    (r'등록\s*민간\s*임대|임대\s*사업자', '민간임대주택 임대사업자 임대료 임대차계약'),
    (r'중개사|중개\s*보수|중개\s*수수료', '중개대상물 확인 설명 중개보수'),
)


def concepts(query,channel):
    if channel not in ('general','civil'):raise ValueError('Unknown channel')
    rules=LAW_RULES if channel=='general' else CIVIL_RULES
    found=[term for pattern,term in rules if re.search(pattern,query)]
    if channel=='civil' and re.search(r'보증금',query) and re.search(r'이사|퇴거|비우|인도|짐',query):
        found.append('쌍무계약 동시이행의 항변권 채무이행 거절')
    if channel=='general' and '확정일자' in query:
        found.append('확정일자 부여 현황 정보제공' if re.search(r'어디|신청|발급|방법|현황|열람',query)
                     else '확정일자 우선변제 주택 인도 주민등록')
    return list(dict.fromkeys(found))


def expanded_terms(query,channel):
    base=(expand_law if channel=='general' else expand_civil)(query)
    return list(dict.fromkeys(base+concepts(query,channel)))


def dense_query(query,channel):
    extra=concepts(query,channel)
    return query+'\n관련 검색 개념: '+'; '.join(extra) if extra else query


def merge_ranks(*lists):
    scores={}
    for ids in lists:
        for rank,cid in enumerate(ids,1):scores[cid]=scores.get(cid,0)+1/(5+rank)
    return sorted(scores,key=lambda c:(-scores[c],c))


def general_select(row,policy):
    if policy not in GENERAL:raise ValueError('Unknown general policy')
    if policy=='current':return row['current_laws']
    hits=row['general']
    dense=hits['dense_original'] if policy=='lexical' else hits['dense_expanded']
    ranked=fuse(hits['bm25_expanded'],dense)
    if policy in ('blend','core3_blend'):
        ranked=merge_ranks(fuse(hits['bm25_original'],hits['dense_original'])[:20],ranked[:20])
    prefix=row['core'][:3] if policy=='core3_blend' else []
    return list(dict.fromkeys(prefix+ranked))[:5]


def civil_select(row,policy):
    if policy not in CIVILS:raise ValueError('Unknown civil policy')
    if policy=='current':return row['current_civil']
    hits=row['civil'];dense=hits['dense_original'] if policy=='lexical' else hits['dense_expanded']
    ranked=fuse(hits['bm25_expanded'],dense)
    if policy=='plain_both':return ranked[:3]
    seed=row['civil_seed'][:1 if policy=='seed1_both' else 2]
    picked=list(dict.fromkeys(seed+ranked))[:3]
    if len(picked)==3:
        # The production third-slot rule, using this request's new member ranks.
        picked[2]=next(c for c in fuse(hits['bm25_expanded'],dense,2) if c not in picked[:2])
    return picked


def configure_expansion(service):
    for corpus,channel in ((service.corpora[0],'general'),(service.civil,'civil')):
        lexical=service._retrievers[corpus.name].members[0].retriever
        members=lexical.partitions.values() if hasattr(lexical,'partitions') else [lexical]
        for member in members:member.query_expander=lambda q,ch=channel:expanded_terms(q,ch)


def capture(out,candidate):
    from src.retrieval.dense import SentenceTransformerEmbedding,ChromaRetriever
    if out.exists() or not out.resolve().is_relative_to(ROOT/'tmp'):raise ValueError('Use fresh tmp output')
    if subprocess.check_output(['git','status','--porcelain'],text=True).strip():raise ValueError('Commit source first')
    check_bundle()
    old_audit=read(PREVIOUS/'audit.json');old_rows=read(PREVIOUS/'traces.json')
    prior=read(ROOT/'data/eval/patch026-full/capture/audit.json')
    expected=read(ROOT/'data/eval/patch026-full/capture/after.json')
    assert all(sha(candidate/p)==v for p,v in prior['candidate_files'].items())
    chunks=[c for n in ('chunks','cases','guides') for c in load_chunks(candidate/f'chunks/{n}.jsonl')]
    backend=SentenceTransformerEmbedding('nlpai-lab/KURE-v1')
    modelroot=Path.home()/'.cache/huggingface/hub/models--nlpai-lab--KURE-v1'
    assert all(sha(modelroot/p)==v for p,v in prior['model_files'].items())
    original=backend.embed;cache={}
    def cached(texts):
        key=tuple(texts)
        if key not in cache:cache[key]=original(texts)
        return cache[key]
    backend.embed=cached
    dense=ChromaRetriever(backend,candidate/'index/chroma_kurev1_1024')
    civil_dense=ChromaRetriever(backend,candidate/'index/chroma_civil_kurev1_1024')
    digests=[index_digest(r) for r in (dense,civil_dense)]
    assert digests==prior['candidate_index_hashes']
    civil_ids=tuple(prior['candidate_settings']['corpora']['civil']['include_ids'])
    svc=RetrievalService(chunks,dense,civil=replace(CIVIL,include_ids=civil_ids),civil_dense=civil_dense)
    assert json.loads(json.dumps(settings(svc)))==prior['candidate_settings']
    # Separate instances: never mutate the baseline's retrievers or topic routing.
    tuned=RetrievalService(chunks,dense,civil=replace(CIVIL,include_ids=civil_ids),civil_dense=civil_dense)
    configure_expansion(tuned)
    rows=[];anchors=old_audit['anchors']
    queries=read(ROOT/'data/eval/patch015-baseline/capture/results.json')
    for i,q in enumerate(queries):
        query=q['query'];old=old_rows[i]
        result=svc.search(query,**SEARCH_K)
        actual={'qid':q['qid'],'mode':q['mode'],'laws':[anchors[e.chunk_id] for e in result.laws],
                'civil_laws':[anchors[e.chunk_id] for e in result.civil_laws],
                'cases':[e.chunk_id for e in result.cases],'guides':[e.chunk_id for e in result.guides]}
        assert actual==expected[i],('Baseline drift',q['qid'])
        row={k:old[k] for k in ('qid','mode','query_sha256','current_laws','current_civil','civil_seed')}
        row['core']=[c for c,s in old['core']]
        row['expansion']={ch:concepts(query,ch) for ch in ('general','civil')}
        for channel,corpus,where,depth in (
            ('general',svc.corpora[0],route_law_corpus(query).where(),20),
            ('civil',svc.civil,svc.civil.where(),26)):
            base=svc._retrievers[corpus.name];new=tuned._retrievers[corpus.name]
            ask=lambda member,text:[c for c,s in HybridRetriever._ask(member,text,depth,where)]
            row[channel]={'bm25_original':ask(base.members[0],query),
                          'bm25_expanded':ask(new.members[0],query),
                          'dense_original':ask(base.members[1],query),
                          'dense_expanded':ask(base.members[1],dense_query(query,channel))}
        assert fuse(row['general']['bm25_original'],row['general']['dense_original'])[:5]==row['current_laws']
        assert row['civil']['bm25_original']==old['civil_bm25'] and row['civil']['dense_original']==old['civil_dense']
        rows.append(row)
        if len(rows)%25==0:print(f'{len(rows)}/235 concept-tuning traces',flush=True)
    assert digests==[index_digest(r) for r in (dense,civil_dense)]
    assert all(sha(candidate/p)==v for p,v in prior['candidate_files'].items())
    assert not subprocess.check_output(['git','status','--porcelain'],text=True).strip()
    out.mkdir(parents=True)
    write(out/'traces.json',rows)
    write(out/'audit.json',{'patch':'PATCH-027','commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'clean':True,'baseline_matches':235,'candidate_unchanged':True,'cases_guides_preserved':True,
        'index_hashes':digests,'settings':settings(svc),'general_policies':GENERAL,'civil_policies':CIVILS,
        'joint_policy':['core3_blend','seed1_both'],'traces_sha256':sha(out/'traces.json'),
        'previous_manifest_sha256':sha(PREVIOUS/'manifest.json'),'script_sha256':sha(Path(__file__))})
    print('235 baseline outputs reproduced; candidate data unchanged',flush=True)


def report(run):
    audit=read(run/'audit.json');rows=read(run/'traces.json')
    if not re.fullmatch(r'[0-9a-f]{40}',audit['commit']):raise ValueError('Invalid capture commit')
    source=subprocess.check_output(['git','show',audit['commit']+':scripts/patch027_tuning.py'],cwd=ROOT)
    if audit['script_sha256'] not in {hashlib.sha256(source).hexdigest(),hashlib.sha256(source.replace(b'\n',b'\r\n')).hexdigest()}:
        raise ValueError('Capture source mismatch')
    if sha(run/'traces.json')!=audit['traces_sha256'] or sha(PREVIOUS/'manifest.json')!=audit['previous_manifest_sha256']:raise ValueError('Capture/dependency changed')
    if (audit['general_policies']!=list(GENERAL) or audit['civil_policies']!=list(CIVILS) or
        audit['joint_policy']!=['core3_blend','seed1_both']):raise ValueError('Policies changed')
    old_audit=read(PREVIOUS/'audit.json');old_rows=read(PREVIOUS/'traces.json')
    if not (audit['clean'] and audit['baseline_matches']==235 and audit['candidate_unchanged'] and audit['cases_guides_preserved']):raise ValueError('Capture contract changed')
    if audit['settings']!=old_audit['settings'] or audit['index_hashes']!=old_audit['index_hashes']:raise ValueError('Settings/index drift')
    if audit['patch']!='PATCH-027':raise ValueError('Wrong patch identity')
    queries=read(ROOT/'data/eval/patch015-baseline/capture/results.json')
    keys=lambda rs:[(r['qid'],r['mode']) for r in rs]
    if len(rows)!=235 or keys(rows)!=keys(queries) or len(set(keys(rows)))!=235:raise ValueError('Input identity changed')
    allowed={'general':set(old_audit['core_ids']+old_audit['extra_ids']),'civil':set(old_audit['civil_ids'])}
    for row,old,q in zip(rows,old_rows,queries):
        if row['query_sha256']!=q['query_sha256'] or any(row[k]!=old[k] for k in ('current_laws','current_civil','civil_seed')):raise ValueError('Baseline/input changed')
        if row['core']!=[c for c,s in old['core']]:raise ValueError('Core baseline changed')
        for channel in ('general','civil'):
            if row['expansion'][channel]!=concepts(q['query'],channel):raise ValueError('Concept rules changed')
            if set(row[channel])!={'bm25_original','bm25_expanded','dense_original','dense_expanded'}:raise ValueError('Missing member')
            for hits in row[channel].values():
                if len(hits)>(20 if channel=='general' else 26) or len(set(hits))!=len(hits) or not set(hits)<=allowed[channel]:raise ValueError('Invalid member trace')
        if fuse(row['general']['bm25_original'],row['general']['dense_original'])[:5]!=row['current_laws']:raise ValueError('General baseline drift')
        if row['civil']['bm25_original']!=old['civil_bm25'] or row['civil']['dense_original']!=old['civil_dense']:raise ValueError('Civil baseline drift')
        if civil_select({**row,'civil':{**row['civil'],'bm25_expanded':row['civil']['bm25_original']}},'lexical')!=row['current_civil']:
            raise ValueError('Civil selection control drift')
    prior=read(ROOT/'data/eval/patch026-full/capture/before.json');current=read(ROOT/'data/eval/patch026-full/capture/after.json')
    anchors=old_audit['anchors'];available=set(anchors.values())
    def assess(g,c):
        actual=[{**current[i],'laws':[anchors[x] for x in general_select(row,g)],
                 'civil_laws':[anchors[x] for x in civil_select(row,c)]} for i,row in enumerate(rows)]
        result=score(actual,available,prior);changes=rank_changes(prior,actual,result['details'])
        out={'groups':result['groups'],'losses':result['losses'],'rank_changes':changes,
             'top3_general_loss_inputs':sum(bool(r['lost']) for r in changes['laws']['3']),
             'new_loss_vs_full':score(actual,available,current)['losses']}
        out['adoption']=adoption_check(out)
        new_civil=available-set(read(ROOT/'data/eval/patch026-full/capture/audit.json')['available_before'])
        out['new_required_civil_hits']=[{'qid':d['qid'],'mode':d['mode'],'anchors':sorted(set(a['civil_laws'])&set(d['targets'])&new_civil)}
            for a,d in zip(actual,result['details']) if set(a['civil_laws'])&set(d['targets'])&new_civil]
        return out
    return {'general':{p:assess(p,'current') for p in GENERAL},'civil':{p:assess('current',p) for p in CIVILS},
            'joint':{'core3_blend+seed1_both':assess('core3_blend','seed1_both')}}


def check(run=BUNDLE):
    manifest=read(run/'manifest.json')
    if set(manifest)!={'audit.json','traces.json','comparison.json'}:raise ValueError('Incomplete bundle')
    if any(sha(run/p)!=v for p,v in manifest.items()):raise ValueError('Bundle changed')
    check_bundle()
    result=report(run)
    if not close(result,read(run/'comparison.json')):raise ValueError('Comparison does not replay')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture',type=Path);parser.add_argument('--candidate',type=Path)
    parser.add_argument('--report',type=Path);args=parser.parse_args()
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',LANGSMITH_TRACING='false',ANONYMIZED_TELEMETRY='False')
    if args.capture:capture(args.capture,args.candidate)
    elif args.report:
        result=report(args.report);write(args.report/'comparison.json',result)
        for ch,policies in result.items():
            for p,r in policies.items():print(ch,p,'complete',*[r['groups'][m]['union_all_required']['hits'] for m in ('question_only','context_diagnostic')],
                'loss',len(r['losses']),'law3loss',r['top3_general_loss_inputs'],'newCivilInputs',len(r['new_required_civil_hits']))
    else:check();print('Frozen concept tuning: PASS')
