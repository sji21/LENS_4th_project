"""Create a human-auditable content rubric from the fixed DEV100-v2 answer book."""
from __future__ import annotations
import argparse, csv, json, re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
EXPECTED=ROOT/'data/eval/dev100-v2/raw/01_최종정답_근거통합본.md'
HEADING=re.compile(r'^## (DEV-\d{3}) —',re.M)
SECTIONS=(
 ('must_answer','### 현재 정보로 반드시 답할 내용'),
 ('avoid','### 피해야 할 단정·오답'),
 ('conditions','### 추가 확인할 사실·조건·예외'),
)
def extract(text:str)->dict[str,dict[str,str]]:
    headings=list(HEADING.finditer(text)); out={}
    for i,h in enumerate(headings):
        part=text[h.end(): headings[i+1].start() if i+1<len(headings) else len(text)]
        item={}
        for j,(key,marker) in enumerate(SECTIONS):
            start=part.find(marker)
            if start<0: item[key]=''; continue
            start+=len(marker)
            ends=[part.find(next_marker,start) for _,next_marker in SECTIONS[j+1:]]
            ends=[end for end in ends if end>=0]
            item[key]=part[start:min(ends) if ends else len(part)].strip()
        out[h.group(1)]=item
    return out
def main():
    p=argparse.ArgumentParser();p.add_argument('--results',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    expected=extract(EXPECTED.read_text(encoding='utf-8'))
    results=[json.loads(line) for line in a.results.read_text(encoding='utf-8').splitlines() if line]
    rows=[]
    for result in results:
        qid=result['qid']; answer=result['answer']; rubric=expected[qid]
        rows.append({'qid':qid,'actual_status':answer['status'],'actual_answer':answer.get('text',''),'must_answer_expected':rubric['must_answer'],'avoid_expected':rubric['avoid'],'conditions_expected':rubric['conditions'],'must_answer_verdict':'review_required','avoid_verdict':'review_required','conditions_verdict':'review_required','evidence_note':''})
    a.out.parent.mkdir(parents=True,exist_ok=True)
    with a.out.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    print(a.out);print(f'items={len(rows)}')
if __name__=='__main__': main()
