"""Record reproducible focused tests and syntax checks for the case change."""
import argparse,json,subprocess,sys,re,py_compile
from pathlib import Path
from experiments.patch043_case_internal.scripts.case_internal_run import sha,write


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True);args=ap.parse_args()
    if args.output.exists():raise FileExistsError(args.output)
    repo=Path(__file__).resolve().parents[3]
    experiment_root=Path(__file__).resolve().parents[1]
    scripts=list((experiment_root/'scripts').glob('case_internal*.py'))
    for p in scripts:py_compile.compile(str(p),doraise=True)
    command=[sys.executable,'-X','utf8','-m','pytest','experiments/patch043_case_internal/tests/test_case_internal.py',
        'experiments/patch043_case_internal/tests/test_case_internal_metrics.py','experiments/patch043_case_internal/tests/test_case_internal_final_score.py',
        'tests/test_case_profile.py','tests/test_retrieval_service.py','-q']
    run=subprocess.run(command,cwd=repo,capture_output=True,text=True,encoding='utf-8')
    text=run.stdout+run.stderr;args.output.parent.mkdir(parents=True,exist_ok=True)
    log=args.output.with_suffix('.log');log.write_text(text,'utf-8')
    passed=re.search(r'(\d+) passed',text)
    write(args.output,{'command':command,'exit_code':run.returncode,'passed':int(passed.group(1)) if passed else 0,
        'failed':0 if run.returncode==0 else 1,'syntax_checked_scripts':len(scripts),'log_sha256':sha(log),
        'code_hashes':({str(p.relative_to(repo)):sha(p) for p in (repo/'src/retrieval').glob('*.py')}
                        | {str(p.relative_to(repo)):sha(p) for p in experiment_root.rglob('*.py')}
                        | {str(p.relative_to(repo)):sha(p) for p in (repo/'tests').glob('test_case_profile.py')} )})
    print(text);raise SystemExit(run.returncode)


if __name__=='__main__':main()
