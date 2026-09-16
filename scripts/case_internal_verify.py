"""Record reproducible focused tests and syntax checks for the case change."""
import argparse,json,subprocess,sys,re,py_compile
from pathlib import Path
from case_internal_run import sha,write


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True);args=ap.parse_args()
    if args.output.exists():raise FileExistsError(args.output)
    repo=Path(__file__).resolve().parents[1]
    scripts=list((repo/'scripts').glob('case_internal*.py'))
    for p in scripts:py_compile.compile(str(p),doraise=True)
    command=[sys.executable,'-X','utf8','-m','pytest','tests/test_case_internal.py',
        'tests/test_case_internal_metrics.py','tests/test_case_internal_final_score.py',
        'tests/test_case_profile.py','tests/test_retrieval_service.py','-q']
    run=subprocess.run(command,cwd=repo,capture_output=True,text=True,encoding='utf-8')
    text=run.stdout+run.stderr;args.output.parent.mkdir(parents=True,exist_ok=True)
    log=args.output.with_suffix('.log');log.write_text(text,'utf-8')
    passed=re.search(r'(\d+) passed',text)
    write(args.output,{'command':command,'exit_code':run.returncode,'passed':int(passed.group(1)) if passed else 0,
        'failed':0 if run.returncode==0 else 1,'syntax_checked_scripts':len(scripts),'log_sha256':sha(log),
        'code_hashes':{str(p.relative_to(repo)):sha(p) for folder in ('src/retrieval','src/evaluation','tests')
            for p in (repo/folder).glob('*.py')}})
    print(text);raise SystemExit(run.returncode)


if __name__=='__main__':main()
