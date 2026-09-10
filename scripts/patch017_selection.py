"""Compare three fixed list-fusion policies without models or product changes."""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

from scripts.patch015_baseline import ROOT, norm, read, sha, write
from scripts.patch015_report import check_shared_bundle
from scripts.patch016_candidates import validate_capture

PLAN = ROOT/'data/eval/patch017-selection/plan.json'


def validate_product_code(root, hashes):
    """Accept LF/CRLF checkout differences against the original raw hashes."""
    for rel, expected in hashes.items():
        raw = (root/rel).read_bytes()
        lf = raw.replace(b'\r\n', b'\n')
        # The original capture used CRLF for some files and LF for others.
        variants = (raw, lf, lf.replace(b'\n', b'\r\n'))
        if not any(hashlib.sha256(data).hexdigest() == expected for data in variants):
            raise ValueError(f'Baseline product code changed: {rel}')


def blend(baseline, civil, weight, rrf_k=5):
    baseline, civil = [norm(a) for a in baseline], [norm(a) for a in civil]
    if len(set(baseline)) != len(baseline) or len(set(civil)) != len(civil):
        raise ValueError('Duplicate article in input list')
    weight = Fraction(str(weight))
    if weight <= 0 or rrf_k < 0:
        raise ValueError('Positive weight and nonnegative RRF constant required')
    scores = {}
    for ranking, scale in ((baseline, Fraction(1)), (civil, weight)):
        for rank, article in enumerate(ranking, 1):
            scores[article] = scores.get(article, Fraction(0)) + scale/(rrf_k+rank)
    positions = {a:r for r,a in enumerate(baseline)}
    return sorted(scores, key=lambda a:(-scores[a], positions.get(a,len(baseline)), a))[:5]


def measure(targets, baseline, ranked):
    targets = set(targets)
    return {str(k):{
        'any': int(bool(targets & set(ranked[:k]))) if targets else None,
        'all': int(targets <= set(ranked[:k])) if targets else None,
        'lost_required': sorted((targets & set(baseline[:k])) - set(ranked[:k])),
    } for k in (3,5)}


def aggregate(rows, cohort, variant):
    selected = [r for r in rows if r['cohort'] == cohort]
    scorable = [r for r in selected if r['targets']]
    return {
        'n':len(selected), 'scorable_n':len(scorable), 'targetless_n':len(selected)-len(scorable),
        'metrics': {str(k):{
            'AnyHit': sum(r['metrics'][variant][str(k)]['any'] for r in scorable) if scorable else None,
            'AllRequired': sum(r['metrics'][variant][str(k)]['all'] for r in scorable) if scorable else None,
            'lost_required_inputs':[{'qid':r['qid'],'mode':r['mode'],'articles':r['metrics'][variant][str(k)]['lost_required']}
                                    for r in scorable if r['metrics'][variant][str(k)]['lost_required']],
        } for k in (3,5)},
        'changed_ranking_n':sum(r['rankings'][variant] != r['rankings']['baseline'] for r in selected),
    }


