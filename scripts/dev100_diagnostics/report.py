"""Article-anchor diagnostics, retaining conditions rather than inventing gold scores."""
import json,re,sys,argparse
from pathlib import Path
from collections import Counter,defaultdict
sys.stdout.reconfigure(encoding='utf-8')
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--run-dir',type=Path,default=Path('tmp/dev100-run'))
out=parser.parse_args().run_dir
rows=json.loads((out/'results.json').read_text(encoding='utf-8'))
inventory=json.loads((out/'db-inventory.json').read_text(encoding='utf-8'))
available={r['law_name'].replace(' ','')+'-'+r['article_number'] for r in inventory}
names=['전세사기피해자 지원 및 주거안정에 관한 특별법','민간임대주택에 관한 특별법','부동산 거래신고 등에 관한 법률','부동산거래신고법','상가건물 임대차보호법','주택임대차보호법','공공주택 특별법','공인중개사법','주민등록법','국세징수법','지방세징수법','민사소송법','민사집행법','부동산등기법','부동산등기규칙','신탁법','민법','상법','형법']
names=sorted(set(names+[s.replace(' ','') for s in names]),key=len,reverse=True)
token=re.compile('|'.join(map(re.escape,names))+r'|시행령|시행규칙|제\d+조(?:의\d+)?')
def extract(text):
    current=None; refs=[]
    for m in token.finditer(text):
        s=m.group()
        if s.startswith('제'):
            if current:refs.append(current+'-'+s)
        elif s in ('시행령','시행규칙'):
            if current:current=re.sub(r'시행령$|시행규칙$','',current)+s
        else:current=s.replace(' ','').replace('부동산거래신고법','부동산거래신고등에관한법률')
    return list(dict.fromkeys(refs))
def sections(g):
    if g['roles_as_written']:return g['roles_as_written']
    return {m.group(1):m.group(2).strip() for m in re.finditer(r'(?ms)^- \*\*([^*]+):\*\*\s*(.*?)(?=^- \*\*|\Z)',g['effective_evidence_text'])}
missing=defaultdict(set); detail=[]; counts=Counter(); changes=Counter()
md=['# PATCH-009 개발용 100문항 검색 진단','','조문 인용의 보유·반환 관측이다. 조건부·대체·보조 근거를 모두 필수로 합산하지 않는다. 항·호·목의 의미 충족, 적용 판본, 안내의 특정 구간 및 판례 적합성은 이 자동 대조로 확정하지 않는다.','']
for row in rows:
    g=row['gold']; grouped=sections(g)
    # Success prose often refers back to a different law without repeating its name.
    # Do not infer new article anchors from those cross-references.
    anchor_groups={role:text for role,text in grouped.items() if role!='검색 성공 기준'}
    refs=list(dict.fromkeys(a for text in anchor_groups.values() for a in extract(text)))
    present=[a for a in refs if a in available]; absent=[a for a in refs if a not in available]
    category='조문 인용 없음' if not refs else '인용 조문 전부 미보유' if not present else '인용 조문 일부 보유' if absent else '인용 조문 모두 보유'
    counts[category]+=1
    for a in absent:missing[a].add(row['qid'])
    ranks={mode:{a:(m['article_ids'].index(a)+1 if a in m['article_ids'] else None) for a in present} for mode,m in row['modes'].items()}
    for mode,m in row['modes'].items():changes[mode]+=int(m['law_ranking_changed'])
    detail.append({'qid':row['qid'],'inventory_observation':category,'references_by_role':{role:{'text':text,'article_anchors':extract(text) if role!='검색 성공 기준' else []} for role,text in grouped.items()},'present':present,'missing':absent,'ranks':ranks,'gold_status':g['gold_status'],'formal_score':None})
    md += [f"## {row['qid']} — {g['area']}",'',row['question'],'',f"- 근거 상태: {g['gold_status']}; {category}",'- 미보유 인용: '+(', '.join(absent) or '없음 또는 조문 미지정'),'',g['effective_evidence_text'],'','| 입력 | 보유 인용 조문 순위(TOP5) | 반환 안내 |','| --- | --- | --- |']
    for mode,m in row['modes'].items():
        ranktext='; '.join(f'{a}: {r if r else "미반환"}' for a,r in ranks[mode].items()) or '대조할 보유 조문 없음'
        guides='; '.join(e['citation'] for e in m['result']['guides']) or '없음'
        md.append(f'| {mode} | {ranktext} | {guides.replace("|","／")} |')
    md.append('')
(out/'items.md').write_text('\n'.join(md),encoding='utf-8')
(out/'diagnostics.json').write_text(json.dumps(detail,ensure_ascii=False,indent=2),encoding='utf-8')
lines=['# 미보유 인용 조문 후보','','조건부·대체·보조 인용도 포함한다. 필수 데이터 부족 확정 목록이나 최종 적재 명세가 아니다. 약칭·이하·생략된 조문은 별도 검토해야 한다.','','| 인용 조문 | 관련 문항 |','| --- | --- |']
for a,ids in sorted(missing.items()):lines.append(f'| {a} | {", ".join(sorted(ids))} |')
(out/'missing-anchors.md').write_text('\n'.join(lines),encoding='utf-8')
summary={'inventory_reference_categories':dict(counts),'unique_missing_referenced_articles':len(missing),'law_top5_changed_vs_pre_patch008':dict(changes),'gold_statuses':dict(Counter(r['gold']['gold_status'] for r in rows)),'no_formal_accuracy_calculated':True}
(out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False,indent=2))
for d in detail:
    if d['qid'] in ['DEV-001','DEV-004','DEV-006','DEV-007','DEV-010','DEV-038','DEV-071','DEV-081']:print(d['qid'],d['present'],d['missing'],d['ranks'])
