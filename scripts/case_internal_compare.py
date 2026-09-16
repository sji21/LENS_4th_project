"""Score sealed DEV candidate ablations without changing questions or Gold."""
import argparse,json,itertools,statistics,sys
from pathlib import Path
from dataclasses import asdict,replace
from case_internal_run import sha,write
from src.retrieval.case_internal import CaseInternalRetriever,CaseInternalPolicy,continuous_case_bodies
from src.evaluation.case_internal import score


def support(group,ids):
    ids=set(ids)
    if ids.intersection(group.get('supporting_chunk_ids',[])):return True
    if 'evidence_requirements' in group:
        required=group['evidence_requirements']
        return bool(required) and all(ids.intersection(r['chunk_ids']) for r in required)
    return any(a['requirements'] and all(ids.intersection(r['chunk_ids']) for r in a['requirements'])
               for a in group.get('alternative_evidence_requirements',[]))


def score_selection(items,records,chunks):
    rows,summary=score(items,records)
    byid={r['qid']:r for r in records};supported=0;known=0;groups=0
    contexts=continuous_case_bodies(list(chunks.values()))
    for item in items:
        record=byid[item['qid']];returned=record['cases'];ids=[]
        sources=record.get('trace',{}).get('body_sources',[])
        for index,e in enumerate(returned):
            source=sources[index] if index<len(sources) else {'policy':'selected_chunk'}
            if source['policy']=='continuous_official_case':
                context=contexts[e['canonical_case_key']]
                if e['text']!=context['text'] or source['source_chunk_ids']!=context['source_chunk_ids']:
                    raise ValueError('returned text differs from continuous official case source')
                ids.extend(context['source_chunk_ids'])
            else:
                if e['text']!=chunks[e['chunk_id']]['text']:raise ValueError('returned text differs from source chunk')
                ids.append(e['chunk_id'])
        targets=set().union(*(set(g['available_keys']) for g in item['groups']))
        known+=sum(e['canonical_case_key'] in targets for e in returned)
        for g in item['groups']:
            if g['available_keys']:
                groups+=1;supported+=support(g,ids)
    summary.update(full_returned_body_group_support=supported/groups if groups else None,
        known_gold_precision=known/max(1,summary['returned']),
        relevance_note='Known Gold case membership is a DEV proxy, not official relevance review of every returned case.')
    scopes=sorted({str(item.get('source_scope','not_specified')) for item in items})
    summary['source_scope_breakdown']={}
    for scope in scopes:
        scoped=[item for item in items if str(item.get('source_scope','not_specified'))==scope]
        _,metrics=score(scoped,[byid[item['qid']] for item in scoped])
        summary['source_scope_breakdown'][scope]=metrics
    return rows,summary


def selection_record(qid,query,hits,backend,scores=None,latency=None):
    selected=backend.select(query,hits,2,scores)
    return {'qid':qid,'query':query,'error':None,'latency_seconds':latency,
        'cases':[{'chunk_id':cid,'canonical_case_key':backend.chunks[cid]['metadata']['canonical_case_key'],
            'text':backend.chunks[cid]['text'],'source_url':backend.chunks[cid]['metadata']['source_url'],
            'score':value} for cid,value in selected]}


def objective(s,policy):
    return (s['macro_group_recall_at_2'] or 0,s['hit_at_2'] or 0,
            s['full_returned_body_group_support'] or 0,s['known_gold_precision'],
            -policy.candidate_depth,-policy.fusion_depth,-(s.get('median_seconds') or 0))


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--capture',type=Path,required=True)
    ap.add_argument('--membership',type=Path,required=True);ap.add_argument('--data',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True);args=ap.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    seal=json.loads((args.capture/'RESULT_SEAL.json').read_text('utf-8'))
    assert all(sha(args.capture/n)==v for n,v in seal.items())
    items=json.loads(args.membership.read_text('utf-8'))['items']
    chunks={c['chunk_id']:c for c in [json.loads(l) for l in (args.data/'chunks/cases.jsonl').read_text('utf-8').splitlines() if l]}
    backend=CaseInternalRetriever.__new__(CaseInternalRetriever);backend.chunks=chunks
    captures=[json.loads(l) for l in (args.capture/'results.jsonl').read_text('utf-8').splitlines()]
    grouped={}
    for r in captures:
        key=json.dumps(r['policy'],sort_keys=True);grouped.setdefault(key,[]).append(r)
    table=[];best=None;best_records=None;best_inputs=None
    for key,records in grouped.items():
        base=CaseInternalPolicy(**json.loads(key))
        for weights,rrf,fusion,rerank in itertools.product(((2,1),(1,1),(1,2)),(60,20),
                sorted({base.candidate_depth,min(80,base.candidate_depth)}),('band_3pct','pure_rrf')):
            policy=replace(base,bm25_weight=weights[0],dense_weight=weights[1],rrf_k=rrf,fusion_depth=fusion,rerank=rerank)
            backend.policy=policy;results=[];inputs=[];stage=[]
            for r in records:
                if r['error']:raise ValueError('candidate execution error')
                fused={};members=r['trace']['member_hits']
                for name,weight in zip(('bm25','dense'),weights):
                    for rank,cid in enumerate(members[name],1):fused[cid]=fused.get(cid,0)+weight/(rrf+rank)
                hits=sorted(fused.items(),key=lambda h:(-h[1],h[0]))[:fusion]
                results.append(selection_record(r['qid'],r['query'],hits,backend,latency=r['latency_seconds']))
                inputs.append({'qid':r['qid'],'query':r['query'],'hits':hits,'member_hits':members,
                               'candidate_seconds':r['trace']['candidate_seconds']})
                gold=next(g for g in items if g['qid']==r['qid'])
                sets={n:{chunks[cid]['metadata']['canonical_case_key'] for cid in ids} for n,ids in members.items()}
                sets['fusion']={chunks[cid]['metadata']['canonical_case_key'] for cid,_ in hits}
                stage.append({'qid':r['qid'],'groups':[{'group_id':g['group_id'],**{n:bool(keys.intersection(g['available_keys'])) for n,keys in sets.items()}} for g in gold['groups']]})
            rows,summary=score_selection(items,results,chunks)
            counts={n:sum(g[n] for row in stage for g in row['groups']) for n in ('bm25','dense','fusion')}
            entry={'policy':asdict(policy),'metrics':summary,'candidate_supported_groups':counts,
                   'stage_losses':stage,'comparison_kind':'cached member-rank DEV comparison; final selection is explicitly k=2'}
            table.append(entry)
            if best is None or objective(summary,policy)>objective(best['metrics'],CaseInternalPolicy(**best['policy'])):
                best=entry;best_records=results;best_inputs=inputs
    write(args.output/'comparison.json',table);write(args.output/'selected_rule.json',best)
    write(args.output/'selected_rule_results.json',best_records)
    write(args.output/'selected_candidate_inputs.json',best_inputs)
    write(args.output/'SEAL.json',{'membership_sha256':sha(args.membership),'capture_seal_sha256':sha(args.capture/'RESULT_SEAL.json'),
        'files':{p.name:sha(p) for p in args.output.iterdir() if p.is_file()}})
    print(json.dumps({'tested_policies':len(table),'selected':best['policy'],'metrics':best['metrics']},ensure_ascii=False))


if __name__=='__main__':main()
