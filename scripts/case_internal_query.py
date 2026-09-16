"""Run a standalone case-only query without loading the product's other corpora."""
import argparse,json,os
from pathlib import Path
from case_internal_run import write


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--profile',type=Path,required=True)
    ap.add_argument('--question',required=True);ap.add_argument('--k',type=int,default=2)
    ap.add_argument('--output',type=Path,required=True);args=ap.parse_args()
    if args.output.exists():raise FileExistsError(args.output)
    os.environ.update(HF_HUB_OFFLINE='1',
        TRANSFORMERS_OFFLINE='1',ANONYMIZED_TELEMETRY='False',TOKENIZERS_PARALLELISM='false',
        LANGCHAIN_TRACING_V2='false',LANGSMITH_TRACING='false')
    import torch
    from src.retrieval.case_internal_profile import load_internal_case_profile
    torch.set_num_threads(4)
    service=load_internal_case_profile(args.profile.resolve())
    result=service.search(args.question,k_law=0,k_case=args.k,k_guide=0,k_civil=0)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    write(args.output,service.evidence_payload(result))
    print(json.dumps({'output':str(args.output),'case_count':len(result.cases),'generation_executed':False}))


if __name__=='__main__':main()
