"""Replay the three ingestion candidates against frozen development targets."""
import hashlib
from pathlib import Path
from scripts.patch026_expand import ROOT, read, sha, score
from scripts.patch025_ranking import close
from scripts.patch026_sources import SPECS

BUNDLE=ROOT/'data/eval/patch026-expansion'


def summarize(bundle=BUNDLE, verify=True):
    if verify:
        manifest=read(bundle/'bundle-manifest.json')
        required={'records.jsonl','source-manifest.json','before.json','summary.json','dependencies.json'}
        required|={f'sources/{s[0]}.html' for s in SPECS}
        required|={f'{name}/{f}.json' for name in ('full','tax','residence') for f in ('after','audit')}
        if set(manifest)!=required: raise ValueError('Incomplete bundle')
        if any(sha(bundle/p)!=v for p,v in manifest.items()): raise ValueError('Bundle hash mismatch')
        dependencies=read(bundle/'dependencies.json')
        if set(dependencies)!={'data/eval/patch024-expansion/report.json','data/eval/patch024-expansion/results.json',
                              'data/eval/patch025-ranking/live-verification.json','data/eval/patch015-baseline/capture/results.json'}:
            raise ValueError('Incomplete baseline provenance')
        for p,v in dependencies.items():
            if hashlib.sha256((ROOT/p).read_bytes().replace(b'\r\n',b'\n')).hexdigest()!=v: raise ValueError('Baseline changed')
    before=read(bundle/'before.json')
    old={(r['qid'],r['mode']):r for r in read(ROOT/'data/eval/patch024-expansion/results.json')}
    for r in read(ROOT/'data/eval/patch025-ranking/live-verification.json')['rows']:
        old[r['qid'],r['mode']]['civil_laws']=r['civil_laws']
    if len(before)!=235 or {(r['qid'],r['mode']) for r in before}!=set(old): raise ValueError('Incomplete before rows')
    if any(r!=old[r['qid'],r['mode']] for r in before): raise ValueError('Baseline result mismatch')
    available=read(bundle/'full/audit.json')['available_before']
    result={'baseline':score(before,available,before)['groups'],'variants':{},'operating_adopted':False}
    for name in ('full','tax','residence'):
        after=read(bundle/name/'after.json'); audit=read(bundle/name/'audit.json')
        if len(after)!=235 or {(r['qid'],r['mode']) for r in after}!=set(old): raise ValueError('Incomplete after rows')
        if any(r[k]!=old[r['qid'],r['mode']][k] for r in after for k in ('civil_laws','cases','guides')): raise ValueError('Other channel changed')
        added={s[1].replace(' ','')+'-'+s[2] for s in SPECS if name=='full' or (s[0]=='RR16')==(name=='residence')}
        inventory=set(audit['available_after'] if name=='full' else audit['available'])
        if inventory!=set(available)|added: raise ValueError('Inventory mismatch')
        report=score(after,inventory,before)
        result['variants'][name]={'added':sorted(added),'groups':report['groups'],'losses':report['losses'],
                                 'adoption_gate_passed':not report['losses']}
    if verify and not close(result,read(bundle/'summary.json')): raise ValueError('Summary mismatch')
    return result


if __name__=='__main__':
    result=summarize()
    print('Three variants replayed on 235 inputs each; loss counts:',{k:len(v['losses']) for k,v in result['variants'].items()})