def run(out):
    b15=ROOT/'data/eval/patch015-baseline/capture'
    b16=ROOT/'data/eval/patch016-candidates/capture'
    check_shared_bundle(b15)
    validate_capture(b16)
    original_manifest = read(b15/'manifest.json')
    audit = read(b15/'audit.json')
    if (audit['queries']!=235 or audit['operating_unchanged'] is not True
            or audit['dataset_unchanged'] is not True
            or original_manifest['operating_hashes_before']!=audit['operating_hashes_after']):
        raise ValueError('Invalid baseline audit')
    validate_product_code(ROOT, original_manifest['code_hashes'])
    m16=read(b16/'manifest.json')
    if sha(b15/'manifest.json')!=m16['baseline_manifest_sha256']:
        raise ValueError('Candidate/baseline link mismatch')
    for rel,digest in m16['input_policy_hashes'].items():
        if sha(ROOT/rel)!=digest: raise ValueError('Policy/input changed')
    policy=read(PLAN)
    baselines=read(b15/'results.json')
    candidates=read(b16/'results.json')
    byid={(r['qid'],r['mode']):r for r in candidates}
    if len(baselines)!=235 or len(candidates)!=235 or len(byid)!=235 or {(r['qid'],r['mode']) for r in baselines}!=set(byid):
        raise ValueError('Expected identical 235 unique inputs')
    inv=read(b15/'inventory.json')
    civil_articles={r['article_anchor'] for r in inv if r['title']=='민법'}
    cp={r['id']:r for r in read(ROOT/'data/eval/civil-review2/retrieval-plan.json')['items']}
    dp={r['qid']:r for r in read(ROOT/'data/eval/dev100-v2/diagnostic-plan.json')}
    rows=[]
    for old in baselines:
        other=byid[old['qid'],old['mode']]
        if old['query_sha256']!=other['query_sha256'] or old['query_sha256']!=hashlib.sha256(old['query'].encode()).hexdigest():
            raise ValueError('Query hash mismatch')
        base=[norm(h['article_anchor']) for h in old['laws']]
        civil=[norm(a) for a in other['rankings']['rrf']]
        if set(civil)!=civil_articles or len(civil)!=7: raise ValueError('Incomplete civil ranking')
        if old['qid'] in cp:
            p=cp[old['qid']]
            targets={norm(t) for g in p['required_groups_all_of'] for t in g['article_targets_all_of']}
            cohort='civil_'+p['track']
        else:
            p=dp[old['qid']]
            targets={norm(t['article_anchor']) for t in p['law_targets']}
            cohort='dev_'+old['mode'] if not p['historical_review_required'] else 'historical_observation'
        rankings={'baseline':base}
        for v in policy['variants']:
            rankings[v['name']]=blend(base,civil,v['civil_weight'],policy['rrf_k'])
        rows.append({'qid':old['qid'],'mode':old['mode'],'cohort':cohort,'targets':sorted(targets),
                     'rankings':rankings,'metrics':{name:measure(targets,base,ranking) for name,ranking in rankings.items()}})
    cohorts=sorted({r['cohort'] for r in rows})
    variants=['baseline', *[v['name'] for v in policy['variants']]]
    summaries={name:{cohort:aggregate(rows,cohort,name) for cohort in cohorts} for name in variants}
    decisions={}
    for name in variants[1:]:
        regressions = [r for r in rows if r['cohort'] in ('civil_required_law','dev_question_only','dev_context_diagnostic')
                       and any(r['metrics'][name][str(k)]['lost_required'] for k in (3,5))]
        improves = all(summaries[name]['civil_required_law']['metrics'][str(k)]['AllRequired'] >
                       summaries['baseline']['civil_required_law']['metrics'][str(k)]['AllRequired'] for k in (3,5))
        decisions[name]={'technical_gate_passed':not regressions and improves,
                         'regressed_inputs':len(regressions),'civil_primary_improves_both_k':improves,
                         'deployment_approved':False}
    out.mkdir(parents=True,exist_ok=False)
    write(out/'summary.json',summaries)
    write(out/'details.json',rows)
    write(out/'decisions.json',decisions)
    input_files=[PLAN,b15/'results.json',b15/'manifest.json',b15.parent/'bundle-manifest.json',
                 b16/'results.json',b16/'manifest.json',b16.parent/'bundle-manifest.json',
                 ROOT/'data/eval/civil-review2/retrieval-plan.json',ROOT/'data/eval/dev100-v2/diagnostic-plan.json']
    write(out/'manifest.json',{'schema':'patch017-selection-run-v1',
          'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
          'status':subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip(),
          'script_sha256':sha(__file__),'input_hashes':{p.relative_to(ROOT).as_posix():sha(p) for p in input_files},
          'new_retrieval_executed':False,'generation_performed':False,'product_modified':False,
          'rows':len(rows),'variants':policy['variants']})
    print(decisions)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    if not args.out.resolve().is_relative_to(ROOT/'tmp') or args.out.exists(): parser.error('Use a new directory under tmp/')
    run(args.out)
